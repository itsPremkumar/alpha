"""Data contracts for Alpha's affective (emotional) memory.

The continuous dimensions follow James A. Russell's valence-arousal
circumplex model (*A circumplex model of affect*, Journal of Personality and
Social Psychology, 39(6), 1980). Intensity and confidence are separate
memory-quality dimensions rather than extra circumplex axes.

All models are deliberately closed and serializable. Numeric affect values are
clamped to their declared ranges and the affected field names are retained in
``clamped_fields`` so a caller can disclose that normalization occurred.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AffectSubject(StrEnum):
    """Whose affect an event describes."""

    USER = "user"
    AGENT = "agent"
    INTERACTION = "interaction"
    TOPIC = "topic"


class AffectSource(StrEnum):
    """How an affect label was obtained."""

    EXPLICIT = "explicit"
    MODEL_INFERRED = "model_inferred"
    CALLER = "caller"


class ExtractionStatus(StrEnum):
    """Closed result set for sentiment extraction."""

    OK = "ok"
    NO_JSON = "no_json"
    PARSE_FAIL = "parse_fail"
    NOT_ARRAY = "not_array"
    EMPTY = "empty"
    LLM_ERROR = "llm_error"


class MoodStatus(StrEnum):
    """Whether a mood state contains observed signal."""

    OK = "ok"
    NO_EVENTS = "no_events"
    UNAVAILABLE = "unavailable"


class IngestStatus(StrEnum):
    """Closed result set for an affect-event ingest attempt."""

    STORED = "stored"
    DISABLED = "disabled"
    BELOW_CONFIDENCE = "below_confidence"
    EMPTY = "empty"
    EXTRACTION_FAILED = "extraction_failed"
    PARTIAL = "partial"
    STORE_ERROR = "store_error"


def _event_id() -> str:
    return f"aff_{uuid.uuid4().hex}"


def _disclose_clamping(
    payload: Mapping[str, Any],
    *,
    fields: tuple[tuple[str, float, float], ...],
) -> dict[str, Any]:
    """Clamp known numeric fields and return a new payload with disclosure."""
    data = dict(payload)
    disclosed = data.get("clamped_fields")
    names = [str(name) for name in disclosed] if isinstance(disclosed, list) else []
    for name, lower, upper in fields:
        if name not in data or data[name] is None:
            continue
        try:
            original = float(data[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a finite number") from exc
        if not math.isfinite(original):
            raise ValueError(f"{name} must be a finite number")
        bounded = min(upper, max(lower, original))
        data[name] = bounded
        if bounded != original and name not in names:
            names.append(name)
    data["clamped_fields"] = names
    return data


class AffectEvent(BaseModel):
    """One remembered emotional moment on valence/arousal dimensions."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_event_id, min_length=1, max_length=128)
    user_id: str = Field(max_length=256)
    agent_name: str | None = Field(default=None, max_length=256)
    subject: AffectSubject
    content: str = Field(min_length=1, max_length=4000)
    valence: float
    arousal: float
    intensity: float
    confidence: float
    source: AffectSource
    record_refs: list[str] = Field(default_factory=list, max_length=64)
    created_at: float = Field(default_factory=time.time)
    clamped_fields: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="before")
    @classmethod
    def _clamp_affective_values(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        return _disclose_clamping(
            value,
            fields=(
                ("valence", -1.0, 1.0),
                ("arousal", 0.0, 1.0),
                ("intensity", 0.0, 1.0),
                ("confidence", 0.0, 1.0),
            ),
        )

    @field_validator("content")
    @classmethod
    def _normalize_content(cls, value: str) -> str:
        content = value.strip()
        if not content:
            raise ValueError("content must not be blank")
        return content

    @field_validator("record_refs")
    @classmethod
    def _normalize_refs(cls, value: list[str]) -> list[str]:
        return [item.strip()[:256] for item in value if item.strip()]

    @property
    def was_clamped(self) -> bool:
        """Whether any input value was clamped during validation."""
        return bool(self.clamped_fields)

    @property
    def clamp_disclosure(self) -> str:
        """Stable disclosure suitable for provenance and diagnostics."""
        if not self.clamped_fields:
            return "none"
        return "clamped:" + ",".join(self.clamped_fields)


class MoodState(BaseModel):
    """Decay-weighted aggregate of observed affect at one point in time."""

    model_config = ConfigDict(extra="forbid")

    valence: float = 0.0
    arousal: float = 0.0
    intensity: float = 0.0
    confidence: float = 0.0
    sample_size: int = Field(default=0, ge=0)
    computed_at: float = Field(default_factory=time.time)
    half_life_hours: float = Field(gt=0.0)
    status: MoodStatus = MoodStatus.NO_EVENTS
    clamped_fields: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="before")
    @classmethod
    def _clamp_mood_values(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        return _disclose_clamping(
            value,
            fields=(
                ("valence", -1.0, 1.0),
                ("arousal", 0.0, 1.0),
                ("intensity", 0.0, 1.0),
                ("confidence", 0.0, 1.0),
            ),
        )

    @property
    def was_clamped(self) -> bool:
        return bool(self.clamped_fields)

    @property
    def clamp_disclosure(self) -> str:
        if not self.clamped_fields:
            return "none"
        return "clamped:" + ",".join(self.clamped_fields)


class ExtractionOutcome(BaseModel):
    """Result of one optional model-based sentiment extraction call."""

    model_config = ConfigDict(extra="forbid")

    status: ExtractionStatus
    events: list[AffectEvent] = Field(default_factory=list)
    raw: str = ""
    error: str = ""
    rejected_items: int = Field(default=0, ge=0)

    @property
    def ok(self) -> bool:
        return self.status is ExtractionStatus.OK


class IngestResult(BaseModel):
    """Honest disclosure of an event-ingest attempt."""

    model_config = ConfigDict(extra="forbid")

    status: IngestStatus
    considered: int = Field(default=0, ge=0)
    stored: int = Field(default=0, ge=0)
    filtered: int = Field(default=0, ge=0)
    event_ids: list[str] = Field(default_factory=list)
    evicted_event_ids: list[str] = Field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (IngestStatus.STORED, IngestStatus.PARTIAL)


__all__ = [
    "AffectEvent",
    "AffectSource",
    "AffectSubject",
    "ExtractionOutcome",
    "ExtractionStatus",
    "IngestResult",
    "IngestStatus",
    "MoodState",
    "MoodStatus",
]
