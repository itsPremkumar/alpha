"""Plasticity controller: how aggressively Alpha is currently allowed to learn.

The rule this implements, in one sentence
-----------------------------------------
*More self-modification is not more intelligence.* The controller therefore
never increases plasticity because a single measurement improved — it requires
a **trend** over ``plasticity.window`` observations, and it distinguishes four
outcomes that are genuinely different states rather than one "learn more" dial:

===================  ==========================================================
Outcome              Condition
===================  ==========================================================
``INCREASE``         held-out trend improving, no regression, low failure rate
``MAINTAIN``         stable
``DECREASE``         no improvement, or a mild regression
``FREEZE``           forgetting, or a regression worse than the configured bar
``ROLLBACK``         regression worse than ``rollback_regression_delta``
===================  ==========================================================

Why trend, not point
--------------------
:class:`PlasticityController.observe` refuses to decide on fewer than
``window`` observations and reports ``insufficient_observations`` until then.
This is the module's central honesty property: a single lucky evaluation must not
be able to double the learning rate, and the reason is not caution but
arithmetic — one point has no slope.

Tiers, and why they are the opposite of intuitive
-------------------------------------------------
The multipliers are *increasing* with how little history a surface has, which is
deliberately backwards from "protect what is stable":

* ``core`` (0.05) — the stable core learns slowest, because catastrophic
  forgetting lands here.
* ``router`` (0.25) — routing adapts cheaply and its regressions are local.
* ``expert`` (0.6) — learned experts adapt faster; they have lineage and can be
  pruned back.
* ``new_expert`` (1.0) — a trial expert has no history to forget, so it is
  unconstrained.

The effective multiplier for any surface is
``tier_multiplier x direction_multiplier``, and it is clamped to ``[0, 1]``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "PlasticityAction",
    "PlasticityTier",
    "Observation",
    "PlasticityDecision",
    "PlasticityController",
    "PlasticityState",
]


class PlasticityAction(StrEnum):
    """What the controller decided."""

    INCREASE = "INCREASE"
    MAINTAIN = "MAINTAIN"
    DECREASE = "DECREASE"
    FREEZE = "FREEZE"
    ROLLBACK = "ROLLBACK"

    @property
    def direction(self) -> int:
        """Signed multiplier on top of the tier's own value."""
        return {
            PlasticityAction.INCREASE: 1,
            PlasticityAction.MAINTAIN: 0,
            PlasticityAction.DECREASE: -1,
            PlasticityAction.FREEZE: -2,
            PlasticityAction.ROLLBACK: -3,
        }[self]


class PlasticityTier(StrEnum):
    """Which class of surface a multiplier applies to."""

    CORE = "core"
    ROUTER = "router"
    EXPERT = "expert"
    NEW_EXPERT = "new_expert"


@dataclass(frozen=True)
class Observation:
    """One measured point fed to the controller.

    Every field is optional **except** that an observation carrying neither a
    held-out nor a train score is refused by the controller — an observation with
    no measurement is not evidence, and accepting it would let an unmeasured
    cycle look like a neutral one.
    """

    train_score: float | None = None
    heldout_score: float | None = None
    regression_score: float | None = None
    failure_rate: float | None = None
    novelty: float | None = None
    expert_usefulness: float | None = None
    new_information_rate: float | None = None
    label: str = ""

    def has_measurement(self) -> bool:
        return any(value is not None for value in (self.train_score, self.heldout_score, self.regression_score))

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_score": self.train_score,
            "heldout_score": self.heldout_score,
            "regression_score": self.regression_score,
            "failure_rate": self.failure_rate,
            "novelty": self.novelty,
            "expert_usefulness": self.expert_usefulness,
            "new_information_rate": self.new_information_rate,
            "label": self.label,
        }


@dataclass
class PlasticityDecision:
    """A decision plus everything needed to explain it."""

    action: PlasticityAction
    reasons: list[str] = field(default_factory=list)
    heldout_trend: float | None = None
    regression_trend: float | None = None
    forgetting_ratio: float | None = None
    overfitting_gap: float | None = None
    observations_used: int = 0
    insufficient_observations: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reasons": list(self.reasons),
            "heldout_trend": self.heldout_trend,
            "regression_trend": self.regression_trend,
            "forgetting_ratio": self.forgetting_ratio,
            "overfitting_gap": self.overfitting_gap,
            "observations_used": self.observations_used,
            "insufficient_observations": self.insufficient_observations,
        }


@dataclass
class PlasticityState:
    """Persisted controller state: the observation window and the action."""

    action: PlasticityAction = PlasticityAction.MAINTAIN
    history: list[Observation] = field(default_factory=list)
    window: int = 5
    peak_heldout: float | None = None
    """Highest held-out score ever seen. Retention is measured against this, so
    forgetting is a drop from a real peak rather than from an arbitrary line."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "history": [obs.to_dict() for obs in self.history],
            "window": self.window,
            "peak_heldout": self.peak_heldout,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlasticityState:
        if not isinstance(data, dict):
            raise ValueError(f"PlasticityState payload must be an object, got {type(data).__name__}")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"PlasticityState has unknown field(s): {unknown}")
        history = [Observation(**dict(item)) for item in data.get("history", [])]
        return cls(
            action=PlasticityAction(data.get("action", PlasticityAction.MAINTAIN)),
            history=history,
            window=int(data.get("window", 5)),
            peak_heldout=data.get("peak_heldout"),
        )


def _linear_trend(values: list[float]) -> float:
    """Least-squares slope of ``values`` against their index.

    Returns the per-observation change. A constant series yields exactly
    ``0.0`` — "stable" is a measurement, not an absence of one.
    """
    count = len(values)
    if count < 2:
        return 0.0
    mean_x = (count - 1) / 2.0
    mean_y = sum(values) / count
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    if denominator == 0:
        return 0.0
    return numerator / denominator


class PlasticityController:
    """Trend-driven, tiered learning-rate controller.

    Holds no global state; :meth:`decide` is a pure function of the supplied
    observations plus the persisted peak. Persistence is the caller's business
    (this module is pure logic), which is why :class:`PlasticityState` exists as
    a separate serialisable value.
    """

    def __init__(
        self,
        *,
        tier_multipliers: dict[PlasticityTier, float] | None = None,
        window: int = 5,
        rollback_regression_delta: float = 0.05,
        forgetting_ratio: float = 0.15,
        overfitting_gap: float = 0.2,
        enabled: bool = False,
    ) -> None:
        if window < 2:
            raise ValueError(f"window must be >= 2 (one measurement is not a trend), got {window}")
        if not 0 < forgetting_ratio <= 1.0:
            raise ValueError(f"forgetting_ratio must be within (0.0, 1.0], got {forgetting_ratio}")
        if rollback_regression_delta <= 0:
            raise ValueError(f"rollback_regression_delta must be > 0, got {rollback_regression_delta}")
        self.tier_multipliers = dict(tier_multipliers or {})
        self.window = int(window)
        self.rollback_regression_delta = float(rollback_regression_delta)
        self.forgetting_ratio = float(forgetting_ratio)
        self.overfitting_gap = float(overfitting_gap)
        self.enabled = bool(enabled)
        self._lock = threading.RLock()

    # -- tier multipliers ---------------------------------------------------

    def base_multiplier(self, tier: PlasticityTier) -> float:
        return float(self.tier_multipliers.get(tier, 0.0))

    def effective_multiplier(self, tier: PlasticityTier, action: PlasticityAction) -> float:
        """``tier_multiplier`` scaled by ``action`` and clamped to ``[0, 1]``.

        A disabled controller returns the tier value untouched — "disabled"
        means "not plastic", not "plastic in some other way".
        """
        base = self.base_multiplier(tier)
        if not self.enabled:
            return base
        scale = {1: 1.0, 0: 0.75, -1: 0.5, -2: 0.25, -3: 0.0}[action.direction]
        return max(0.0, min(1.0, base * scale))

    # -- decision -----------------------------------------------------------

    def decide(
        self,
        observations: list[Observation],
        *,
        peak_heldout: float | None = None,
        failure_rate: float | None = None,
    ) -> PlasticityDecision:
        """Decide from the last ``window`` observations. Never raises on bad input."""
        reasons: list[str] = []
        window = list(observations)[-self.window :]
        if len(window) < self.window:
            return PlasticityDecision(
                action=PlasticityAction.MAINTAIN,
                reasons=[f"only {len(window)} of {self.window} required observations; a single measurement has no slope, so plasticity is held at MAINTAIN rather than guessed"],
                observations_used=len(window),
                insufficient_observations=True,
            )

        heldout_values = [obs.heldout_score for obs in window if obs.heldout_score is not None]
        regression_values = [obs.regression_score for obs in window if obs.regression_score is not None]
        train_values = [obs.train_score for obs in window if obs.train_score is not None]

        if not heldout_values and not regression_values and not train_values:
            return PlasticityDecision(
                action=PlasticityAction.MAINTAIN,
                reasons=["no observation in the window carried a measurement; an unmeasured cycle is not a neutral one"],
                observations_used=len(window),
                insufficient_observations=True,
            )

        heldout_trend = _linear_trend(heldout_values) if len(heldout_values) >= 2 else None
        regression_trend = _linear_trend(regression_values) if len(regression_values) >= 2 else None

        # Regression: a falling trend is the strongest negative signal, and a
        # large one is the only thing that may trigger ROLLBACK.
        if regression_trend is not None and regression_trend < -self.rollback_regression_delta:
            return PlasticityDecision(
                action=PlasticityAction.ROLLBACK,
                reasons=[f"regression score trend {regression_trend:+.4f} per observation is worse than the rollback bar {-self.rollback_regression_delta:+.4f}; restore the last snapshot"],
                heldout_trend=heldout_trend,
                regression_trend=regression_trend,
                observations_used=len(window),
            )
        if regression_trend is not None and regression_trend < 0:
            reasons.append(f"regression score is declining ({regression_trend:+.4f} per observation)")

        # Forgetting: measured against the recorded peak, not an absolute line.
        forgetting_ratio: float | None = None
        current_heldout = heldout_values[-1] if heldout_values else None
        if current_heldout is not None and peak_heldout:
            forgetting_ratio = max(0.0, 1.0 - (current_heldout / peak_heldout)) if peak_heldout else 0.0
            if forgetting_ratio >= self.forgetting_ratio:
                return PlasticityDecision(
                    action=PlasticityAction.FREEZE,
                    reasons=[
                        f"held-out score {current_heldout:.4f} is {forgetting_ratio:.1%} below the recorded peak "
                        f"{peak_heldout:.4f} (bar {self.forgetting_ratio:.1%}); forgetting is the strongest signal of "
                        "catastrophic forgetting, so learning freezes and replay is raised",
                        *reasons,
                    ],
                    heldout_trend=heldout_trend,
                    regression_trend=regression_trend,
                    forgetting_ratio=forgetting_ratio,
                    observations_used=len(window),
                )
            reasons.append(f"held-out retention {forgetting_ratio:.1%} below peak is within the {self.forgetting_ratio:.1%} bar")

        # Overfitting: train up while held-out is flat or down.
        overfitting_gap: float | None = None
        if train_values and current_heldout is not None and len(train_values) >= 2:
            train_trend = _linear_trend(train_values)
            overfitting_gap = train_values[-1] - current_heldout
            if train_trend > 0 and (heldout_trend is None or heldout_trend <= 0) and overfitting_gap > self.overfitting_gap:
                return PlasticityDecision(
                    action=PlasticityAction.FREEZE,
                    reasons=[
                        f"train score is improving ({train_trend:+.4f}/observation) while held-out is flat or falling, "
                        f"gap {overfitting_gap:+.4f} > {self.overfitting_gap:+.4f}: possible overfitting, so increase "
                        "replay and reduce plasticity",
                        *reasons,
                    ],
                    heldout_trend=heldout_trend,
                    regression_trend=regression_trend,
                    overfitting_gap=overfitting_gap,
                    observations_used=len(window),
                )

        # Failure rate, from the caller's aggregate when the window has none.
        rate = failure_rate
        if rate is None:
            observed = [obs.failure_rate for obs in window if obs.failure_rate is not None]
            rate = sum(observed) / len(observed) if observed else None
        if rate is not None:
            reasons.append(f"recent failure rate {rate:.1%}")
            if rate >= 0.5:
                return PlasticityDecision(
                    action=PlasticityAction.DECREASE,
                    reasons=[f"recent failure rate {rate:.1%} is at or above 50%; reduce plasticity", *reasons],
                    heldout_trend=heldout_trend,
                    regression_trend=regression_trend,
                    forgetting_ratio=forgetting_ratio,
                    overfitting_gap=overfitting_gap,
                    observations_used=len(window),
                )

        # Final: held-out trend decides increase vs maintain vs decrease.
        if heldout_trend is None:
            action = PlasticityAction.MAINTAIN
            reasons.append("no held-out trend available (fewer than two held-out measurements); holding steady")
        elif heldout_trend > 0.01:
            action = PlasticityAction.INCREASE
            reasons.append(f"held-out score improving at {heldout_trend:+.4f} per observation with no regression; increasing plasticity")
        elif heldout_trend < -0.01:
            action = PlasticityAction.DECREASE
            reasons.append(f"held-out score declining at {heldout_trend:+.4f} per observation; reducing plasticity")
        else:
            action = PlasticityAction.MAINTAIN
            reasons.append(f"held-out score is stable ({heldout_trend:+.4f} per observation); holding plasticity")

        return PlasticityDecision(
            action=action,
            reasons=reasons,
            heldout_trend=heldout_trend,
            regression_trend=regression_trend,
            forgetting_ratio=forgetting_ratio,
            overfitting_gap=overfitting_gap,
            observations_used=len(window),
        )

    # -- accumulation -------------------------------------------------------

    def push(self, state: PlasticityState, observation: Observation) -> PlasticityDecision:
        """Append ``observation`` to ``state`` (bounded to the window), then decide.

        Updates :attr:`PlasticityState.peak_heldout` only when a held-out score
        was actually measured. That is what makes forgetting measurable: without
        a peak recorded from a *real* measurement, retention cannot be computed
        and the controller refuses to claim it.
        """
        if not observation.has_measurement():
            return PlasticityDecision(
                action=state.action,
                reasons=["observation carried no measurement and was not recorded; an unmeasured cycle cannot move the trend"],
                observations_used=len(state.history),
                insufficient_observations=True,
            )
        with self._lock:
            state.history.append(observation)
            if len(state.history) > max(self.window, 1):
                del state.history[: len(state.history) - self.window]
            if observation.heldout_score is not None:
                prior_peak = state.peak_heldout
                state.peak_heldout = observation.heldout_score if prior_peak is None else max(prior_peak, observation.heldout_score)
            decision = self.decide(state.history, peak_heldout=state.peak_heldout)
            state.action = decision.action
            return decision

    @classmethod
    def from_config(cls, config: Any = None) -> PlasticityController:
        """Build from the ``intelligence.plasticity`` config block."""
        if config is None:
            from alpha.intelligence.config import intelligence_config

            config = intelligence_config().plasticity
        return cls(
            tier_multipliers={
                PlasticityTier.CORE: config.core_multiplier,
                PlasticityTier.ROUTER: config.router_multiplier,
                PlasticityTier.EXPERT: config.expert_multiplier,
                PlasticityTier.NEW_EXPERT: config.new_expert_multiplier,
            },
            window=config.window,
            rollback_regression_delta=config.rollback_regression_delta,
            forgetting_ratio=config.forgetting_ratio,
            enabled=config.enabled,
        )

    def status(self) -> dict[str, Any]:
        """A JSON-safe summary, for the self-knowledge surface."""
        return {
            "enabled": self.enabled,
            "window": self.window,
            "rollback_regression_delta": self.rollback_regression_delta,
            "forgetting_ratio": self.forgetting_ratio,
            "overfitting_gap": self.overfitting_gap,
            "tier_multipliers": {tier.value: self.base_multiplier(tier) for tier in PlasticityTier},
            "effective_now": {tier.value: round(self.effective_multiplier(tier, PlasticityAction.MAINTAIN), 4) for tier in PlasticityTier},
        }
