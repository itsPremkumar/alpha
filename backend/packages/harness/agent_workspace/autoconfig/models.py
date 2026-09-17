"""Data models for Autonomous Self-Configuration Engine."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ComplexityLevel(str, Enum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    RESEARCH_FRONTIER = "research_frontier"


class OperatingMode(str, Enum):
    DIRECT = "direct"
    PLAN = "plan"
    CODING = "coding"
    RESEARCH = "research"
    SWARM = "swarm"
    AUTONOMOUS = "autonomous"
    RECOVER = "recover"
    COMPANY = "company"


class ModelTier(str, Enum):
    FAST_LOCAL = "fast_local"
    STANDARD = "standard"
    REASONING_FRONTIER = "reasoning_frontier"
    MULTI_MODEL_ENSEMBLE = "multi_model_ensemble"


class TopologyType(str, Enum):
    SOLO = "solo"
    HIERARCHICAL = "hierarchical"
    DEBATE_COUNCIL = "debate_council"
    PARALLEL_MESH = "parallel_mesh"


@dataclass
class GoalAnalysis:
    """Intelligent parsing and decomposition of user goal intent."""

    raw_goal: str
    intent: str
    domain: str
    complexity: ComplexityLevel
    risk_score: float  # 0.0 to 1.0
    estimated_turns: int
    suggested_mode: OperatingMode
    recommended_model_tier: ModelTier
    recommended_topology: TopologyType
    required_capabilities: list[str] = field(default_factory=list)
    tool_whitelist: list[str] = field(default_factory=list)
    reasoning_budget_tokens: int = 4096
    reflection_frequency: int = 2
    context_compaction_threshold: int = 45000
    ambiguities: list[str] = field(default_factory=list)
    mitigation_strategies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["complexity"] = self.complexity.value
        d["suggested_mode"] = self.suggested_mode.value
        d["recommended_model_tier"] = self.recommended_model_tier.value
        d["recommended_topology"] = self.recommended_topology.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalAnalysis:
        d = dict(data)
        if "complexity" in d and isinstance(d["complexity"], str):
            d["complexity"] = ComplexityLevel(d["complexity"])
        if "suggested_mode" in d and isinstance(d["suggested_mode"], str):
            d["suggested_mode"] = OperatingMode(d["suggested_mode"])
        if "recommended_model_tier" in d and isinstance(d["recommended_model_tier"], str):
            d["recommended_model_tier"] = ModelTier(d["recommended_model_tier"])
        if "recommended_topology" in d and isinstance(d["recommended_topology"], str):
            d["recommended_topology"] = TopologyType(d["recommended_topology"])
        return cls(**d)


@dataclass
class SelfConfigProfile:
    """Active runtime self-configuration profile for an autonomous agent/project."""

    profile_id: str = field(default_factory=lambda: f"cfg_{uuid.uuid4().hex[:8]}")
    project_id: str = "default"
    goal: str = ""
    operating_mode: OperatingMode = OperatingMode.AUTONOMOUS
    model_tier: ModelTier = ModelTier.REASONING_FRONTIER
    primary_model: str = "claude-3-7-sonnet-thinking"
    fallback_model: str = "gpt-4o"
    active_tools: list[str] = field(default_factory=lambda: [
        "code_executor",
        "filesystem_editor",
        "ripgrep_search",
        "web_research",
        "epistemic_belief_graph",
        "rsi_engine",
    ])
    reasoning_budget_tokens: int = 8192
    thought_depth: str = "deep"
    max_turns: int = 50
    context_compaction_threshold: int = 50000
    loop_detection_limit: int = 3
    topology: TopologyType = TopologyType.HIERARCHICAL
    swarm_roles: list[str] = field(default_factory=lambda: [
        "lead_architect",
        "implementation_engineer",
        "verification_critic",
    ])
    autonomous_pivots_enabled: bool = True
    guardrails: dict[str, Any] = field(default_factory=lambda: {
        "max_file_deletions": 2,
        "require_test_verification": True,
        "allow_network_search": True,
    })
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["operating_mode"] = self.operating_mode.value
        d["model_tier"] = self.model_tier.value
        d["topology"] = self.topology.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelfConfigProfile:
        d = dict(data)
        if "operating_mode" in d and isinstance(d["operating_mode"], str):
            d["operating_mode"] = OperatingMode(d["operating_mode"])
        if "model_tier" in d and isinstance(d["model_tier"], str):
            d["model_tier"] = ModelTier(d["model_tier"])
        if "topology" in d and isinstance(d["topology"], str):
            d["topology"] = TopologyType(d["topology"])
        return cls(**d)


@dataclass
class RuntimeTuningUpdate:
    """Dynamic parameters for in-flight self-configuration tuning."""

    reasoning_budget_tokens: int | None = None
    context_compaction_threshold: int | None = None
    loop_detection_limit: int | None = None
    primary_model: str | None = None
    operating_mode: str | None = None
    thought_depth: str | None = None
    extra_tools: list[str] | None = None
    disabled_tools: list[str] | None = None
