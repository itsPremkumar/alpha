"""Deterministic, uncertainty-aware utility scoring.

The scorer is intentionally a small transparent heuristic rather than a
learned model.  For an observation at time ``t`` it applies
``2 ** (-(now - t) / half_life)`` to its evidence mass.  Positive evidence is
weighted by the event's value (surfacing is weak, recall/click/confirmation
stronger), negative evidence is weighted by unused/contradiction, and a
contradiction reduces the normalized signal.  An explicit confirmation adds a
small, disclosed bonus.  The resulting number is a relative ranking signal, not
a probability.

Calibration is a seam, not an automatic claim.  ``calibrate`` refuses a sample
below the configured minimum and leaves the label heuristic.  A caller must
carry the returned :class:`CalibrationResult` into scoring to obtain a
``calibrated`` label and its recorded sample size.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .config import UtilityConfig
from .models import ScoreLabel, UtilityEvent, UtilityObservation, UtilityRecord

_EVENT_VALUE: dict[UtilityEvent, float] = {
    UtilityEvent.SURFACED: 0.20,
    UtilityEvent.RECALLED: 0.55,
    UtilityEvent.CLICKED: 0.80,
    UtilityEvent.CONFIRMED: 1.00,
    UtilityEvent.UNUSED: -0.65,
    UtilityEvent.CONTRADICTED: -1.00,
}
_POSITIVE_EVENTS = {UtilityEvent.SURFACED, UtilityEvent.RECALLED, UtilityEvent.CLICKED, UtilityEvent.CONFIRMED}
_NEGATIVE_EVENTS = {UtilityEvent.UNUSED, UtilityEvent.CONTRADICTED}
_EXPLICIT_CONFIRMATION_BONUS = 0.05
_SCORE_PRECISION = 12


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """Outcome of an explicit calibration attempt."""

    calibrated: bool
    label: ScoreLabel
    sample_size: int
    min_samples: int
    disclosure: str
    offset: float = 0.0
    scale: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibrated": self.calibrated,
            "label": self.label.value,
            "sample_size": self.sample_size,
            "min_samples": self.min_samples,
            "disclosure": self.disclosure,
            "offset": self.offset,
            "scale": self.scale,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Scoring result with an explicit label and optional persisted record."""

    score: float | None
    label: ScoreLabel
    sample_size: int
    confidence: float
    disclosure: str
    decay: float = 0.0
    contradiction_count: int = 0
    confirmation_count: int = 0
    positive_evidence: float = 0.0
    negative_evidence: float = 0.0
    terms: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    record: UtilityRecord | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "terms", MappingProxyType(dict(self.terms)))

    @property
    def utility(self) -> float | None:
        return self.score

    @property
    def available(self) -> bool:
        return self.score is not None and self.label is not ScoreLabel.UNAVAILABLE

    @property
    def calibrated(self) -> bool:
        return self.label is ScoreLabel.CALIBRATED

    @property
    def calibration_sample_size(self) -> int:
        return self.record.calibration_sample_size if self.record else 0

    def __float__(self) -> float:
        if self.score is None:
            raise ValueError("an unavailable utility score has no numeric value")
        return self.score

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "utility": self.score,
            "label": self.label.value,
            "sample_size": self.sample_size,
            "calibration_sample_size": self.calibration_sample_size,
            "confidence": self.confidence,
            "disclosure": self.disclosure,
            "decay": self.decay,
            "contradiction_count": self.contradiction_count,
            "confirmation_count": self.confirmation_count,
            "positive_evidence": self.positive_evidence,
            "negative_evidence": self.negative_evidence,
            "terms": dict(self.terms),
            "reason": self.reason,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __getattr__(self, name: str) -> Any:
        record = object.__getattribute__(self, "record")
        if record is not None and hasattr(record, name):
            return getattr(record, name)
        raise AttributeError(name)


def _config(value: UtilityConfig | Mapping[str, Any] | None) -> UtilityConfig:
    if value is None:
        return UtilityConfig()
    if isinstance(value, UtilityConfig):
        return value
    return UtilityConfig.from_mapping(value)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


def _round(value: float) -> float:
    return round(float(value), _SCORE_PRECISION)


def _coerce_observation(value: Any) -> UtilityObservation | None:
    if isinstance(value, UtilityObservation):
        return value
    if isinstance(value, Mapping):
        try:
            return UtilityObservation.model_validate(dict(value))
        except (TypeError, ValueError):
            return None
    return None


def _as_observations(values: Any) -> list[UtilityObservation]:
    if isinstance(values, (UtilityObservation, Mapping)):
        values = [values]
    try:
        iterator = iter(values)
    except TypeError:
        return []
    result: list[UtilityObservation] = []
    for value in iterator:
        observation = _coerce_observation(value)
        if observation is not None and observation.source_is_explicit and observation.observed_at is not None:
            result.append(observation)
    return result


def _deduplicate(observations: Iterable[UtilityObservation]) -> list[UtilityObservation]:
    by_id: dict[str, UtilityObservation] = {}
    for observation in observations:
        by_id.setdefault(observation.observation_id, observation)
    return sorted(by_id.values(), key=lambda item: (item.observed_at or 0.0, item.observation_id))


def _resolve_now(
    observations: Iterable[UtilityObservation],
    *,
    now: float | None,
    clock: Any = None,
) -> float | None:
    if now is not None:
        try:
            candidate = float(now)
        except (TypeError, ValueError, OverflowError):
            return None
        return candidate if math.isfinite(candidate) and candidate >= 0.0 else None
    timestamps = [item.observed_at for item in observations if item.observed_at is not None]
    if timestamps:
        return max(timestamps)
    if clock is None:
        return None
    try:
        value = float(clock() if callable(clock) else clock.now())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0.0 else None


def _decay_factor(age: float, half_life: float) -> float:
    return 0.5 ** (max(0.0, age) / half_life)


def _raw_score(
    *,
    positive: float,
    negative: float,
    confirmation: float,
    contradiction_count: int,
    explicit_confirmation: bool,
) -> tuple[float, dict[str, float]]:
    total = positive + negative
    if total <= 0.0:
        return 0.5, {
            "positive_evidence": 0.0,
            "negative_evidence": 0.0,
            "normalized_signal": 0.0,
            "evidence_strength": 0.0,
            "confirmation_bonus": 0.0,
            "contradiction_penalty": 0.0,
        }
    normalized_signal = (positive - negative) / total
    # The absolute amount of surviving evidence matters as well as its sign.
    # Without this term, scaling every positive observation by the same decay
    # factor would leave a pure-success ratio at 1.0 and old successes would
    # incorrectly tie recent ones.
    evidence_strength = 1.0 - math.exp(-total)
    separation = 0.5 * normalized_signal * (0.25 + 0.75 * evidence_strength)
    confirmation_bonus = (0.15 * min(1.0, confirmation / total)) * (0.5 + 0.5 * evidence_strength)
    if explicit_confirmation:
        confirmation_bonus += _EXPLICIT_CONFIRMATION_BONUS * (0.5 + 0.5 * evidence_strength)
    contradiction_penalty = min(0.35, 0.12 * max(0, contradiction_count))
    raw = 0.5 + separation + confirmation_bonus - contradiction_penalty
    terms = {
        "positive_evidence": _round(positive),
        "negative_evidence": _round(negative),
        "normalized_signal": _round(normalized_signal),
        "evidence_strength": _round(evidence_strength),
        "confirmation_bonus": _round(confirmation_bonus),
        "contradiction_penalty": _round(contradiction_penalty),
    }
    return _clamp(raw), terms


def _label_for(calibration: CalibrationResult | None, sample_size: int, reason: str) -> tuple[ScoreLabel, str]:
    if calibration is not None and calibration.calibrated:
        return (
            ScoreLabel.CALIBRATED,
            f"calibrated: explicit calibration used {calibration.sample_size} samples; current observations={sample_size}",
        )
    if calibration is not None and not calibration.calibrated:
        return ScoreLabel.HEURISTIC, f"heuristic: {calibration.disclosure}; current observations={sample_size}"
    return ScoreLabel.HEURISTIC, f"heuristic: no calibration run; current observations={sample_size}; {reason}".strip()


def _apply_calibration(raw: float, calibration: CalibrationResult | None) -> float:
    if calibration is None or not calibration.calibrated:
        return raw
    return _clamp(0.5 + (raw - 0.5) * calibration.scale + calibration.offset)


def _confidence(sample_size: int, label: ScoreLabel) -> float:
    if sample_size <= 0:
        return 0.0
    # This is a sample-size heuristic, not a probability or confidence interval.
    value = 1.0 - 0.5 ** (sample_size / 3.0)
    if label is ScoreLabel.CALIBRATED:
        value = min(0.99, value + 0.05)
    return _round(min(0.99, value))


def _disclosure(label: ScoreLabel, sample_size: int, reason: str = "") -> str:
    if label is ScoreLabel.UNAVAILABLE:
        return f"unavailable: {reason or 'no accepted utility observations'}"
    if label is ScoreLabel.CALIBRATED:
        return f"calibrated: score is based on an explicit calibration sample ({sample_size} samples); confidence is a sample-size heuristic, not statistical"
    return f"heuristic: relative decayed feedback signal; calibration not established ({sample_size} observations); confidence is a sample-size heuristic, not statistical"


def score_observations(
    observations: Any,
    *,
    now: float | None = None,
    clock: Any = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
    calibration: CalibrationResult | None = None,
) -> ScoreResult:
    """Score one record's observations with a documented decayed heuristic."""

    active_config = _config(config)
    parsed = _deduplicate(_as_observations(observations))
    resolved_now = _resolve_now(parsed, now=now, clock=clock)
    record_ids = {item.record_id for item in parsed}
    if len(record_ids) > 1:
        return ScoreResult(
            score=None,
            label=ScoreLabel.UNAVAILABLE,
            sample_size=len(parsed),
            confidence=0.0,
            disclosure="unavailable: observations contain multiple record ids; use score_records",
            reason="multiple_record_ids",
        )
    if not parsed or resolved_now is None:
        reason = "no accepted utility observations" if not parsed else "clock unavailable"
        return ScoreResult(
            score=None,
            label=ScoreLabel.UNAVAILABLE,
            sample_size=0,
            confidence=0.0,
            disclosure=_disclosure(ScoreLabel.UNAVAILABLE, 0, reason),
            reason=reason,
        )

    positive = 0.0
    negative = 0.0
    confirmation = 0.0
    explicit_confirmation = False
    weighted_total = 0.0
    recency_total = 0.0
    contradictions = 0
    confirmations = 0
    event_counts: dict[str, int] = {}
    for observation in parsed:
        assert observation.observed_at is not None
        age = resolved_now - observation.observed_at
        factor = _decay_factor(age, active_config.decay_half_life)
        mass = observation.weight * factor
        value = _EVENT_VALUE[observation.event]
        recency_total += mass
        weighted_total += observation.weight
        event_counts[observation.event.value] = event_counts.get(observation.event.value, 0) + 1
        if observation.event in _POSITIVE_EVENTS:
            positive += abs(value) * mass
            if observation.event is UtilityEvent.CONFIRMED:
                confirmation += mass
                confirmations += 1
                explicit_confirmation = explicit_confirmation or observation.source.casefold() in {"explicit", "user", "human"}
        elif observation.event in _NEGATIVE_EVENTS:
            negative += abs(value) * mass
            if observation.event is UtilityEvent.CONTRADICTED:
                contradictions += 1

    raw, terms = _raw_score(
        positive=positive,
        negative=negative,
        confirmation=confirmation,
        contradiction_count=contradictions,
        explicit_confirmation=explicit_confirmation,
    )
    label, _ = _label_for(calibration, len(parsed), "")
    score = _round(_apply_calibration(raw, calibration))
    decay = _round(recency_total / weighted_total) if weighted_total else 0.0
    first_seen = min(item.observed_at or 0.0 for item in parsed)
    last_seen = max(item.observed_at or 0.0 for item in parsed)
    disclosure = _disclosure(label, len(parsed))
    if calibration is not None and calibration.calibrated:
        disclosure = f"{disclosure}; calibration_sample_size={calibration.sample_size}"
    elif calibration is not None and not calibration.calibrated:
        disclosure = f"{disclosure}; {calibration.disclosure}"
    record = UtilityRecord(
        record_id=parsed[0].record_id,
        score=score,
        observation_count=len(parsed),
        first_seen=first_seen,
        last_seen=last_seen,
        confidence=_confidence(len(parsed), label),
        disclosure=disclosure,
        decay=decay,
        observation_ids=[item.observation_id for item in parsed],
        contradiction_count=contradictions,
        confirmation_count=confirmations,
        calibration_label=label,
        calibration_sample_size=calibration.sample_size if calibration else 0,
        positive_evidence=positive,
        negative_evidence=negative,
        confirmation_evidence=confirmation,
        last_scored_at=resolved_now,
        event_counts=event_counts,
    )
    return ScoreResult(
        score=score,
        label=label,
        sample_size=len(parsed),
        confidence=record.confidence,
        disclosure=disclosure,
        decay=decay,
        contradiction_count=contradictions,
        confirmation_count=confirmations,
        positive_evidence=positive,
        negative_evidence=negative,
        terms=terms,
        record=record,
        reason="scored",
    )


def merge_observation(
    record: UtilityRecord | None,
    observation: UtilityObservation,
    *,
    now: float | None = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
    calibration: CalibrationResult | None = None,
) -> ScoreResult:
    """Merge one newly accepted observation into a persisted aggregate."""

    if observation.observed_at is None:
        return ScoreResult(
            score=None,
            label=ScoreLabel.UNAVAILABLE,
            sample_size=record.observation_count if record else 0,
            confidence=0.0,
            disclosure=_disclosure(ScoreLabel.UNAVAILABLE, record.observation_count if record else 0, "observation timestamp missing"),
            record=record.model_copy(deep=True) if record else None,
            reason="observation timestamp missing",
        )
    active_config = _config(config)
    resolved_now = float(now) if now is not None else float(observation.observed_at)
    if not math.isfinite(resolved_now) or resolved_now < 0.0:
        return ScoreResult(
            score=None,
            label=ScoreLabel.UNAVAILABLE,
            sample_size=record.observation_count if record else 0,
            confidence=0.0,
            disclosure=_disclosure(ScoreLabel.UNAVAILABLE, record.observation_count if record else 0, "clock invalid"),
            record=record.model_copy(deep=True) if record else None,
            reason="clock invalid",
        )
    if record is None:
        return score_observations([observation], now=resolved_now, config=active_config, calibration=calibration)
    if observation.observation_id in record.observation_ids:
        return score_record(record, now=resolved_now, config=active_config, calibration=calibration)

    prior = score_record(record, now=resolved_now, config=active_config, calibration=calibration)
    prior_record = prior.record or record
    factor = _decay_factor(max(0.0, resolved_now - observation.observed_at), active_config.decay_half_life)
    mass = observation.weight * factor
    value = _EVENT_VALUE[observation.event]
    positive = prior_record.positive_evidence
    negative = prior_record.negative_evidence
    confirmation = prior_record.confirmation_evidence
    contradictions = prior_record.contradiction_count
    confirmations = prior_record.confirmation_count
    event_counts = dict(prior_record.event_counts)
    event_counts[observation.event.value] = event_counts.get(observation.event.value, 0) + 1
    explicit_confirmation = False
    if observation.event in _POSITIVE_EVENTS:
        positive += abs(value) * mass
        if observation.event is UtilityEvent.CONFIRMED:
            confirmation += mass
            confirmations += 1
            explicit_confirmation = observation.source.casefold() in {"explicit", "user", "human"}
    elif observation.event in _NEGATIVE_EVENTS:
        negative += abs(value) * mass
        if observation.event is UtilityEvent.CONTRADICTED:
            contradictions += 1
    raw, terms = _raw_score(
        positive=positive,
        negative=negative,
        confirmation=confirmation,
        contradiction_count=contradictions,
        explicit_confirmation=explicit_confirmation,
    )
    label, _ = _label_for(calibration, prior_record.observation_count + 1, "")
    score = _round(_apply_calibration(raw, calibration))
    first_seen = min(prior_record.first_seen or observation.observed_at, observation.observed_at)
    last_seen = max(prior_record.last_seen, observation.observed_at)
    ids = list(prior_record.observation_ids)
    ids.append(observation.observation_id)
    disclosure = _disclosure(label, len(ids))
    if calibration is not None and calibration.calibrated:
        disclosure = f"{disclosure}; calibration_sample_size={calibration.sample_size}"
    elif calibration is not None and not calibration.calibrated:
        disclosure = f"{disclosure}; {calibration.disclosure}"
    updated = UtilityRecord(
        record_id=prior_record.record_id,
        score=score,
        observation_count=prior_record.observation_count + 1,
        first_seen=first_seen,
        last_seen=last_seen,
        confidence=_confidence(len(ids), label),
        disclosure=disclosure,
        decay=_round(min(1.0, max(0.0, (prior_record.decay * prior_record.observation_count + factor) / max(1, len(ids))))),
        observation_ids=ids,
        contradiction_count=contradictions,
        confirmation_count=confirmations,
        calibration_label=label,
        calibration_sample_size=calibration.sample_size if calibration else prior_record.calibration_sample_size,
        positive_evidence=positive,
        negative_evidence=negative,
        confirmation_evidence=confirmation,
        last_scored_at=resolved_now,
        event_counts=event_counts,
    )
    return ScoreResult(
        score=score,
        label=label,
        sample_size=len(ids),
        confidence=updated.confidence,
        disclosure=disclosure,
        decay=updated.decay,
        contradiction_count=contradictions,
        confirmation_count=confirmations,
        positive_evidence=positive,
        negative_evidence=negative,
        terms=terms,
        record=updated,
        reason="merged_observation",
    )


def score_record(
    record: UtilityRecord,
    *,
    now: float | None = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
    calibration: CalibrationResult | None = None,
) -> ScoreResult:
    """Re-score a persisted aggregate at an injected time.

    Sufficient statistics are decayed from ``last_scored_at``.  This lets a
    restart preserve the half-life behavior without retaining unbounded raw
    feedback events.
    """

    active_config = _config(config)
    resolved_now = now
    if resolved_now is None:
        resolved_now = record.last_scored_at
    if not record.has_signal or resolved_now is None:
        reason = "no accepted utility observations" if not record.has_signal else "clock unavailable"
        return ScoreResult(
            score=None,
            label=ScoreLabel.UNAVAILABLE,
            sample_size=record.observation_count,
            confidence=0.0,
            disclosure=_disclosure(ScoreLabel.UNAVAILABLE, record.observation_count, reason),
            record=record.model_copy(deep=True),
            reason=reason,
        )
    resolved_now = float(resolved_now)
    if not math.isfinite(resolved_now) or resolved_now < 0.0:
        reason = "clock invalid"
        return ScoreResult(
            score=None,
            label=ScoreLabel.UNAVAILABLE,
            sample_size=record.observation_count,
            confidence=0.0,
            disclosure=_disclosure(ScoreLabel.UNAVAILABLE, record.observation_count, reason),
            record=record.model_copy(deep=True),
            reason=reason,
        )
    age = max(0.0, resolved_now - record.last_scored_at)
    factor = _decay_factor(age, active_config.decay_half_life)
    positive = record.positive_evidence * factor
    negative = record.negative_evidence * factor
    confirmation = record.confirmation_evidence * factor
    if positive <= 0.0 and negative <= 0.0:
        # Older/hand-authored records may contain only a cached score.  Apply
        # the same exponential factor directly rather than inventing evidence.
        penalty = min(0.35, 0.12 * max(0, record.contradiction_count))
        raw = _clamp(record.score * factor - penalty)
        terms = {
            "positive_evidence": 0.0,
            "negative_evidence": 0.0,
            "normalized_signal": 0.0,
            "evidence_strength": 0.0,
            "confirmation_bonus": 0.0,
            "contradiction_penalty": _round(penalty),
            "cached_score_decay": _round(factor),
        }
    else:
        raw, terms = _raw_score(
            positive=positive,
            negative=negative,
            confirmation=confirmation,
            contradiction_count=record.contradiction_count,
            explicit_confirmation=False,
        )
    label, _ = _label_for(calibration, record.observation_count, "")
    score = _round(_apply_calibration(raw, calibration))
    disclosure = _disclosure(label, record.observation_count)
    if calibration is not None and calibration.calibrated:
        disclosure = f"{disclosure}; calibration_sample_size={calibration.sample_size}"
    elif calibration is not None and not calibration.calibrated:
        disclosure = f"{disclosure}; {calibration.disclosure}"
    updated = record.model_copy(
        update={
            "score": score,
            "confidence": _confidence(record.observation_count, label),
            "disclosure": disclosure,
            "decay": factor,
            "calibration_label": label,
            "calibration_sample_size": calibration.sample_size if calibration else record.calibration_sample_size,
            "positive_evidence": positive,
            "negative_evidence": negative,
            "confirmation_evidence": confirmation,
            "last_scored_at": resolved_now,
        }
    )
    return ScoreResult(
        score=score,
        label=label,
        sample_size=record.observation_count,
        confidence=updated.confidence,
        disclosure=disclosure,
        decay=factor,
        contradiction_count=record.contradiction_count,
        confirmation_count=record.confirmation_count,
        positive_evidence=positive,
        negative_evidence=negative,
        terms=terms,
        record=updated,
        reason="rescored",
    )


def score_records(
    observations: Any,
    *,
    now: float | None = None,
    clock: Any = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
    calibration: CalibrationResult | None = None,
) -> dict[str, ScoreResult]:
    """Group a heterogeneous observation iterable by record id."""

    parsed = _deduplicate(_as_observations(observations))
    grouped: dict[str, list[UtilityObservation]] = {}
    for observation in parsed:
        grouped.setdefault(observation.record_id, []).append(observation)
    return {
        record_id: score_observations(
            group,
            now=now,
            clock=clock,
            config=config,
            calibration=calibration,
        )
        for record_id, group in sorted(grouped.items())
    }


def score_observation(
    observation: Any,
    *,
    now: float | None = None,
    clock: Any = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
    calibration: CalibrationResult | None = None,
) -> ScoreResult:
    return score_observations(observation, now=now, clock=clock, config=config, calibration=calibration)


def calibrate(
    samples: Any,
    *,
    min_samples: int | None = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
) -> CalibrationResult:
    """Attempt explicit calibration, refusing small samples honestly."""

    active_config = _config(config)
    required = active_config.min_calibration_samples if min_samples is None else int(min_samples)
    if required < 1:
        raise ValueError("min_samples must be positive")
    if isinstance(samples, (UtilityObservation, Mapping)):
        samples = [samples]
    try:
        materialized = list(samples)
    except TypeError:
        materialized = []
    parsed = _deduplicate(_as_observations(materialized))
    sample_size = len(parsed)
    if sample_size < required:
        disclosure = f"calibration_refused: {sample_size} samples are below the configured minimum of {required}; label remains heuristic"
        return CalibrationResult(
            calibrated=False,
            label=ScoreLabel.HEURISTIC,
            sample_size=sample_size,
            min_samples=required,
            disclosure=disclosure,
        )

    values: list[float] = []
    for observation in parsed:
        result = score_observations([observation], now=observation.observed_at, config=active_config)
        if result.score is not None:
            values.append(result.score)
    if len(values) < required:
        disclosure = f"calibration_refused: only {len(values)} usable samples are below the configured minimum of {required}; label remains heuristic"
        return CalibrationResult(
            calibrated=False,
            label=ScoreLabel.HEURISTIC,
            sample_size=len(values),
            min_samples=required,
            disclosure=disclosure,
        )
    mean = math.fsum(values) / len(values)
    offset = _clamp(mean - 0.5, -0.10, 0.10)
    scale = 1.0
    return CalibrationResult(
        calibrated=True,
        label=ScoreLabel.CALIBRATED,
        sample_size=len(values),
        min_samples=required,
        disclosure=f"calibrated: explicit calibration accepted {len(values)} samples",
        offset=offset,
        scale=scale,
    )


def calibrated_score(
    observations: Any,
    samples: Any,
    *,
    now: float | None = None,
    config: UtilityConfig | Mapping[str, Any] | None = None,
) -> ScoreResult:
    """Convenience seam that calibrates first, then scores the observations."""

    return score_observations(observations, now=now, config=config, calibration=calibrate(samples, config=config))


# Names commonly used by embedding hosts.
score = score_observations
score_one = score_observation
score_utility = score_observations

__all__ = [
    "CalibrationResult",
    "ScoreResult",
    "calibrate",
    "calibrated_score",
    "merge_observation",
    "score",
    "score_one",
    "score_observation",
    "score_observations",
    "score_record",
    "score_records",
    "score_utility",
]
