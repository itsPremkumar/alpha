"""Data models for Multi-Tier Cognitive Memory Architecture.

Defines schemas for all cognitive memory tiers, graph topologies,
epistemic belief states, procedural skills, spatio-temporal events,
and hybrid retrieval results.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


class CognitiveTier(enum.StrEnum):
    """Cognitive memory tier categorization."""

    WORKING = "working"
    EPISODIC_FLAT = "episodic_flat"
    EPISODIC_HIERARCHICAL = "episodic_hierarchical"
    SEMANTIC_FACT = "semantic_fact"
    PROCEDURAL_SKILL = "procedural_skill"
    SPATIO_TEMPORAL = "spatio_temporal"
    ASSOCIATIVE = "associative"


class BeliefStatus(enum.StrEnum):
    """Status of an epistemic belief or semantic fact."""

    ACTIVE = "active"
    CONTESTED = "contested"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"


class TraceOutcome(enum.StrEnum):
    """Outcome classification for episodic traces."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass
class WorkingMemoryItem:
    """Short-term scratchpad item for in-flight tasks and current focus."""

    content: str
    context_tag: str = "scratch"  # goal, hypothesis, scratch, focus, observation
    attention_score: float = 1.0  # Decays quickly without refresh
    salience: float = 0.5
    item_id: str = field(default_factory=lambda: f"wm_{uuid.uuid4().hex[:8]}")
    task_id: str = "default"
    created_at: float = field(default_factory=time.time)
    last_decayed_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EpisodicTrace:
    """A discrete episodic event or agent turn trace."""

    action: str
    observation: str
    outcome: TraceOutcome = TraceOutcome.UNKNOWN
    session_id: str = "default"
    step_index: int = 0
    error_context: str | None = None
    tokens: int = 0
    salience: float = 0.5
    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex[:8]}")
    parent_episode_id: str | None = None
    timestamp: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d


@dataclass
class HierarchicalEpisode:
    """Higher-level abstracted episode aggregating multiple traces."""

    title: str
    summary: str
    session_id: str = "default"
    traces: list[str] = field(default_factory=list)  # list of trace_ids
    key_learnings: list[str] = field(default_factory=list)
    importance: float = 0.7
    start_time: float = field(default_factory=time.time)
    end_time: float = field(default_factory=time.time)
    episode_id: str = field(default_factory=lambda: f"ep_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticFactNode:
    """Epistemic belief node in the knowledge graph."""

    subject: str
    predicate: str
    object_val: str
    confidence: float = 0.8
    status: BeliefStatus = BeliefStatus.ACTIVE
    evidence: list[str] = field(default_factory=list)
    valid_from: float = field(default_factory=time.time)
    valid_to: float | None = None
    revision: int = 1
    access_count: int = 0
    last_accessed_at: float = field(default_factory=time.time)
    salience: float = 0.7
    created_at: float = field(default_factory=time.time)
    superseded_by: str | None = None
    tags: list[str] = field(default_factory=list)
    # Additive provenance disclosure: where this belief came from, e.g.
    # "bootstrap-assumption" for unverified startup seeds, "user" for
    # user-asserted facts, or an evidence id. None = unstated provenance.
    source: str | None = None
    node_id: str = field(default_factory=lambda: f"fact_{uuid.uuid4().hex[:8]}")

    @property
    def statement(self) -> str:
        return f"{self.subject} {self.predicate} {self.object_val}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["statement"] = self.statement
        return d


@dataclass
class SemanticRelationEdge:
    """Topological edge connecting belief concepts or entities."""

    source_id: str
    target_id: str
    relation: str = "relates_to"  # contradicts, supports, implies, causes, relates_to
    weight: float = 1.0
    edge_id: str = field(default_factory=lambda: f"edge_{uuid.uuid4().hex[:8]}")
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProceduralSkill:
    """A durable learned playbook, recipe, or reusable workflow.

    ``lifecycle`` records where the *operator or the consolidation cycle* left
    the skill; :class:`alpha.memory.cognitive.skill_lifecycle.SkillVerdict` is
    the independent read of what the measurements support, and the two are
    allowed to disagree on purpose.

    ``success_rate`` is a display convenience and **reports 1.0 when nothing has
    ever been measured** (``total > 0`` guard, else ``1.0``). It is kept only so
    existing callers and persisted snapshots keep their meaning; it must not be
    used for ranking, promotion, or eviction, because "no evidence" and "100%
    success" are different claims. Use :attr:`effectiveness` (``None`` when
    unproven) or :attr:`smoothed_effectiveness` (the ranking number).
    """

    name: str
    description: str
    trigger_pattern: str
    preconditions: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    code_snippet: str = ""
    postconditions: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    last_executed_at: float = 0.0
    failure_reasons: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    skill_id: str = field(default_factory=lambda: f"skill_{uuid.uuid4().hex[:8]}")
    lifecycle: str = "proposed"
    lifecycle_reason: str = ""

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return (self.success_count / total) if total > 0 else 1.0

    @property
    def evidence_count(self) -> int:
        """How many times this skill has actually been run, either way.

        Derived from the outcome counters rather than stored, so a snapshot
        restored from an older file cannot drift out of step with its own counts,
        and there is no migration to perform.
        """
        return self.success_count + self.failure_count

    @property
    def effectiveness(self) -> float | None:
        """Measured success ratio, or ``None`` when nothing was ever measured.

        ``None`` is the honest answer for an unproven skill: substituting ``0``
        reads as "measured and always fails" and substituting ``1.0`` (which
        ``success_rate`` does) reads as "measured and always wins". Both would
        rank an unproven skill against tested ones.
        """
        total = self.evidence_count
        if total == 0:
            return None
        return self.success_count / total

    @property
    def smoothed_effectiveness(self) -> float:
        """Laplace-smoothed ratio — the number used for *ranking and eviction*.

        The prior (1 success in 2 uses) fixes the unproven point at the middle of
        the distribution, so a never-run skill neither leads a well-tested one
        nor ranks below a consistently failing one.
        """
        return (self.success_count + 1) / (self.evidence_count + 2)

    @property
    def strength(self) -> float:
        """Alias of :attr:`smoothed_effectiveness`, named for its use in scoring."""
        return self.smoothed_effectiveness

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["success_rate"] = round(self.success_rate, 3)
        # The measured facts travel beside the display convenience, so a consumer
        # can always tell an absent measurement from a perfect one.
        d["evidence_count"] = self.evidence_count
        d["effectiveness"] = None if self.effectiveness is None else round(self.effectiveness, 3)
        d["smoothed_effectiveness"] = round(self.smoothed_effectiveness, 3)
        d["evidence_basis"] = "measured" if self.evidence_count > 0 else "unproven"
        return d


@dataclass
class SpatioTemporalEvent:
    """Contextually anchored event with temporal bounds and environment location."""

    title: str
    description: str
    timestamp: float = field(default_factory=time.time)
    valid_from: float = field(default_factory=time.time)
    valid_to: float | None = None
    environment: str = "local"  # local, docker, dev, prod
    location: str = "workspace"
    host: str = "localhost"
    entities: list[str] = field(default_factory=list)
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AssociativeLink:
    """Hebbian cross-tier memory link connecting related experiences."""

    source_tier: CognitiveTier
    source_id: str
    target_tier: CognitiveTier
    target_id: str
    weight: float = 0.5
    co_occurrences: int = 1
    last_reinforced_at: float = field(default_factory=time.time)
    link_id: str = field(default_factory=lambda: f"assoc_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_tier"] = self.source_tier.value
        d["target_tier"] = self.target_tier.value
        return d


@dataclass
class ScoredMemoryItem:
    """Scored result from hybrid retrieval combining all signal dimensions."""

    tier: CognitiveTier
    item_id: str
    title: str
    snippet: str
    composite_score: float
    bm25_score: float = 0.0
    vector_score: float = 0.0
    graph_score: float = 0.0
    temporal_score: float = 0.0
    salience_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


@dataclass
class HybridRecallQuery:
    """Query parameters for context-aware hybrid retrieval."""

    query: str
    limit: int = 10
    bm25_weight: float = 0.35
    vector_weight: float = 0.35
    temporal_weight: float = 0.15
    graph_weight: float = 0.15
    min_score: float = 0.05
    tier_filter: list[str] | None = None
    as_of_timestamp: float | None = None


@dataclass
class ConsolidationReport:
    """Outcome report for a 3-phase Sleep/Dream consolidation run."""

    cycle_id: str
    timestamp: float
    light_sleep_pruned: int
    rem_sleep_patterns_discovered: int
    deep_sleep_beliefs_crystallized: int
    conflicts_reconciled: int
    skills_indexed: int
    decayed_items_count: int
    insights: list[dict[str, Any]]
    summary: str
    # Reconsolidation fields, all defaulted so every existing construction of
    # this report stays valid. `reconsolidation_records` counts retrievals folded
    # back into the store during the pass; `replayed_traces`/`replayed_budget`
    # report how many successful episodes were strengthened and the cap that
    # bound the pass; `tier_disclosure` carries the per-tier counts where a
    # measured number exists and `None` where it does not.
    reconsolidation_records: int = 0
    replayed_traces: int = 0
    replayed_budget: int = 0
    tier_disclosure: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
