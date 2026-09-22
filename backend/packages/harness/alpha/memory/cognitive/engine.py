"""Cognitive Memory System: Master Orchestrator.

Integrates all 6 cognitive tiers (Working, Episodic Flat/Hierarchical, Semantic Graph,
Procedural Skills, Spatio-Temporal Events, Cross-Session Associative Network),
Sleep/Dream Consolidation, and Hybrid Retrieval into a unified, durable engine.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import RLock
from typing import Any

from alpha.config.paths import get_paths
from alpha.memory.cognitive.associative_memory import AssociativeNetwork
from alpha.memory.cognitive.consolidation import CognitiveConsolidationEngine
from alpha.memory.cognitive.episodic_memory import EpisodicMemoryEngine
from alpha.memory.cognitive.models import (
    BeliefStatus,
    ConsolidationReport,
    HybridRecallQuery,
    ScoredMemoryItem,
    TraceOutcome,
)
from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory
from alpha.memory.cognitive.retrieval import HybridCognitiveRetriever
from alpha.memory.cognitive.semantic_graph import SemanticBeliefGraph
from alpha.memory.cognitive.spatio_temporal import SpatioTemporalMemory
from alpha.memory.cognitive.working_memory import WorkingMemoryEngine
from alpha.runtime.user_context import require_current_user

logger = logging.getLogger(__name__)


class CognitiveMemorySystem:
    """Master production-grade cognitive memory system for frontier autonomous agents."""

    def __init__(self, storage_dir: Path | None = None, *, user_id: str | None = None) -> None:
        if storage_dir is not None and user_id is not None:
            raise ValueError("Pass storage_dir or user_id, not both")
        self.storage_dir = Path(storage_dir).resolve() if storage_dir is not None else _owner_storage_dir(user_id)
        self.lock = RLock()
        self.working_mem = WorkingMemoryEngine()
        self.episodic_mem = EpisodicMemoryEngine()
        self.semantic_graph = SemanticBeliefGraph()
        self.procedural_mem = ProceduralSkillMemory()
        self.spatio_temporal = SpatioTemporalMemory()
        self.assoc_net = AssociativeNetwork()
        self.consolidation = CognitiveConsolidationEngine()
        self.retriever = HybridCognitiveRetriever()

        # Load persisted state or bootstrap defaults
        self._load_or_bootstrap()

    def recall(self, query: HybridRecallQuery) -> list[ScoredMemoryItem]:
        """Hybrid multi-tier recall combining BM25, vector similarity, graph traversal, and temporal decay."""
        return self.retriever.recall(
            query=query,
            working_mem=self.working_mem,
            episodic_mem=self.episodic_mem,
            semantic_graph=self.semantic_graph,
            procedural_mem=self.procedural_mem,
            spatio_temporal=self.spatio_temporal,
            assoc_net=self.assoc_net,
        )

    def consolidate(self) -> ConsolidationReport:
        """Run 3-phase Sleep/Dream memory consolidation cycle."""
        report = self.consolidation.run_consolidation_cycle(
            working_mem=self.working_mem,
            episodic_mem=self.episodic_mem,
            semantic_graph=self.semantic_graph,
            procedural_mem=self.procedural_mem,
            assoc_net=self.assoc_net,
        )
        self.save_to_disk()
        return report

    def overview(self) -> dict[str, Any]:
        """Comprehensive cognitive memory overview across all tiers."""
        graph_stats = self.semantic_graph.density_metrics()
        latest_report = self.consolidation.get_latest_report()

        return {
            "tiers": {
                "working_memory": {
                    "count": len(self.working_mem.list_active(min_attention=0.0)),
                    "active_high_attention": len(self.working_mem.list_active(min_attention=0.5)),
                },
                "episodic_memory": {
                    "flat_traces_count": len(self.episodic_mem.list_traces(limit=1000)),
                    "hierarchical_episodes_count": len(self.episodic_mem.list_episodes(limit=200)),
                },
                "semantic_graph": graph_stats,
                "procedural_memory": {
                    "skills_count": len(self.procedural_mem.list_skills(limit=500)),
                },
                "spatio_temporal": {
                    "events_count": len(self.spatio_temporal.list_events(limit=500)),
                },
                "associative_network": {
                    "links_count": len(self.assoc_net._links),
                },
            },
            "consolidation": {
                "latest_report": latest_report.to_dict() if latest_report else None,
                "total_cycles": len(self.consolidation._reports),
            },
        }

    def save_to_disk(self) -> None:
        """Persist state cleanly to storage directory."""
        with self.lock:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "version": "2.1",
                "semantic_nodes": [n.to_dict() for n in self.semantic_graph.list_nodes(limit=2000)],
                "semantic_edges": [e.to_dict() for e in self.semantic_graph._edges.values()],
                "procedural_skills": [s.to_dict() for s in self.procedural_mem._skills.values()],
                "spatio_temporal_events": [e.to_dict() for e in self.spatio_temporal._events.values()],
                "episodic_traces": [t.to_dict() for t in self.episodic_mem._traces.values()],
                "hierarchical_episodes": [ep.to_dict() for ep in self.episodic_mem._episodes.values()],
                "associative_links": [link.to_dict() for link in self.assoc_net._links.values()],
            }
            payload = json.dumps(snapshot, indent=2, allow_nan=False)
            target_path = self.storage_dir / "cognitive_state.json"
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.storage_dir, prefix=".cognitive-", suffix=".tmp", delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(payload)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_path, target_path)
            finally:
                if temporary_path is not None:
                    # Cleanup must never replace the real failure. On Windows an
                    # open handle makes ``unlink`` raise PermissionError; raised
                    # from a ``finally`` block it would *suppress* the original
                    # write/replace error and send the caller chasing the wrong
                    # cause. Mirrors the guarded cleanup in ``skills/proposals.py``.
                    with suppress(OSError):
                        temporary_path.unlink(missing_ok=True)

    @contextmanager
    def operation(self):
        with self.lock:
            state = copy.deepcopy({key: value for key, value in vars(self).items() if key not in {"lock", "storage_dir"}})
            try:
                yield self
            except BaseException:
                for key, value in state.items():
                    setattr(self, key, value)
                raise

    def _load_or_bootstrap(self) -> None:
        """Load persisted state or bootstrap with frontier defaults."""
        target_path = self.storage_dir / "cognitive_state.json"
        if target_path.exists():
            try:
                data = json.loads(target_path.read_text(encoding="utf-8"))
                for n_dict in data.get("semantic_nodes", []):
                    self.semantic_graph.add_belief(
                        subject=n_dict.get("subject", ""),
                        predicate=n_dict.get("predicate", ""),
                        object_val=n_dict.get("object_val", ""),
                        confidence=float(n_dict.get("confidence", 0.8)),
                        status=n_dict.get("status", BeliefStatus.ACTIVE.value),
                        evidence=n_dict.get("evidence", []),
                        valid_from=n_dict.get("valid_from"),
                        valid_to=n_dict.get("valid_to"),
                        salience=float(n_dict.get("salience", 0.7)),
                        tags=n_dict.get("tags", []),
                        node_id=n_dict.get("node_id"),
                        revision=int(n_dict.get("revision", 1)),
                        access_count=int(n_dict.get("access_count", 1)),
                        last_accessed_at=n_dict.get("last_accessed_at"),
                        created_at=n_dict.get("created_at"),
                        superseded_by=n_dict.get("superseded_by"),
                    )
                for e_dict in data.get("semantic_edges", []):
                    self.semantic_graph.add_edge(
                        source_id=e_dict.get("source_id", ""),
                        target_id=e_dict.get("target_id", ""),
                        relation=e_dict.get("relation", "relates_to"),
                        weight=float(e_dict.get("weight", 1.0)),
                        edge_id=e_dict.get("edge_id"),
                        metadata=e_dict.get("metadata", {}),
                    )
                for s_dict in data.get("procedural_skills", []):
                    self.procedural_mem.register_skill(
                        name=s_dict.get("name", ""),
                        description=s_dict.get("description", ""),
                        trigger_pattern=s_dict.get("trigger_pattern", ""),
                        preconditions=s_dict.get("preconditions", []),
                        steps=s_dict.get("steps", []),
                        code_snippet=s_dict.get("code_snippet", ""),
                        postconditions=s_dict.get("postconditions", []),
                        skill_id=s_dict.get("skill_id"),
                        success_count=int(s_dict.get("success_count", 0)),
                        failure_count=int(s_dict.get("failure_count", 0)),
                        last_executed_at=float(s_dict.get("last_executed_at", 0.0)),
                        failure_reasons=s_dict.get("failure_reasons", []),
                        created_at=s_dict.get("created_at"),
                    )
                for e_dict in data.get("spatio_temporal_events", []):
                    self.spatio_temporal.record_event(
                        title=e_dict.get("title", ""),
                        description=e_dict.get("description", ""),
                        timestamp=e_dict.get("timestamp"),
                        valid_from=e_dict.get("valid_from"),
                        valid_to=e_dict.get("valid_to"),
                        environment=e_dict.get("environment", "local"),
                        location=e_dict.get("location", "workspace"),
                        host=e_dict.get("host", "localhost"),
                        entities=e_dict.get("entities", []),
                        event_id=e_dict.get("event_id"),
                    )
                for t_dict in data.get("episodic_traces", []):
                    self.episodic_mem.record_trace(
                        action=t_dict.get("action", ""),
                        observation=t_dict.get("observation", ""),
                        outcome=t_dict.get("outcome", TraceOutcome.UNKNOWN.value),
                        session_id=t_dict.get("session_id", "default"),
                        step_index=int(t_dict.get("step_index", 0)),
                        error_context=t_dict.get("error_context"),
                        tokens=int(t_dict.get("tokens", 0)),
                        salience=float(t_dict.get("salience", 0.5)),
                        parent_episode_id=t_dict.get("parent_episode_id"),
                        tags=t_dict.get("tags", []),
                        trace_id=t_dict.get("trace_id"),
                        timestamp=t_dict.get("timestamp"),
                    )
                for ep_dict in data.get("hierarchical_episodes", []):
                    self.episodic_mem.create_hierarchical_episode(
                        title=ep_dict.get("title", ""),
                        summary=ep_dict.get("summary", ""),
                        session_id=ep_dict.get("session_id", "default"),
                        trace_ids=ep_dict.get("traces", []),
                        key_learnings=ep_dict.get("key_learnings", []),
                        importance=float(ep_dict.get("importance", 0.7)),
                        episode_id=ep_dict.get("episode_id"),
                        start_time=ep_dict.get("start_time"),
                        end_time=ep_dict.get("end_time"),
                    )
                for l_dict in data.get("associative_links", []):
                    self.assoc_net.link_memories(
                        source_tier=l_dict.get("source_tier", "working"),
                        source_id=l_dict.get("source_id", ""),
                        target_tier=l_dict.get("target_tier", "working"),
                        target_id=l_dict.get("target_id", ""),
                        initial_weight=float(l_dict.get("weight", 0.5)),
                        link_id=l_dict.get("link_id"),
                        co_occurrences=int(l_dict.get("co_occurrences", 1)),
                        last_reinforced_at=l_dict.get("last_reinforced_at"),
                    )
                logger.info("Loaded persisted cognitive memory from %s", target_path)
                return
            except Exception:
                logger.error("Failed to load cognitive state")
                raise

        # Bootstrap Frontier Cognitive Knowledge
        self._bootstrap_defaults()

    def _bootstrap_defaults(self) -> None:
        """Seed foundational architectural beliefs, rules, and procedural skills."""
        # Foundational Semantic Beliefs
        b1 = self.semantic_graph.add_belief(
            subject="AgentArchitecture",
            predicate="operates_on",
            object_val="MultiTierCognitiveMemory",
            confidence=0.98,
            tags=["architecture", "pinned"],
        )
        b2 = self.semantic_graph.add_belief(
            subject="MemoryRetrieval",
            predicate="uses_hybrid_fusion",
            object_val="BM25_Vector_Graph_TemporalDecay",
            confidence=0.95,
            tags=["retrieval", "pinned"],
        )
        b3 = self.semantic_graph.add_belief(
            subject="CognitiveConsolidation",
            predicate="implements_phases",
            object_val="LightSleep_REM_DeepSleep",
            confidence=0.96,
            tags=["consolidation", "dreaming"],
        )
        b4 = self.semantic_graph.add_belief(
            subject="EpistemicBeliefs",
            predicate="enforces_consistency",
            object_val="ConflictDetectionAndBayesianRecencySupersession",
            confidence=0.92,
            tags=["epistemics"],
        )

        # Connect beliefs in graph
        self.semantic_graph.add_edge(b1.node_id, b2.node_id, relation="enables", weight=1.0)
        self.semantic_graph.add_edge(b1.node_id, b3.node_id, relation="regulates", weight=1.0)
        self.semantic_graph.add_edge(b3.node_id, b4.node_id, relation="crystallizes", weight=1.0)

        # Foundational Procedural Skills
        sk1 = self.procedural_mem.register_skill(
            name="verify_code_with_targeted_pytest",
            description="Run fast, targeted unit tests before committing changes to avoid regression",
            trigger_pattern=r"(test|pytest|verify|unit test)",
            preconditions=["Identify specific modified module or router", "Ensure virtual environment is active"],
            steps=[
                "Run pytest on specific test file (e.g. pytest tests/test_memory_router.py)",
                "Inspect failure traceback if any",
                "Verify zero warnings or assertion failures",
            ],
            code_snippet="uv run pytest tests/test_target.py -v",
            postconditions=["Exit code 0 and all tests passed"],
        )
        sk1.success_count = 5

        sk2 = self.procedural_mem.register_skill(
            name="reconcile_contradictory_user_preferences",
            description="Detect and gracefully resolve conflicting user instructions or facts",
            trigger_pattern=r"(conflict|contradict|supersede|preference)",
            preconditions=["Identify conflicting belief triples with matching subject and predicate"],
            steps=[
                "Compare observation timestamps and confidence scores",
                "Mark older or lower-confidence triple as SUPERSEDED with link to new winner",
                "If ambiguous, escalate to CONTESTED status and notify user in next response",
            ],
            postconditions=["No unmanaged contradictory beliefs active simultaneously"],
        )
        sk2.success_count = 3

        # Foundational Spatio-Temporal Event
        self.spatio_temporal.record_event(
            title="Cognitive Memory Architecture Activation",
            description="Multi-tier cognitive memory system initialized with working, episodic, semantic, procedural, spatio-temporal, and associative tiers.",
            environment="production",
            location="agent-workspace-core",
            entities=["AgentArchitecture", "MemoryConsolidation", "HybridRetriever"],
        )


_MAX_OWNER_SYSTEMS = 128
_owner_systems: dict[tuple[str, Path], CognitiveMemorySystem] = {}
_owner_systems_lock = RLock()


def _evict_lru_owner_system() -> None:
    for key, system in _owner_systems.items():
        if system.lock._is_owned() or not system.lock.acquire(blocking=False):
            continue
        try:
            del _owner_systems[key]
            return
        finally:
            system.lock.release()
    raise RuntimeError("Cognitive memory owner cache is busy")


def _owner_storage_dir(user_id: str | None) -> Path:
    owner = str(require_current_user().id) if user_id is None else user_id
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("Cognitive memory requires an owner")
    return (get_paths().user_dir(owner) / "cognitive_memory").absolute()


def get_cognitive_memory_system(storage_dir: Path | None = None, *, user_id: str | None = None) -> CognitiveMemorySystem:
    """Return an owner-scoped process cache or an independent explicit-path instance."""
    if storage_dir is not None:
        return CognitiveMemorySystem(storage_dir=storage_dir, user_id=user_id)
    owner = str(require_current_user().id) if user_id is None else user_id
    directory = _owner_storage_dir(owner)
    key = (owner, directory)
    with _owner_systems_lock:
        if key in _owner_systems:
            system = _owner_systems.pop(key)
        else:
            while len(_owner_systems) >= _MAX_OWNER_SYSTEMS:
                _evict_lru_owner_system()
            system = CognitiveMemorySystem(storage_dir=directory)
        _owner_systems[key] = system
        return system
