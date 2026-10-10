"""DAG & Dynamic Task Workflow Engine for Alpha.

Supports both static topological wave execution (mass-ulw / omo-dag)
and adaptive, durable Dynamic Workflow Engine (DWE) with runtime graph mutation,
dynamic routing, fan-out/fan-in, bounded loops, and human-in-the-loop approvals.
"""

from alpha.workflow.dag_engine import (
    DAGEngine,
    DAGNode,
    DAGWorkflow,
    NodeRunResult,
    WorkflowRunResult,
)
from alpha.workflow.dag_engine import (
    UnverifiedNodeCompletionError as LegacyUnverifiedNodeCompletionError,
)
from alpha.workflow.dag_engine import (
    WriteScopeCollisionError as LegacyWriteScopeCollisionError,
)
from alpha.workflow.dynamic_assembler import (
    AssembledResources,
    DynamicResourceAssembler,
    SwarmWorkgroup,
    get_dynamic_resource_assembler,
)
from alpha.workflow.dynamic_bridge import (
    DynamicExecutionResult,
    DynamicWorkflowBridge,
    get_dynamic_workflow_bridge,
)
from alpha.workflow.dynamic_decomposer import (
    CATEGORY_NODE_TYPES,
    DynamicDecomposer,
    DynamicGoal,
    DynamicTaskItem,
    SagaCompensation,
    get_dynamic_decomposer,
)

# Dynamic perceive→decompose→assemble→bridge layer (DY-R3: exports added once
# the four modules import cleanly against the real models API).
from alpha.workflow.dynamic_perception import (
    DynamicExecutionTier,
    DynamicIntentType,
    DynamicPerceptionEngine,
    PerceivedIntent,
    get_dynamic_perception_engine,
)
from alpha.workflow.events import (
    WorkflowEvent,
    WorkflowEventDispatcher,
    get_event_dispatcher,
)
from alpha.workflow.execution import (
    ConcurrencyGovernor,
    DeadlineResult,
    WaveOutcome,
    clamp_concurrency,
    execute_wave,
    run_with_deadline,
)
from alpha.workflow.expressions import (
    ExpressionSecurityError,
    SafeExpressionEvaluator,
    evaluate_condition,
)
from alpha.workflow.leases import (
    LeaseManager,
    WorkerLease,
)
from alpha.workflow.models import (
    EdgeMode,
    LoopPolicy,
    NodeStatus,
    NodeType,
    PatchOperation,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.observability import (
    NodeTiming,
    RunTimeline,
    build_run_observability,
    critical_path,
)
from alpha.workflow.patch import (
    WorkflowPatchEngine,
)
from alpha.workflow.patch_validator import (
    PatchValidationResult,
    PatchValidator,
)
from alpha.workflow.plan_graph import (
    PlanGraphError,
    PlanGraphStore,
    PlanVersionConflict,
)
from alpha.workflow.quarantine import (
    QuarantineRecord,
    QuarantineStatus,
    QuarantineStore,
    QuarantineTrigger,
)
from alpha.workflow.replanner import (
    RuntimeReplanner,
)
from alpha.workflow.router import (
    DynamicRouter,
    RouteDecision,
)
from alpha.workflow.runtime import (
    DynamicWorkflowEngine,
    DynamicWorkflowError,
    UnverifiedNodeCompletionError,
)
from alpha.workflow.scheduler import (
    WorkflowScheduler,
    WriteScopeCollisionError,
)
from alpha.workflow.schemas import (
    EdgeSpec,
    ExecutionEventRecord,
    NodeSpec,
    WorkflowDecisionRecord,
    WorkflowPlanPatch,
    WorkflowPlanVersion,
)
from alpha.workflow.time_travel import (
    ForkError,
    ForkResult,
    RunHistoryEntry,
    SimulationResult,
    fork_run,
    run_history,
    run_report,
    simulate_run,
)
from alpha.workflow.triggers import (
    TriggerFireResult,
    TriggerKind,
    TriggerStore,
    WorkflowTrigger,
    cron_next_after,
    fire_trigger_on_engine,
    parse_cron_expression,
)

__all__ = [
    # Legacy DAG engine
    "DAGNode",
    "DAGWorkflow",
    "DAGEngine",
    "NodeRunResult",
    "WorkflowRunResult",
    "LegacyUnverifiedNodeCompletionError",
    "LegacyWriteScopeCollisionError",
    # Dynamic Workflow Engine
    "DynamicWorkflowEngine",
    "DynamicWorkflowError",
    "UnverifiedNodeCompletionError",
    "WorkflowDefinition",
    "WorkflowGraph",
    "WorkflowNode",
    "WorkflowEdge",
    "WorkflowRun",
    "WorkflowRunStatus",
    "NodeSpec",
    "EdgeSpec",
    "WorkflowPlanVersion",
    "WorkflowPlanPatch",
    "ExecutionEventRecord",
    "WorkflowDecisionRecord",
    "NodeType",
    "NodeStatus",
    "EdgeMode",
    "RetryPolicy",
    "LoopPolicy",
    "PatchOperation",
    "WorkflowPatch",
    "PatchValidator",
    "PatchValidationResult",
    "WorkflowPatchEngine",
    "WorkflowScheduler",
    "WriteScopeCollisionError",
    # Bounded execution primitives (deadlines, wave concurrency, admission)
    "ConcurrencyGovernor",
    "DeadlineResult",
    "WaveOutcome",
    "clamp_concurrency",
    "execute_wave",
    "run_with_deadline",
    # Measured execution observability
    "NodeTiming",
    "RunTimeline",
    "build_run_observability",
    "critical_path",
    # Forking, time travel, and dry-run simulation
    "ForkError",
    "ForkResult",
    "RunHistoryEntry",
    "SimulationResult",
    "fork_run",
    "run_history",
    "run_report",
    "simulate_run",
    "DynamicRouter",
    "RouteDecision",
    "RuntimeReplanner",
    "SafeExpressionEvaluator",
    "ExpressionSecurityError",
    "evaluate_condition",
    "LeaseManager",
    "WorkerLease",
    "PlanGraphError",
    "PlanGraphStore",
    "PlanVersionConflict",
    "WorkflowEvent",
    "WorkflowEventDispatcher",
    "get_event_dispatcher",
    # Durable triggers (schedules as data; the host owns the firing)
    "TriggerKind",
    "TriggerStore",
    "TriggerStoreError",
    "TriggerFireResult",
    "WorkflowTrigger",
    "cron_next_after",
    "parse_cron_expression",
    "fire_trigger_on_engine",
    # Dead-letter quarantine for nodes that ran out of road
    "QuarantineStatus",
    "QuarantineStore",
    "QuarantineStoreError",
    "QuarantineTrigger",
    "QuarantineRecord",
    # Connectivity wait policy
    "CONNECTIVITY_WAIT_KIND",
    "DEFAULT_RELEASE_EVENT",
    "MAX_CONNECTIVITY_WAIT_SECONDS",
    "connectivity_evidence",
    "parse_deadline_seconds",
    "release_event_for",
    # Dynamic perceive→decompose→assemble→bridge layer
    "DynamicPerceptionEngine",
    "PerceivedIntent",
    "DynamicIntentType",
    "DynamicExecutionTier",
    "get_dynamic_perception_engine",
    "DynamicDecomposer",
    "DynamicGoal",
    "DynamicTaskItem",
    "SagaCompensation",
    "CATEGORY_NODE_TYPES",
    "get_dynamic_decomposer",
    "DynamicResourceAssembler",
    "AssembledResources",
    "SwarmWorkgroup",
    "get_dynamic_resource_assembler",
    "DynamicWorkflowBridge",
    "DynamicExecutionResult",
    "get_dynamic_workflow_bridge",
]
