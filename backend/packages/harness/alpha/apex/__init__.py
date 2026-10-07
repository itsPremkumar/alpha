"""APEX — the executive control plane.

See ``README.md`` in this package for the doctrine and the "what this does not
own" table. The short version: APEX holds an autonomy contract and runs a
bounded executive cycle over engines that already exist; it is not an execution
path and not a second policy kernel.

Specification: ``docs/ALPHA_APEX_AUTOPILOT_MASTER_SPEC.md``.
Integration rationale: ``docs/APEX_INTEGRATION_MAP.md``.
Operations: ``docs/APEX_AUTOPILOT.md``.
"""

from __future__ import annotations

from alpha.apex.agents import (
    ApexAgentFactory,
    ApexAgentRole,
    ApexAgentSpec,
    assess_specialization,
    resolve_delegation_limits,
)
from alpha.apex.contract import (
    ApexBudget,
    ApexControls,
    AutonomyContract,
    AutonomyProfile,
    ContractViolation,
    default_contract,
    narrow_contract,
    profile_for,
)
from alpha.apex.executive import (
    ExecutiveDecision,
    ExecutiveResult,
    NextAction,
    run_cycle,
    select_next_action,
)
from alpha.apex.goals import (
    ApexGoal,
    ApexGoalStore,
    GoalEvidence,
    GoalState,
    get_goal_store,
)
from alpha.apex.invariants import (
    INVARIANTS,
    InvariantCheck,
    InvariantReport,
    check_invariants,
    invariant_summary,
)
from alpha.apex.status import apex_status, contract_status, fleet_status
from alpha.apex.store import ApexSession, ApexSessionState, ApexStore, get_apex_store
from alpha.apex.strategy import (
    AttemptRecord,
    OrchestrationStrategy,
    StuckVerdict,
    TaskShape,
    choose_orchestration,
    classify_stuck,
    recommended_swarm_size,
    shape_from_goal,
)

__all__ = [
    "INVARIANTS",
    "ApexAgentFactory",
    "ApexAgentRole",
    "ApexAgentSpec",
    "ApexBudget",
    "ApexControls",
    "ApexGoal",
    "ApexGoalStore",
    "ApexSession",
    "ApexSessionState",
    "ApexStore",
    "AttemptRecord",
    "AutonomyContract",
    "AutonomyProfile",
    "ContractViolation",
    "ExecutiveDecision",
    "ExecutiveResult",
    "GoalEvidence",
    "GoalState",
    "InvariantCheck",
    "InvariantReport",
    "NextAction",
    "OrchestrationStrategy",
    "StuckVerdict",
    "TaskShape",
    "apex_status",
    "assess_specialization",
    "check_invariants",
    "classify_stuck",
    "choose_orchestration",
    "contract_status",
    "default_contract",
    "fleet_status",
    "get_apex_store",
    "get_goal_store",
    "invariant_summary",
    "narrow_contract",
    "profile_for",
    "recommended_swarm_size",
    "resolve_delegation_limits",
    "run_cycle",
    "select_next_action",
    "shape_from_goal",
]
