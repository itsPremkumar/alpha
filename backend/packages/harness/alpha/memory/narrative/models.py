"""Validated data contracts for Alpha's narrative (autobiographical) memory.

Narrative memory is deliberately separate from episodic traces and persona
facts.  An event is a dated, source-backed moment; a chapter is an ordered
bucket of moments; a document is the bounded human-readable story assembled
from those chapters.  The models are strict about ordering and disclose any
normalization applied to untrusted source dictionaries.

The reflection/importance-weighted distillation idea is inspired by
*Generative Agents: Interactive Simulacra of Human Behavior* (Park et al.,
UIST 2023).  The implementation here is original and deliberately small: no
model is required to produce an honest deterministic story.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NarrativeScope(StrEnum):
    """Closed kinds of life/project/agent story namespaces."""

    USER = "user"
    PROJECT = "project"
    AGENT = "agent"


class SynthesisStatus(StrEnum):
    """Closed result states for optional model refinement."""

    OK = "ok"
    NO_JSON = "no_json"
    PARSE_FAIL = "parse_fail"
    NOT_ARRAY = "not_array"
    EMPTY = "empty"
    LLM_ERROR = "llm_error"


class IngestStatus(StrEnum):
    """Closed result states for an event-ingest attempt."""

    STORED = "stored"
    PARTIAL = "partial"
    DISABLED = "disabled"
    FILTERED = "filtered"
    EMPTY = "empty"
    FAILED = "failed"
    STORE_ERROR = "store_error"


class RegenerationStatus(StrEnum):
    """Closed high-level states for a story regeneration attempt."""

    OK = "ok"
    UNCHANGED = "unchanged"
    DISABLED = "disabled"
    EMPTY = "empty"
    FAILED = "failed"
    STORE_ERROR = "store_error"


SCOPE_TYPES: frozenset[str] = frozenset(item.value for item in NarrativeScope)
SYNTHESIS_STATUSES: frozenset[str] = frozenset(item.value for item in SynthesisStatus)
_MAX_REF_LENGTH = 512
_MAX_TITLE_LENGTH = 500
_MAX_SUMMARY_LENGTH = 4_000
_MAX_ENTRIES = 256
_MAX_EVENT_IDS = 1_024
_MAX_CHAPTERS = 1_000
_MAX_DOCUMENT_CHARS = 2_000_000
MAX_CHAPTERS = _MAX_CHAPTERS


def _utc_timestamp(value: datetime) -> float:
    """Convert a datetime to a UTC timestamp without depending on local time."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _timestamp(value: Any, field_name: str) -> float:
    """Coerce common plain-dict timestamp forms to a finite epoch value."""

    if isinstance(value, datetime):
        result = _utc_timestamp(value)
    elif isinstance(value, date):
        result = _utc_timestamp(datetime.combine(value, datetime.min.time()))
    elif isinstance(value, bool):
        raise ValueError(f"{field_name} must be a timestamp, not bool")
    else:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{field_name} must not be blank")
        try:
            result = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"{field_name} must be an epoch or ISO timestamp") from exc
            result = _utc_timestamp(parsed)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _text(value: Any, *, limit: int, field_name: str) -> str:
    """Normalize and bound one text field while retaining a useful value."""

    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{field_name} must not be blank")
    return text[:limit]


def _string_list(value: Any, *, limit: int, field_name: str) -> list[str]:
    """Normalize a scalar/list source field into unique bounded strings."""

    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, set):
        values = sorted(value, key=str)
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = [value]
    output: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item is None:
            continue
        text = " ".join(str(item).split())
        if not text:
            continue
        text = text[:_MAX_REF_LENGTH]
        if text not in seen:
            seen.add(text)
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _disclosure(values: Any, name: str) -> list[str]:
    """Add one stable disclosure name without duplicating it."""

    current = values if isinstance(values, list) else _string_list(values, limit=32, field_name="clamped_fields")
    if name not in current:
        current.append(name)
    return current


def _parse_scope(value: Any, explicit_id: Any = None) -> tuple[str, str | None]:
    """Parse ``user``/``project``/``agent`` and optional ``type:id`` forms."""

    scope_id = str(explicit_id).strip() if explicit_id is not None else ""
    if isinstance(value, Mapping):
        kind = value.get("type") or value.get("scope") or value.get("kind")
        nested_id = value.get("id") or value.get("scope_id") or value.get("key")
        if nested_id is not None and not scope_id:
            scope_id = str(nested_id).strip()
        value = kind
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        kind, nested_id = value
        if nested_id is not None and not scope_id:
            scope_id = str(nested_id).strip()
        value = kind
    text = str(value or "user").strip().lower()
    for separator in (":", "/", "#"):
        if separator in text:
            prefix, suffix = text.split(separator, 1)
            if prefix in SCOPE_TYPES:
                if suffix.strip() and not scope_id:
                    scope_id = suffix.strip()
                text = prefix
                break
    if text not in SCOPE_TYPES:
        raise ValueError(f"scope must be one of {sorted(SCOPE_TYPES)}")
    return text, scope_id or None


def _importance(value: Any, disclosures: list[str]) -> float:
    """Coerce importance to 0..100 and disclose out-of-range normalization."""

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("importance must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError("importance must be a finite number")
    bounded = min(100.0, max(0.0, number))
    if bounded != number:
        _disclosure(disclosures, "importance")
    return bounded


def _event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


class NarrativeEvent(BaseModel):
    """One dated, source-backed event in a life/project/agent story."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    id: str = Field(default_factory=_event_id, min_length=1, max_length=256)
    scope: NarrativeScope = Field(default=NarrativeScope.USER)
    scope_id: str = Field(default="default", min_length=1, max_length=256)
    period_start: float = Field()
    period_end: float = Field()
    title: str = Field(min_length=1, max_length=_MAX_TITLE_LENGTH)
    summary: str = Field(min_length=1, max_length=_MAX_SUMMARY_LENGTH)
    participants: list[str] = Field(default_factory=list, max_length=128)
    outcomes: list[str] = Field(default_factory=list, max_length=128)
    importance: float = Field(default=50.0, ge=0.0, le=100.0)
    source_refs: list[str] = Field(default_factory=list, max_length=256)
    created_at: float = Field(default_factory=time.time)
    clamped_fields: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def _prepare_source_dict(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        disclosures = _string_list(data.get("clamped_fields"), limit=32, field_name="clamped_fields")
        scope_value = data.get("scope", NarrativeScope.USER.value)
        explicit_id = data.get("scope_id")
        if explicit_id is None:
            explicit_id = data.get("subject_id")
        if explicit_id is None:
            explicit_id = data.get("user_id") or data.get("project_id") or data.get("agent_id")
        scope, inferred_id = _parse_scope(scope_value, explicit_id)
        data["scope"] = scope
        if inferred_id and str(data.get("scope_id") or "default").strip() in {"", "default"}:
            data["scope_id"] = inferred_id
        if not str(data.get("scope_id") or "").strip():
            data["scope_id"] = "default"
        if not str(data.get("id") or "").strip():
            data["id"] = _event_id()
        if not str(data.get("title") or "").strip():
            data["title"] = str(data.get("summary") or data.get("content") or "Untitled event")
        if not str(data.get("summary") or "").strip():
            data["summary"] = data.get("title")
        created = data.get("created_at", data.get("timestamp"))
        if created is None:
            created = time.time()
        data["created_at"] = _timestamp(created, "created_at")
        period_start = data.get("period_start", data.get("start_time", data.get("timestamp")))
        if period_start is None:
            period_start = data["created_at"]
        data["period_start"] = _timestamp(period_start, "period_start")
        period_end = data.get("period_end", data.get("end_time"))
        if period_end is None:
            period_end = data["period_start"]
        data["period_end"] = _timestamp(period_end, "period_end")
        for field_name, limit in (
            ("title", _MAX_TITLE_LENGTH),
            ("summary", _MAX_SUMMARY_LENGTH),
        ):
            original = str(data.get(field_name) or "")
            normalized = _text(original, limit=limit, field_name=field_name)
            if len(normalized) != len(original):
                _disclosure(disclosures, f"{field_name}_truncated")
            data[field_name] = normalized
        for field_name, limit in (
            ("participants", 128),
            ("outcomes", 128),
            ("source_refs", 256),
        ):
            original = data.get(field_name)
            normalized = _string_list(original, limit=limit, field_name=field_name)
            if isinstance(original, (list, tuple, set)) and len(original) > limit:
                _disclosure(disclosures, f"{field_name}_truncated")
            if isinstance(original, str) and len(original) > _MAX_REF_LENGTH:
                _disclosure(disclosures, f"{field_name}_truncated")
            data[field_name] = normalized
        data["importance"] = _importance(data.get("importance", 50.0), disclosures)
        data["clamped_fields"] = disclosures
        return data

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in SCOPE_TYPES:
            raise ValueError(f"scope must be one of {sorted(SCOPE_TYPES)}")
        return NarrativeScope(normalized)

    @field_validator("scope_id")
    @classmethod
    def _validate_scope_id(cls, value: str) -> str:
        normalized = " ".join(str(value).split())
        if not normalized:
            return "default"
        return normalized[:256]

    @field_validator("period_start", "period_end", "created_at", mode="before")
    @classmethod
    def _validate_timestamp(cls, value: Any, info: Any) -> float:
        return _timestamp(value, info.field_name)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _text(value, limit=_MAX_TITLE_LENGTH, field_name="title")

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _text(value, limit=_MAX_SUMMARY_LENGTH, field_name="summary")

    @field_validator("participants", "outcomes", "source_refs", "clamped_fields")
    @classmethod
    def _validate_string_lists(cls, value: list[str]) -> list[str]:
        return _string_list(value, limit=512, field_name="list")

    @model_validator(mode="after")
    def _validate_period_order(self) -> NarrativeEvent:
        if self.period_end < self.period_start:
            raise ValueError("period_end must be greater than or equal to period_start")
        return self

    @property
    def scope_key(self) -> str:
        """Canonical key used by the per-scope filesystem and store."""

        if self.scope_id == "default":
            return self.scope
        return f"{self.scope}:{self.scope_id}"

    @property
    def was_clamped(self) -> bool:
        return bool(self.clamped_fields)

    @property
    def disclosures(self) -> list[str]:
        """Alias for the persisted normalization disclosure list."""

        return list(self.clamped_fields)

    @property
    def clamp_disclosure(self) -> str:
        return "none" if not self.clamped_fields else "clamped:" + ",".join(self.clamped_fields)

    def fingerprint(self) -> str:
        """Return a stable semantic fingerprint for incremental regeneration."""

        import hashlib

        material = "|".join(
            (
                self.id,
                self.scope_key,
                f"{self.period_start:.6f}",
                f"{self.period_end:.6f}",
                self.title,
                self.summary,
                "\x1f".join(self.participants),
                "\x1f".join(self.outcomes),
                f"{self.importance:.6f}",
                "\x1f".join(self.source_refs),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


class StoryChapter(BaseModel):
    """An ordered period bucket containing source-backed story entries."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    heading: str = Field(min_length=1, max_length=500)
    period: str = Field(min_length=1, max_length=128)
    entries: list[str] = Field(default_factory=list, max_length=_MAX_ENTRIES)
    summary: str = Field(default="", max_length=_MAX_SUMMARY_LENGTH)
    event_ids: list[str] = Field(default_factory=list, max_length=_MAX_EVENT_IDS)
    period_start: float | None = Field(default=None)
    period_end: float | None = Field(default=None)
    synthesis: str = Field(default="deterministic", max_length=32)
    source_fingerprint: str = Field(default="", max_length=128)
    truncated: bool = False
    clamped_fields: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def _prepare_chapter(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        disclosures = _string_list(data.get("clamped_fields"), limit=32, field_name="clamped_fields")
        period = " ".join(str(data.get("period") or data.get("heading") or "undated").split())
        data["period"] = period[:128] or "undated"
        heading = str(data.get("heading") or period).strip()
        data["heading"] = " ".join(heading.split())[:500] or period
        entries_raw = data.get("entries")
        if entries_raw is None:
            entries_raw = []
        if isinstance(entries_raw, str):
            entries_raw = [entries_raw]
        if not isinstance(entries_raw, (list, tuple)):
            entries_raw = []
        entries = [" ".join(str(item).split())[:2_000] for item in entries_raw]
        entries = [item for item in entries if item]
        if len(entries) > _MAX_ENTRIES:
            entries = entries[:_MAX_ENTRIES]
            _disclosure(disclosures, "entries_truncated")
        data["entries"] = entries
        event_ids_raw = data.get("event_ids") or []
        if isinstance(event_ids_raw, str):
            event_ids_raw = [event_ids_raw]
        if not isinstance(event_ids_raw, (list, tuple, set)):
            event_ids_raw = []
        event_ids = _string_list(event_ids_raw, limit=_MAX_EVENT_IDS, field_name="event_ids")
        if len(event_ids_raw) > _MAX_EVENT_IDS:
            _disclosure(disclosures, "event_ids_truncated")
        data["event_ids"] = event_ids
        synthesis = str(data.get("synthesis") or "deterministic").strip().lower()
        if synthesis not in {"deterministic", "model"}:
            _disclosure(disclosures, "synthesis_invalid_defaulted")
            synthesis = "deterministic"
        data["synthesis"] = synthesis
        data["clamped_fields"] = disclosures
        return data

    @field_validator("heading")
    @classmethod
    def _validate_heading(cls, value: str) -> str:
        return " ".join(str(value).split())[:500]

    @field_validator("period")
    @classmethod
    def _validate_period(cls, value: str) -> str:
        return " ".join(str(value).split())[:128]

    @field_validator("entries")
    @classmethod
    def _validate_entries(cls, value: list[str]) -> list[str]:
        return _string_list(value, limit=_MAX_ENTRIES, field_name="entries")

    @field_validator("event_ids")
    @classmethod
    def _validate_event_ids(cls, value: list[str]) -> list[str]:
        return _string_list(value, limit=_MAX_EVENT_IDS, field_name="event_ids")

    @model_validator(mode="after")
    def _validate_period_order(self) -> StoryChapter:
        if self.period_start is not None and self.period_end is not None:
            if self.period_end < self.period_start:
                raise ValueError("chapter period_end must be >= period_start")
        return self

    def render(self) -> str:
        """Render this chapter as compact Markdown."""

        lines = [f"## {self.heading}"]
        if self.period and self.period not in self.heading:
            lines.append(f"Period: {self.period}")
        lines.extend(f"- {entry}" for entry in self.entries)
        return "\n".join(lines)

    def source_changed(self, events: list[NarrativeEvent]) -> bool:
        """Whether source IDs/content differ from this chapter's fingerprint."""

        import hashlib

        material = "|".join(event.fingerprint() for event in events)
        current = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        return self.source_fingerprint != current


class StoryDocument(BaseModel):
    """A bounded, ordered story document for one scope."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    scope: str = Field(default=NarrativeScope.USER.value, min_length=1, max_length=32)
    scope_id: str = Field(default="default", min_length=1, max_length=256)
    chapters: list[StoryChapter] = Field(default_factory=list, max_length=_MAX_CHAPTERS)
    total_chars: int = Field(default=0, ge=0)
    generated_at: float = Field(default_factory=time.time)
    source_event_count: int = Field(default=0, ge=0)
    synthesis: str = Field(default="deterministic", max_length=32)
    changed_chapters: list[str] = Field(default_factory=list, max_length=_MAX_CHAPTERS)
    truncated: bool = False
    disclosures: list[str] = Field(default_factory=list, max_length=64)
    clamped_fields: list[str] = Field(default_factory=list, max_length=64)
    rendered_text: str = Field(default="", max_length=_MAX_DOCUMENT_CHARS)

    @model_validator(mode="before")
    @classmethod
    def _prepare_document(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        scope, inferred_id = _parse_scope(data.get("scope", NarrativeScope.USER.value), data.get("scope_id"))
        data["scope"] = scope
        if inferred_id and str(data.get("scope_id") or "default").strip() in {"", "default"}:
            data["scope_id"] = inferred_id
        if not str(data.get("scope_id") or "").strip():
            data["scope_id"] = "default"
        disclosures = _string_list(data.get("disclosures"), limit=64, field_name="disclosures")
        clamped = _string_list(data.get("clamped_fields"), limit=64, field_name="clamped_fields")
        synthesis = str(data.get("synthesis") or "deterministic").strip().lower()
        if synthesis not in {"deterministic", "model", "mixed"}:
            _disclosure(disclosures, "synthesis_invalid_defaulted")
            synthesis = "deterministic"
        data["synthesis"] = synthesis
        data["disclosures"] = disclosures
        data["clamped_fields"] = clamped
        if len(data.get("chapters") or []) > _MAX_CHAPTERS:
            _disclosure(disclosures, "chapters_truncated")
            data["chapters"] = list(data["chapters"])[:_MAX_CHAPTERS]
        return data

    @field_validator("generated_at", mode="before")
    @classmethod
    def _validate_generated_at(cls, value: Any) -> float:
        return _timestamp(value, "generated_at")

    @field_validator("chapters")
    @classmethod
    def _validate_chapters(cls, value: list[StoryChapter]) -> list[StoryChapter]:
        return list(value)

    @model_validator(mode="after")
    def _validate_order_and_length(self) -> StoryDocument:
        previous = float("-inf")
        for chapter in self.chapters:
            start = chapter.period_start if chapter.period_start is not None else previous
            if start < previous:
                raise ValueError("story chapters must be ordered by period_start")
            previous = start
        if self.rendered_text and self.total_chars != len(self.rendered_text):
            object.__setattr__(self, "total_chars", len(self.rendered_text))
        if self.total_chars < 0:
            raise ValueError("total_chars must not be negative")
        return self

    def render(self) -> str:
        """Return the stored bounded text or render a legacy document."""

        if self.rendered_text:
            return self.rendered_text
        lines = [f"# {self.scope} story", f"synthesis={self.synthesis}"]
        for chapter in self.chapters:
            lines.append(chapter.render())
        if self.truncated:
            lines.append("[story truncated to configured character budget]")
        if self.disclosures:
            lines.append("disclosures: " + ", ".join(self.disclosures))
        return "\n".join(lines)

    @property
    def text(self) -> str:
        return self.render()

    @property
    def ok(self) -> bool:
        return True

    @property
    def clamp_disclosure(self) -> str:
        return "none" if not self.clamped_fields else "clamped:" + ",".join(self.clamped_fields)


class SynthesisOutcome(BaseModel):
    """Result of deterministic or model-refined chapter synthesis."""

    model_config = ConfigDict(extra="forbid")

    status: SynthesisStatus
    chapters: list[StoryChapter] = Field(default_factory=list, max_length=_MAX_CHAPTERS)
    changed_chapters: list[str] = Field(default_factory=list, max_length=_MAX_CHAPTERS)
    raw: str = ""
    error: str = ""
    model_name: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is SynthesisStatus.OK


class IngestResult(BaseModel):
    """Honest disclosure of one event-ingest attempt."""

    model_config = ConfigDict(extra="forbid")

    status: IngestStatus
    considered: int = Field(default=0, ge=0)
    stored: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    filtered: int = Field(default=0, ge=0)
    event_ids: list[str] = Field(default_factory=list, max_length=1_024)
    evicted_event_ids: list[str] = Field(default_factory=list, max_length=1_024)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {IngestStatus.STORED, IngestStatus.PARTIAL}


class RegenerationResult(BaseModel):
    """Outcome of a story regeneration, including model failure disclosure."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=False)

    status: str
    synthesis_status: str
    document: StoryDocument | None = None
    changed_chapters: list[str] = Field(default_factory=list, max_length=_MAX_CHAPTERS)
    event_count: int = Field(default=0, ge=0)
    chars: int = Field(default=0, ge=0)
    error: str = ""
    story_unchanged: bool = True

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "unchanged", "empty"}

    @property
    def synthesis(self) -> str:
        """Synthesis label carried by the returned document, if any."""

        return self.document.synthesis if self.document is not None else ""

    @property
    def story(self) -> StoryDocument | None:
        """Compatibility alias for the persisted document result."""

        return self.document

    @property
    def text(self) -> str:
        """Rendered story text, or an honest empty string on failure."""

        return self.document.render() if self.document is not None else ""


__all__ = [
    "MAX_CHAPTERS",
    "SCOPE_TYPES",
    "SYNTHESIS_STATUSES",
    "IngestResult",
    "IngestStatus",
    "NarrativeEvent",
    "NarrativeScope",
    "RegenerationResult",
    "RegenerationStatus",
    "StoryChapter",
    "StoryDocument",
    "SynthesisOutcome",
    "SynthesisStatus",
]
