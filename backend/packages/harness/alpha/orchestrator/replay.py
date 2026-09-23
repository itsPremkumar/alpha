"""Event-log replay: rebuild a run on a fresh DynamicWorkflowEngine (P1 recovery).

The append-only DWE event log is the single truth; run state is a projection.
:func:`replay_run` folds that log back into a ``WorkflowRun`` on a NEW engine
instance (the "restart"), re-installing the projection WITHOUT re-emitting any
event — replay never pollutes the log it is reading.

Fold rules (each one mirrors data the engine actually journaled; nothing is
invented):

- ``workflow_started``            -> run identity + state at start (RUNNING).
- ``run_mode_selected``           -> ``metrics['execution_mode']`` (kernel start).
- ``node_started``                -> node status RUNNING (+ remembered node kind).
- ``node_completed``              -> node SUCCEEDED + completed_nodes; for the
                                     engine's documented state-key shapes
                                     (condition -> ``<id>_result``,
                                     map -> ``<id>_mapped``,
                                     reduce -> ``<id>_reduced``) the output is
                                     folded into state, and map/reduce keys are
                                     also recorded under
                                     ``EXECUTOR_STATE_KEYS`` exactly as the
                                     engine does.
- ``node_failed``                 -> node FAILED + failed_nodes.
- ``workflow_completed``          -> terminal status + authoritative final state.
- ``workflow_failed``             -> terminal FAILED status.
- ``patch_committed``             -> graph_version + patches_applied + PENDING
                                     seeding for nodes the patch added.
- ``approval_requested/granted/denied`` -> approval status/fields.
- ``compensation_triggered``      -> node COMPENSATING.

Disclosed limitations (reported, not silently papered over):

- node-level ``evidence``/``output`` payloads, per-node ``iteration_counts``,
  and runner side-writes into ``run.state`` between start and completion are
  NOT in the DWE event payloads, so they cannot be replayed from this log
  alone (a terminal ``workflow_completed`` state snapshot covers the final
  state dict).
- loop nodes: the engine emits ``node_completed`` for an intermediate loop
  iteration while the live run keeps the node ``ready`` and OUT of
  ``completed_nodes`` until ``max_iterations``/stop-condition, so a MID-RUN
  replay of a loop run over-states that node as succeeded; the terminal
  snapshot still converges on the true final state.

Fixing either belongs in ``alpha/workflow/runtime.py`` event payloads —
do-not-touch here.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy

from alpha.workflow.events import WorkflowEvent
from alpha.workflow.models import (
    NodeStatus,
    WorkflowDefinition,
    WorkflowPatch,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import EXECUTOR_STATE_KEYS, DynamicWorkflowEngine

# Engine state-key shapes mirrored by the fold (see module docstring).
_STATE_KEY_SUFFIXES: dict[str, str] = {
    "condition": "_result",
    "map": "_mapped",
    "reduce": "_reduced",
}


def replay_run(
    events: Sequence[WorkflowEvent],
    definition: WorkflowDefinition,
    engine: DynamicWorkflowEngine | None = None,
) -> tuple[DynamicWorkflowEngine, WorkflowRun]:
    """Rebuild the run described by ``events`` on a fresh engine.

    ``definition`` is the workflow definition as registered before the run
    (replayed on a deep copy so replay never mutates the caller's graph).
    Returns ``(engine, run)`` with ``engine.get_run(run_id)`` populated and NO
    events emitted.
    """
    log = list(events)
    started = next((e for e in log if e.event_type == "workflow_started"), None)
    if started is None:
        raise ValueError(
            "event log has no 'workflow_started' event; run state cannot be replayed honestly"
        )

    target_engine = engine if engine is not None else DynamicWorkflowEngine()
    replayed_definition = definition.model_copy(deep=True)
    target_engine.register_definition(replayed_definition)

    run_id = started.workflow_run_id
    run = WorkflowRun(
        run_id=run_id,
        workflow_id=started.payload.get("workflow_id", replayed_definition.id),
        graph_version=replayed_definition.graph.version,
        status=WorkflowRunStatus.RUNNING,
        state=deepcopy(started.payload.get("state") or {}),
        node_states={nid: NodeStatus.PENDING for nid in replayed_definition.graph.nodes},
    )

    node_kinds: dict[str, str] = {}

    for event in log:
        payload = event.payload
        kind = event.event_type

        if kind == "workflow_started":
            continue

        if kind == "run_mode_selected":
            mode = payload.get("mode")
            if mode is not None:
                run.metrics["execution_mode"] = mode

        elif kind == "node_started":
            nid = payload.get("node_id")
            if isinstance(nid, str):
                run.node_states[nid] = NodeStatus.RUNNING
                node_type = payload.get("type")
                if node_type is not None:
                    node_kinds[nid] = str(node_type)

        elif kind == "node_completed":
            nid = payload.get("node_id")
            if not isinstance(nid, str):
                continue
            run.node_states[nid] = NodeStatus.SUCCEEDED
            if nid not in run.completed_nodes:
                run.completed_nodes.append(nid)
            suffix = _STATE_KEY_SUFFIXES.get(node_kinds.get(nid, ""))
            if suffix is not None:
                key = f"{nid}{suffix}"
                run.state[key] = payload.get("output")
                if suffix in ("_mapped", "_reduced"):
                    produced = run.metrics.setdefault(EXECUTOR_STATE_KEYS, [])
                    if key not in produced:
                        produced.append(key)

        elif kind == "node_failed":
            nid = payload.get("node_id")
            if not isinstance(nid, str):
                continue
            run.node_states[nid] = NodeStatus.FAILED
            if nid not in run.failed_nodes:
                run.failed_nodes.append(nid)

        elif kind == "workflow_completed":
            run.status = WorkflowRunStatus.COMPLETED
            if "state" in payload:
                run.state = deepcopy(payload["state"])

        elif kind == "workflow_failed":
            run.status = WorkflowRunStatus.FAILED

        elif kind == "patch_committed":
            graph_version = payload.get("graph_version")
            if isinstance(graph_version, int):
                run.graph_version = graph_version
            patch_payload = payload.get("patch")
            if isinstance(patch_payload, dict):
                patch = WorkflowPatch(**patch_payload)
                run.patches_applied.append(patch)
                for op in patch.operations:
                    if op.op in ("add_node", "insert_before", "insert_after", "replace_node"):
                        node_data = op.args.get("node") or op.args.get("new_node")
                        if isinstance(node_data, dict) and node_data.get("id") not in run.node_states:
                            run.node_states[node_data["id"]] = NodeStatus.PENDING

        elif kind == "approval_requested":
            run.status = WorkflowRunStatus.WAITING_APPROVAL
            approval_id = payload.get("approval_id")
            if isinstance(approval_id, str):
                run.approval_request_id = approval_id

        elif kind == "approval_granted":
            run.status = WorkflowRunStatus.RUNNING
            run.approval_request_id = None

        elif kind == "approval_denied":
            run.status = WorkflowRunStatus.FAILED
            run.approval_request_id = None

        elif kind == "compensation_triggered":
            nid = payload.get("node_id")
            if isinstance(nid, str):
                run.node_states[nid] = NodeStatus.COMPENSATING

        run.updated_at = event.timestamp

    # Install the projection on the fresh engine without re-emitting events.
    target_engine.runs[run_id] = run
    return target_engine, run
