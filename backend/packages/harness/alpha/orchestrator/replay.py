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
                                      engine does. The payload's ``evidence``
                                      list is SET onto the registered graph
                                      node and ``iteration_counts`` is folded
                                      into the run.
- ``node_iteration``              -> an interim bounded-loop iteration: node
                                      READY (deliberately NOT added to
                                      ``completed_nodes``) + evidence +
                                      ``iteration_counts``.
- ``node_failed``                 -> node FAILED + failed_nodes + evidence +
                                      ``iteration_counts``.
- ``workflow_completed``          -> terminal status + authoritative final state.
- ``workflow_failed``             -> terminal FAILED status.
- ``patch_committed``             -> graph_version + patches_applied + PENDING
                                      seeding for nodes the patch added + the
                                      patched graph is REBUILT and REGISTERED
                                      on the fresh engine (see
                                      :func:`_rebuild_patched_graph`), so
                                      dispatching the replayed run schedules
                                      patched-in nodes.
- ``approval_requested/granted/denied`` -> approval status/fields plus the
                                      journaled per-node ``node_status``
                                      (WAITING/READY/FAILED); a denial also
                                      appends the node to ``failed_nodes``.
- ``compensation_triggered``      -> node COMPENSATING.

Disclosed limitations (reported, not silently papered over):

- node-level ``output`` and graph-node ``status`` are NOT in the DWE event
  payloads, so they cannot be replayed from this log alone: the replayed graph
  keeps the caller's definition snapshot for those two fields (``evidence`` and
  ``iteration_counts`` now DO fold from the payloads). Runner side-writes into
  ``run.state`` between start and completion are likewise not journaled (the
  terminal ``workflow_completed`` state snapshot covers the final state dict).
- ``run.history`` / ``run.waiting_nodes`` live bookkeeping is not folded (the
  REST compare covers status/state/completed/failed/node_states/graph_version
  only), so a replayed run reports an empty transition history.
- a ``patch_committed`` recorded live but no longer valid against the rebuilt
  base graph is skipped (graph registration keeps the pre-patch version) -
  never force-applied.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from alpha.workflow.events import WorkflowEvent
from alpha.workflow.models import (
    NodeStatus,
    WorkflowDefinition,
    WorkflowPatch,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.patch import WorkflowPatchEngine
from alpha.workflow.runtime import EXECUTOR_STATE_KEYS, DynamicWorkflowEngine

# Engine state-key shapes mirrored by the fold (see module docstring).
_STATE_KEY_SUFFIXES: dict[str, str] = {
    "condition": "_result",
    "map": "_mapped",
    "reduce": "_reduced",
}


class _QuietEventSink:
    """Drop-in ``events`` stand-in for :class:`WorkflowPatchEngine` in replay.

    ``WorkflowPatchEngine.apply`` journals ``patch_committed``/``patch_rejected``
    through ``self.events.emit``. During replay that must write NOTHING — and
    the real ``WorkflowEventDispatcher`` would not only append to the log being
    read, it also publishes to the global alpha event bus. So replay swaps this
    silent sink in before touching the patch engine (zero-re-emission invariant).
    """

    def emit(self, event_type: str, run_id: str, **payload: Any) -> None:
        return None


def _fold_evidence_onto_graph(target_engine: DynamicWorkflowEngine, run: WorkflowRun, payload: dict[str, Any]) -> None:
    """SET the journaled evidence onto the registered graph node (gap 10).

    SET, never append: the payload carries the node's full evidence list at
    emit time, so re-folding is idempotent and a mid-log event can't double the
    list. Missing graph/node/evidence simply skips — nothing is invented.
    """
    graph = target_engine.graphs.get(f"{run.workflow_id}:v{run.graph_version}")
    nid = payload.get("node_id")
    evidence = payload.get("evidence")
    if graph is None or not isinstance(nid, str) or nid not in graph.nodes or not isinstance(evidence, list):
        return
    graph.nodes[nid].evidence = list(evidence)


def _fold_iteration_counts(run: WorkflowRun, payload: dict[str, Any]) -> None:
    """Fold the journaled per-node iteration counts into the run (gap 10)."""
    counts = payload.get("iteration_counts")
    if isinstance(counts, dict):
        run.iteration_counts.update({str(k): int(v) for k, v in counts.items()})


def _fold_node_status(run: WorkflowRun, payload: dict[str, Any]) -> None:
    """Fold the journaled per-node ``node_status`` of an approval event (gap 7).

    Older logs without the field simply keep the previous PENDING fold — an
    absent payload never gets invented into a status.
    """
    nid = payload.get("node_id")
    raw = payload.get("node_status")
    if not isinstance(nid, str) or not isinstance(raw, str):
        return
    try:
        run.node_states[nid] = NodeStatus(raw)
    except ValueError:
        pass


def _rebuild_patched_graph(engine: DynamicWorkflowEngine, run: WorkflowRun, patch: WorkflowPatch) -> None:
    """Rebuild and REGISTER the graph this committed patch produced (gap 6).

    Dispatching a replayed+patched run must schedule the patched-in nodes:
    ``execute_step`` resolves its graph as ``{workflow_id}:v{graph_version}``,
    so without this registration the fresh engine would fall back to the
    definition's base graph and the patched-in nodes would never run.

    Zero re-emission invariant: the patch engine journals through
    ``self.events``, which is swapped for a silent sink here, and a SCRATCH run
    absorbs the engine's own ``patches_applied``/``graph_version`` bookkeeping so
    the replayed run keeps exactly one entry per recorded patch. If the
    recorded patch no longer validates against the rebuilt base graph, the
    registration is skipped (the run-level fields were still folded from the
    log) — never force-applied.
    """
    base = engine.graphs.get(f"{run.workflow_id}:v{patch.base_graph_version}")
    if base is None:
        return
    scratch = WorkflowRun(run_id=run.run_id, workflow_id=run.workflow_id, graph_version=patch.base_graph_version)
    quiet_engine = WorkflowPatchEngine()
    quiet_engine.events = _QuietEventSink()
    new_graph, validation = quiet_engine.apply(scratch, base, patch)
    if validation.allowed:
        engine.graphs[f"{run.workflow_id}:v{new_graph.version}"] = new_graph


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
        raise ValueError("event log has no 'workflow_started' event; run state cannot be replayed honestly")

    target_engine = engine if engine is not None else DynamicWorkflowEngine()
    replayed_definition = definition.model_copy(deep=True)
    target_engine.register_definition(replayed_definition)

    run_id = started.workflow_run_id
    run = WorkflowRun(
        run_id=run_id,
        workflow_id=started.payload.get("workflow_id", replayed_definition.id),
        owner_id=started.payload.get("owner_id"),
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

        elif kind == "decision_recorded":
            run.metrics.setdefault("decisions", []).append(deepcopy(payload))

        elif kind == "node_started":
            nid = payload.get("node_id")
            if isinstance(nid, str):
                run.node_states[nid] = NodeStatus.RUNNING
                if nid not in run.active_nodes:
                    run.active_nodes.append(nid)
                node_type = payload.get("type")
                if node_type is not None:
                    node_kinds[nid] = str(node_type)

        elif kind == "node_completed":
            nid = payload.get("node_id")
            if not isinstance(nid, str):
                continue
            run.node_states[nid] = NodeStatus.SUCCEEDED
            run.active_nodes = [active for active in run.active_nodes if active != nid]
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
            _fold_evidence_onto_graph(target_engine, run, payload)
            _fold_iteration_counts(run, payload)

        elif kind == "node_iteration":
            # Gap 11: an interim bounded-loop iteration. The node stays READY
            # and is deliberately NOT added to completed_nodes (only a terminal
            # node_completed is a completion).
            nid = payload.get("node_id")
            if not isinstance(nid, str):
                continue
            run.node_states[nid] = NodeStatus.READY
            _fold_evidence_onto_graph(target_engine, run, payload)
            _fold_iteration_counts(run, payload)

        elif kind == "node_failed":
            nid = payload.get("node_id")
            if not isinstance(nid, str):
                continue
            run.node_states[nid] = NodeStatus.FAILED
            run.active_nodes = [active for active in run.active_nodes if active != nid]
            if nid not in run.failed_nodes:
                run.failed_nodes.append(nid)
            _fold_evidence_onto_graph(target_engine, run, payload)
            _fold_iteration_counts(run, payload)

        elif kind == "run_reopened":
            run.status = WorkflowRunStatus.RUNNING
            run.waiting_reason = None
            run.approval_request_id = None

        elif kind == "workflow_completed":
            run.status = WorkflowRunStatus.COMPLETED
            if "state" in payload:
                run.state = deepcopy(payload["state"])

        elif kind == "workflow_failed":
            run.status = WorkflowRunStatus.FAILED

        elif kind == "workflow_cancelled":
            run.status = WorkflowRunStatus.CANCELLED
            run.active_nodes.clear()

        elif kind == "node_skipped":
            nid = payload.get("node_id")
            if isinstance(nid, str):
                run.node_states[nid] = NodeStatus.SKIPPED
                _fold_evidence_onto_graph(target_engine, run, payload)

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
                    elif op.op == "retry_node":
                        retry_id = op.args.get("node_id")
                        if isinstance(retry_id, str):
                            run.node_states[retry_id] = NodeStatus.READY
                            if retry_id in run.failed_nodes:
                                run.failed_nodes.remove(retry_id)
                _rebuild_patched_graph(target_engine, run, patch)

        elif kind == "approval_requested":
            run.status = WorkflowRunStatus.WAITING_APPROVAL
            approval_id = payload.get("approval_id")
            if isinstance(approval_id, str):
                run.approval_request_id = approval_id
            _fold_node_status(run, payload)

        elif kind == "approval_granted":
            run.status = WorkflowRunStatus.RUNNING
            run.approval_request_id = None
            _fold_node_status(run, payload)

        elif kind == "approval_denied":
            run.status = WorkflowRunStatus.FAILED
            run.approval_request_id = None
            _fold_node_status(run, payload)
            # Gap 8 mirrors the engine: a denial fails the gated node too.
            denied_nid = payload.get("node_id")
            if isinstance(denied_nid, str) and denied_nid not in run.failed_nodes:
                run.failed_nodes.append(denied_nid)

        elif kind == "approval_timed_out":
            run.status = WorkflowRunStatus.FAILED
            run.approval_request_id = None
            _fold_node_status(run, payload)
            timed_out_nid = payload.get("node_id")
            if isinstance(timed_out_nid, str) and timed_out_nid not in run.failed_nodes:
                run.failed_nodes.append(timed_out_nid)

        elif kind == "compensation_triggered":
            nid = payload.get("node_id")
            if isinstance(nid, str):
                run.node_states[nid] = NodeStatus.COMPENSATING

        run.updated_at = event.timestamp

    # Install the projection on the fresh engine without re-emitting events.
    target_engine.runs[run_id] = run
    return target_engine, run
