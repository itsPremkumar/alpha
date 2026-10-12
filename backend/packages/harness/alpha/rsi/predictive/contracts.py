"""Predictive RSI contracts: a forecast is not a measurement, and the types say so.

This module owns the vocabulary every other module in
:mod:`alpha.rsi.predictive` speaks. Three properties are load-bearing and each
one exists because a plausible alternative produces a specific lie:

1. **A forecast can never be mistaken for an observation.** Every forecast
   carries ``evidence_kind="forecast"``. The existing RSI gates
   (:func:`alpha.rsi.holdout.holdout_gate`,
   :func:`alpha.rsi.rrsi.selection.election_gate_for`) only accept
   ``evidence_kind == "measured"``, so stamping a forecast with its own label is
   what makes it *structurally* incapable of promoting — the safety property is
   carried by the same field the gates already test, not by a convention someone
   has to remember. The field is pinned on the dataclass, so a caller cannot
   override it.

2. **Predicted and measured quantities never share a name.** A forecast says
   ``predicted_success_probability``; an observation says
   ``measured_success``. There is no ``score`` key a downstream consumer could
   read without knowing which side it came from. This is deliberately more
   verbose than the surrounding ``Ŝ``/``ΔS`` vocabulary — the surrounding
   notation is fine when everything is measured and becomes dangerous the moment
   forecasts and measurements share a namespace.

3. **Unknown is ``None``, never ``0.0``.** An absent prediction, an absent
   outcome, an uncalibrated report and a missing sample count are all ``None``.
   Every numeric here is ``float | None``, and ``bool`` is rejected as a number
   throughout, matching the house rule that ``True`` is not a probability of 1.

House doctrine inherited from :mod:`alpha.rsi.rrsi`: bounds are refused, never
clamped; a boolean is never accepted as a number; an absent store is a first run
while an unreadable one is a disclosed failure.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "CANDIDATE_MAX_STEPS",
    "FORECAST_EVIDENCE_KIND",
    "MEASURED_EVIDENCE_KIND",
    "PREDICTION_SCHEMA_VERSION",
    "CandidateFeatures",
    "ForecastStatus",
    "ObservedOutcome",
    "OutcomePrediction",
    "PredictorHealth",
    "PredictorIdentity",
    "abstained",
    "new_prediction_id",
]

#: Bumped whenever the forecast payload's meaning changes. A stored record whose
#: version does not match is refused rather than reinterpreted — silently parsing
#: an old payload under new semantics is how a stale forecast becomes evidence.
PREDICTION_SCHEMA_VERSION = 1

#: The only ``evidence_kind`` a forecast may ever carry. It is deliberately not
#: one of the RSI whitelist values (``measured`` / ``simulated`` / ``heuristic`` /
#: ``unverified``) that the gates switch on, so "unrecognized" is itself a second
#: line of defence: a gate that validated its input against the whitelist rejects
#: a forecast before it ever compares evidence kinds.
FORECAST_EVIDENCE_KIND = "forecast"

#: The label an *observation* must carry to count as measured. Mirrors
#: :class:`alpha.rsi.rrsi.acceptance.Measurement` — a forecast may not be
#: relabelled as this, and an observation without it is unmeasured.
MEASURED_EVIDENCE_KIND = "measured"

#: Hard ceiling on a declared candidate's step count. A "plan" with ten thousand
#: steps is not a bounded candidate, and accepting the number silently would let
#: an unbounded plan score as merely unusual rather than as malformed.
CANDIDATE_MAX_STEPS = 64


def new_prediction_id() -> str:
    """A fresh prediction id (``pred_`` + 16 hex chars)."""
    return "pred_" + uuid.uuid4().hex[:16]


def _is_number(value: Any) -> bool:
    """A real number. ``bool`` is excluded: ``True`` is not a probability of 1."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _optional_probability(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if not _is_number(value):
        raise ValueError(f"{name} must be a number in [0, 1] or None (None = unknown); got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be a probability in [0, 1]; got {value!r}")
    return float(value)


def _optional_finite(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if not _is_number(value):
        raise ValueError(f"{name} must be a finite number or None (None = unmeasured); got {value!r}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite; got {value!r}")
    return number


class ForecastStatus(str, Enum):
    """The four states a forecast can be in.

    ``PREDICTED`` is the only state carrying a usable probability. The other
    three are distinct rather than collapsed into one "no answer":

    * ``ABSTAINED`` — the predictor ran and **chose** not to answer (insufficient
      support, out-of-distribution, degraded store). It carries a reason.
    * ``INVALID`` — the request could not be answered at all (malformed
      features). Carries a reason.
    * ``EXPIRED`` — a forecast older than the configured maximum age.

    Collapsing the last three would make "the predictor refused" and "the caller
    sent nonsense" indistinguishable, and those demand different fixes.
    """

    PREDICTED = "predicted"
    ABSTAINED = "abstained"
    INVALID = "invalid"
    EXPIRED = "expired"

    @property
    def usable(self) -> bool:
        """Whether this status carries a probability a consumer may read."""
        return self is ForecastStatus.PREDICTED


@dataclass(frozen=True)
class PredictorIdentity:
    """Who produced a forecast, and at which version.

    ``tier`` is the honest label — ``0`` deterministic/rules, ``1`` empirical,
    ``2+`` learned — so a report can say *how* a number was produced without
    trusting the name.
    """

    predictor_id: str
    version: str
    tier: int

    def __post_init__(self) -> None:
        for name in ("predictor_id", "version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"predictor identity {name} must be a non-empty string; got {value!r}")
        if isinstance(self.tier, bool) or not isinstance(self.tier, int) or self.tier < 0:
            raise ValueError(f"predictor identity tier must be an integer >= 0; got {self.tier!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"predictor_id": self.predictor_id, "version": self.version, "tier": self.tier}


@dataclass(frozen=True)
class PredictorHealth:
    """A predictor's own state.

    ``calibrated`` is ``None`` when no forecast has been scored against an
    outcome yet — never ``True``. A predictor that has never been checked is
    exactly the one an operator most needs flagged.
    """

    available: bool
    detail: str
    observed_outcomes: int | None = None
    scored_forecasts: int | None = None
    calibrated: bool | None = None
    abstained_forecasts: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "detail": self.detail,
            "observed_outcomes": self.observed_outcomes,
            "scored_forecasts": self.scored_forecasts,
            "calibrated": self.calibrated,
            "abstained_forecasts": self.abstained_forecasts,
        }


@dataclass(frozen=True)
class CandidateFeatures:
    """The declared, task-grounded inputs a predictor is allowed to read.

    Deliberately small and entirely explicit: there is no raw prompt, no context
    window and no hidden test content, because a pre-action predictor that reads
    post-action material is measuring leakage rather than forecasting.

    ``regime`` is the stratification key for the empirical tier — a short,
    bounded, caller-declared label such as ``"cold_start"``. ``components`` is
    the RRSI attribution (:data:`alpha.rsi.rrsi.components.COMPONENTS`), read
    from here rather than re-declared.
    """

    candidate_id: str
    component: str
    regime: str = "default"
    steps: int = 1
    edit_budget: int | None = None
    budget_saturated: bool = False
    structural_novelty: int | None = None
    components: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError(f"candidate_id must be a non-empty string; got {self.candidate_id!r}")
        if not isinstance(self.component, str) or not self.component:
            raise ValueError(f"component must be a non-empty string; got {self.component!r}")
        if not isinstance(self.regime, str) or not self.regime:
            raise ValueError(f"regime must be a non-empty string; got {self.regime!r}")
        if isinstance(self.steps, bool) or not isinstance(self.steps, int) or not 1 <= self.steps <= CANDIDATE_MAX_STEPS:
            raise ValueError(f"steps must be an integer in [1, {CANDIDATE_MAX_STEPS}] (a bounded plan, not an unbounded one); got {self.steps!r}")
        if self.edit_budget is not None and (isinstance(self.edit_budget, bool) or not isinstance(self.edit_budget, int) or self.edit_budget < 0):
            raise ValueError(f"edit_budget must be a non-negative integer or None (None = not planned); got {self.edit_budget!r}")
        if self.structural_novelty is not None and (isinstance(self.structural_novelty, bool) or not isinstance(self.structural_novelty, int) or self.structural_novelty < 0):
            raise ValueError(f"structural_novelty must be a non-negative integer or None (None = unmeasured); got {self.structural_novelty!r}")
        if isinstance(self.budget_saturated, bool) is False:
            raise ValueError(f"budget_saturated must be a real bool; got {self.budget_saturated!r}")
        if not isinstance(self.components, tuple):
            raise ValueError(f"components must be a tuple of str; got {type(self.components).__name__}")

    def stratum(self) -> tuple[str, str]:
        """The ``(component, regime)`` key the empirical tier aggregates over.

        Returned as a tuple rather than a joined string so two different
        ``(component, regime)`` pairs cannot collide into one bucket through a
        separator appearing inside a value.
        """
        return (self.component, self.regime)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "component": self.component,
            "regime": self.regime,
            "steps": self.steps,
            "edit_budget": self.edit_budget,
            "budget_saturated": self.budget_saturated,
            "structural_novelty": self.structural_novelty,
            "components": list(self.components),
        }


@dataclass(frozen=True)
class OutcomePrediction:
    """A forecast of what a candidate will do, made **before** it runs.

    Every field that could be mistaken for a result is named ``predicted_*``,
    ``evidence_kind`` is pinned to :data:`FORECAST_EVIDENCE_KIND` so this object
    cannot satisfy a promotion gate, and every numeric is ``None`` rather than a
    default when it is not known.

    ``status`` is authoritative: when it is not :attr:`ForecastStatus.PREDICTED`
    the probability is ``None`` **by construction** (enforced in
    ``__post_init__``), so a consumer cannot read a stale number off an abstained
    forecast.
    """

    prediction_id: str
    candidate_id: str
    identity: PredictorIdentity
    status: ForecastStatus = ForecastStatus.PREDICTED
    predicted_success_probability: float | None = None
    predicted_cost: float | None = None
    uncertainty: float | None = None
    support: int | None = None
    assumptions: tuple[str, ...] = ()
    abstain_reason: str = ""
    created_at: float = field(default_factory=time.time)
    schema_version: int = PREDICTION_SCHEMA_VERSION
    evidence_kind: str = field(default=FORECAST_EVIDENCE_KIND, init=False)

    def __post_init__(self) -> None:
        for name in ("prediction_id", "candidate_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string; got {value!r}")
        if not isinstance(self.status, ForecastStatus):
            raise ValueError(f"status must be a ForecastStatus; got {self.status!r}")
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError(f"schema_version must be an integer >= 1; got {self.schema_version!r}")

        probability = _optional_probability(self.predicted_success_probability, "predicted_success_probability")
        uncertainty = _optional_probability(self.uncertainty, "uncertainty")
        cost = _optional_finite(self.predicted_cost, "predicted_cost")
        if self.support is not None and (isinstance(self.support, bool) or not isinstance(self.support, int) or self.support < 0):
            raise ValueError(f"support must be a non-negative integer or None (None = no sample support); got {self.support!r}")
        if not isinstance(self.assumptions, tuple):
            raise ValueError(f"assumptions must be a tuple of str; got {type(self.assumptions).__name__}")
        if not isinstance(self.abstain_reason, str):
            raise ValueError(f"abstain_reason must be a str; got {type(self.abstain_reason).__name__}")

        # The coupling that makes abstention honest: a non-predicted status
        # cannot carry a probability, and a predicted status must carry one.
        if self.status is not ForecastStatus.PREDICTED:
            if probability is not None or uncertainty is not None:
                raise ValueError(f"a {self.status.value} forecast must not carry a probability or uncertainty; refusing to store a number nobody should read (got p={probability!r}, u={uncertainty!r})")
            if not self.abstain_reason.strip():
                raise ValueError(f"a {self.status.value} forecast must state a non-empty abstain_reason")
        elif probability is None:
            raise ValueError("a predicted forecast must carry predicted_success_probability")

        object.__setattr__(self, "predicted_success_probability", probability)
        object.__setattr__(self, "predicted_cost", cost)
        object.__setattr__(self, "uncertainty", uncertainty)

    @property
    def usable(self) -> bool:
        """Whether this forecast carries a probability a consumer may read."""
        return self.status.usable and self.predicted_success_probability is not None

    def reason_text(self) -> str:
        """One operator-facing line: the abstention reason, or the probability."""
        if self.usable:
            uncertainty = None if self.uncertainty is None else round(self.uncertainty, 3)
            return f"forecast p={self.predicted_success_probability:.3f} by {self.identity.predictor_id} v{self.identity.version} (support={self.support}, uncertainty={uncertainty})"
        return f"forecast {self.status.value}: {self.abstain_reason}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "prediction_id": self.prediction_id,
            "candidate_id": self.candidate_id,
            "identity": self.identity.to_dict(),
            "status": self.status.value,
            "predicted_success_probability": self.predicted_success_probability,
            "predicted_cost": self.predicted_cost,
            "uncertainty": self.uncertainty,
            "support": self.support,
            "assumptions": list(self.assumptions),
            "abstain_reason": self.abstain_reason,
            "created_at": self.created_at,
            "evidence_kind": self.evidence_kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutcomePrediction:
        if not isinstance(data, dict):
            raise ValueError(f"forecast payload must be an object; got {type(data).__name__}")
        if data.get("schema_version") != PREDICTION_SCHEMA_VERSION:
            raise ValueError(f"forecast schema_version {data.get('schema_version')!r} is not the supported version {PREDICTION_SCHEMA_VERSION}")
        raw_identity = data.get("identity")
        if not isinstance(raw_identity, dict):
            raise ValueError(f"forecast identity must be an object; got {type(raw_identity).__name__}")
        return cls(
            prediction_id=str(data.get("prediction_id") or ""),
            candidate_id=str(data.get("candidate_id") or ""),
            identity=PredictorIdentity(
                predictor_id=str(raw_identity.get("predictor_id") or ""),
                version=str(raw_identity.get("version") or ""),
                tier=raw_identity.get("tier", 0),
            ),
            status=ForecastStatus(data.get("status", ForecastStatus.ABSTAINED.value)),
            predicted_success_probability=data.get("predicted_success_probability"),
            predicted_cost=data.get("predicted_cost"),
            uncertainty=data.get("uncertainty"),
            support=data.get("support"),
            assumptions=tuple(data.get("assumptions") or ()),
            abstain_reason=str(data.get("abstain_reason") or ""),
            created_at=float(data.get("created_at") or 0.0),
            schema_version=PREDICTION_SCHEMA_VERSION,
        )


def abstained(candidate_id: str, identity: PredictorIdentity, reason: str, status: ForecastStatus = ForecastStatus.ABSTAINED, *, support: int | None = None, assumptions: tuple[str, ...] = ()) -> OutcomePrediction:
    """Build a reason-carrying non-forecast for ``candidate_id``.

    The single place a refusal is constructed, so "abstain" cannot be spelled
    three slightly different ways in three modules. ``reason`` is required and
    non-empty: a refusal with no reason is indistinguishable from a bug.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("abstained() requires a non-empty reason: a refusal nobody can act on is not a disclosure")
    return OutcomePrediction(
        prediction_id=new_prediction_id(),
        candidate_id=candidate_id,
        identity=identity,
        status=status,
        abstain_reason=reason,
        support=support,
        assumptions=assumptions,
    )


@dataclass(frozen=True)
class ObservedOutcome:
    """What actually happened to a candidate, measured after the fact.

    ``evidence_kind`` must be :data:`MEASURED_EVIDENCE_KIND` for
    :attr:`measured` to be ``True``. That is what stops a forecast — or a
    preview's ``simulated`` numbers — from becoming training data for the
    predictor that is supposed to be learning from reality.

    ``success`` is ``None`` when the outcome was not measured; it is never
    defaulted, because "not measured" trained as "failed" or "succeeded" is the
    single most damaging thing a learning loop can do silently.
    """

    candidate_id: str
    prediction_id: str = ""
    success: bool | None = None
    measured_cost: float | None = None
    component: str = ""
    regime: str = ""
    evidence_kind: str = MEASURED_EVIDENCE_KIND
    verifier_id: str = ""
    observed_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError(f"candidate_id must be a non-empty string; got {self.candidate_id!r}")
        if self.success is not None and not isinstance(self.success, bool):
            raise ValueError(f"success must be a real bool or None (None = not measured); got {self.success!r}")
        _optional_finite(self.measured_cost, "measured_cost")
        if not isinstance(self.evidence_kind, str) or not self.evidence_kind:
            raise ValueError(f"evidence_kind must be a non-empty string; got {self.evidence_kind!r}")
        for name in ("component", "regime", "verifier_id", "prediction_id"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a str; got {type(getattr(self, name)).__name__}")

    @property
    def measured(self) -> bool:
        """Whether a real, measured success/failure label is attached.

        Mirrors :attr:`alpha.rsi.rrsi.acceptance.Measurement.measured`: the
        label the producer stamped must be ``measured`` **and** the value must
        be a real boolean.
        """
        return self.evidence_kind == MEASURED_EVIDENCE_KIND and isinstance(self.success, bool)

    def stratum(self) -> tuple[str, str]:
        """The ``(component, regime)`` key matching :meth:`CandidateFeatures.stratum`."""
        return (self.component, self.regime)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "prediction_id": self.prediction_id,
            "success": self.success,
            "measured_cost": self.measured_cost,
            "component": self.component,
            "regime": self.regime,
            "evidence_kind": self.evidence_kind,
            "verifier_id": self.verifier_id,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservedOutcome:
        if not isinstance(data, dict):
            raise ValueError(f"outcome payload must be an object; got {type(data).__name__}")
        success = data.get("success")
        if success is not None and not isinstance(success, bool):
            raise ValueError(f"stored outcome success must be a bool or null; got {success!r}")
        return cls(
            candidate_id=str(data.get("candidate_id") or ""),
            prediction_id=str(data.get("prediction_id") or ""),
            success=success,
            measured_cost=data.get("measured_cost"),
            component=str(data.get("component") or ""),
            regime=str(data.get("regime") or ""),
            evidence_kind=str(data.get("evidence_kind") or ""),
            verifier_id=str(data.get("verifier_id") or ""),
            observed_at=float(data.get("observed_at") or 0.0),
        )
