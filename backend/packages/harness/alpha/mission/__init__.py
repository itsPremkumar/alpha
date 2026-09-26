"""Mission Compiler, Goal Hierarchy, Durable Work Queue and State Machine package."""

from alpha.mission.acceptance import (
    AcceptanceNotSatisfied,
    AcceptanceRegistry,
    AcceptanceReport,
    CriterionResult,
    CriterionVerdict,
    assert_acceptance_passed,
    evaluate_acceptance,
    get_acceptance_registry,
    unevaluated_report,
)
from alpha.mission.compiler import MissionCompiler
from alpha.mission.hierarchy import (
    ActionNode,
    ExecutionStatus,
    GoalNode,
    HierarchyLevel,
    HierarchyNode,
    MissionHierarchyTree,
    MissionNode,
    SubtaskNode,
    TaskNode,
    ToolCallNode,
)
from alpha.mission.lifecycle import (
    GATED_TERMINAL_PHASE,
    MISSION_TRANSITIONS,
    TERMINAL_MISSION_PHASES,
    IllegalMissionTransition,
    MissionEvent,
    MissionEventFeed,
    MissionLifecycle,
    MissionPhase,
    can_transition,
    new_mission_id,
)
from alpha.mission.models import Mission, ProofObligation, RiskTier
from alpha.mission.state_machine import (
    InvalidStateTransitionError,
    StateTransitionRecord,
    TaskState,
    TaskStateMachine,
)
from alpha.mission.work_queue import (
    BudgetExceededError,
    CyclicDependencyError,
    DurableWorkQueue,
    ExecutionBudget,
    QueuedTask,
    TaskPriority,
)

__all__ = [
    "RiskTier",
    "ProofObligation",
    "Mission",
    "MissionCompiler",
    # Hierarchy
    "HierarchyLevel",
    "ExecutionStatus",
    "HierarchyNode",
    "GoalNode",
    "MissionNode",
    "TaskNode",
    "SubtaskNode",
    "ActionNode",
    "ToolCallNode",
    "MissionHierarchyTree",
    # State Machine
    "TaskState",
    "InvalidStateTransitionError",
    "StateTransitionRecord",
    "TaskStateMachine",
    # Work Queue & DAG Scheduler
    "TaskPriority",
    "CyclicDependencyError",
    "BudgetExceededError",
    "ExecutionBudget",
    "QueuedTask",
    "DurableWorkQueue",
    # Acceptance evaluation (no criterion is ever MET without measured evidence)
    "AcceptanceNotSatisfied",
    "AcceptanceRegistry",
    "AcceptanceReport",
    "CriterionResult",
    "CriterionVerdict",
    "assert_acceptance_passed",
    "evaluate_acceptance",
    "get_acceptance_registry",
    "unevaluated_report",
    # Mission lifecycle + live event feed
    "GATED_TERMINAL_PHASE",
    "MISSION_TRANSITIONS",
    "TERMINAL_MISSION_PHASES",
    "IllegalMissionTransition",
    "MissionEvent",
    "MissionEventFeed",
    "MissionLifecycle",
    "MissionPhase",
    "can_transition",
    "new_mission_id",
]
