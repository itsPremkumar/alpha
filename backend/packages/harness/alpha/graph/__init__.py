"""Alpha Graph Engineering (AGE) — the graph plane.

This package implements the graph-engineering architecture described in
``docs/ALPHA_GRAPH_ENGINEERING_IMPLEMENTATION_PLAN.md``. It is **additive**:
it does not replace RunManager, DynamicWorkflowEngine, or the lead agent. It
adds a canonical task vocabulary, a deterministic DAG validator, a SQLite-backed
ledger, and a top-level LangGraph execution graph.

The public surface
------------------
The package exports the following names. Callers should import from
``alpha.graph`` rather than from submodules, so the internal structure can
change without breaking consumers.

* **Models**: ``Goal``, ``TaskNode``, ``GraphEdge``, ``TaskAttempt``,
  ``Artifact``, ``EvidenceClaim``, ``RunEvent``, ``ApprovalRequest``,
  ``CapabilityRequirement``, ``GraphBudget``, ``GraphSnapshot``
* **Enums**: ``GoalMode``, ``GoalStatus``, ``TaskStatus``, ``TaskType``,
  ``EdgeType``, ``ClaimStatus``, ``EventType``, ``EventSeverity``,
  ``RiskLevel``, ``ArtifactSensitivity``, ``ApprovalState``,
  ``ApprovalDecision``, ``AttemptOutcome``, ``CriterionVerdict``
* **State**: ``AlphaGraphState``, ``GraphStatus``, ``GraphIntent``
* **Ledger**: ``TaskLedger``, ``TaskLedgerConfig``, ``LedgerError``,
  ``LedgerConflict``
* **DAG**: ``validate_tasks``, ``execution_waves``, ``ready_tasks``,
  ``critical_path``, ``propagate_failures``, ``snapshot``, ``CycleDetected``,
  ``UnknownDependency``
* **Runtime**: ``GraphRuntime``, ``GraphRuntimeConfig``, ``build_graph``,
  ``build_graph_runtime``
* **Adapters**: ``RunManagerAdapter``, ``DynamicWorkflowEngineAdapter``,
  ``LeadAgentAdapter``
* **Failures**: ``GraphFailureClass``, ``classify_graph_failure``,
  ``raise_graph_failure``
* **Redaction**: ``RedactionLevel``, ``redact_event_message``,
  ``redact_artifact_uri``, ``redact_approval_payload``,
  ``redact_claim_locator``, ``redact_event_attributes``, ``redact_structured``
* **IDs**: ``new_goal_id``, ``new_task_id``, ``new_graph_id``,
  ``new_attempt_id``, ``new_event_id``, ``new_claim_id``, ``new_artifact_id``,
  ``new_approval_id``, ``timestamp_of``

What this package is **not**
----------------------------
* It is not a replacement for ``alpha.workflow``. The DWE owns wave scheduling,
  leases, and approval waits. This package owns the task vocabulary and the
  top-level execution graph.
* It is not a replacement for ``alpha.agents``. The lead agent owns the
  conversational surface. This package owns the task contract.
* It is not a replacement for ``alpha.persistence``. The SQL layer owns
  migrations and repositories. This package owns the graph plane's ledger,
  which is a separate store with a separate schema.

The feature flag
----------------
The graph plane is opt-in. The ``ALPHA_GRAPH_EXECUTION_ENABLED`` feature flag
(defaults to ``False``) controls whether the Gateway routes requests through
the graph plane. When disabled, the lead agent handles everything as before.
"""

from __future__ import annotations

from alpha.graph.adapter import (
    DynamicWorkflowEngineAdapter,
    LeadAgentAdapter,
    RunManagerAdapter,
    SpecialistTeamAdapter,
)
from alpha.graph.dag import (
    CycleDetected,
    DagReport,
    UnknownDependency,
    blocked_tasks,
    build_graph_id,
    critical_path,
    dependency_map,
    dependents_map,
    execution_waves,
    find_cycle,
    find_unknown_dependencies,
    propagate_failures,
    ready_tasks,
    select_ready_batch,
    snapshot,
    validate_tasks,
)
from alpha.graph.failures import (
    GraphFailureClass,
    classify_graph_failure,
    raise_graph_failure,
)
from alpha.graph.ids import (
    ApprovalId,
    ArtifactId,
    AttemptId,
    ClaimId,
    EventId,
    GoalId,
    GraphId,
    TaskId,
    make_id,
    new_approval_id,
    new_artifact_id,
    new_attempt_id,
    new_claim_id,
    new_event_id,
    new_goal_id,
    new_graph_id,
    new_task_id,
    timestamp_of,
)
from alpha.graph.ledger import (
    LedgerConflict,
    LedgerError,
    TaskLedger,
    TaskLedgerConfig,
)
from alpha.graph.models import (
    TERMINAL_GOAL_STATUSES,
    TERMINAL_TASK_STATUSES,
    AcceptanceCheck,
    AcceptanceReport,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    Artifact,
    ArtifactSensitivity,
    AttemptOutcome,
    CapabilityRequirement,
    ClaimStatus,
    CriterionVerdict,
    EdgeType,
    EventSeverity,
    EventType,
    Goal,
    GoalMode,
    GoalStatus,
    GraphBudget,
    GraphEdge,
    GraphSnapshot,
    IllegalTransition,
    ModelValidationError,
    RiskLevel,
    RunEvent,
    TaskAttempt,
    TaskNode,
    TaskStatus,
    TaskType,
    now_iso,
)
from alpha.graph.redact import (
    RedactionLevel,
    RedactionSummary,
    redact_approval_payload,
    redact_artifact_uri,
    redact_claim_locator,
    redact_event_attributes,
    redact_event_message,
    redact_structured,
)
from alpha.graph.runtime import (
    GraphRuntime,
    GraphRuntimeConfig,
    build_graph,
    build_graph_runtime,
    build_initial_state,
)
from alpha.graph.state import (
    AlphaGraphState,
    GraphIntent,
    GraphStatus,
    append_dedupe,
    merge_graph_state,
)

__all__ = [
    # Adapters
    "DynamicWorkflowEngineAdapter",
    "LeadAgentAdapter",
    "RunManagerAdapter",
    "SpecialistTeamAdapter",
    # DAG
    "CycleDetected",
    "DagReport",
    "UnknownDependency",
    "blocked_tasks",
    "build_graph_id",
    "critical_path",
    "dependency_map",
    "dependents_map",
    "execution_waves",
    "find_cycle",
    "find_unknown_dependencies",
    "propagate_failures",
    "ready_tasks",
    "select_ready_batch",
    "snapshot",
    "validate_tasks",
    # Failures
    "GraphFailureClass",
    "classify_graph_failure",
    "raise_graph_failure",
    # IDs
    "ApprovalId",
    "ArtifactId",
    "AttemptId",
    "ClaimId",
    "EventId",
    "GoalId",
    "GraphId",
    "TaskId",
    "make_id",
    "new_approval_id",
    "new_artifact_id",
    "new_attempt_id",
    "new_claim_id",
    "new_event_id",
    "new_goal_id",
    "new_graph_id",
    "new_task_id",
    "timestamp_of",
    # Ledger
    "LedgerConflict",
    "LedgerError",
    "TaskLedger",
    "TaskLedgerConfig",
    # Models
    "TERMINAL_GOAL_STATUSES",
    "TERMINAL_TASK_STATUSES",
    "AcceptanceCheck",
    "AcceptanceReport",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalState",
    "Artifact",
    "ArtifactSensitivity",
    "AttemptOutcome",
    "CapabilityRequirement",
    "ClaimStatus",
    "CriterionVerdict",
    "EdgeType",
    "EventSeverity",
    "EventType",
    "Goal",
    "GoalMode",
    "GoalStatus",
    "GraphBudget",
    "GraphEdge",
    "GraphSnapshot",
    "IllegalTransition",
    "ModelValidationError",
    "RiskLevel",
    "RunEvent",
    "TaskAttempt",
    "TaskNode",
    "TaskStatus",
    "TaskType",
    "now_iso",
    # Redaction
    "RedactionLevel",
    "RedactionSummary",
    "redact_approval_payload",
    "redact_artifact_uri",
    "redact_claim_locator",
    "redact_event_attributes",
    "redact_event_message",
    "redact_structured",
    # Runtime
    "GraphRuntime",
    "GraphRuntimeConfig",
    "build_graph",
    "build_graph_runtime",
    "build_initial_state",
    # State
    "AlphaGraphState",
    "GraphIntent",
    "GraphStatus",
    "append_dedupe",
    "merge_graph_state",
]
