"""Outcome predictors: Tier 0 deterministic, Tier 1 empirical — behind one protocol.

The point of this module is a *narrow, replaceable* seam: a caller asks for a
forecast and never learns whether it came from a rule, a counted history, or a
learned model. That is what lets Tier 2+ be added without touching the selector.

Two tiers ship, and the reason for stopping at two is evidence, not taste:

**Tier 0 — :class:`DeterministicPredictor`.** Stdlib only. No model, no network,
no API key, no credentials. It combines an explicit prior with rule-based
adjustments over :class:`~alpha.rsi.predictive.contracts.CandidateFeatures`:
a saturated edit budget, an untried structural component, and a long plan are
all defensible reasons to expect less, each stated in ``assumptions`` so the
caller can see which rule moved the number. This is the reproducible baseline
Tier 1 must beat to be worth shipping at all.

**Tier 1 — :class:`EmpiricalPredictor`.** Success rate per ``(component,
regime)`` stratum, from outcomes the predictor was actually shown. Three
properties make it honest rather than merely counting:

* **Minimum-sample floor.** Below ``min_samples_per_stratum`` it **abstains**.
  A stratum with one observation is not a rate, it is a memory, and reporting it
  as `1.0` would be the most dangerous single line in this module.
* **Shrinkage toward the global prior.** ``(successes + α·prior) / (n + α)``,
  so a small stratum is pulled toward the population instead of sitting at an
  extreme. 1-of-1 with ``α=4`` and prior ``0.5`` reports ``0.6``, not ``1.0``.
* **Wilson interval** for the reported uncertainty, because a bare point
  estimate on a small stratum reads as certainty it does not have.

:func:`resolve_predictor` is the one lookup seam and refuses an unknown backend
by name rather than falling back — a silent fallback to Tier 0 would let an
operator believe they configured the empirical predictor when they did not.

**Both tiers refuse to learn from anything unmeasured.** :meth:`observe`
requires ``ObservedOutcome.measured``; a ``simulated`` or ``unverified`` label
is refused with a reason. Without that rule a preview's placeholder numbers
would become the training set of the thing meant to predict reality.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from alpha.rsi.predictive.contracts import (
    CandidateFeatures,
    ForecastStatus,
    ObservedOutcome,
    OutcomePrediction,
    PredictorHealth,
    PredictorIdentity,
    abstained,
    new_prediction_id,
)

__all__ = [
    "DEFAULT_PRIOR",
    "MIN_EDIT_BUDGET_FOR_CONFIDENCE",
    "PREDICTOR_BACKENDS",
    "DeterministicPredictor",
    "EmpiricalPredictor",
    "OutcomePredictor",
    "PredictorParams",
    "wilson_interval",
    "resolve_predictor",
]

#: The neutral starting probability. Alpha's own release gate treats an
#: unverified result as a disclosed 0.5-neutral (``alpha.benchmarks.release_gate``);
#: a predictor with no evidence has exactly as much claim to a different number.
DEFAULT_PRIOR = 0.5

#: A saturated edit budget means the round is at/past its annealing horizon and
#: may only tune. Such a candidate has had its exploration budget spent, so Tier 0
#: discounts it by this much — a declared rule constant, stated here and in the
#: emitted assumptions rather than buried in the arithmetic.
MIN_EDIT_BUDGET_FOR_CONFIDENCE = 0.95

_STRONG_PRIOR_STEP_PENALTY = 0.25
_WEAK_PRIOR_STEP_PENALTY = 0.08
_LONG_PLAN_STEPS = 8
_NOVELTY_BONUS = 0.05


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Chosen over the normal approximation because that one is meaningless at the
    sample sizes this system actually has: a 1-of-1 stratum under a normal
    interval yields a degenerate or inverted range, which is how a small stratum
    ends up advertising false certainty.

    Returns ``(low, high)``; raises on a non-positive total rather than
    returning ``(0.0, 1.0)``, which would read as "maximally uncertain" and hide
    that the caller had no data at all.
    """
    if isinstance(successes, bool) or isinstance(total, bool) or not isinstance(successes, int) or not isinstance(total, int):
        raise ValueError(f"wilson_interval takes integer counts; got successes={successes!r}, total={total!r}")
    if total <= 0:
        raise ValueError(f"wilson_interval requires total > 0; got {total}. A zero-sample stratum has no interval, it has an abstention.")
    if not 0 <= successes <= total:
        raise ValueError(f"wilson_interval requires 0 <= successes <= total; got successes={successes}, total={total}")
    n = float(total)
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    margin = (z / denominator) * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass(frozen=True)
class PredictorParams:
    """Validated knobs for the empirical tier.

    Bounds are **refused, never clamped** (house rule): a silently clamped
    ``min_samples_per_stratum=0`` would turn "abstain below a floor" into "never
    abstain", which is the exact opposite of what the operator wrote.
    """

    prior: float = DEFAULT_PRIOR
    prior_weight: float = 4.0
    min_samples_global: int = 12
    min_samples_per_stratum: int = 3
    max_prediction_age_seconds: float = 3600.0

    def validate(self) -> PredictorParams:
        if not _is_number(self.prior) or not 0.0 <= float(self.prior) <= 1.0:
            raise ValueError(f"prior must be a probability in [0, 1]; got {self.prior!r}")
        if not _is_number(self.prior_weight) or float(self.prior_weight) <= 0:
            raise ValueError(f"prior_weight must be a number > 0 (0 would disable shrinkage entirely); got {self.prior_weight!r}")
        for name in ("min_samples_global", "min_samples_per_stratum"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an integer >= 0; got {value!r}")
        if not _is_number(self.max_prediction_age_seconds) or float(self.max_prediction_age_seconds) <= 0:
            raise ValueError(f"max_prediction_age_seconds must be a number > 0; got {self.max_prediction_age_seconds!r}")
        return self


@runtime_checkable
class OutcomePredictor(Protocol):
    """The one seam a caller programs against."""

    @property
    def identity(self) -> PredictorIdentity:
        """Who this predictor is, at which version and tier."""

    def predict(self, candidate: CandidateFeatures) -> OutcomePrediction:
        """Forecast ``candidate``. May return an abstention with a reason."""

    def observe(self, outcome: ObservedOutcome) -> None:
        """Feed one measured outcome. Unmeasured outcomes are refused."""

    def health(self) -> PredictorHealth:
        """Current self-reported state; unmeasured facts stay ``None``."""


class DeterministicPredictor:
    """Tier 0: rules over declared features. Stdlib only, fully reproducible."""

    def __init__(self, params: PredictorParams | None = None) -> None:
        self._params = (params or PredictorParams()).validate()
        self._observed = 0

    @property
    def identity(self) -> PredictorIdentity:
        return PredictorIdentity(predictor_id="deterministic_baseline", version="v1", tier=0)

    def predict(self, candidate: CandidateFeatures) -> OutcomePrediction:
        params = self._params
        assumptions: list[str] = [f"prior={params.prior} (Alpha's disclosed unverified neutral)"]
        probability = float(params.prior)

        if candidate.budget_saturated:
            probability -= _STRONG_PRIOR_STEP_PENALTY
            assumptions.append(f"edit budget saturated (-{_STRONG_PRIOR_STEP_PENALTY}): the round is at/past its annealing horizon and may only tune")
        elif candidate.edit_budget is not None and candidate.edit_budget >= 1:
            assumptions.append(f"edit budget b={candidate.edit_budget} is unsaturated, so no budget discount applies")

        if candidate.structural_novelty is not None and candidate.structural_novelty > 0:
            probability += _NOVELTY_BONUS
            assumptions.append(f"structural novelty ν={candidate.structural_novelty} (+{_NOVELTY_BONUS}): an untried component is where a structural gain can still come from")

        if candidate.steps > _LONG_PLAN_STEPS:
            probability -= _WEAK_PRIOR_STEP_PENALTY
            assumptions.append(f"plan length {candidate.steps} > {_LONG_PLAN_STEPS} (-{_WEAK_PRIOR_STEP_PENALTY}): more steps means more opportunities to regress")
        else:
            assumptions.append(f"plan length {candidate.steps} is within the short-plan band (<= {_LONG_PLAN_STEPS}), so no length discount applies")

        probability = max(0.0, min(1.0, probability))
        # A deterministic rule has no sampling uncertainty to quantify, and
        # claiming a number it cannot support would be the same lie as a
        # confident point estimate — so the half-width is disclosed as `None`.
        return OutcomePrediction(
            prediction_id=new_prediction_id(),
            candidate_id=candidate.candidate_id,
            identity=self.identity,
            status=ForecastStatus.PREDICTED,
            predicted_success_probability=probability,
            uncertainty=None,
            support=self._observed,
            assumptions=tuple(assumptions),
        )

    def observe(self, outcome: ObservedOutcome) -> None:
        """Count measured outcomes for support reporting only.

        Tier 0 is deliberately **not** trained. It is the fixed baseline every
        later tier has to beat; letting it drift would destroy the only
        reproducible reference in the system.
        """
        if not outcome.measured:
            return
        self._observed += 1

    def health(self) -> PredictorHealth:
        return PredictorHealth(
            available=True,
            detail=f"tier 0 deterministic rules over declared features; prior={self._params.prior}; untrained by design (it is the fixed baseline)",
            observed_outcomes=self._observed,
            scored_forecasts=None,
            calibrated=None,
            abstained_forecasts=None,
        )


@dataclass
class _Stratum:
    """Counted successes/total for one ``(component, regime)`` key."""

    successes: int = 0
    total: int = 0


class EmpiricalPredictor:
    """Tier 1: measured success rate per stratum, shrunk toward the global prior.

    State lives in memory and is (re)built from the recorded outcomes the service
    feeds it. It is intentionally **not** a second persistent store: the durable
    record is the forecast ledger, and rebuilding from it means a restart cannot
    leave a predictor holding counts that no longer match any evidence.
    """

    def __init__(self, params: PredictorParams | None = None) -> None:
        self._params = (params or PredictorParams()).validate()
        self._strata: dict[tuple[str, str], _Stratum] = defaultdict(_Stratum)
        self._successes = 0
        self._observed = 0
        self._abstained = 0
        self._unusable = 0
        self._unattributed = 0

    @property
    def identity(self) -> PredictorIdentity:
        return PredictorIdentity(predictor_id="empirical_baseline", version="v1", tier=1)

    def _global_rate(self) -> float:
        if self._observed == 0:
            return float(self._params.prior)
        alpha = float(self._params.prior_weight)
        return (self._successes + alpha * float(self._params.prior)) / (self._observed + alpha)

    def predict(self, candidate: CandidateFeatures) -> OutcomePrediction:
        params = self._params
        key = candidate.stratum()
        stratum = self._strata.get(key)

        if self._observed < params.min_samples_global:
            self._abstained += 1
            return abstained(
                candidate.candidate_id,
                self.identity,
                f"insufficient global sample support: {self._observed} measured outcome(s) observed, {params.min_samples_global} required before any rate is reported",
                support=self._observed,
                assumptions=(f"a prior of {params.prior} exists but is not reported as a prediction: below the global floor, a number would be indistinguishable from the prior itself",),
            )

        if stratum is None or stratum.total < params.min_samples_per_stratum:
            seen = 0 if stratum is None else stratum.total
            self._abstained += 1
            return abstained(
                candidate.candidate_id,
                self.identity,
                f"insufficient stratum support for {key[0]}/{key[1]}: {seen} measured outcome(s), {params.min_samples_per_stratum} required",
                support=seen,
                assumptions=(f"global support is sufficient ({self._observed} >= {params.min_samples_global}) but this stratum is too thin to quote a rate",),
            )

        assert stratum is not None  # narrowed by the check above; a bare assert would vanish under python -O
        alpha = float(params.prior_weight)
        probability = (stratum.successes + alpha * float(params.prior)) / (stratum.total + alpha)
        low, high = wilson_interval(stratum.successes, stratum.total)
        return OutcomePrediction(
            prediction_id=new_prediction_id(),
            candidate_id=candidate.candidate_id,
            identity=self.identity,
            status=ForecastStatus.PREDICTED,
            predicted_success_probability=probability,
            uncertainty=high - probability,
            support=stratum.total,
            assumptions=(
                f"stratum {key[0]}/{key[1]}: {stratum.successes}/{stratum.total} measured successes",
                f"shrunk toward the prior with weight {params.prior_weight}: (s + α·prior)/(n + α)",
                f"Wilson 95% interval [{low:.3f}, {high:.3f}]",
            ),
        )

    def observe(self, outcome: ObservedOutcome) -> None:
        """Record one **measured** outcome.

        Refusals are disclosed rather than silent:

        * an unmeasured outcome (``simulated`` / ``unverified``) is refused and
          counted in ``unusable_outcomes`` — otherwise a preview's placeholder
          numbers would become this predictor's training set;
        * a measured outcome with **no component attribution** cannot be
          stratified, so it is counted in ``unattributed_outcomes`` and excluded
          from the rates instead of being filed under a fabricated bucket.
        """
        if not outcome.measured:
            self._unusable += 1
            return
        assert outcome.success is not None  # narrowed by `.measured`
        key = outcome.stratum()
        if not key[0]:
            self._unattributed += 1
            return
        stratum = self._strata[key]
        stratum.total += 1
        if outcome.success:
            stratum.successes += 1
        self._observed += 1
        if outcome.success:
            self._successes += 1

    def health(self) -> PredictorHealth:
        detail = (
            f"tier 1 empirical rates over {len(self._strata)} recorded stratum/strata; global rate={self._global_rate():.3f}; "
            f"{self._unusable} unmeasured outcome(s) refused as training data; "
            f"{self._unattributed} measured outcome(s) excluded for lacking a component attribution"
        )
        return PredictorHealth(
            available=True,
            detail=detail,
            observed_outcomes=self._observed,
            scored_forecasts=None,
            calibrated=None,
            abstained_forecasts=self._abstained,
        )

    @property
    def strata(self) -> dict[tuple[str, str], tuple[int, int]]:
        """``(component, regime) -> (successes, total)`` — a copy, for disclosure."""
        return {key: (value.successes, value.total) for key, value in self._strata.items()}


#: Backends :func:`resolve_predictor` accepts. An unknown name is refused by
#: name: falling back to Tier 0 would let an operator believe they configured
#: the empirical predictor when they configured nothing at all.
PREDICTOR_BACKENDS: frozenset[str] = frozenset({"deterministic", "empirical_baseline"})


def resolve_predictor(backend: str, params: PredictorParams | None = None) -> OutcomePredictor:
    """Return the predictor for ``backend``.

    Refuses an unknown backend naming the accepted set, rather than defaulting.
    """
    if not isinstance(backend, str) or backend not in PREDICTOR_BACKENDS:
        raise ValueError(f"unknown predictor backend {backend!r}; accepted backends are {sorted(PREDICTOR_BACKENDS)}")
    if backend == "deterministic":
        return DeterministicPredictor(params)
    return EmpiricalPredictor(params)
