"""Tests for workflow forking, time travel, and dry-run simulation.

Pins the two properties that make these capabilities safe to expose:

- **A simulation is never reported as execution.** Every simulated result is
  labelled, runs on a throwaway engine, and leaves the caller's definitions,
  runs, durable sink and budgets untouched.
- **A fork never mutates its source**, and it inherits the work that had already
  happened at the fork point rather than repeating it.

Also pins the honesty failures: a fork point that cannot be replayed, an unknown
event id, an out-of-range index, and a graph that parks are all reported rather
than papered over.
"""

from __future__ import annotations

import pytest

from alpha.workflow.events import WorkflowEventDispatcher
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.time_travel import (
    SIMULATION_LABEL,
    ForkError,
    fork_run,
    recording_executor,
    run_history,
    run_report,
    simulate_run,
)


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _ok(node, run):
    return {"status": "completed", "output": {"node": node.id}, "evidence": f"ran {node.id}", "tokens_used": 5}


def _chain(length: int = 3) -> WorkflowGraph:
    """A linear chain ``n0 -> n1 -> ...`` so a fork point is unambiguous."""
    nodes = {f"n{i}": WorkflowNode(id=f"n{i}", prompt=f"step {i}") for i in range(length)}
    edges = [WorkflowEdge(source=f"n{i - 1}", target=f"n{i}") for i in range(1, length)]
    return WorkflowGraph(nodes=nodes, edges=edges)


def _engine_with_chain(length: int = 3) -> tuple[DynamicWorkflowEngine, str]:
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    engine.register_definition(WorkflowDefinition(id="chain", name="chain", graph=_chain(length), policies={}))
    return engine, "chain"


# ------------------------------------------------------------------- history


def test_run_history_is_ordered_and_carries_node_and_reason():
    engine, _ = _engine_with_chain()
    run = engine.start_run("chain")
    engine.execute_step(run.run_id, node_runner=_ok)

    history = run_history(engine, run.run_id)
    assert history, "a started run must have history"
    assert [entry.index for entry in history] == list(range(1, len(history) + 1))
    assert history[0].event_type == "workflow_started"
    node_entries = [entry for entry in history if entry.node_id]
    assert node_entries, "node events must name their node"
    assert all(entry.to_dict()["index"] > 0 for entry in history)


def test_run_history_of_an_unknown_run_is_refused():
    engine, _ = _engine_with_chain()
    with pytest.raises(ForkError, match="not found"):
        run_history(engine, "run_missing")


# ---------------------------------------------------------------------- forks


def test_fork_inherits_completed_work_and_resumes_after_it():
    """The point of forking: already-done work is not performed a second time."""
    engine, _ = _engine_with_chain(3)
    source = engine.start_run("chain")
    engine.execute_step(source.run_id, node_runner=_ok)  # completes n0
    assert source.completed_nodes == ["n0"]

    history = run_history(engine, source.run_id)
    fork_point = next(entry for entry in history if entry.event_type == "node_completed")
    result = fork_run(engine, source.run_id, at_event_id=fork_point.event_id)

    assert result.run.run_id != source.run_id, "a fork must be a distinct run"
    assert result.inherited_completed_nodes == ["n0"]
    assert result.run.node_states["n0"] == NodeStatus.SUCCEEDED
    assert result.run.node_states["n1"] == NodeStatus.PENDING
    assert result.run.status is WorkflowRunStatus.RUNNING

    # Drive the fork to completion and confirm the inherited node is not redone.
    performed: list[str] = []
    for _ in range(4):
        engine.execute_step(result.run.run_id, node_runner=lambda node, run: performed.append(node.id) or _ok(node, run))
    assert result.run.status is WorkflowRunStatus.COMPLETED
    assert "n0" not in performed, "the fork repeated work the source had already done"
    assert sorted(performed) == ["n1", "n2"]


def test_a_fork_never_mutates_its_source():
    engine, _ = _engine_with_chain(3)
    source = engine.start_run("chain")
    engine.execute_step(source.run_id, node_runner=_ok)
    before_status = source.status
    before_completed = list(source.completed_nodes)
    before_history = len(engine.events.get_events(source.run_id))

    fork_run(engine, source.run_id)

    assert source.status is before_status
    assert source.completed_nodes == before_completed
    assert len(engine.events.get_events(source.run_id)) == before_history, "the source's log must not grow"


def test_reset_completed_nodes_is_opt_in_and_disclosed():
    engine, _ = _engine_with_chain(3)
    source = engine.start_run("chain")
    engine.execute_step(source.run_id, node_runner=_ok)

    result = fork_run(engine, source.run_id, reset_completed_nodes=True)

    assert result.inherited_completed_nodes == []
    assert result.run.node_states["n0"] == NodeStatus.PENDING
    assert any("idempotency keys cannot protect them" in note for note in result.notes), "repeating a side effect must be disclosed, not silently allowed"


def test_forking_from_the_whole_history_carries_every_completion():
    engine, _ = _engine_with_chain(3)
    source = engine.start_run("chain")
    engine.execute_step(source.run_id, node_runner=_ok)
    engine.execute_step(source.run_id, node_runner=_ok)
    assert source.completed_nodes == ["n0", "n1"]

    result = fork_run(engine, source.run_id)
    assert result.inherited_completed_nodes == ["n0", "n1"]
    assert result.run.node_states["n2"] == NodeStatus.PENDING


def test_fork_rejects_an_unknown_event_id():
    engine, _ = _engine_with_chain(2)
    source = engine.start_run("chain")
    with pytest.raises(ForkError, match="not in the source run"):
        fork_run(engine, source.run_id, at_event_id="event_does_not_exist")


def test_fork_rejects_an_out_of_range_index():
    engine, _ = _engine_with_chain(2)
    source = engine.start_run("chain")
    with pytest.raises(ForkError, match="outside the source run"):
        fork_run(engine, source.run_id, at_index=9999)


def test_fork_rejects_a_zero_index_because_indexes_are_one_based():
    engine, _ = _engine_with_chain(2)
    source = engine.start_run("chain")
    with pytest.raises(ForkError, match="outside the source run"):
        fork_run(engine, source.run_id, at_index=0)


def test_fork_rejects_both_an_event_id_and_an_index():
    engine, _ = _engine_with_chain(2)
    source = engine.start_run("chain")
    with pytest.raises(ForkError, match="not both"):
        fork_run(engine, source.run_id, at_event_id="x", at_index=1)


def test_fork_of_an_unknown_run_is_refused():
    engine, _ = _engine_with_chain(2)
    with pytest.raises(ForkError, match="not found"):
        fork_run(engine, "run_nope")


def test_fork_gives_each_fork_its_own_graph_and_workflow_id():
    """Two forks must never share mutable graph state."""
    engine, _ = _engine_with_chain(3)
    source = engine.start_run("chain")
    engine.execute_step(source.run_id, node_runner=_ok)

    first = fork_run(engine, source.run_id)
    second = fork_run(engine, source.run_id)

    assert first.run.workflow_id != second.run.workflow_id
    assert first.definition.id != second.definition.id
    engine.execute_step(first.run.run_id, node_runner=_ok)
    # Mutating one fork's graph must not be visible in the other.
    assert first.run.graph_version == second.run.graph_version
    assert second.run.node_states["n1"] == NodeStatus.PENDING


# ----------------------------------------------------------------- simulation


def test_simulation_reports_the_nodes_a_graph_would_reach():
    engine, _ = _engine_with_chain(3)
    result = simulate_run(engine, "chain")

    assert result.simulated is True
    assert result.execution_label == SIMULATION_LABEL
    assert result.status == "completed"
    assert result.nodes_visited == ["n0", "n1", "n2"]
    assert result.waves >= 1


def test_simulation_labels_every_node_as_simulated_and_charges_no_tokens():
    engine, _ = _engine_with_chain(2)
    # The simulation itself must be labelled, and so must a single recorded node.
    simulation = simulate_run(engine, "chain")
    assert simulation.execution_label == SIMULATION_LABEL
    assert all(status == NodeStatus.SUCCEEDED.value for status in simulation.node_outcomes.values())

    recorded = recording_executor(engine.get_definition("chain").graph.nodes["n0"], engine.start_run("chain"))
    assert recorded["output"]["simulated"] is True
    assert recorded["tokens_used"] == 0
    assert SIMULATION_LABEL in recorded["evidence"]
    assert "no work was performed" in recorded["evidence"]


def test_simulation_does_not_touch_the_callers_engine():
    engine, _ = _engine_with_chain(3)
    before_runs = set(engine.runs)
    before_graph_version = engine.get_definition("chain").graph.version

    simulate_run(engine, "chain")

    assert set(engine.runs) == before_runs, "a dry run must not create a run on the live engine"
    assert engine.get_definition("chain").graph.version == before_graph_version
    assert not [e for e in engine.events.get_events() if e.event_type == "node_started"]


def test_simulation_never_claims_acceptance():
    """The dry-run payload must carry no acceptance verdict at all."""
    engine, _ = _engine_with_chain(2)
    payload = simulate_run(engine, "chain").to_dict()
    assert payload["simulated"] is True
    assert payload["execution_label"] == SIMULATION_LABEL
    assert "acceptance_passed" not in payload
    assert "verified" not in payload


def test_simulation_of_a_graph_with_a_gate_reports_that_it_parked():
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    graph = WorkflowGraph(
        nodes={
            "gate": WorkflowNode(id="gate", requires_approval=True, prompt="needs a human"),
            "after": WorkflowNode(id="after", prompt="then this", depends_on=["gate"]),
        },
        edges=[],
    )
    engine.register_definition(WorkflowDefinition(id="gated", name="gated", graph=graph, policies={}))

    result = simulate_run(engine, "gated")

    assert result.status == "waiting_approval"
    assert "after" not in result.nodes_visited
    assert any("would wait there for an approval" in note for note in result.notes)


def test_simulation_of_an_unknown_workflow_is_refused():
    engine, _ = _engine_with_chain(2)
    with pytest.raises(ForkError, match="not registered"):
        simulate_run(engine, "does_not_exist")


def test_simulation_rejects_a_hostile_wave_ceiling():
    engine, _ = _engine_with_chain(2)
    with pytest.raises(ValueError, match="max_waves"):
        simulate_run(engine, "chain", max_waves=0)
    with pytest.raises(ValueError, match="max_waves"):
        simulate_run(engine, "chain", max_waves=10_000)


def test_simulation_of_a_conditional_graph_follows_the_evaluated_branch():
    """Branch selection lives on the EDGE condition, not on ``depends_on``.

    Two nodes that merely ``depends_on`` the same parent are both legitimately
    ready, so this graph expresses the branch the way the engine actually
    discriminates it: conditional edges out of the router.

    The losing arm is still *visited* — the scheduler reached a decision about
    it — so the assertion is that it was proven-SKIPPED rather than executed.
    """
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    graph = WorkflowGraph(
        nodes={
            "check": WorkflowNode(id="check", type=NodeType.CONDITION, condition="state.score > 0.5"),
            "yes": WorkflowNode(id="yes", prompt="taken", write_scope=["yes"]),
            "no": WorkflowNode(id="no", prompt="not taken", write_scope=["no"]),
        },
        edges=[
            WorkflowEdge(source="check", target="yes", condition="state.score > 0.5"),
            WorkflowEdge(source="check", target="no", condition="state.score <= 0.5"),
        ],
    )
    engine.register_definition(WorkflowDefinition(id="branch", name="branch", graph=graph, policies={}))

    taken = simulate_run(engine, "branch", initial_state={"score": 0.9})
    assert taken.node_outcomes["yes"] == NodeStatus.SUCCEEDED.value
    assert taken.node_outcomes["no"] == NodeStatus.SKIPPED.value, "the losing arm must be proven-skipped, not executed"

    declined = simulate_run(engine, "branch", initial_state={"score": 0.1})
    assert declined.node_outcomes["no"] == NodeStatus.SUCCEEDED.value
    assert declined.node_outcomes["yes"] == NodeStatus.SKIPPED.value


# --------------------------------------------------------------------- report


def test_run_report_combines_history_observability_and_provenance():
    engine, _ = _engine_with_chain(2)
    run = engine.start_run("chain")
    engine.execute_step(run.run_id, node_runner=_ok)

    report = run_report(engine, run.run_id)

    assert report["run_id"] == run.run_id
    assert report["status"] == "running"
    assert report["history_depth"] > 0
    assert report["first_event"]["event_type"] == "workflow_started"
    assert report["observability"]["nodes_completed"] == 1
    assert report["observability"]["measured_executions"] == 1
    assert report["observability"]["critical_path"]["path"]
    assert "acceptance_passed" not in report["observability"]
    assert "durability" in report


def test_run_report_of_an_unknown_run_is_refused():
    engine, _ = _engine_with_chain(2)
    with pytest.raises(ForkError, match="not found"):
        run_report(engine, "run_nope")
