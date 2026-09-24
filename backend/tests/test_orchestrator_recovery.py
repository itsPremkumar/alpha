"""Event-log replay / restart-recovery tests (P1 kernel, section 8 resumability).

The append-only DWE event log is the single truth; run state is a projection.
``alpha.orchestrator.replay.replay_run`` (and ``loop.recover_run``) rebuild that
projection on a FRESH engine after a simulated restart:

- a completed run replays to a projection equal to the live one, and replay
  re-emits NOTHING (the run's slice of the log is byte-identical afterwards);
- a mid-run replay reconstructs partial progress, and dispatching on the
  recovered kernel resumes to completion WITHOUT re-executing finished nodes;
- mode journaling (``run_mode_selected``) and ``patch_committed`` graph versions
  fold back, including PENDING seeding for patch-added nodes;
- a fail-closed run replays FAILED with its real failed nodes;
- a log without ``workflow_started`` is refused with the honest error.

Root-caused engine gaps this suite used to disclose: ``evidence`` and
``iteration_counts`` now ride along on every ``node_completed``/
``node_failed`` payload and are folded by replay; committed patches are
REBUILT and registered on the fresh engine. Graph-node ``output`` still
comes from the caller's definition snapshot (see the ``alpha.orchestrator.
replay`` module docstring).
"""

from __future__ import annotations

import pytest

import alpha.orchestrator.executors as executors_module
from alpha.orchestrator.executors import DIGEST_EXECUTOR, ExecutorRegistry
from alpha.orchestrator.loop import ExecutionKernel, recover_run
from alpha.orchestrator.replay import replay_run
from alpha.workflow.events import WorkflowEvent
from alpha.workflow.models import (
    NodeStatus,
    PatchOperation,
    WorkflowDefinition,
    WorkflowEdge,
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


def _chain_definition(workflow_id: str, executor_name: str) -> WorkflowDefinition:
    n1 = WorkflowNode(id="n1", prompt="First task", executor=executor_name)
    n2 = WorkflowNode(id="n2", prompt="Second task", executor=executor_name, depends_on=["n1"])
    graph = WorkflowGraph(version=1, nodes={"n1": n1, "n2": n2}, edges=[WorkflowEdge(source="n1", target="n2")])
    return WorkflowDefinition(id=workflow_id, name=workflow_id, graph=graph)


def _single_definition(workflow_id: str, executor_name: str, node_id: str) -> WorkflowDefinition:
    node = WorkflowNode(id=node_id, prompt="One task", executor=executor_name)
    graph = WorkflowGraph(version=1, nodes={node_id: node}, edges=[])
    return WorkflowDefinition(id=workflow_id, name=workflow_id, graph=graph)


def _engine_kernel(definition: WorkflowDefinition) -> ExecutionKernel:
    engine = DynamicWorkflowEngine()
    engine.register_definition(definition)
    return ExecutionKernel(engine=engine)


def test_replay_reconstructs_completed_run_on_a_fresh_engine(registry):
    """Restart after completion: new engine, state equals the live projection."""
    registry.register(DIGEST_EXECUTOR, executors_module.local_digest_executor)
    kernel = _engine_kernel(_chain_definition("wf_replay_done", DIGEST_EXECUTOR))
    run = kernel.start_run("wf_replay_done", initial_state={"topic": "recovery"}, mode="bot")
    final_run, waves = kernel.run_to_completion(run.run_id)
    assert final_run.status == WorkflowRunStatus.COMPLETED
    assert waves == 2

    events = kernel.engine.events.get_events(run.run_id)
    assert [e.event_type for e in events][:2] == ["workflow_started", "run_mode_selected"]
    log_before = list(kernel.engine.events.get_events())

    # --- simulated restart: a FRESH engine, state folded from the log only ---
    fresh_engine = DynamicWorkflowEngine()
    replayed_engine, replayed = replay_run(
        events,
        kernel.engine.get_definition("wf_replay_done"),
        engine=fresh_engine,
    )

    # Replay re-emits nothing — the shared append-only log is untouched.
    assert kernel.engine.events.get_events() == log_before
    assert [e.event_id for e in fresh_engine.events.get_events(run.run_id)] == [e.event_id for e in events]

    assert replayed_engine is fresh_engine
    assert fresh_engine.get_run(run.run_id) is replayed

    # Projection equality on everything the log journals.
    assert replayed.run_id == final_run.run_id
    assert replayed.workflow_id == final_run.workflow_id
    assert replayed.status == final_run.status == WorkflowRunStatus.COMPLETED
    assert replayed.graph_version == final_run.graph_version
    assert replayed.completed_nodes == final_run.completed_nodes == ["n1", "n2"]
    assert replayed.failed_nodes == final_run.failed_nodes == []
    assert replayed.node_states == final_run.node_states
    assert replayed.state == final_run.state
    assert replayed.metrics == final_run.metrics
    assert replayed.iteration_counts == final_run.iteration_counts
    assert replayed.metrics.get("execution_mode") == "bot"

    # Evidence folds from the event payloads (gap 10 fix) and lands equal to
    # the live graph; node output still comes from the caller's definition
    # snapshot (not journaled - disclosed in the replay module docstring).
    live_graph = kernel.engine.graphs[f"wf_replay_done:v{final_run.graph_version}"]
    replayed_graph = fresh_engine.graphs[f"wf_replay_done:v{replayed.graph_version}"]
    for nid in final_run.completed_nodes:
        assert replayed_graph.nodes[nid].evidence == live_graph.nodes[nid].evidence
        assert replayed_graph.nodes[nid].output == live_graph.nodes[nid].output
        assert replayed_graph.nodes[nid].evidence != []  # real digest evidence existed


def test_mid_run_replay_then_resume_completes_without_re_executing(registry):
    """Restart mid-run: partial state reconstructs, resume finishes the rest once."""
    calls: list[str] = []

    def recording_executor(node, run):
        calls.append(node.id)
        return {"status": "completed", "output": f"ran:{node.id}", "evidence": f"ev:{node.id}", "tokens_used": 0}

    registry.register("test.record", recording_executor)
    kernel = _engine_kernel(_chain_definition("wf_replay_resume", "test.record"))
    run = kernel.start_run("wf_replay_resume")

    first_wave = kernel.dispatch(run.run_id)
    assert first_wave.status == WorkflowRunStatus.RUNNING  # n2 still pending
    assert first_wave.completed_nodes == ["n1"]
    assert calls == ["n1"]

    events = kernel.engine.events.get_events(run.run_id)

    # --- simulated restart: recover a NEW kernel + engine from the log ---
    recovered_kernel, replayed = recover_run(kernel.engine.get_definition("wf_replay_resume"), events)

    assert replayed.run_id == run.run_id
    assert replayed.status == WorkflowRunStatus.RUNNING
    assert replayed.completed_nodes == ["n1"]
    assert replayed.failed_nodes == []
    assert replayed.node_states["n1"] == NodeStatus.SUCCEEDED
    assert replayed.node_states["n2"] == NodeStatus.PENDING
    assert replayed.state == first_wave.state
    assert recovered_kernel.engine is not kernel.engine  # a genuine restart

    # Resume: only the unfinished node executes; the finished one is not redone.
    resumed, waves = recovered_kernel.run_to_completion(replayed.run_id)
    assert waves == 1
    assert resumed.status == WorkflowRunStatus.COMPLETED
    assert resumed.completed_nodes == ["n1", "n2"]
    assert calls == ["n1", "n2"], "resume must not re-execute replayed nodes"


def test_replay_folds_mode_and_patch_events(registry):
    """``run_mode_selected`` and ``patch_committed`` fold: mode + graph version."""
    registry.register(DIGEST_EXECUTOR, executors_module.local_digest_executor)
    kernel = _engine_kernel(_chain_definition("wf_replay_patch", DIGEST_EXECUTOR))
    run = kernel.start_run("wf_replay_patch", mode="bot")
    first_wave = kernel.dispatch(run.run_id)
    assert first_wave.completed_nodes == ["n1"]

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
    log_before = list(kernel.engine.events.get_events())
    fresh_engine, replayed = replay_run(events, kernel.engine.get_definition("wf_replay_patch"))

    # Gap 6 root-cause fix: the committed patch is REBUILT and registered on
    # the fresh engine - and the rebuild still re-emits nothing.
    assert kernel.engine.events.get_events() == log_before
    assert "wf_replay_patch:v2" in fresh_engine.graphs

    assert replayed.metrics.get("execution_mode") == "bot"
    assert replayed.graph_version == 2
    assert len(replayed.patches_applied) == 1
    assert replayed.patches_applied[0].reason == "insert a verification node"
    # Patch-added node is seeded PENDING so a resumed dispatch could run it.
    assert replayed.node_states["check"] == NodeStatus.PENDING
    assert replayed.completed_nodes == ["n1"]
    assert replayed.status == WorkflowRunStatus.RUNNING


def test_replay_reflects_fail_closed_failed_run(registry):
    """A fail-closed run replays FAILED with its real failed nodes."""
    def raising_executor(node, run):
        raise KeyError("missing shard")

    registry.register("test.raise", raising_executor)
    kernel = _engine_kernel(_single_definition("wf_replay_failed", "test.raise", node_id="fragile"))
    run = kernel.start_run("wf_replay_failed")

    result = kernel.dispatch(run.run_id)
    assert result.status == WorkflowRunStatus.FAILED
    assert result.failed_nodes == ["fragile"]

    events = kernel.engine.events.get_events(run.run_id)
    assert any(e.event_type == "workflow_failed" for e in events), "fail-closed must be journaled"

    _, replayed = replay_run(events, kernel.engine.get_definition("wf_replay_failed"))

    assert replayed.status == WorkflowRunStatus.FAILED
    assert replayed.failed_nodes == ["fragile"]
    assert replayed.node_states["fragile"] == NodeStatus.FAILED
    assert replayed.completed_nodes == []


def test_replay_refuses_a_log_without_workflow_started():
    """Replay without a start event is refused honestly — never a made-up run."""
    definition = _chain_definition("wf_replay_no_start", DIGEST_EXECUTOR)

    with pytest.raises(ValueError, match="no 'workflow_started' event"):
        replay_run([], definition)

    # Even a non-empty log that never started a run is refused the same way.
    stray_events = [
        WorkflowEvent(workflow_run_id="ghost", event_type="node_completed", payload={"node_id": "n1"}),
    ]
    with pytest.raises(ValueError, match="no 'workflow_started' event"):
        replay_run(stray_events, definition)
