"""GRPO-inspired Group-Relative Orchestration (GRO).

One objective, a bounded group of candidates executed under *distinct*
strategies, then selection by **hard gates first, absolute score second,
relative rank third**. The contract for the package — the honesty rules, the
state machine, the tool surface, the config section — lives in
``alpha/gro/AGENTS.md``.

Submodule imports are cheap (stdlib + pydantic only); the subagent executor
and acceptance checker are imported lazily inside the functions that use them.
"""

from alpha.gro.config import GroConfig
from alpha.gro.evaluation import evaluate_candidate, rank_group, select_candidate
from alpha.gro.models import (
    CandidateEvaluation,
    CandidateOutcome,
    CandidateSpec,
    CandidateStatus,
    GateResult,
    GateStatus,
    GroBudget,
    GroRun,
    GroRunState,
    RiskTier,
    RunMode,
    SelectionResult,
    StrategySpec,
    TaskContract,
    TaskType,
)
from alpha.gro.policy import GroDecision, classify_risk_tier, classify_task_type, decide_mode

__all__ = [
    "CandidateEvaluation",
    "CandidateOutcome",
    "CandidateSpec",
    "CandidateStatus",
    "GateResult",
    "GateStatus",
    "GroBudget",
    "GroConfig",
    "GroDecision",
    "GroRun",
    "GroRunState",
    "RiskTier",
    "RunMode",
    "SelectionResult",
    "StrategySpec",
    "TaskContract",
    "TaskType",
    "classify_risk_tier",
    "classify_task_type",
    "decide_mode",
    "evaluate_candidate",
    "rank_group",
    "select_candidate",
]
