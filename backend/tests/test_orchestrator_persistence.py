"""Approval-lifecycle replay folds + patched-graph registration (P1 recovery).

Extends ``test_orchestrator_recovery.py`` (identity/completion/resume/patch
folds) with the event-log folds that suite does not touch:

- ``approval_requested`` folds to ``WAITING_APPROVAL`` with the approval id
  AND the journaled per-node ``node_status`` (gap 7 root-cause fix), so the
  replayed gated node is WAITING exactly like the live one.
- ``approval_granted`` folds back to RUNNING with the approval id cleared
  and the node READY; ``approval_denied`` folds to FAILED with the node
  FAILED and recorded in ``failed_nodes`` (gap 8 fix).
- a PATCHED run's replay reconstructs ``graph_version``/``patches_applied``,
  seeds the added node PENDING, and (gap 6 root-cause fix) REBUILDS and
  registers the patched graph on the fresh engine, so dispatching the
  replayed run schedules the patched-in node.
"""

from __future__ import annotations

import pytest

import alpha.orchestrator.executors as executors_module
from alpha.orchestrator.executors import DIGEST_EXECUTOR, ExecutorRegistry
from alpha.orchestrator.loop import ExecutionKernel
from alpha.orchestrator.replay import replay_run
from alpha.workflow.models import (
    NodeStatus,
    PatchOperation,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    """AGENT_WORKSPACE_HOME points at a per-test temp dir (process-global env)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


@pytest.fixture()
def registry(monkeypatch):
    """Fresh EMPTY executor registry per test (module-seam swap, auto-restored)."""
    reg = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", reg)
    return reg


def _approval_definition(workflow_id: str) -> WorkflowDefinition:
    node = WorkflowNode(
        id="gate",
        prompt="Deploy to production",
        requires_approval=True,
        executor=DIGEST_EXECUTOR,
    )
    return WorkflowDefinition(
        id=workflow_id,
        name=workflow_id,
        graph=WorkflowGraph(version=1, nodes={"gate": node}, edges=[]),
    )


def _kernel_for(definition: WorkflowDefinition) -> ExecutionKernel:
    engine = DynamicWorkflowEngine()
    engine.register_definition(definition)
    return ExecutionKernel(engine=engine)


def _paused_approval_run(workflow_id: str) -> tuple[ExecutionKernel, str]:
    """Start + dispatch to WAITING_APPROVAL (the gate suspends before exec)."""
    kernel = _kernel_for(_approval_definition(workflow_id))
    run = kernel.start_run(workflow_id)
    paused = kernel.dispatch(run.run_id)
    assert paused.status == WorkflowRunStatus.WAITING_APPROVAL
    assert paused.approval_request_id is not None
    return kernel, run.run_id


# ---------------------------------------------------------- approval folds


def test_approval_requested_fold_reconstructs_waiting_status(registry):
    """Paused run replays WAITING_APPROVAL + approval id + node WAITING status."""
    kernel, run_id = _paused_approval_run("wf_replay_approval_wait")
    live = kernel.engine.get_run(run_id)
    assert live is not None
    assert live.node_states["gate"] == NodeStatus.WAITING  # live truth

    events = kernel.engine.events.get_events(run_id)
    assert any(e.event_type == "approval_requested" for e in events), "the gate must be journaled"

    _, replayed = replay_run(events, kernel.engine.get_definition("wf_replay_approval_wait"))

    # Folded honestly from the log:
    assert replayed.status == WorkflowRunStatus.WAITING_APPROVAL
    assert replayed.approval_request_id == live.approval_request_id
    assert replayed.completed_nodes == [] and replayed.failed_nodes == []
    assert replayed.metrics.get("execution_mode") == "normal"

    # Gap 7 root-cause fix: the approval event journals the node's WAITING
    # status, so the replayed gated node matches the live one exactly.
    assert replayed.node_states["gate"] == NodeStatus.WAITING
    assert replayed.node_states["gate"] == live.node_states["gate"]


def test_approval_granted_fold_returns_run_to_running(registry):
    """A human grant folds back to RUNNING with the approval id cleared."""
    kernel, run_id = _paused_approval_run("wf_replay_approval_grant")
    granted = kernel.resolve_approval(run_id, "gate", approved=True, feedback="lgtm")
    assert granted.status == WorkflowRunStatus.RUNNING
    assert granted.node_states["gate"] == NodeStatus.READY
    assert granted.approval_request_id is None

    events = kernel.engine.events.get_events(run_id)
    _, replayed = replay_run(events, kernel.engine.get_definition("wf_replay_approval_grant"))

    assert replayed.status == WorkflowRunStatus.RUNNING
    assert replayed.approval_request_id is None
    # Gap 7 root-cause fix: the grant journals READY, so replay folds READY.
    assert replayed.node_states["gate"] == NodeStatus.READY
    assert replayed.completed_nodes == [] and replayed.failed_nodes == []


def test_approval_denied_fold_reconstructs_failed_run(registry):
    """A human denial folds to FAILED — the run's real terminal outcome."""
    kernel, run_id = _paused_approval_run("wf_replay_approval_deny")
    denied = kernel.resolve_approval(run_id, "gate", approved=False, feedback="not now")
    assert denied.status == WorkflowRunStatus.FAILED
    assert denied.node_states["gate"] == NodeStatus.FAILED
    # Gap 8 root-cause fix: the denied node IS recorded as a failed node.
    assert denied.failed_nodes == ["gate"]

    events = kernel.engine.events.get_events(run_id)
    _, replayed = replay_run(events, kernel.engine.get_definition("wf_replay_approval_deny"))

    assert replayed.status == WorkflowRunStatus.FAILED
    assert replayed.failed_nodes == ["gate"]
    assert replayed.completed_nodes == []
    # Gap 7 + 8 root-cause fixes: the denial journals node FAILED + failed_nodes.
    assert replayed.node_states["gate"] == NodeStatus.FAILED


# ------------------------------------- patched-graph rebuild (gap 6 fixed)


def test_patched_replay_rebuilds_and_registers_the_patched_graph(registry):
    """Patch fold rebuilds run fields AND registers the rebuilt v2 graph.

    Gap 6 root-cause fix: ``replay_run`` now re-applies each recorded
    ``patch_committed`` through a quiet patch engine (zero re-emission) and
    registers the resulting graph on the fresh engine, so dispatching the
    replayed run schedules the patched-in node instead of silently falling
    back to the definition's v1 graph.
    """
    registry.register(DIGEST_EXECUTOR, executors_module.local_digest_executor)
    definition = WorkflowDefinition(
        id="wf_replay_patched_gap",
        name="wf_replay_patched_gap",
        graph=WorkflowGraph(
            version=1,
            nodes={"solo": WorkflowNode(id="solo", prompt="One step", executor=DIGEST_EXECUTOR)},
            edges=[],
        ),
    )
    kernel = _kernel_for(definition)
    run = kernel.start_run("wf_replay_patched_gap")

    patch = WorkflowPatch(
        workflow_run_id=run.run_id,
        base_graph_version=1,
        reason="insert a verification node",
        operations=[
            PatchOperation(
                op="add_node",
                args={"node": {"id": "check", "prompt": "Verify", "executor": DIGEST_EXECUTOR}},
            )
        ],
    )
    new_graph, validation = kernel.apply_patch(run.run_id, patch)
    assert validation.allowed is True
    assert new_graph.version == 2

    events = kernel.engine.events.get_events(run.run_id)
    assert [e.event_type for e in events][-1] == "patch_committed"

    fresh_engine, replayed = replay_run(events, kernel.engine.get_definition("wf_replay_patched_gap"))

    # Reconstructed from the log (no dispatch happened on the fresh engine):
    assert replayed.run_id == run.run_id
    assert replayed.status == WorkflowRunStatus.RUNNING
    assert replayed.graph_version == 2
    assert len(replayed.patches_applied) == 1
    assert replayed.patches_applied[0].reason == "insert a verification node"
    assert replayed.node_states["check"] == NodeStatus.PENDING

    # Gap 6 root-cause fix: the patch is rebuilt and registered on the fresh
    # engine, so the replayed run resolves its v2 graph...
    assert sorted(fresh_engine.graphs) == [
        "wf_replay_patched_gap:v1",
        "wf_replay_patched_gap:v2",
    ]
    assert f"wf_replay_patched_gap:v{replayed.graph_version}" in fresh_engine.graphs

    # ...and dispatching it actually schedules the patched-in node (with the
    # old v1 fallback, "check" could never run).
    resumed = ExecutionKernel(engine=fresh_engine).dispatch(run.run_id)
    assert resumed.status == WorkflowRunStatus.COMPLETED
    assert set(resumed.completed_nodes) == {"solo", "check"}
    assert resumed.node_states["check"] == NodeStatus.SUCCEEDED
