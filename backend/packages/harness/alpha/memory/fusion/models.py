"""Typed contracts for multi-stage memory fusion and context composition.

The models in this module are intentionally storage-neutral. Retrieval adapters
map their backend records into :class:`Candidate`; no adapter is allowed to
invent a candidate that its provider did not return.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RetrievalMode = Literal["exact", "semantic", "graph", "temporal", "procedural", "hybrid"]
StageStatus = Literal["ok", "unavailable", "empty"]
TemporalOperation = Literal["latest", "as_of", "changed_in_window"]


class Candidate(BaseModel):
    """One retrieval candidate plus the metadata needed for fusion/composition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=512)
    memory_type: str = Field(min_length=1, max_length=128)
    subtype: str | None = Field(default=None, max_length=128)
    content: str = ""
    summary: str | None = None
    score_components: dict[str, float] = Field(default_factory=dict)
    scope_id: str | None = Field(default=None, max_length=512)
    created_at: float | None = Field(default=None, ge=0.0)
    updated_at: float | None = Field(default=None, ge=0.0)
    event_time: float | None = Field(default=None, ge=0.0)
    valid_from: float | None = Field(default=None, ge=0.0)
    valid_to: float | None = Field(default=None, ge=0.0)
    last_accessed_at: float | None = Field(default=None, ge=0.0)
    access_count: int = Field(default=0, ge=0)
    authority: float = Field(default=0.0, ge=0.0, le=1.0)
    source_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    evidence_group: str | None = Field(default=None, max_length=512)
    contradiction_group: str | None = Field(default=None, max_length=512)
    supersedes: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("score_components")
    @classmethod
    def _finite_score_components(cls, value: dict[str, float]) -> dict[str, float]:
        cleaned: dict[str, float] = {}
        for key, score in value.items():
            numeric = float(score)
            if not math.isfinite(numeric):
                raise ValueError(f"score component {key!r} must be finite")
            cleaned[str(key)] = numeric
        return cleaned

    @model_validator(mode="after")
    def _has_compact_content(self) -> Candidate:
        if not self.content.strip() and not (self.summary or "").strip():
            raise ValueError("candidate requires content or summary")
        return self

    @property
    def recency(self) -> float:
        """Best available validity/event/update timestamp for deterministic ordering."""
        moments = [value for value in (self.valid_from, self.event_time, self.updated_at, self.created_at) if value is not None]
        return max(moments, default=0.0)

    @property
    def searchable_text(self) -> str:
        """Candidate text used by duplicate and diversity policies."""
        return " ".join(part for part in (self.summary, self.content) if part)


class StageResult(BaseModel):
    """Outcome of one attempted retrieval stage.

    ``unavailable`` is deliberately distinct from ``empty``: only the latter
    means a working provider returned no candidates.
    """

    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1, max_length=64)
    candidates: tuple[Candidate, ...] = ()
    status: StageStatus
    reason: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _status_is_honest(self) -> StageResult:
        if self.status == "unavailable" and not (self.reason or "").strip():
            raise ValueError("unavailable stage results require a reason")
        if self.status != "ok" and self.candidates:
            raise ValueError("only ok stage results may contain candidates")
        return self

    @property
    def stage_name(self) -> str:
        """Compatibility/readability alias for integrations that prefer ``stage_name``."""
        return self.stage


class DroppedItem(BaseModel):
    """A candidate/result that was not selected, with a machine-readable reason."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=512)
    reason: str = Field(min_length=1, max_length=128)
    details: dict[str, object] = Field(default_factory=dict)


class FusedCandidate(Candidate):
    """A candidate with reproducible fusion score and score disclosures."""

    fused_score: float
    weighted_contributions: dict[str, float] = Field(default_factory=dict)
    stage_contributions: dict[str, float] = Field(default_factory=dict)
    penalty_contributions: dict[str, float] = Field(default_factory=dict)
    missing_components: tuple[str, ...] = ()
    source_stages: tuple[str, ...] = ()


class FusionResult(BaseModel):
    """Fused candidates plus stage and latency disclosures."""

    model_config = ConfigDict(extra="forbid")

    candidates: tuple[FusedCandidate, ...] = ()
    strategy: Literal["weighted", "rrf"]
    stages_run: tuple[str, ...] = ()
    stages_unavailable: dict[str, str] = Field(default_factory=dict)
    total_latency_ms: float = Field(default=0.0, ge=0.0)
    latency_budget_ms: float = Field(ge=0.0)
    latency_budget_exceeded: bool
    latency_disclosure: str = Field(min_length=1)
    dropped_with_reason: tuple[DroppedItem, ...] = ()


class EvidenceGroup(BaseModel):
    """One compact evidence group inside a memory-type context block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str
    candidate_ids: tuple[str, ...]
    text: str
    token_count: int = Field(ge=0)


class ContextBlock(BaseModel):
    """A complete, never-character-truncated block for one memory type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_type: str
    text: str
    token_count: int = Field(ge=0)
    candidate_ids: tuple[str, ...]
    evidence_groups: tuple[EvidenceGroup, ...]


class ComposedContext(BaseModel):
    """Budgeted context grouped by memory type and evidence."""

    model_config = ConfigDict(extra="forbid")

    blocks: tuple[ContextBlock, ...] = ()
    per_type_budget: dict[str, int] = Field(default_factory=dict)
    per_type_token_spend: dict[str, int] = Field(default_factory=dict)
    total_tokens: int = Field(default=0, ge=0)
    budget: int = Field(ge=0)
    dropped_with_reason: tuple[DroppedItem, ...] = ()

    def render(self) -> str:
        """Render only complete blocks; no hidden partial block is emitted."""
        return "\n\n".join(block.text for block in self.blocks)


class GraphBatch(BaseModel):
    """Graph-provider response traversed under stage-owned depth/cycle caps."""

    model_config = ConfigDict(extra="forbid")

    seed_ids: tuple[str, ...] = ()
    adjacency: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    candidates: dict[str, Candidate] = Field(default_factory=dict)


class ProvenanceWriteResult(BaseModel):
    """Honest result of an append-only provenance write attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    written: bool
    path: str | None = None
    reason: str


__all__ = [
    "Candidate",
    "ComposedContext",
    "ContextBlock",
    "DroppedItem",
    "EvidenceGroup",
    "FusionResult",
    "FusedCandidate",
    "GraphBatch",
    "ProvenanceWriteResult",
    "RetrievalMode",
    "StageResult",
    "StageStatus",
    "TemporalOperation",
]
