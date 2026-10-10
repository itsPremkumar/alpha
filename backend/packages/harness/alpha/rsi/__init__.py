"""Recursive Self-Improvement (RSI) package."""

# RRSI (Regularized Recursive Self-Improvement) is a subpackage of this one;
# re-exporting it here keeps `alpha.rsi.rrsi` reachable from the package root
# and satisfies the orphan-module gate without every caller spelling the
# dotted path. The normative contract for it lives in `rrsi/AGENTS.md`.
from alpha.rsi import rrsi as rrsi
from alpha.rsi.archive import archive_candidate
from alpha.rsi.engine import RSIEngine, get_rsi_engine
from alpha.rsi.models import (
    ABTestResult,
    HoldoutResult,
    RSICandidate,
    RSIHypothesis,
    RSIResult,
    RSIStage,
)
from alpha.rsi.opportunity import OPPORTUNITY_CATEGORIES, Opportunity
from alpha.rsi.rrsi import (
    RrsiParams,
    RrsiRoundStore,
    build_proposal_plan,
    election_gate_for,
    select_round,
)
from alpha.rsi.state import (
    RsiCycleState,
    load_cycle_state,
    load_state,
    new_state,
    resume,
    save_cycle_state,
    save_state,
)
from alpha.rsi.strategy_memory import StrategyMemory, get_strategy_memory

__all__ = [
    "RSIStage",
    "RSIHypothesis",
    "RSICandidate",
    "ABTestResult",
    "HoldoutResult",
    "RSIResult",
    "RSIEngine",
    "get_rsi_engine",
    "archive_candidate",
    "OPPORTUNITY_CATEGORIES",
    "Opportunity",
    "RsiCycleState",
    "load_cycle_state",
    "save_cycle_state",
    "load_state",
    "save_state",
    "new_state",
    "resume",
    "StrategyMemory",
    "get_strategy_memory",
    "rrsi",
    "RrsiParams",
    "RrsiRoundStore",
    "build_proposal_plan",
    "election_gate_for",
    "select_round",
]
