"""Gap 1/3/4/9 root-cause pins: the engine's fail-closed + status-history paths.

The older suites DISCLOSED these as engine gaps; this suite pins the FIXED
behavior directly against ``alpha.workflow.runtime`` (and the kernel seam):

- gap 1: ``execute_step`` fail-closes a run left with failed nodes after EVERY
  wave - exactly ONE ``workflow_failed`` with the kernel's exact reason format,
  deferred only while a saga compensation wave is pending; the
  ``ExecutionKernel`` guard then emits nothing on top (defense-in-depth) and a
  second dispatch re-enters nothing;
- gap 3: ``BUDGET_EXHAUSTED`` is terminal for scheduling - never re-entered;
- gap 4: stagnation recovery targets a REAL graph node; no candidate and a
  rejected remediation patch both end FAILED with the disclosed real reasons;
- gap 9: run-status transitions land in ``run.history`` and ``waiting_nodes``
  stays derived from the per-node statuses.

Every test isolates ``AGENT_WORKSPACE_HOME`` to a per-test temp dir.
"""

from __future__ import annotations

import pytest

import alpha.orchestrator.executors as executors_module
import alpha.workflow.runtime as runtime_module
from alpha.orchestrator.executors import ExecutorRegistry
from alpha.orchestrator.loop import ExecutionKernel
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine

# The kernel's exact fail-closed reason format (single node 'n1' failed).
FAIL_CLOSED_REASON_N1 = (
    "fail-closed: 1 node(s) failed: ['n1']; " "see the node_failed events for the real per-node reasons"
)


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


def _engine_with(workflow_id: str, *nodes: WorkflowNode) -> DynamicWorkflowEngine:
    """A fresh engine with one definition whose graph holds exactly ``nodes``."""
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(version=1, nodes={node.id: node for node in nodes}, edges=[])
    engine.register_definition(WorkflowDefinition(id=workflow_id, name=workflow_id, graph=graph))
    return engine


def _events_of_type(engine: DynamicWorkflowEngine, run_id: str, event_type: str) -> list:
    return [e for e in engine.events.get_events(run_id) if e.event_type == event_type]


# ------------------------------------------------------------------- gap 1


def test_engine_fail_closes_with_exactly_one_workflow_failed(monkeypatch):
    """One failing wave in: one honest workflow_failed out, then the run is terminal."""
    monkeypatch.setattr(runtime_module, "_NODE_RUNNER", None)  # seam explicitly unbound

    engine = _engine_with("wf_rt_failclosed", WorkflowNode(id="n1", prompt="Only step"))
    run = engine.start_run("wf_rt_failclosed")
    assert run.history == []  # gap 9: nothing happened yet

    engine.execute_step(run.run_id)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_nodes == ["n1"]
    fail_close = _events_of_type(engine, run.run_id, "workflow_failed")
    assert len(fail_close) == 1, "the engine must journal exactly ONE workflow_failed"
    assert fail_close[0].payload["reason"] == FAIL_CLOSED_REASON_N1
    # gap 9: the transition is journaled with the real reason.
    assert [(h["from"], h["to"]) for h in run.history] == [("running", "failed")]
    assert run.history[0]["reason"] == FAIL_CLOSED_REASON_N1

    # Terminal: a second step re-enters nothing and emits nothing at all.
    before = len(engine.events.get_events(run.run_id))
    engine.execute_step(run.run_id)
    assert run.status == WorkflowRunStatus.FAILED
    assert len(engine.events.get_events(run.run_id)) == before


def test_saga_defers_fail_close_until_the_compensation_wave_finishes():
    """Deferral rule: nothing while a node is COMPENSATING, one emission after."""
    engine = _engine_with(
        "wf_rt_saga",
        WorkflowNode(id="bad", prompt="Will fail", compensation_node_id="undo"),
        WorkflowNode(id="undo", type=NodeType.COMPENSATION, config={"target_rollback_node": "bad"}),
    )
    run = engine.start_run("wf_rt_saga")

    def runner(node, _run):
        if node.id == "bad":
            return {"status": "failed", "output": "Infrastructure deployment error"}
        return {"status": "completed", "output": "reverted", "evidence": "rollback receipt"}

    engine.execute_step(run.run_id, node_runner=runner)
    # The compensation wave is pending, so fail-close is deferred for THIS wave.
    assert run.status == WorkflowRunStatus.RUNNING
    assert run.node_states["undo"] == NodeStatus.COMPENSATING
    assert _events_of_type(engine, run.run_id, "workflow_failed") == []

    engine.execute_step(run.run_id, node_runner=runner)
    assert run.node_states["undo"] == NodeStatus.SUCCEEDED
    assert "undo" in run.completed_nodes
    assert run.status == WorkflowRunStatus.FAILED
    fail_close = _events_of_type(engine, run.run_id, "workflow_failed")
    assert len(fail_close) == 1, "single emission once the saga wave finished"
    assert fail_close[0].payload["reason"] == (
        "fail-closed: 1 node(s) failed: ['bad']; "
        "see the node_failed events for the real per-node reasons"
    )


def test_kernel_dispatch_emits_one_workflow_failed_and_the_guard_stays_silent(registry):
    """Kernel seam: the engine emits once; the guard and re-dispatch emit nothing."""

    def failing_executor(node, run):
        raise RuntimeError("ledger backend down")

    registry.register("test.boom", failing_executor)
    engine = _engine_with("wf_rt_kernel", WorkflowNode(id="n1", prompt="Step", executor="test.boom"))
    kernel = ExecutionKernel(engine=engine)
    run = kernel.start_run("wf_rt_kernel")

    kernel.dispatch(run.run_id)

    assert run.status == WorkflowRunStatus.FAILED
    assert run.failed_nodes == ["n1"]
    fail_close = _events_of_type(engine, run.run_id, "workflow_failed")
    assert len(fail_close) == 1, "engine emits; the kernel guard is defense-in-depth only"

    # A terminal FAILED run dispatches no further waves: nothing new journaled.
    before = len(engine.events.get_events(run.run_id))
    kernel.dispatch(run.run_id)
    assert run.status == WorkflowRunStatus.FAILED
    assert len(engine.events.get_events(run.run_id)) == before


# ------------------------------------------------------------------- gap 3


def test_budget_exhaustion_is_terminal_and_never_reentered():
    """A budget-spent run ends BUDGET_EXHAUSTED and execute_step no-ops afterwards."""
    engine = _engine_with("wf_rt_budget", WorkflowNode(id="spendy", prompt="Spend tokens", budget=5))
    run = engine.start_run("wf_rt_budget")

    def spender(node, _run):
        return {"status": "completed", "output": "spent", "evidence": "receipt", "tokens_used": 100}

    engine.execute_step(run.run_id, node_runner=spender)

    assert run.status == WorkflowRunStatus.BUDGET_EXHAUSTED
    assert run.node_states["spendy"] == NodeStatus.FAILED
    assert run.failed_nodes == ["spendy"]
    events = engine.events.get_events(run.run_id)
    # The budget terminal has its own node_failed; fail-close does NOT also fire.
    assert [e for e in events if e.event_type == "workflow_failed"] == []
    budget_failed = [e for e in events if e.event_type == "node_failed"]
    assert len(budget_failed) == 1
    assert budget_failed[0].payload["reason"] == "Node budget exhausted."
    assert "evidence" in budget_failed[0].payload and "iteration_counts" in budget_failed[0].payload
    # gap 9: journaled transition with the real reason.
    assert [(h["from"], h["to"]) for h in run.history] == [("running", "budget_exhausted")]
    assert run.history[-1]["reason"] == "Node budget exhausted."

    # gap 3: never re-entered - no new events, status intact.
    before = len(engine.events.get_events(run.run_id))
    engine.execute_step(run.run_id)
    assert run.status == WorkflowRunStatus.BUDGET_EXHAUSTED
    assert len(engine.events.get_events(run.run_id)) == before


# ------------------------------------------------------------------- gap 4


def test_deadlock_recovery_patches_a_real_graph_node():
    """Allowed branch: the remediation patch targets a REAL node and registers v2."""
    engine = _engine_with(
        "wf_rt_deadlock",
        WorkflowNode(id="stuck", prompt="Blocked forever", depends_on=["ghost"]),
    )
    run = engine.start_run("wf_rt_deadlock")

    engine.execute_step(run.run_id)  # nothing ready -> stagnation recovery

    # Recovery was ALLOWED: no failure is claimed, no run-status change happened.
    assert run.status == WorkflowRunStatus.RUNNING
    assert run.failed_nodes == []
    assert run.history == []
    assert run.graph_version == 2
    assert sorted(engine.graphs) == ["wf_rt_deadlock:v1", "wf_rt_deadlock:v2"]
    # The recovery node is real: seeded into the run AND into the v2 graph with
    # the patch validator's insert_before edge (target exists now).
    assert run.node_states["gather_evidence_stuck_1"] == NodeStatus.PENDING
    v2 = engine.graphs["wf_rt_deadlock:v2"]
    assert "gather_evidence_stuck_1" in v2.nodes
    assert any(e.source == "gather_evidence_stuck_1" and e.target == "stuck" for e in v2.edges)
    events = engine.events.get_events(run.run_id)
    assert len([e for e in events if e.event_type == "patch_committed"]) == 1
    assert [e for e in events if e.event_type == "patch_rejected"] == []
    assert [e for e in events if e.event_type == "workflow_failed"] == []


def test_deadlock_without_any_candidate_node_fails_with_the_disclosed_reason():
    """No-candidate branch: honest FAILED with the disclosed no-op reason."""
    engine = _engine_with("wf_rt_noop", WorkflowNode(id="solo", prompt="Never schedulable"))
    run = engine.start_run("wf_rt_noop")
    # Neither PENDING nor READY: nothing can anchor a remediation patch.
    run.node_states["solo"] = NodeStatus.SUSPENDED

    engine.execute_step(run.run_id)

    expected = (
        "Deadlock: no nodes ready to execute; no PENDING/READY node exists to anchor a "
        "stagnation-recovery patch (no recovery attempted)."
    )
    assert run.status == WorkflowRunStatus.FAILED
    fail_close = _events_of_type(engine, run.run_id, "workflow_failed")
    assert len(fail_close) == 1
    assert fail_close[0].payload["reason"] == expected
    assert [(h["from"], h["to"]) for h in run.history] == [("running", "failed")]
    assert run.history[0]["reason"] == expected
    # No patch was fabricated: only the base graph exists.
    assert sorted(engine.graphs) == ["wf_rt_noop:v1"]
    assert run.patches_applied == []


def test_deadlock_remediation_patch_rejection_surfaces_the_real_reason():
    """Rejected branch: FAILED carrying the patch layer's REAL validation reason."""
    engine = _engine_with(
        "wf_rt_rejected",
        WorkflowNode(id="n1", prompt="Blocked", depends_on=["ghost"]),
        # Pre-existing impostor colliding with the replanner's generated id.
        WorkflowNode(id="gather_evidence_n1_1", prompt="Impostor", depends_on=["n1"]),
    )
    run = engine.start_run("wf_rt_rejected")

    engine.execute_step(run.run_id)

    expected = (
        "Deadlock: no nodes ready to execute; remediation patch rejected: "
        "Node 'gather_evidence_n1_1' already exists."
    )
    assert run.status == WorkflowRunStatus.FAILED
    fail_close = _events_of_type(engine, run.run_id, "workflow_failed")
    assert len(fail_close) == 1
    assert fail_close[0].payload["reason"] == expected
    assert run.history[0]["reason"] == expected
    rejected = _events_of_type(engine, run.run_id, "patch_rejected")
    assert len(rejected) == 1
    assert "already exists" in str(rejected[0].payload.get("reason", ""))
    # Nothing was registered and no version moved.
    assert sorted(engine.graphs) == ["wf_rt_rejected:v1"]
    assert run.graph_version == 1
    assert run.patches_applied == []


# ------------------------------------------------------------------- gap 9


def test_run_history_and_waiting_nodes_track_the_approval_lifecycle():
    """Every status transition is journaled; waiting_nodes stays derived state."""
    engine = _engine_with("wf_rt_history", WorkflowNode(id="gate", prompt="Deploy", requires_approval=True))
    run = engine.start_run("wf_rt_history")
    assert run.history == []  # nothing happened yet
    assert run.waiting_nodes == []

    def ok_executor(node, _run):
        return {"status": "completed", "output": "deployed", "evidence": "deploy log"}

    engine.execute_step(run.run_id, node_runner=ok_executor)
    assert run.status == WorkflowRunStatus.WAITING_APPROVAL
    assert run.waiting_nodes == ["gate"]  # derived from node_states == WAITING
    assert [(h["from"], h["to"]) for h in run.history] == [("running", "waiting_approval")]
    assert "requires human approval" in run.history[0]["reason"]

    engine.resolve_approval(run.run_id, "gate", approved=True, feedback="lgtm")
    assert run.status == WorkflowRunStatus.RUNNING
    assert run.waiting_nodes == []  # cleared on resolution
    assert [(h["from"], h["to"]) for h in run.history][-1] == ("waiting_approval", "running")

    engine.execute_step(run.run_id, node_runner=ok_executor)
    assert run.status == WorkflowRunStatus.COMPLETED
    assert [(h["from"], h["to"]) for h in run.history] == [
        ("running", "waiting_approval"),
        ("waiting_approval", "running"),
        ("running", "completed"),
    ]
    assert run.waiting_nodes == []
