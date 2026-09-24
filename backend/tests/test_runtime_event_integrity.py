"""Gap 2/5/7/8/10/11 pins: event payload integrity of the workflow engine.

The older suites DISCLOSED these as engine gaps; this suite pins the FIXED
payload/fold behavior directly against ``alpha.workflow.runtime``:

- gap 2: the exception path journals under the SAME ``reason`` key as every
  other node failure - never a one-off ``error`` key;
- gap 5: ``workflow_started``/``workflow_completed`` state and event payloads
  are SNAPSHOTs, decoupled from the live run/graph objects;
- gap 7/8: approval events carry the per-node ``node_status``, and a denial
  records the gated node in ``failed_nodes`` (journaled in ``run.history``);
- gap 10: ``node_completed``/``node_failed`` carry ``evidence`` +
  ``iteration_counts`` so replay can rebuild them;
- gap 11: interim bounded-loop iterations journal ``node_iteration`` (node
  stays READY, NOT completed) while terminal stops journal ``node_completed``
  with ``stopped_by``.

Every test isolates ``AGENT_WORKSPACE_HOME`` to a per-test temp dir.
"""

from __future__ import annotations

import pytest

from alpha.orchestrator.replay import replay_run
from alpha.workflow.models import (
    LoopPolicy,
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    """AGENT_WORKSPACE_HOME points at a per-test temp dir (process-global env)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _start(
    workflow_id: str,
    *nodes: WorkflowNode,
    initial_state: dict | None = None,
) -> tuple[DynamicWorkflowEngine, WorkflowRun]:
    """A fresh engine + started run over one definition holding exactly ``nodes``."""
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(version=1, nodes={node.id: node for node in nodes}, edges=[])
    engine.register_definition(WorkflowDefinition(id=workflow_id, name=workflow_id, graph=graph))
    return engine, engine.start_run(workflow_id, initial_state=initial_state)


def _events_of_type(engine: DynamicWorkflowEngine, run_id: str, event_type: str) -> list:
    return [e for e in engine.events.get_events(run_id) if e.event_type == event_type]


# ------------------------------------------------------------------- gap 2


def test_exception_path_journals_the_unified_reason_key():
    """A raising runner fails the node under ``reason`` - no one-off ``error``."""
    engine, run = _start("wf_rt_reason", WorkflowNode(id="boom", prompt="Raises"))

    def raiser(node, _run):
        raise RuntimeError("kapow")

    engine.execute_step(run.run_id, node_runner=raiser)

    failed = _events_of_type(engine, run.run_id, "node_failed")
    assert len(failed) == 1
    payload = failed[0].payload
    assert payload["reason"] == "kapow"  # str(exc) verbatim
    assert "error" not in payload, "gap 2: unified reason key"
    # The SAME unified key lands in the node output dict.
    node = engine.graphs["wf_rt_reason:v1"].nodes["boom"]
    assert node.output["reason"] == "kapow"
    assert "error" not in node.output


# ------------------------------------------------------------------- gap 5


def test_logged_state_and_payloads_are_snapshots_not_live_references():
    """Started/completed state + payloads decouple from live run/graph objects."""
    engine, run = _start(
        "wf_rt_snap",
        WorkflowNode(id="solo", prompt="One step"),
        initial_state={"cfg": {"v": 1}},
    )

    started = engine.events.get_events(run.run_id)[0]
    assert started.event_type == "workflow_started"
    assert started.payload["state"] == {"cfg": {"v": 1}}
    assert started.payload["state"] is not run.state  # snapshot, not the live dict

    # Later mutations of the live state never rewrite what was journaled.
    run.state["cfg"]["v"] = 99
    run.state["extra"] = True
    assert started.payload["state"] == {"cfg": {"v": 1}}

    def runner(node, _run):
        return {"status": "completed", "output": "done", "evidence": "ev"}

    engine.execute_step(run.run_id, node_runner=runner)
    assert run.status == WorkflowRunStatus.COMPLETED

    completed = _events_of_type(engine, run.run_id, "workflow_completed")
    assert len(completed) == 1
    completed_state = completed[0].payload["state"]
    assert completed_state is not run.state
    iteration_payload = _events_of_type(engine, run.run_id, "node_completed")[0].payload["iteration_counts"]
    assert iteration_payload is not run.iteration_counts

    # Mutating the live objects after the fact cannot rewrite the log.
    run.state["after"] = "post-hoc"
    run.iteration_counts["x"] = 9
    assert "after" not in completed_state
    assert completed[0].payload["state"] == {"cfg": {"v": 99}, "extra": True}
    assert iteration_payload == {}


def test_failed_node_payload_snapshots_evidence():
    """The logged ``evidence`` list is a copy: later mutation cannot rewrite it."""
    engine, run = _start(
        "wf_rt_mapev",
        WorkflowNode(id="m", type=NodeType.MAP, config={"items_key": "items"}),
        initial_state={"items": ["a", "b"]},
    )

    def flaky(node, _run):
        if node.config.get("item") == "b":
            return {"status": "failed", "output": "second item exploded"}
        return {"status": "completed", "output": "ok", "evidence": "ev-a"}

    engine.execute_step(run.run_id, node_runner=flaky)

    failed = _events_of_type(engine, run.run_id, "node_failed")
    assert len(failed) == 1
    payload = failed[0].payload
    assert payload["evidence"] == ["map[0] item='a': ev-a"]  # gap 10 on failures
    assert payload["iteration_counts"] == dict(run.iteration_counts)
    assert "error" not in payload
    assert "map item 1: node_runner reported failure" in payload["reason"]

    # gap 5: the logged evidence is a SNAPSHOT.
    engine.graphs["wf_rt_mapev:v1"].nodes["m"].evidence.append("post-hoc")
    assert failed[0].payload["evidence"] == ["map[0] item='a': ev-a"]


# ------------------------------------------------------------------- gap 7/8


def test_approval_request_and_denial_carry_node_status_and_failed_nodes():
    """Approval payloads journal node_status; denial records a REAL failed node."""
    engine, run = _start("wf_rt_approval", WorkflowNode(id="gate", prompt="Deploy", requires_approval=True))

    def runner(node, _run):
        return {"status": "completed", "output": "deployed", "evidence": "deploy log"}

    engine.execute_step(run.run_id, node_runner=runner)
    assert run.status == WorkflowRunStatus.WAITING_APPROVAL
    assert run.waiting_nodes == ["gate"]

    requested = _events_of_type(engine, run.run_id, "approval_requested")
    assert len(requested) == 1
    assert requested[0].payload["node_id"] == "gate"
    assert requested[0].payload["node_status"] == NodeStatus.WAITING.value  # gap 7

    denied = engine.resolve_approval(run.run_id, "gate", approved=False, feedback="not now")
    assert denied.status == WorkflowRunStatus.FAILED
    assert denied.node_states["gate"] == NodeStatus.FAILED
    assert denied.failed_nodes == ["gate"]  # gap 8
    assert denied.waiting_nodes == []
    assert [(h["from"], h["to"]) for h in denied.history][-1] == ("waiting_approval", "failed")
    assert "Human rejected node 'gate'" in denied.history[-1]["reason"]

    denial = _events_of_type(engine, run.run_id, "approval_denied")
    assert len(denial) == 1
    assert denial[0].payload["node_status"] == NodeStatus.FAILED.value  # gap 7


def test_approval_grant_carries_ready_node_status_without_failing_the_node():
    """A grant journals node_status=READY and leaves failed_nodes untouched."""
    engine, run = _start("wf_rt_grant", WorkflowNode(id="gate", prompt="Deploy", requires_approval=True))

    def runner(node, _run):
        return {"status": "completed", "output": "deployed", "evidence": "deploy log"}

    engine.execute_step(run.run_id, node_runner=runner)
    granted = engine.resolve_approval(run.run_id, "gate", approved=True, feedback="lgtm")
    assert granted.status == WorkflowRunStatus.RUNNING
    assert granted.node_states["gate"] == NodeStatus.READY
    assert granted.failed_nodes == []  # gap 8: only DENIAL fails the node
    assert granted.waiting_nodes == []
    assert granted.history[-1]["from"] == "waiting_approval" and granted.history[-1]["to"] == "running"

    grant = _events_of_type(engine, run.run_id, "approval_granted")
    assert len(grant) == 1
    assert grant[0].payload["node_status"] == NodeStatus.READY.value  # gap 7

    # After the grant the gate really executes and completes.
    engine.execute_step(run.run_id, node_runner=runner)
    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.completed_nodes == ["gate"]


# ----------------------------------------------------------- gaps 10 + 11


def test_terminal_loop_stop_journals_evidence_counts_and_stopped_by():
    """Terminal loop stop = a real node_completed with evidence/counts/stopped_by."""
    engine, run = _start(
        "wf_rt_loop_max",
        WorkflowNode(id="it", prompt="Iterate", loop_policy=LoopPolicy(max_iterations=1)),
    )

    def runner(node, _run):
        return {"status": "completed", "output": "refined", "evidence": "iteration evidence"}

    engine.execute_step(run.run_id, node_runner=runner)

    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.completed_nodes == ["it"]
    assert run.iteration_counts == {"it": 1}
    completed = _events_of_type(engine, run.run_id, "node_completed")
    assert len(completed) == 1
    payload = completed[0].payload
    assert payload["stopped_by"] == "loop_max_iterations"  # gap 11
    assert payload["evidence"] == ["iteration evidence"]  # gap 10
    assert payload["iteration_counts"] == {"it": 1}  # gap 10
    assert _events_of_type(engine, run.run_id, "node_iteration") == []


def test_interim_loop_iteration_is_node_iteration_and_replays_as_ready():
    """Interim iterations journal node_iteration; replay keeps the node READY."""
    engine, run = _start(
        "wf_rt_interim",
        WorkflowNode(
            id="think",
            prompt="Reflect",
            loop_policy=LoopPolicy(max_iterations=3, stop_condition="state.done == true"),
        ),
    )

    def runner(node, _run):
        return {"status": "completed", "output": "reflection", "evidence": "ev"}

    engine.execute_step(run.run_id, node_runner=runner)

    # Interim iteration: NOT completed, node stays READY (gap 11).
    assert run.status == WorkflowRunStatus.RUNNING
    assert run.node_states["think"] == NodeStatus.READY
    assert run.completed_nodes == []
    assert run.iteration_counts == {"think": 1}
    kinds = [e.event_type for e in engine.events.get_events(run.run_id)]
    assert "node_iteration" in kinds
    assert "node_completed" not in kinds
    iteration = _events_of_type(engine, run.run_id, "node_iteration")
    assert len(iteration) == 1
    assert iteration[0].payload["evidence"] == ["ev"]  # gap 10
    assert iteration[0].payload["iteration_counts"] == {"think": 1}  # gap 10

    # Mid-loop replay: READY, deliberately NOT completed; zero re-emission.
    log_before = list(engine.events.get_events())
    replayed_engine, replayed = replay_run(
        engine.events.get_events(run.run_id),
        engine.get_definition("wf_rt_interim"),
    )
    assert engine.events.get_events() == log_before, "replay must not re-emit"
    assert replayed.node_states["think"] == NodeStatus.READY  # gap 11 fold
    assert replayed.completed_nodes == []
    assert replayed.iteration_counts == {"think": 1}  # gap 10 fold
    assert replayed_engine.graphs["wf_rt_interim:v1"].nodes["think"].evidence == ["ev"]

    # Terminal: the stop condition makes it a TRUE completion (gap 11).
    run.state["done"] = True
    engine.execute_step(run.run_id, node_runner=runner)
    assert run.status == WorkflowRunStatus.COMPLETED
    assert run.completed_nodes == ["think"]
    terminal = _events_of_type(engine, run.run_id, "node_completed")
    assert len(terminal) == 1
    assert terminal[0].payload["stopped_by"] == "loop_stop_condition"
