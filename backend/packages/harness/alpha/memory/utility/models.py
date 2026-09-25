"""Typed contracts for the opt-in memory-utility feedback plane.

The utility plane is deliberately a *shadow* subsystem.  It records what the
host says happened to a memory and emits recommendations; it does not own the
memory records themselves and it never performs a retention or merge action.
Every score-bearing contract carries a disclosure so a caller cannot mistake a
small, uncalibrated sample for a measured probability.
"""

from __future__ import annotations

import math
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class UtilityEvent(StrEnum):
    """Closed vocabulary of observations accepted by the normalizer."""

    SURFACED = "surfaced"
    CLICKED = "clicked"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNUSED = "unused"
    RECALLED = "recalled"


class RetentionAction(StrEnum):
    """Actions that a host may choose to authorize after review."""

    KEEP = "keep"
    DEMOTE = "demote"
    EVICT = "evict"
    QUARANTINE = "quarantine"


class SignalStatus(StrEnum):
    """Outcome states for one feedback normalization attempt."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class ScoreLabel(StrEnum):
    """Honesty labels attached to utility scores."""

    HEURISTIC = "heuristic"
    CALIBRATED = "calibrated"
    UNAVAILABLE = "unavailable"


UtilityEventName = Literal[
    "surfaced",
    "clicked",
    "confirmed",
    "contradicted",
    "unused",
    "recalled",
]
RetentionActionName = Literal["keep", "demote", "evict", "quarantine"]

_NO_DATA_DISCLOSURE = "unavailable: no accepted utility observations"


class UtilityObservation(BaseModel):
    """One normalized, idempotent feedback event for a stored record.

    ``observation_id`` is the idempotency key.  A caller should reuse the
    provider's event id when one exists; the normalizer creates a deterministic
    id for otherwise anonymous events.  ``observed_at`` is an explicit source
    timestamp, not an invented wall-clock value: when it is absent the injected
    clock supplies one.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)

    observation_id: str = Field(
        default_factory=lambda: f"obs_{uuid.uuid4().hex}",
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices("observation_id", "id"),
    )
    record_id: str = Field(min_length=1, max_length=512)
    event: UtilityEvent
    weight: float = Field(default=1.0, gt=0.0, le=1000.0)
    source: str = Field(default="unspecified", min_length=1, max_length=256)
    observed_at: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _validate_finite_numbers(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for name in ("weight", "observed_at"):
            raw = data.get(name)
            if raw is None:
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{name} must be a finite number") from exc
            if not math.isfinite(number):
                raise ValueError(f"{name} must be a finite number")
            data[name] = number
        return data

    @property
    def id(self) -> str:
        """Compatibility/readability alias for the idempotency key."""

        return self.observation_id

    @property
    def timestamp(self) -> float | None:
        """Alias used by providers that call the source timestamp ``timestamp``."""

        return self.observed_at

    @property
    def source_is_explicit(self) -> bool:
        return self.source.strip().casefold() not in {"", "unspecified", "unknown", "none"}

    @property
    def clock_timestamp(self) -> float | None:
        return self.observed_at


class UtilityRecord(BaseModel):
    """Per-record aggregate persisted by the utility store.

    ``score`` is a bounded heuristic signal, not a probability.  The
    ``disclosure`` and ``calibration_label`` fields travel with the record so a
    later rank/retention call cannot silently upgrade the claim.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)

    record_id: str = Field(min_length=1, max_length=512)
    score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("score", "utility", "smoothed_score"),
    )
    observation_count: int = Field(default=0, ge=0)
    first_seen: float = Field(default=0.0, ge=0.0)
    last_seen: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    disclosure: str = Field(default=_NO_DATA_DISCLOSURE, min_length=1)
    decay: float = Field(default=0.0, ge=0.0, le=1.0)
    observation_ids: list[str] = Field(default_factory=list)
    contradiction_count: int = Field(default=0, ge=0)
    confirmation_count: int = Field(default=0, ge=0)
    calibration_label: ScoreLabel = Field(default=ScoreLabel.HEURISTIC)
    calibration_sample_size: int = Field(default=0, ge=0)
    # Persisted sufficient statistics let a later read decay the score without
    # replaying an unbounded event log.  They are evidence, not hidden state.
    positive_evidence: float = Field(default=0.0, ge=0.0)
    negative_evidence: float = Field(default=0.0, ge=0.0)
    confirmation_evidence: float = Field(default=0.0, ge=0.0)
    last_scored_at: float = Field(default=0.0, ge=0.0)
    event_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def utility(self) -> float:
        """Readable alias for callers that use the domain term ``utility``."""

        return self.score

    @property
    def smoothed_score(self) -> float:
        return self.score

    @property
    def score_label(self) -> str:
        return self.calibration_label.value

    @property
    def first_seen_at(self) -> float:
        return self.first_seen

    @property
    def last_seen_at(self) -> float:
        return self.last_seen

    @property
    def has_signal(self) -> bool:
        return self.observation_count > 0


class RetentionDecision(BaseModel):
    """A deterministic recommendation; ``evict`` still requires host action."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    record_id: str = Field(min_length=1, max_length=512)
    action: RetentionAction
    reason: str = Field(min_length=1, max_length=1000)
    utility: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    budget_pressure: float = Field(ge=0.0, le=1.0)
    disclosure: str = Field(default="heuristic recommendation; host authorization required", min_length=1)

    @property
    def is_destructive(self) -> bool:
        """Whether a caller would need a separately authorized destructive step."""

        return self.action is RetentionAction.EVICT

    @property
    def threshold_used(self) -> float:
        return self.threshold


class DedupSuggestion(BaseModel):
    """A merge proposal.  No member is removed by producing this object."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    record_ids: list[str] = Field(min_length=2)
    suggested_survivor: str = Field(min_length=1, max_length=512)
    why: str = Field(min_length=1, max_length=1000)
    merged_utility: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_group(self) -> DedupSuggestion:
        if len(set(self.record_ids)) != len(self.record_ids):
            raise ValueError("record_ids must be unique")
        if self.suggested_survivor not in self.record_ids:
            raise ValueError("suggested_survivor must belong to record_ids")
        return self

    @property
    def survivor(self) -> str:
        return self.suggested_survivor

    @property
    def survivor_id(self) -> str:
        return self.suggested_survivor

    @property
    def group(self) -> list[str]:
        return self.record_ids


class FeedbackPolicy(BaseModel):
    """Threshold contract consumed by the pure retention policy."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    keep_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    demote_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    evict_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    demote_budget_pressure: float = Field(default=0.50, ge=0.0, le=1.0)
    evict_budget_pressure: float = Field(default=0.80, ge=0.0, le=1.0)
    quarantine_on_contradiction: Literal[True] = True

    @model_validator(mode="after")
    def _validate_order(self) -> FeedbackPolicy:
        if not self.evict_threshold <= self.demote_threshold <= self.keep_threshold:
            raise ValueError("thresholds must satisfy evict <= demote <= keep")
        if not self.demote_budget_pressure <= self.evict_budget_pressure:
            raise ValueError("budget thresholds must satisfy demote <= evict")
        return self

    @property
    def thresholds(self) -> dict[str, float]:
        return {
            "keep": self.keep_threshold,
            "demote": self.demote_threshold,
            "evict": self.evict_threshold,
        }

    @property
    def budget_thresholds(self) -> dict[str, float]:
        return {
            "demote": self.demote_budget_pressure,
            "evict": self.evict_budget_pressure,
        }


class PolicyDecision(BaseModel):
    """Decision envelope shared by policy adapters and retention output."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    action: RetentionAction
    reason: str = Field(min_length=1)
    utility: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    budget_pressure: float = Field(ge=0.0, le=1.0)
    disclosure: str = Field(min_length=1)
    record_id: str = ""

    @property
    def decision(self) -> RetentionAction:
        return self.action


class BudgetState(BaseModel):
    """Host-provided storage pressure; pressure is always bounded to 0..1."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    total: float = Field(default=0.0, ge=0.0)
    used: float = Field(default=0.0, ge=0.0)
    pressure: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _derive_pressure_when_omitted(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "pressure" in value:
            return value
        data = dict(value)
        try:
            total = float(data.get("total", 0.0))
            used = float(data.get("used", 0.0))
        except (TypeError, ValueError, OverflowError):
            return value
        if total > 0.0:
            data["pressure"] = min(1.0, max(0.0, used / total))
        elif used > 0.0:
            data["pressure"] = 1.0
        return data

    @property
    def available(self) -> float:
        return max(0.0, self.total - self.used)

    @property
    def pressure_ratio(self) -> float:
        return self.pressure

    @classmethod
    def from_values(cls, total: float, used: float) -> BudgetState:
        return cls(total=total, used=used)


class SignalOutcome(BaseModel):
    """Honest result of normalizing one heterogeneous feedback payload."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    status: SignalStatus
    observation: UtilityObservation | None = None
    reason: str = ""
    disclosure: str = ""
    observation_id: str = ""

    @property
    def accepted(self) -> bool:
        return self.status is SignalStatus.ACCEPTED

    @property
    def duplicate(self) -> bool:
        return self.status is SignalStatus.DUPLICATE

    @property
    def record_id(self) -> str:
        return self.observation.record_id if self.observation else ""

    @property
    def event(self) -> UtilityEvent | None:
        return self.observation.event if self.observation else None

    @property
    def observed_at(self) -> float | None:
        return self.observation.observed_at if self.observation else None


class ObserveResult(BaseModel):
    """Facade result for one observation attempt."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    status: SignalStatus
    record: UtilityRecord | None = None
    reason: str = ""
    disclosure: str = ""
    observation_id: str = ""

    @property
    def accepted(self) -> bool:
        return self.status is SignalStatus.ACCEPTED

    @property
    def duplicate(self) -> bool:
        return self.status is SignalStatus.DUPLICATE

    @property
    def score(self) -> float | None:
        return self.record.score if self.record else None

    @property
    def utility(self) -> float | None:
        return self.score

    @property
    def observation_idempotency_key(self) -> str:
        return self.observation_id

    def __getattr__(self, name: str) -> Any:
        record = self.__dict__.get("record")
        if record is not None and hasattr(record, name):
            return getattr(record, name)
        raise AttributeError(name)


class EvictionNotice(BaseModel):
    """Disclosure emitted when the utility store bounds its own state."""

    record_id: str
    reason: str = "bounded_retention"
    utility: float = 0.0
    last_seen: float = 0.0


class UtilitySnapshot(BaseModel):
    """Serializable facade snapshot with explicit provenance/disclosure."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool
    scope: dict[str, str] = Field(default_factory=dict)
    records: list[UtilityRecord] = Field(default_factory=list)
    evictions: list[EvictionNotice] = Field(default_factory=list)
    disclosure: str = "heuristic utility state; proposals require host authorization"


# A small compatibility alias for callers that prefer the term outcome.
ObservationOutcome = SignalOutcome

__all__ = [
    "BudgetState",
    "DedupSuggestion",
    "EvictionNotice",
    "FeedbackPolicy",
    "ObservationOutcome",
    "ObserveResult",
    "PolicyDecision",
    "RetentionAction",
    "RetentionActionName",
    "RetentionDecision",
    "ScoreLabel",
    "SignalOutcome",
    "SignalStatus",
    "UtilityEvent",
    "UtilityEventName",
    "UtilityObservation",
    "UtilityRecord",
    "UtilitySnapshot",
]
