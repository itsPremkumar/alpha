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
from alpha.workflow.patch import (
    WorkflowPatchEngine,
)
from alpha.workflow.patch_validator import (
    PatchValidationResult,
    PatchValidator,
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
    "DynamicRouter",
    "RouteDecision",
    "RuntimeReplanner",
    "SafeExpressionEvaluator",
    "ExpressionSecurityError",
    "evaluate_condition",
    "LeaseManager",
    "WorkerLease",
    "WorkflowEvent",
    "WorkflowEventDispatcher",
    "get_event_dispatcher",
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
