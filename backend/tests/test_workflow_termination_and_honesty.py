"""Regression pins for the dynamic-workflow engine's termination and honesty gaps.

Every test in this file reproduces a defect that was confirmed by driving
``alpha.workflow.runtime`` directly, and each one FAILS against the pre-fix
behaviour:

- ``test_diamond_branch_reaches_a_terminal_state`` -- the flagship
  ``a -> condition -> {b, c} -> j`` fork NEVER terminated.  The engine proved the
  losing arm ``c`` was a skip and marked it ``SKIPPED``, but never recorded it as
  a discharged dependency, so the join ``j`` was permanently unschedulable and
  the run spun in the deadlock-recovery path forever, adding a
  ``gather_evidence_*`` node per step.  A conditional branch is THE reason the
  scheduler exists.
- ``test_stagnation_recovery_has_a_hard_ceiling`` -- any graph that cannot make
  progress (an unschedulable target) had no ceiling on remediation patches, so
  ``execute_step`` could be called forever with the run still ``running`` and the
  graph growing without bound.  A workflow that re-enters its own step forever is
  a hang, not a slow run.
- ``test_definition_is_validated_before_any_run_exists`` -- malformed graphs
  (dangling edge, unknown ``depends_on``, dependency cycle, unbounded self-loop)
  were accepted.  A dangling edge was silently dropped and the run reported
  ``completed``; the rest livelocked.  Validation now happens before a run
  exists, so no side effect can precede it.
- ``test_a_retried_idempotent_step_does_not_repeat_its_effect`` -- the engine
  journalled an ``idempotency_key`` but never consumed it, so a retried
  "send the mail" node repeated its effect on every attempt.
- ``test_budget_exhaustion_replays_to_the_same_outcome`` -- budget exhaustion
  emitted no terminal event, so a replay of the same log reported a DIFFERENT
  outcome than the live run: the node-budget case replayed as still ``running``
  (a terminated run reading as in-flight) and the pre-wave case replayed as
  plain ``failed`` instead of ``budget_exhausted``.
- ``test_a_failed_step_never_reads_as_a_completed_run`` -- the headline
  invariant, checked across graph shapes, failure positions and runner failure
  modes.
"""

from __future__ import annotations

import pytest

from alpha.orchestrator.replay import replay_run
from alpha.workflow.models import (
    EdgeMode,
    LoopPolicy,
    NodeStatus,
    NodeType,
    PatchOperation,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import (
    STAGNATION_RECOVERY_LIMIT,
    DynamicWorkflowEngine,
    WorkflowDefinitionError,
    validate_workflow_graph,
)

TERMINAL = {
    WorkflowRunStatus.COMPLETED,
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.CANCELLED,
    WorkflowRunStatus.BUDGET_EXHAUSTED,
    WorkflowRunStatus.ABORTED,
}


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _ok_runner(calls: list[str], fail_ids: frozenset[str] = frozenset(), mode: str = "ok"):
    def runner(node, run):
        calls.append(node.id)
        if node.id in fail_ids:
            if mode == "raise":
                raise RuntimeError(f"boom in {node.id}")
            if mode == "no_evidence":
                return {"status": "completed", "output": "x", "evidence": "", "tokens_used": 1}
            return {"status": "failed", "output": f"failed {node.id}", "evidence": "", "tokens_used": 1}
        return {"status": "completed", "output": f"ran {node.id}", "evidence": f"e:{node.id}", "tokens_used": 1}

    return runner


def _drive(engine, run, runner, ceiling: int = 25):
    """Step until terminal or ``ceiling``; return (run, steps, node_counts)."""
    counts = [len(engine._run_graph_for(run).nodes)]
    for step in range(1, ceiling + 1):
        run = engine.execute_step(run.run_id, node_runner=runner)
        counts.append(len(engine._run_graph_for(run).nodes))
        if run.status in TERMINAL:
            return run, step, counts
    return run, ceiling, counts


def _diamond_graph(loop: bool = False) -> WorkflowGraph:
    """a -> condition -> {b, c} -> j : the conditional fork with a join."""
    policy = {"loop_policy": LoopPolicy(max_iterations=2)} if loop else {}
    nodes = {
        "a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a", **policy),
        "d": WorkflowNode(id="d", type=NodeType.CONDITION, condition="1 > 0"),
        "b": WorkflowNode(id="b", type=NodeType.TOOL, prompt="b", **policy),
        "c": WorkflowNode(id="c", type=NodeType.TOOL, prompt="c", **policy),
        "j": WorkflowNode(id="j", type=NodeType.TOOL, prompt="j", **policy),
    }
    edges = [
        WorkflowEdge(source="a", target="d"),
        WorkflowEdge(source="d", target="b", condition="state.d_result == true"),
        WorkflowEdge(source="d", target="c", condition="state.d_result == false"),
        WorkflowEdge(source="b", target="j"),
        WorkflowEdge(source="c", target="j"),
    ]
    return WorkflowGraph(version=1, nodes=nodes, edges=edges)


# ------------------------------------------------------------- diamond fork


def test_diamond_branch_reaches_a_terminal_state():
    """The conditional fork with a join completes instead of livelocking."""
    engine = DynamicWorkflowEngine()
    engine.register_definition(WorkflowDefinition(id="wf_diamond", name="diamond", graph=_diamond_graph()))
    run = engine.start_run("wf_diamond")
    calls: list[str] = []

    run, steps, counts = _drive(engine, run, _ok_runner(calls), ceiling=10)

    assert run.status == WorkflowRunStatus.COMPLETED, (
        f"a branching workflow must terminate; it was still {run.status.value} after {steps} steps "
        f"with graph sizes {counts}"
    )
    # The losing arm is a proven skip, and the join really ran.
    assert run.node_states["c"] == NodeStatus.SKIPPED
    assert "c" not in run.completed_nodes, "a skipped arm must not be reported as executed work"
    assert "j" in run.completed_nodes, "the join must run once both arms are discharged"
    # No remediation patch was needed: the graph never grew.
    assert counts == [5, 5, 5, 5, 5], f"the run must not have grown its graph: {counts}"
    assert run.patches_applied == []


def test_diamond_branch_completes_through_the_kernel():
    """The same fork through the kernel's wave driver, not just the engine."""
    from alpha.orchestrator.loop import ExecutionKernel

    kernel = ExecutionKernel(engine=DynamicWorkflowEngine())
    kernel.engine.register_definition(WorkflowDefinition(id="wf_diamond_k", name="diamond", graph=_diamond_graph()))
    run = kernel.start_run("wf_diamond_k")
    calls: list[str] = []
    final, waves = kernel.run_to_completion(run.run_id, max_waves=12, node_runner=_ok_runner(calls))

    assert final.status == WorkflowRunStatus.COMPLETED
    assert "j" in final.completed_nodes
    assert waves <= 6


# ------------------------------------------------- bounded recovery / no hang


def test_stagnation_recovery_has_a_hard_ceiling():
    """A graph that cannot progress ends FAILED with the real attempt count.

    The target node depends on a node the test suspended, so no wave can ever
    schedule it.  Before the fix each step committed another
    ``gather_evidence_*`` remediation patch against the same target and the run
    stayed ``running`` forever.
    """
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        WorkflowDefinition(
            id="wf_stuck",
            name="stuck",
            graph=WorkflowGraph(
                version=1,
                nodes={
                    "held": WorkflowNode(id="held", type=NodeType.TOOL, prompt="never schedulable"),
                    "stuck": WorkflowNode(id="stuck", type=NodeType.TOOL, prompt="blocked", depends_on=["held"]),
                },
                edges=[],
            ),
        )
    )
    run = engine.start_run("wf_stuck")
    run.node_states["held"] = NodeStatus.SUSPENDED
    calls: list[str] = []

    run, steps, counts = _drive(engine, run, _ok_runner(calls), ceiling=40)

    assert run.status == WorkflowRunStatus.FAILED, (
        f"an unschedulable run must terminate; it was still {run.status.value} after {steps} steps, "
        f"graph sizes {counts}"
    )
    reason = run.history[-1]["reason"]
    assert "stagnation recovery exhausted" in reason
    assert f"targeting node 'stuck'" in reason
    assert f"limit {STAGNATION_RECOVERY_LIMIT}" in reason
    # Bounded: exactly the ceiling of remediation patches, then an honest stop.
    # The graph grew by one node per committed patch and no further, which is
    # the whole point: the pre-fix run grew on every step without limit.
    assert len(run.patches_applied) == STAGNATION_RECOVERY_LIMIT
    assert counts[-1] == 2 + STAGNATION_RECOVERY_LIMIT
    exhausted = [e for e in engine.events.get_events(run.run_id) if e.event_type == "stagnation_recovery_exhausted"]
    assert len(exhausted) == 1
    assert exhausted[0].payload["attempts"] == STAGNATION_RECOVERY_LIMIT
    assert len([e for e in engine.events.get_events(run.run_id) if e.event_type == "workflow_failed"]) == 1


def test_stagnation_ceiling_survives_replay():
    """A replayed run reaches the same ceiling instead of re-entering recovery."""
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        WorkflowDefinition(
            id="wf_stuck_replay",
            name="stuck",
            graph=WorkflowGraph(
                version=1,
                nodes={
                    "held": WorkflowNode(id="held", type=NodeType.TOOL, prompt="held"),
                    "stuck": WorkflowNode(id="stuck", type=NodeType.TOOL, prompt="blocked", depends_on=["held"]),
                },
                edges=[],
            ),
        )
    )
    run = engine.start_run("wf_stuck_replay")
    run.node_states["held"] = NodeStatus.SUSPENDED
    run, _steps, _counts = _drive(engine, run, _ok_runner([]), ceiling=40)
    assert run.status == WorkflowRunStatus.FAILED

    _replayed_engine, replayed = replay_run(engine.events.get_events(run.run_id), engine.get_definition("wf_stuck_replay"))
    assert replayed.status == run.status
    assert replayed.metrics.get("stagnation_recovery_attempts") == STAGNATION_RECOVERY_LIMIT


# ----------------------------------------------------- definition validation


@pytest.mark.parametrize(
    ("label", "graph", "needle"),
    [
        (
            "dangling_edge_target",
            WorkflowGraph(
                version=1,
                nodes={"a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a")},
                edges=[WorkflowEdge(source="a", target="ghost")],
            ),
            "unknown target node 'ghost'",
        ),
        (
            "dangling_edge_source",
            WorkflowGraph(
                version=1,
                nodes={"a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a")},
                edges=[WorkflowEdge(source="ghost", target="a")],
            ),
            "unknown source node 'ghost'",
        ),
        (
            "unknown_depends_on",
            WorkflowGraph(
                version=1,
                nodes={"a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a", depends_on=["ghost"])},
                edges=[],
            ),
            "depends on unknown node 'ghost'",
        ),
        (
            "dependency_cycle",
            WorkflowGraph(
                version=1,
                nodes={
                    "a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a", depends_on=["b"]),
                    "b": WorkflowNode(id="b", type=NodeType.TOOL, prompt="b", depends_on=["a"]),
                },
                edges=[],
            ),
            "dependency cycle",
        ),
        (
            "edge_cycle",
            WorkflowGraph(
                version=1,
                nodes={
                    "a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a"),
                    "b": WorkflowNode(id="b", type=NodeType.TOOL, prompt="b"),
                },
                edges=[WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="a")],
            ),
            "dependency cycle",
        ),
        (
            "unbounded_self_loop",
            WorkflowGraph(
                version=1,
                nodes={"a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a")},
                edges=[WorkflowEdge(source="a", target="a")],
            ),
            "is its own successor",
        ),
        (
            "bounded_self_loop",
            WorkflowGraph(
                version=1,
                nodes={"a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a", loop_policy=LoopPolicy(max_iterations=3))},
                edges=[WorkflowEdge(source="a", target="a")],
            ),
            "is its own successor",
        ),
        (
            "empty_graph",
            WorkflowGraph(version=1, nodes={}, edges=[]),
            "at least one node",
        ),
        (
            "key_id_mismatch",
            WorkflowGraph(version=1, nodes={"wrong_key": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a")}, edges=[]),
            "does not match node id",
        ),
    ],
)
def test_definition_is_validated_before_any_run_exists(label, graph, needle):
    """A malformed definition is refused at the first executable boundary.

    Before the fix these were accepted, and the defect only surfaced once
    execution was underway: a dangling edge was silently dropped and the run
    reported ``completed``; the cycles drove the unbounded recovery livelock.
    """
    with pytest.raises(WorkflowDefinitionError) as excinfo:
        validate_workflow_graph(graph, workflow_id=label)
    assert needle in str(excinfo.value)

    engine = DynamicWorkflowEngine()
    engine.register_definition(WorkflowDefinition(id=label, name=label, graph=graph))
    with pytest.raises(WorkflowDefinitionError):
        engine.start_run(label)
    # No run exists, so no node can have performed a side effect and no
    # ``workflow_started`` event was journaled for it.
    assert engine.runs == {}
    assert [e for e in engine.events.get_events() if e.payload.get("workflow_id") == label] == []


def test_a_bounded_loop_node_is_still_accepted():
    """The guards are about UNBOUNDED re-entry, not loops in general.

    Bounded re-entry is expressed by ``loop_policy`` on a node with no incoming
    edge (it returns to READY via ``node_iteration`` and is re-admitted).  That
    shape must keep working, and must reach the iteration bound.
    """
    graph = WorkflowGraph(
        version=1,
        nodes={"a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a", loop_policy=LoopPolicy(max_iterations=3))},
        edges=[],
    )
    validate_workflow_graph(graph, workflow_id="wf_bounded_loop")

    engine = DynamicWorkflowEngine()
    engine.register_definition(WorkflowDefinition(id="wf_bounded_loop", name="ok", graph=graph))
    run = engine.start_run("wf_bounded_loop")
    calls: list[str] = []
    run, _steps, _counts = _drive(engine, run, _ok_runner(calls), ceiling=10)
    assert run.status == WorkflowRunStatus.COMPLETED
    assert len(calls) == 3, f"the loop must stop at its bound of 3, not spin: {calls}"


# ------------------------------------------------- side-effect idempotency


def test_a_retried_idempotent_step_does_not_repeat_its_effect():
    """A declared ``idempotency_key`` suppresses the repeated side effect.

    Before the fix the key was journalled on every attempt and never consumed,
    so the ``RetryPolicy`` ran the effect once per attempt.
    """
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        WorkflowDefinition(
            id="wf_idem",
            name="charge once",
            graph=WorkflowGraph(
                version=1,
                nodes={"pay": WorkflowNode(id="pay", type=NodeType.TOOL, prompt="charge the card", idempotency_key="order-42")},
                edges=[],
            ),
        )
    )
    run = engine.start_run("wf_idem")
    charges: list[str] = []

    def charge(node, run):
        charges.append(node.id)
        return {"status": "completed", "output": f"charge-{len(charges)}", "evidence": f"receipt-{len(charges)}", "tokens_used": 1}

    run = engine.execute_step(run.run_id, node_runner=charge)
    assert charges == ["pay"], "the first attempt must charge once"
    recorded = run.metrics["completed_idempotency_keys"]
    assert len(recorded) == 1, "the completed attempt key must be recorded, not just journalled"

    # A runtime replan reopens the same node: the effect must NOT happen twice.
    engine.apply_patch(
        run.run_id,
        WorkflowPatch(
            workflow_run_id=run.run_id,
            base_graph_version=run.graph_version,
            reason="retry after transient error",
            operations=[PatchOperation(op="retry_node", args={"node_id": "pay"})],
        ),
    )
    run.status = WorkflowRunStatus.RUNNING
    run = engine.execute_step(run.run_id, node_runner=charge)

    assert charges == ["pay"], f"a retried idempotent step repeated its side effect: {charges}"
    assert run.status == WorkflowRunStatus.COMPLETED
    deduped = [e for e in engine.events.get_events(run.run_id) if e.event_type == "node_deduplicated"]
    assert len(deduped) == 1
    assert deduped[0].payload["declared_key"] == "order-42"
    # The deduplicated attempt still reports real evidence, so the completion
    # gate is satisfied by the ORIGINAL receipt, not by nothing.
    assert any("idempotency key" in e for e in engine._run_graph_for(run).nodes["pay"].evidence)


def test_retry_attempts_are_each_journaled():
    """Every runner attempt is journaled, so a duplicate is observable."""
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        WorkflowDefinition(
            id="wf_retry_journal",
            name="flaky",
            graph=WorkflowGraph(version=1, nodes={"t": WorkflowNode(id="t", type=NodeType.TOOL, prompt="t")}, edges=[]),
        )
    )
    run = engine.start_run("wf_retry_journal")

    def flaky(node, run):
        return {"status": "failed", "output": "transient upstream error", "evidence": "", "tokens_used": 0}

    run = engine.execute_step(run.run_id, node_runner=flaky)
    attempts = [e for e in engine.events.get_events(run.run_id) if e.event_type == "node_attempt_started"]
    assert [a.payload["attempt"] for a in attempts] == [1, 2, 3]
    assert all(a.payload["of_attempts"] == 3 for a in attempts)
    assert run.status == WorkflowRunStatus.FAILED


# --------------------------------------------------------- replay fidelity


def test_budget_exhaustion_replays_to_the_same_outcome():
    """Both budget shapes replay to the live run's real terminal status.

    Before the fix the node-budget case journalled only ``node_failed``, so a
    replay reported the run as still ``running`` -- a terminated run reading as
    in-flight, re-dispatchable -- and the pre-wave case journalled
    ``workflow_failed``, so it replayed as ``failed`` rather than
    ``budget_exhausted``.
    """
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        WorkflowDefinition(
            id="wf_budget_node",
            name="node budget",
            graph=WorkflowGraph(version=1, nodes={"spendy": WorkflowNode(id="spendy", type=NodeType.TOOL, prompt="s", budget=5)}, edges=[]),
        )
    )
    run = engine.start_run("wf_budget_node")
    run = engine.execute_step(
        run.run_id,
        node_runner=lambda n, r: {"status": "completed", "output": "spent", "evidence": "receipt", "tokens_used": 100},
    )
    assert run.status == WorkflowRunStatus.BUDGET_EXHAUSTED
    events = engine.events.get_events(run.run_id)
    assert [e.event_type for e in events if e.event_type == "workflow_budget_exhausted"]
    _eng, replayed = replay_run(events, engine.get_definition("wf_budget_node"))
    assert replayed.status == WorkflowRunStatus.BUDGET_EXHAUSTED
    assert replayed.failed_nodes == run.failed_nodes
    assert replayed.node_states == run.node_states

    engine2 = DynamicWorkflowEngine()
    engine2.register_definition(
        WorkflowDefinition(
            id="wf_budget_run",
            name="run budget",
            budget=3,
            graph=WorkflowGraph(version=1, nodes={"t": WorkflowNode(id="t", type=NodeType.TOOL, prompt="t")}, edges=[]),
        )
    )
    run2 = engine2.start_run("wf_budget_run")
    run2.tokens_consumed = 5
    run2 = engine2.execute_step(run2.run_id, node_runner=_ok_runner([]))
    assert run2.status == WorkflowRunStatus.BUDGET_EXHAUSTED
    _eng2, replayed2 = replay_run(engine2.events.get_events(run2.run_id), engine2.get_definition("wf_budget_run"))
    assert replayed2.status == WorkflowRunStatus.BUDGET_EXHAUSTED


def test_replay_of_a_failed_run_keeps_the_failure():
    """A failed node stays failed across a replay: replay cannot mask it."""
    engine = DynamicWorkflowEngine()
    nodes = {n: WorkflowNode(id=n, type=NodeType.TOOL, prompt=n) for n in ("a", "b", "c")}
    engine.register_definition(
        WorkflowDefinition(
            id="wf_replay_fail",
            name="fail",
            graph=WorkflowGraph(version=1, nodes=nodes, edges=[WorkflowEdge(source="a", target="b"), WorkflowEdge(source="b", target="c")]),
        )
    )
    run = engine.start_run("wf_replay_fail", initial_state={"k": 1})
    run, _steps, _counts = _drive(engine, run, _ok_runner([], frozenset({"b"})), ceiling=5)
    assert run.status == WorkflowRunStatus.FAILED

    _eng, replayed = replay_run(engine.events.get_events(run.run_id), engine.get_definition("wf_replay_fail"))
    assert replayed.status == WorkflowRunStatus.FAILED
    assert replayed.failed_nodes == ["b"]
    assert replayed.completed_nodes == run.completed_nodes
    assert replayed.node_states == run.node_states
    assert replayed.state == run.state
    # A masked failure would show up as the replayed run being able to finish.
    assert "c" not in replayed.completed_nodes


# ------------------------------------------- the headline honesty invariant


@pytest.mark.parametrize("loop_policy", [False, True])
@pytest.mark.parametrize("mode", ["ok", "raise", "no_evidence"])
@pytest.mark.parametrize("fail_on", ["none", "first", "middle", "last", "all"])
def test_a_failed_step_never_reads_as_a_completed_run(loop_policy, mode, fail_on):
    """Across shapes, failure positions and failure modes: no false success.

    A run that recorded a failed node must never end ``completed``, must always
    reach a terminal status, and must never grow its graph without bound while
    still running.
    """
    policy = {"loop_policy": LoopPolicy(max_iterations=2)} if loop_policy else {}
    ids = ["a", "d", "b", "c", "j"]
    nodes = {
        "a": WorkflowNode(id="a", type=NodeType.TOOL, prompt="a", **policy),
        "d": WorkflowNode(id="d", type=NodeType.CONDITION, condition="1 > 0"),
        "b": WorkflowNode(id="b", type=NodeType.TOOL, prompt="b", **policy),
        "c": WorkflowNode(id="c", type=NodeType.TOOL, prompt="c", **policy),
        "j": WorkflowNode(id="j", type=NodeType.TOOL, prompt="j", **policy),
    }
    edges = [
        WorkflowEdge(source="a", target="d"),
        WorkflowEdge(source="d", target="b", condition="state.d_result == true"),
        WorkflowEdge(source="d", target="c", condition="state.d_result == false"),
        WorkflowEdge(source="b", target="j", mode=EdgeMode.NORMAL),
        WorkflowEdge(source="c", target="j", mode=EdgeMode.NORMAL),
    ]
    graph = WorkflowGraph(version=1, nodes=nodes, edges=edges)
    target = {
        "none": frozenset(),
        "first": frozenset({"a"}),
        "middle": frozenset({"b"}),
        "last": frozenset({"j"}),
        "all": frozenset(ids),
    }[fail_on]

    engine = DynamicWorkflowEngine()
    engine.register_definition(WorkflowDefinition(id="wf_invariant", name="invariant", graph=graph))
    run = engine.start_run("wf_invariant")
    calls: list[str] = []
    run, steps, counts = _drive(engine, run, _ok_runner(calls, target, mode), ceiling=15)

    assert run.status in TERMINAL, f"run never terminated: status={run.status.value} steps={steps} sizes={counts}"
    if run.failed_nodes:
        assert run.status != WorkflowRunStatus.COMPLETED, (
            f"a run with failed nodes {run.failed_nodes} reported completed (mode={mode} fail_on={fail_on} loop={loop_policy})"
        )
    if run.status != WorkflowRunStatus.COMPLETED:
        assert counts[-1] == len(graph.nodes), (
            f"a terminal run must not leave remediation nodes behind: {counts} (mode={mode} fail_on={fail_on})"
        )
    else:
        assert target == frozenset() or mode == "no_evidence", (
            "a run completed despite a node that was supposed to fail"
        )
    # Whatever happened, the journal and the run agree.
    assert [e.event_type for e in engine.events.get_events(run.run_id)].count("workflow_completed") == (
        1 if run.status == WorkflowRunStatus.COMPLETED else 0
    )
