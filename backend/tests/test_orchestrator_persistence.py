"""Approval-lifecycle replay folds + disclosed patched-graph gap (P1 recovery).

Extends ``test_orchestrator_recovery.py`` (identity/completion/resume/patch
folds) with the event-log folds and disclosures that suite does not touch:

- ``approval_requested`` folds to ``WAITING_APPROVAL`` with the approval id —
  together with the DISCLOSED payload gap: the log journals no per-node
  WAITING/READY/FAILED status for approval transitions, so a replayed gated
  node stays PENDING while the live node moves. Reported, never papered over.
- ``approval_granted`` folds back to RUNNING with the approval id cleared;
  ``approval_denied`` folds to FAILED.
- a PATCHED run's replay reconstructs ``graph_version``/``patches_applied`` and
  seeds the added node PENDING, but the fresh engine registers only the
  definition's v1 graph — the runtime.py gap that makes dispatching a
  replayed+patched run fall back to the definition graph (patched-in nodes
  never scheduled). Pinned as a DISCLOSURE so fixing runtime.py must update
  this test rather than silently re-breaking recovery.

None of these tests dispatch a patched replayed run (see the gap pin above).
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
    """Paused run replays WAITING_APPROVAL + approval id; node status gap pinned."""
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

    # DISCLOSED payload gap (runtime.py): no per-node approval status is in the
    # event payloads, so the gated node replays as PENDING, not WAITING. This
    # asserts the CURRENT honest behavior — when runtime.py journals node
    # status in approval events, this assertion must be updated deliberately.
    assert replayed.node_states["gate"] == NodeStatus.PENDING
    assert replayed.node_states["gate"] != live.node_states["gate"]


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
    # Same disclosed gap as above: READY is not in the log, PENDING is the fold.
    assert replayed.node_states["gate"] == NodeStatus.PENDING
    assert replayed.completed_nodes == [] and replayed.failed_nodes == []


def test_approval_denied_fold_reconstructs_failed_run(registry):
    """A human denial folds to FAILED — the run's real terminal outcome."""
    kernel, run_id = _paused_approval_run("wf_replay_approval_deny")
    denied = kernel.resolve_approval(run_id, "gate", approved=False, feedback="not now")
    assert denied.status == WorkflowRunStatus.FAILED
    assert denied.node_states["gate"] == NodeStatus.FAILED
    # Observed engine behavior (reported): denial does not append the node to
    # ``failed_nodes`` — the run-level status carries the terminal truth.
    assert denied.failed_nodes == []

    events = kernel.engine.events.get_events(run_id)
    _, replayed = replay_run(events, kernel.engine.get_definition("wf_replay_approval_deny"))

    assert replayed.status == WorkflowRunStatus.FAILED
    assert replayed.failed_nodes == []
    assert replayed.completed_nodes == []
    # Disclosed gap again: node-level FAILED is not journaled in the payload.
    assert replayed.node_states["gate"] == NodeStatus.PENDING


# ----------------------------------------------- disclosed patched-graph gap


def test_patched_replay_reconstructs_version_without_engine_graphs(registry):
    """Patch fold rebuilds run fields, but the fresh engine has NO v2 graph.

    DISCLOSURE (runtime.py/engine gap, reported not edited): ``replay_run``
    reconstructs ``run.graph_version``/``patches_applied`` from
    ``patch_committed`` events, yet the fresh engine only ever registers the
    definition's v1 graph. Dispatching this replayed run would therefore fall
    back to the v1 graph and never schedule the patched-in node — so patched
    recovery stops at state reconstruction until runtime.py registers patched
    graphs on replay. If runtime.py starts rebuilding v2 graphs, this test
    fails on purpose: update the disclosure, do not delete the pin.
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
        operations=[PatchOperation(op="add_node", args={"node": {"id": "check", "prompt": "Verify"}})],
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

    # The disclosed gap: only v1 exists on the fresh engine — no v2 graph.
    assert sorted(fresh_engine.graphs) == ["wf_replay_patched_gap:v1"]
    assert f"wf_replay_patched_gap:v{replayed.graph_version}" not in fresh_engine.graphs
