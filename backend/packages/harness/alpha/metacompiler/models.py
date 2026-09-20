"""Data models for Recursive Agent Self-Replication and Meta-Compiler."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ReasoningStrategy(str, Enum):
    ZERO_SHOT_DIRECT = "zero_shot_direct"
    ITERATIVE_REFLEXION = "iterative_reflexion"
    MCTS_DELIBERATION = "mcts_deliberation"
    EPISTEMIC_FALSIFICATION = "epistemic_falsification"
    HIERARCHICAL_DECOMPOSITION = "hierarchical_decomposition"
    DUAL_PROCESS_DELIBERATION = "dual_process_deliberation"


class MemoryLayout(str, Enum):
    FLAT_EPISODIC = "flat_episodic"
    HYBRID_GRAPH_VECTOR = "hybrid_graph_vector"
    HIERARCHICAL_SUMMARY = "hierarchical_summary"
    CONSOLIDATED_SEMANTIC = "consolidated_semantic"


@dataclass
class AgentBlueprint:
    """Complete architectural blueprint definition of a synthesized/evolved agent."""

    blueprint_id: str = field(default_factory=lambda: f"bp_{uuid.uuid4().hex[:8]}")
    generation: int = 0
    parent_id: str | None = None
    name: str = "Alpha-ASI-Seed"
    architecture_tag: str = "Alpha-ASI-v1"
    system_prompt_template: str = (
        "You are an autonomous ASI agent. Observe environment state, form hypotheses, "
        "verify evidence via epistemic belief graph, execute minimal high-leverage actions, "
        "and continuously review outcomes for closed-loop self-improvement."
    )
    reasoning_strategy: ReasoningStrategy = ReasoningStrategy.ITERATIVE_REFLEXION
    memory_layout: MemoryLayout = MemoryLayout.HYBRID_GRAPH_VECTOR
    tool_bindings: list[str] = field(default_factory=lambda: [
        "code_executor",
        "filesystem_editor",
        "ripgrep_search",
        "git_worktree",
        "epistemic_belief_graph",
        "rsi_engine",
    ])
    reflection_frequency: int = 2
    stagnation_recovery_policy: str = "keel_perturbation_backtrack"
    model_tier: str = "reasoning_frontier"
    hyperparameters: dict[str, Any] = field(default_factory=lambda: {
        "temperature": 0.2,
        "top_p": 0.95,
        "max_reasoning_tokens": 8192,
        "backtrack_threshold": 2,
    })
    specialization: str = "universal"
    mutation_notes: str = "Baseline seed generation"
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reasoning_strategy"] = self.reasoning_strategy.value
        d["memory_layout"] = self.memory_layout.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentBlueprint:
        d = dict(data)
        if "reasoning_strategy" in d and isinstance(d["reasoning_strategy"], str):
            d["reasoning_strategy"] = ReasoningStrategy(d["reasoning_strategy"])
        if "memory_layout" in d and isinstance(d["memory_layout"], str):
            d["memory_layout"] = MemoryLayout(d["memory_layout"])
        return cls(**d)


@dataclass
class BenchmarkScorecard:
    """Quantitative empirical evaluation of an agent candidate blueprint."""

    benchmark_id: str = field(default_factory=lambda: f"bm_{uuid.uuid4().hex[:8]}")
    blueprint_id: str = ""
    generation: int = 0
    coding_score: float = 0.85
    reasoning_score: float = 0.88
    tool_accuracy_score: float = 0.92
    token_efficiency_score: float = 0.80
    robustness_score: float = 0.90
    composite_score: float = 0.87
    passed_regression_suite: bool = True
    details: list[dict[str, Any]] = field(default_factory=list)
    evaluated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HotSwapOutcome:
    """Outcome of promoting and hot-swapping to a verified next-gen agent architecture."""

    success: bool
    previous_head_id: str
    new_head_id: str
    generation: int
    migrated_tasks: int
    telemetry: dict[str, Any] = field(default_factory=dict)
    promoted_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
