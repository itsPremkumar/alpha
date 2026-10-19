"""Data models for the L1 working-memory layer.

Design provenance: field semantics (memory type enum, action verbs, priority
scale including the ``-1`` strict-instruction sentinel, timestamp unions)
follow the L1 extraction/dedup contracts of ``tencentdb-agent-memory``
``MemoryCore/src/core/l1-extraction.ts`` and ``l1-dedup.ts`` (MIT) — see
``docs/THIRD_PARTY_MEMORY_NOTICES.md``. This file is an original English
implementation of those semantics for Alpha.

Notes:

- ``MemoryRecord`` is a Pydantic model because the store round-trips records
  through ``model_validate`` / ``model_dump`` / ``model_copy`` (schema
  validation on load protects the on-disk document from partial corruption).
- Priority is the source's 0-100 scale with ``-1`` reserved for an
  extremely-strict standing order (never dropped by min-priority filtering
  unless the operator sets ``min_priority`` above it, which is impossible
  because -1 is below every band).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alpha.security.memory_redaction import redact_for_memory, redact_mapping

MemoryType = Literal[
    "persona",
    "episodic",
    "instruction",
    "work_fact",
    "work_task",
    "work_method",
    "work_artifact",
]
MemoryAction = Literal["store", "skip", "update", "merge"]

MEMORY_TYPES: tuple[str, ...] = (
    "persona",
    "episodic",
    "instruction",
    "work_fact",
    "work_task",
    "work_method",
    "work_artifact",
)
MEMORY_ACTIONS: tuple[str, ...] = ("store", "skip", "update", "merge")

#: Priority of the ``-1`` strict-instruction sentinel (source convention).
STRICT_INSTRUCTION_PRIORITY = -1
#: Default priority when the extractor omits one (mid-band: kept unless the
#: operator raises ``min_priority`` above 60).
DEFAULT_PRIORITY = 60

#: History chain caps (Hy-Memory 2.0 adoption, Phase 1): an ``update``/
#: ``merge`` that replaces a record keeps the prior value as a bounded chain
#: on the surviving record. The count and the per-entry character cap trade
#: "complete ancestry" for a known prompt/storage bound.
MAX_HISTORY_ENTRIES = 4
MAX_HISTORY_ENTRY_CHARS = 600
MAX_HISTORY_TIMESTAMP_ITEMS = 4


class MemoryHistoryEntry(BaseModel):
    """One superseded value in a record's bounded history chain.

    ``record_id`` identifies the record whose content was REPLACED (absorbed
    ids are not live records, so they cannot collide with store keys).
    ``content`` is the redacted prior text; ``timestamps`` carry that prior
    record's time trail; ``updated_at`` is when the prior record last changed
    and ``recorded_at`` is when this supersession happened.
    """

    model_config = ConfigDict(extra="ignore")

    record_id: str = ""
    content: str = ""
    timestamps: list[str] = Field(default_factory=list)
    updated_at: float = 0.0
    recorded_at: float = 0.0


def build_history_entry(record: MemoryRecord, *, recorded_at: float | None = None) -> MemoryHistoryEntry:
    """Redacted, character-capped history entry for a record's CURRENT value.

    Called by the dedup seam when a record is being replaced: the entry carries
    what the record holds at that moment. Content is redacted here so a
    legacy record that reached the store through a side path cannot smuggle a
    secret into a history chain, and overruns are truncated WITH a visible
    marker rather than silently.
    """
    result = redact_for_memory(record.content)
    text = result.redacted_text
    if len(text) > MAX_HISTORY_ENTRY_CHARS:
        text = text[:MAX_HISTORY_ENTRY_CHARS].rstrip() + " …[truncated]"
    ts = time.time() if recorded_at is None else recorded_at
    return MemoryHistoryEntry(
        record_id=str(record.id),
        content=text,
        timestamps=[str(stamp) for stamp in record.timestamps][-MAX_HISTORY_TIMESTAMP_ITEMS:],
        updated_at=float(record.updated_at or 0.0),
        recorded_at=float(ts),
    )


def generate_memory_id(content: str) -> str:
    """Stable, collision-resistant id for a memory content string."""
    digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:24]
    return f"mem_{digest}_{uuid.uuid4().hex[:8]}"


class ExtractedMemory(BaseModel):
    """One candidate memory produced by the extraction LLM."""

    model_config = ConfigDict(extra="ignore")

    content: str = ""
    memory_type: str = "persona"
    priority: int = DEFAULT_PRIORITY
    source_message_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SceneSegment(BaseModel):
    """One scene returned by the extraction LLM (segmentation + memories)."""

    model_config = ConfigDict(extra="ignore")

    scene_name: str = ""
    message_ids: list[str] = Field(default_factory=list)
    memories: list[ExtractedMemory] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    """An L1 memory record (the store's document element)."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    content: str = ""
    type: str = "persona"
    priority: int = DEFAULT_PRIORITY
    scene_name: str = ""
    #: ISO-8601 timestamps belonging to this memory (union on merge).
    timestamps: list[str] = Field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0
    last_accessed_at: float = 0.0
    expires_at: float | None = None
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    #: Bounded, redacted chain of superseded values (Phase 1). Empty unless the
    #: operator enables ``memory.l1.history_kept``; populated ONLY by the dedup
    #: seam's update/merge path, never by extraction.
    history: list[MemoryHistoryEntry] = Field(default_factory=list)

    @field_validator("history", mode="before")
    @classmethod
    def _tolerate_junk_history_entries(cls, values: Any) -> list[Any]:
        """Degrade a malformed history element instead of failing the record.

        Store documents round-trip through ``model_validate``; a hand-edited or
        partially corrupt chain must not brick the whole record load. Non-
        mapping elements validate as entries with defaulted fields (empty
        strings), which are inert: they never match a live record id and the
        recall tail renders their (empty) content only as contained text.
        """
        if not isinstance(values, list):
            return []
        return [value if isinstance(value, Mapping) else {} for value in values]

    @classmethod
    def create(
        cls,
        content: str,
        *,
        memory_type: str = "persona",
        priority: int = DEFAULT_PRIORITY,
        scene_name: str = "",
        timestamps: list[str] | None = None,
        ttl_days: int | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> MemoryRecord:
        ts = time.time() if now is None else now
        content_result = redact_for_memory(content)
        scene_result = redact_for_memory(scene_name)
        metadata_result = redact_mapping(dict(metadata or {}))
        tags_result = redact_mapping({"tags": list(tags or [])})
        timestamps_result = redact_mapping({"timestamps": list(timestamps or [])})
        safe_metadata = dict(metadata_result.redacted_payload)
        safe_tags = list(tags_result.redacted_payload["tags"])
        safe_timestamps = list(timestamps_result.redacted_payload["timestamps"])
        redactions = [
            *content_result.redactions,
            *scene_result.redactions,
            *metadata_result.redactions,
            *tags_result.redactions,
            *timestamps_result.redactions,
        ]
        # The report is generated by this boundary; do not trust a caller- or
        # extractor-supplied field with the same name.
        safe_metadata.pop("memory_redaction", None)
        if redactions:
            safe_metadata["memory_redaction"] = {
                "redacted_count": len(redactions),
                "redacted_kinds": sorted({item.kind for item in redactions}),
            }
        return cls(
            id=generate_memory_id(content_result.redacted_text),
            content=content_result.redacted_text,
            type=memory_type,
            priority=priority,
            scene_name=scene_result.redacted_text,
            timestamps=safe_timestamps,
            created_at=ts,
            updated_at=ts,
            last_accessed_at=ts,
            expires_at=(ts + ttl_days * 86400.0) if ttl_days else None,
            tags=safe_tags,
            metadata=safe_metadata,
        )

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time()

    @property
    def age_days(self) -> float:
        return max(0.0, (time.time() - self.created_at) / 86400.0)

    def touch(self, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        self.access_count += 1
        self.last_accessed_at = ts

    def bump_version(self, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        self.version += 1
        self.updated_at = ts

    def to_prompt_dict(self) -> dict[str, Any]:
        """Shape the conflict-detection prompt expects for one NEW memory."""
        return {
            "record_id": self.id,
            "content": self.content,
            "type": self.type,
            "priority": self.priority,
            "scene_name": self.scene_name,
            "timestamps": list(self.timestamps),
        }

    def to_candidate_dict(self) -> dict[str, Any]:
        """Shape the conflict-detection prompt expects for an EXISTING record.

        Existing candidates are keyed by ``id`` (the unified candidate pool),
        while new memories are keyed by ``record_id``; the two shapes are
        distinct on purpose, so they get distinct builders.
        """
        return {
            "id": self.id,
            "content": self.content,
            "type": self.type,
            "priority": self.priority,
            "scene_name": self.scene_name,
            "timestamps": list(self.timestamps),
        }


class DedupDecision(BaseModel):
    """One conflict-detection decision (``l1-dedup.ts`` output contract)."""

    model_config = ConfigDict(extra="ignore")

    record_id: str = ""
    action: MemoryAction = "store"
    #: OLD record ids to remove and replace (many-to-many merge).
    target_ids: list[str] = Field(default_factory=list)
    merged_content: str = ""
    merged_type: str = ""
    merged_priority: int | None = None
    merged_timestamps: list[str] = Field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class ExtractionOutcome:
    """Result of a single extraction LLM call."""

    status: str  # ok | no_json | parse_fail | not_array | empty_scenes | llm_error
    scenes: list[SceneSegment] = field(default_factory=list)
    raw: str = ""
    error: str = ""
    #: Credits consumed by the LLM call (0.0 when no usage was reported).
    credits: float = 0.0

    @property
    def memories(self) -> list[ExtractedMemory]:
        """All memories across scenes, in scene order."""
        return [memory for scene in self.scenes for memory in scene.memories]


@dataclass(slots=True)
class DedupOutcome:
    """Result of a single conflict-detection LLM call."""

    status: str  # ok | no_json | parse_fail | not_array | empty | llm_error
    decisions: list[DedupDecision] = field(default_factory=list)
    raw: str = ""
    error: str = ""
    #: Credits consumed by the LLM call (0.0 when no usage was reported).
    credits: float = 0.0

    def by_record_id(self) -> dict[str, DedupDecision]:
        return {d.record_id: d for d in self.decisions if d.record_id}


@dataclass(slots=True)
class RunReport:
    """Honest per-run report (never fakes success).

    ``status`` is one of ``succeeded`` | ``failed`` | ``skipped``; failures
    carry an ``error`` string, skipped runs carry a ``reason``.
    """

    status: Literal["succeeded", "failed", "skipped"]
    reason: str = ""
    error: str = ""
    stored: int = 0
    skipped: int = 0
    updated: int = 0
    merged: int = 0
    quota_blocked: bool = False
    #: Candidates refused by the record quota (disclosed, never silent).
    refused: int = 0
    credits_used: float = 0.0
    extraction_status: str = ""
    dedup_status: str = ""
    retention: dict[str, int] = field(default_factory=dict)
    persona_status: str = ""
    #: Total history-chain entries carried by records written this run
    #: (update/merge priors). 0 unless ``memory.l1.history_kept`` is on.
    history_entries: int = 0
    #: Dependent records marked stale through entity-memory edges this run
    #: (staleness journal entries appended). 0 unless
    #: ``memory.l1.staleness_propagation`` is on.
    stale_dependents: int = 0
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "error": self.error,
            "stored": self.stored,
            "skipped": self.skipped,
            "updated": self.updated,
            "merged": self.merged,
            "quota_blocked": self.quota_blocked,
            "refused": self.refused,
            "credits_used": self.credits_used,
            "extraction_status": self.extraction_status,
            "dedup_status": self.dedup_status,
            "retention": dict(self.retention),
            "persona_status": self.persona_status,
            "history_entries": self.history_entries,
            "stale_dependents": self.stale_dependents,
            "duration_ms": self.duration_ms,
        }


__all__ = [
    "DEFAULT_PRIORITY",
    "MAX_HISTORY_ENTRIES",
    "MAX_HISTORY_ENTRY_CHARS",
    "MAX_HISTORY_TIMESTAMP_ITEMS",
    "MEMORY_ACTIONS",
    "MEMORY_TYPES",
    "STRICT_INSTRUCTION_PRIORITY",
    "DedupDecision",
    "DedupOutcome",
    "ExtractedMemory",
    "ExtractionOutcome",
    "MemoryAction",
    "MemoryHistoryEntry",
    "MemoryRecord",
    "MemoryType",
    "RunReport",
    "SceneSegment",
    "build_history_entry",
    "generate_memory_id",
]
