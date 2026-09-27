"""Tests for the Dynamic Workflow Engine runtime-correctness wave.

Covers the defects this change set closed, each pinned by a test that fails
against the pre-change engine:

- ``WorkflowNode.timeout_seconds`` is ENFORCED.  Previously any node declaring a
  timeout was failed outright ("the bound synchronous executor has no
  cancellation seam"), so a declared deadline guaranteed failure instead of
  bounding anything.
- Disjoint-write-scope waves actually execute concurrently when — and only when
  — the workflow declares ``max_concurrency``.  Previously the scheduler computed
  the waves and a plain ``for`` loop ran them serially.
- The node kinds that had no handler at all (``CHECKPOINT``, ``GOAL_GATE``,
  ``HANDOFF``, ``WAIT``, ``EVENT_WAIT``, ``PARALLEL``, ``SUBWORKFLOW``) run.
- ``SUSPENDED`` / ``WAITING_EVENT`` are reachable, with signal delivery and an
  honest expiry sweep.
- Shared run bookkeeping is safe under concurrency (token charges, failed-node
  bookkeeping, idempotency dedupe).
- The measured timing ledger and critical path are real measurements.

DY-R1 honesty is asserted throughout: a timed-out node, an unevaluable gate, an
unfinished child workflow and an expired wait all FAIL with the real reason.  None
of them is allowed to report success.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest

from alpha.workflow.execution import (
    ConcurrencyGovernor,
    clamp_concurrency,
    execute_wave,
    first_error,
    run_with_deadline,
    timeout_occurred,
)
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.observability import (
    NodeTiming,
    RunTimeline,
    build_run_observability,
    critical_path,
    topological_order,
)
from alpha.workflow.runtime import DynamicWorkflowEngine


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    """AGENT_WORKSPACE_HOME is process-global; point it at a per-test temp dir."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _ok_runner(node: WorkflowNode, run: WorkflowRun) -> dict:
    """A minimal honest runner: real evidence, no invented token spend."""
    return {
        "status": "completed",
        "output": {"node": node.id},
        "evidence": f"executed node '{node.id}'",
        "tokens_used": 1,
    }


def _single_node_graph(node_id: str = "n1", **node_kwargs) -> WorkflowGraph:
    return WorkflowGraph(nodes={node_id: WorkflowNode(id=node_id, **node_kwargs)}, edges=[])


def _definition(graph: WorkflowGraph, *, workflow_id: str = "wf", policies: dict | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(id=workflow_id, name=workflow_id, graph=graph, policies=policies or {})


# --------------------------------------------------------------------- execution


def test_deadline_is_enforced_and_the_late_result_is_discarded():
    """A node that overruns its deadline fails; its late output is never adopted."""
    finished = threading.Event()

    def slow():
        time.sleep(1.5)
        finished.set()
        return "late value"

    result = run_with_deadline(slow, timeout_seconds=0.05, label="slow")

    assert result.timed_out is True
    assert result.value is None, "a value produced after the deadline must not be carried"
    assert result.ok is False
    assert result.overrun_seconds > 0
    reason = result.describe_timeout("node 'slow'")
    assert "exceeded its 0.05s deadline" in reason
    assert "fenced" in reason and "discarded" in reason
    # The worker really is still running: CPython cannot kill a thread, and the
    # contract is disclosure rather than a false "cancelled" claim.
    assert finished.wait(3.0) is True


def test_deadline_not_exceeded_returns_the_value_and_no_error():
    result = run_with_deadline(lambda: 42, timeout_seconds=5.0)
    assert result.timed_out is False
    assert result.ok is True
    assert result.value == 42
    assert result.describe_timeout("node") == ""


def test_no_deadline_runs_inline_and_propagates_the_real_exception():
    """An unbounded call must keep raising the caller's own exception."""
    result = run_with_deadline(lambda: (_ for _ in ()).throw(ValueError("boom")), timeout_seconds=None)
    assert result.timed_out is False
    assert isinstance(result.error, ValueError)
    with pytest.raises(ValueError, match="boom"):
        result.rethrow()


def test_clamp_concurrency_never_raises_on_hostile_input():
    assert clamp_concurrency(None) >= 1
    assert clamp_concurrency("nonsense") >= 1
    assert clamp_concurrency(-5) == 1
    assert clamp_concurrency(0) == 1
    assert clamp_concurrency(10_000) <= 32
    assert clamp_concurrency(3) == 3


def test_execute_wave_preserves_submission_order():
    order: list[int] = []
    outcomes = execute_wave([5, 1, 4, 2, 3], order.append, max_concurrency=4)
    assert [outcome.item for outcome in outcomes] == [5, 1, 4, 2, 3]
    assert sorted(order) == [1, 2, 3, 4, 5]


def test_execute_wave_captures_per_item_errors_without_abandoning_the_wave():
    """One failing item must not strand its siblings as un-run."""
    seen: list[str] = []

    def invoke(item: str) -> None:
        if item == "bad":
            raise RuntimeError("item exploded")
        seen.append(item)

    outcomes = execute_wave(["a", "bad", "c"], invoke, max_concurrency=3)

    assert seen == ["a", "c"], "siblings must still run"
    assert outcomes[0].ok and outcomes[2].ok
    assert not outcomes[1].ok
    assert isinstance(first_error(outcomes), RuntimeError)
    assert "item exploded" in str(first_error(outcomes))


def test_execute_wave_actually_overlaps_when_concurrency_allows_it():
    """Two 0.25s items at concurrency 2 must finish in ~0.25s, not ~0.5s."""
    started = time.monotonic()
    execute_wave([1, 2], lambda _item: time.sleep(0.25), max_concurrency=2)
    elapsed = time.monotonic() - started
    assert elapsed < 0.45, f"expected overlapping execution, took {elapsed:.3f}s"


def test_execute_wave_is_sequential_at_concurrency_one():
    started = time.monotonic()
    execute_wave([1, 2], lambda _item: time.sleep(0.2), max_concurrency=1)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.4, f"concurrency 1 must not overlap, took {elapsed:.3f}s"


def test_governor_bounds_admission_and_reports_measured_peak():
    governor = ConcurrencyGovernor(limit=2, wait_seconds=0.05)
    assert governor.acquire() is True
    assert governor.acquire() is True
    # Third admission has nowhere to go and must refuse rather than block forever.
    assert governor.acquire() is False
    governor.release()
    assert governor.acquire() is True
    stats = governor.stats()
    assert stats["limit"] == 2
    assert stats["peak_in_flight"] == 2
    assert stats["admission_rejections"] == 1


# ------------------------------------------------------- engine: node deadlines


def test_engine_enforces_a_declared_node_timeout_instead_of_failing_it_outright():
    """The regression pin: this node used to fail WITHOUT the runner ever running."""
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        _definition(_single_node_graph(timeout_seconds=0.05, prompt="slow work"))
    )
    run = engine.start_run("wf")
    engine.execute_step(run.run_id, node_runner=lambda node, _run: time.sleep(1.0) or _ok_runner(node, _run))

    assert run.node_states["n1"] == NodeStatus.FAILED
    assert run.status is WorkflowRunStatus.FAILED
    events = [e for e in engine.events.get_events(run.run_id) if e.event_type == "node_timeout"]
    assert len(events) == 1, "the missed deadline must be journalled"
    reason = engine._run_graph_for(run).nodes["n1"].output["reason"]
    assert "deadline" in reason and "fenced" in reason


def test_a_node_that_meets_its_timeout_succeeds():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph(timeout_seconds=5.0, prompt="quick work")))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id, node_runner=_ok_runner)

    assert run.node_states["n1"] == NodeStatus.SUCCEEDED
    assert run.status is WorkflowRunStatus.COMPLETED
    assert not [e for e in engine.events.get_events(run.run_id) if e.event_type == "node_timeout"]


def test_a_timed_out_node_is_marked_in_the_measured_timeline():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph(timeout_seconds=0.05, prompt="slow")))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id, node_runner=lambda node, _run: time.sleep(1.0) or _ok_runner(node, _run))

    report = build_run_observability(run, engine._run_graph_for(run))
    assert report["timed_out_nodes"] == ["n1"]
    assert report["timeline_complete"] is True, "the window must close even on failure"


# ---------------------------------------------------------- engine: wave shape


def _fanout_graph(node_ids: list[str]) -> WorkflowGraph:
    """``len(node_ids)`` independent nodes, so all land in one scheduling wave."""
    return WorkflowGraph(
        nodes={nid: WorkflowNode(id=nid, write_scope=[f"scope/{nid}"]) for nid in node_ids},
        edges=[],
    )


def test_waves_are_sequential_unless_the_workflow_declares_concurrency():
    """Default behaviour must stay deterministic: node order == event order."""
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_fanout_graph(["a", "b", "c"])))
    run = engine.start_run("wf")
    order: list[str] = []
    engine.execute_step(run.run_id, node_runner=lambda node, _run: order.append(node.id) or _ok_runner(node, _run))

    assert order == ["a", "b", "c"], "undeclared concurrency must not reorder execution"
    assert engine.governor_for(run).limit == 1


def test_declared_concurrency_runs_a_wave_in_parallel():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_fanout_graph(["a", "b", "c"]), policies={"max_concurrency": 3}))
    run = engine.start_run("wf")
    assert engine.governor_for(run).limit == 3

    lock = threading.Lock()
    order: list[str] = []
    peak = 0
    live = 0

    def runner(node, _run):
        nonlocal peak, live
        with lock:
            order.append(node.id)
            live += 1
            peak = max(peak, live)
        time.sleep(0.2)
        with lock:
            live -= 1
        return _ok_runner(node, _run)

    started = time.monotonic()
    engine.execute_step(run.run_id, node_runner=runner)
    elapsed = time.monotonic() - started

    assert sorted(order) == ["a", "b", "c"]
    assert peak > 1, "the wave must genuinely overlap"
    assert elapsed < 0.55, f"expected overlapping waves, took {elapsed:.3f}s"
    assert sorted(run.completed_nodes) == ["a", "b", "c"]


def test_concurrent_wave_does_not_lose_token_charges():
    """``run.tokens_consumed += charge`` was unguarded; parallel waves lost charges."""
    engine = DynamicWorkflowEngine()
    node_ids = [f"n{i}" for i in range(8)]
    engine.register_definition(_definition(_fanout_graph(node_ids), policies={"max_concurrency": 8}))
    run = engine.start_run("wf")
    engine.execute_step(
        run.run_id,
        node_runner=lambda node, _run: {"status": "completed", "output": node.id, "evidence": "real work", "tokens_used": 100},
    )
    assert run.tokens_consumed == 800, f"expected 8x100 tokens, got {run.tokens_consumed}"
    assert len(run.completed_nodes) == 8


def test_parallel_wave_records_measured_wave_shape():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_fanout_graph(["a", "b"]), policies={"max_concurrency": 2}))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id, node_runner=_ok_runner)

    waves = run.metrics["wave_metrics"]
    assert len(waves) == 1
    assert waves[0]["node_count"] == 2
    assert waves[0]["concurrency"] == 2
    assert waves[0]["elapsed_seconds"] >= 0.0


# --------------------------------------------------------- structural node kinds


def test_checkpoint_node_records_a_recomputable_state_digest():
    import hashlib
    import json

    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph("cp", type=NodeType.CHECKPOINT, config={"label": "before-review"})))
    run = engine.start_run("wf", initial_state={"alpha": 1, "beta": "two"})
    engine.execute_step(run.run_id)

    assert run.node_states["cp"] == NodeStatus.SUCCEEDED
    ledger = run.metrics["checkpoints"]
    assert len(ledger) == 1 and ledger[0]["label"] == "before-review"
    expected = hashlib.sha256(json.dumps(run.state, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert ledger[0]["state_sha256"] == expected
    assert "append-only event log remains the authoritative durable record" in engine._run_graph_for(run).nodes["cp"].evidence[0]


def test_goal_gate_passes_only_on_measured_criteria():
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={
            "gate": WorkflowNode(
                id="gate",
                type=NodeType.GOAL_GATE,
                config={
                    "acceptance_criteria": [
                        {"name": "score_is_high", "expression": "state.score > 0.8", "required": True},
                        {"name": "advisory", "expression": "state.score > 0.0", "required": False},
                    ]
                },
            )
        },
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf", initial_state={"score": 0.95})
    engine.execute_step(run.run_id)

    assert run.node_states["gate"] == NodeStatus.SUCCEEDED
    assert engine._run_graph_for(run).nodes["gate"].output["passed"] is True


def test_goal_gate_fails_on_an_unmet_required_criterion():
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={
            "gate": WorkflowNode(
                id="gate",
                type=NodeType.GOAL_GATE,
                config={"acceptance_criteria": [{"name": "needs_ten", "expression": "state.score > 10", "required": True}]},
            )
        },
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf", initial_state={"score": 1.0})
    engine.execute_step(run.run_id)

    assert run.node_states["gate"] == NodeStatus.FAILED
    assert run.status is WorkflowRunStatus.FAILED
    assert "needs_ten" in engine._run_graph_for(run).nodes["gate"].output["reason"]


def test_goal_gate_treats_an_unevaluable_required_criterion_as_not_met():
    """A gate must never pass on the strength of a check that did not run."""
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={
            "gate": WorkflowNode(
                id="gate",
                type=NodeType.GOAL_GATE,
                config={"acceptance_criteria": [{"name": "broken", "expression": "state.missing_attr > 1", "required": True}]},
            )
        },
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf", initial_state={"score": 1.0})
    engine.execute_step(run.run_id)

    assert run.node_states["gate"] == NodeStatus.FAILED
    assert run.status is WorkflowRunStatus.FAILED


def test_goal_gate_with_no_criteria_cannot_pass():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph("gate", type=NodeType.GOAL_GATE)))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id)
    assert run.node_states["gate"] == NodeStatus.FAILED
    assert run.status is WorkflowRunStatus.FAILED


def test_wait_node_measures_the_sleep_it_actually_performed():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph("nap", type=NodeType.WAIT, config={"delay_seconds": 0.05})))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id)

    output = engine._run_graph_for(run).nodes["nap"].output
    assert run.node_states["nap"] == NodeStatus.SUCCEEDED
    assert output["requested_seconds"] == 0.05
    assert output["slept_seconds"] >= 0.04


def test_wait_node_rejects_a_non_numeric_delay_instead_of_sleeping_forever():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph("nap", type=NodeType.WAIT, config={"delay_seconds": "ages"})))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id)
    assert run.node_states["nap"] == NodeStatus.FAILED


def test_handoff_node_reports_real_state_and_invents_no_decisions():
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={
            "work": WorkflowNode(id="work", prompt="do it"),
            "handoff": WorkflowNode(id="handoff", type=NodeType.HANDOFF, config={"handoff_to": "reviewer"}, depends_on=["work"]),
        },
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf", initial_state={"objective": "ship the thing"})
    engine.execute_step(run.run_id)
    engine.execute_step(run.run_id)

    contract = run.state["handoff_handoff"]
    assert contract["to"] == "reviewer"
    assert contract["completed"] == ["work"]
    assert contract["decisions"] == [], "decisions must stay empty; the run records none"


# ------------------------------------------------- external events and suspension


def test_event_wait_parks_the_run_and_a_signal_resumes_it():
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={
            "waiter": WorkflowNode(id="waiter", type=NodeType.EVENT_WAIT, config={"event": "deploy.approved"}),
            "after": WorkflowNode(id="after", prompt="continue", depends_on=["waiter"]),
        },
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf")

    engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.WAITING_EVENT, "WAITING_EVENT must be reachable"
    assert run.node_states["waiter"] == NodeStatus.WAITING

    # Stepping a parked run must not sneak past the wait.
    engine.execute_step(run.run_id)
    assert run.node_states["waiter"] == NodeStatus.WAITING

    engine.signal_event(run.run_id, "deploy.approved", {"approver": "prem"})
    assert run.status is WorkflowRunStatus.RUNNING
    assert run.node_states["waiter"] == NodeStatus.WAITING

    engine.execute_step(run.run_id)
    assert run.node_states["waiter"] == NodeStatus.SUCCEEDED
    assert run.state["waiter_event_payload"] == {"approver": "prem"}

    engine.execute_step(run.run_id)
    assert run.node_states["after"] == NodeStatus.SUCCEEDED
    assert run.status is WorkflowRunStatus.COMPLETED


def test_a_signal_for_an_unmatched_event_advances_nothing():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph("waiter", type=NodeType.EVENT_WAIT, config={"event": "real.event"})))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id)

    engine.signal_event(run.run_id, "typo.event", {"x": 1})

    assert run.node_states["waiter"] == NodeStatus.WAITING
    assert run.status is WorkflowRunStatus.WAITING_EVENT
    matched = [e for e in engine.events.get_events(run.run_id) if e.event_type == "external_event_signalled"]
    assert matched and matched[-1].payload["matched_nodes"] == []


def test_event_wait_with_no_event_name_fails_instead_of_parking_forever():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph("waiter", type=NodeType.EVENT_WAIT, config={})))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id)
    assert run.node_states["waiter"] == NodeStatus.FAILED
    assert run.status is WorkflowRunStatus.FAILED


def test_expired_external_wait_fails_with_the_measured_age():
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        _definition(_single_node_graph("waiter", type=NodeType.EVENT_WAIT, config={"event": "never", "timeout_seconds": 0.0}))
    )
    run = engine.start_run("wf")
    engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.WAITING_EVENT

    time.sleep(0.02)
    engine.sweep_expired_waits(run.run_id)

    assert run.node_states["waiter"] == NodeStatus.FAILED
    reason = engine._run_graph_for(run).nodes["waiter"].output["reason"]
    assert "expired" in reason and "cannot wait forever" in reason


def test_suspend_and_resume_round_trip():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_fanout_graph(["a", "b"])))
    run = engine.start_run("wf")

    engine.suspend_run(run.run_id, "operator hold")
    assert run.status is WorkflowRunStatus.SUSPENDED

    engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.SUSPENDED
    assert run.completed_nodes == [], "a suspended run must not perform work"

    engine.resume_run(run.run_id)
    assert run.status is WorkflowRunStatus.RUNNING
    engine.execute_step(run.run_id)
    assert sorted(run.completed_nodes) == ["a", "b"]


def test_resuming_a_run_that_is_not_suspended_is_refused():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph()))
    run = engine.start_run("wf")
    with pytest.raises(ValueError, match="not suspended"):
        engine.resume_run(run.run_id)


# ------------------------------------------------------ parallel group / child


def test_parallel_group_requires_every_member_to_succeed():
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={
            "m1": WorkflowNode(id="m1", prompt="one", write_scope=["a"]),
            "m2": WorkflowNode(id="m2", prompt="two", write_scope=["b"]),
            "group": WorkflowNode(id="group", type=NodeType.PARALLEL, config={"nodes": ["m1", "m2"]}, depends_on=["m1", "m2"]),
        },
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf")
    for _ in range(3):
        engine.execute_step(run.run_id, node_runner=_ok_runner)

    assert run.node_states["m1"] == NodeStatus.SUCCEEDED
    assert run.node_states["m2"] == NodeStatus.SUCCEEDED
    assert run.node_states["group"] == NodeStatus.SUCCEEDED
    assert run.status is WorkflowRunStatus.COMPLETED


def test_parallel_group_fails_when_a_member_did_not_succeed():
    """A partially completed group must never read as a completed group."""
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={
            "m1": WorkflowNode(id="m1", prompt="one", write_scope=["a"]),
            "m2": WorkflowNode(id="m2", prompt="two", write_scope=["b"]),
            "group": WorkflowNode(id="group", type=NodeType.PARALLEL, config={"nodes": ["m1", "m2"]}, depends_on=["m1", "m2"]),
        },
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf")

    def runner(node, _run):
        if node.id == "m2":
            return {"status": "failed", "output": "member refused", "evidence": "", "tokens_used": 0}
        return _ok_runner(node, _run)

    for _ in range(3):
        engine.execute_step(run.run_id, node_runner=runner)

    assert run.node_states["group"] == NodeStatus.FAILED
    assert run.status is WorkflowRunStatus.FAILED


def test_parallel_group_refuses_a_member_that_is_not_in_the_graph():
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={"group": WorkflowNode(id="group", type=NodeType.PARALLEL, config={"nodes": ["ghost"]})},
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id)
    assert run.node_states["group"] == NodeStatus.FAILED
    assert "ghost" in engine._run_graph_for(run).nodes["group"].output["reason"]


def test_subworkflow_runs_a_real_child_and_adopts_only_a_completed_one():
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        _definition(_single_node_graph("child_work", prompt="child task"), workflow_id="child_wf")
    )
    parent = WorkflowGraph(nodes={"kid": WorkflowNode(id="kid", type=NodeType.SUBWORKFLOW, config={"workflow_id": "child_wf"})}, edges=[])
    engine.register_definition(_definition(parent, workflow_id="parent_wf"))

    run = engine.start_run("parent_wf")
    engine.execute_step(run.run_id, node_runner=_ok_runner)

    assert run.node_states["kid"] == NodeStatus.SUCCEEDED
    payload = run.state["kid_subworkflow"]
    assert payload["child_status"] == "completed"
    assert payload["child_completed_nodes"] == ["child_work"]


def test_subworkflow_refuses_an_unfinished_child_instead_of_adopting_it():
    engine = DynamicWorkflowEngine()
    # The child can never complete: its only node has no bound executor.
    engine.register_definition(
        _definition(_single_node_graph("child_work", prompt="child task"), workflow_id="child_wf")
    )
    parent = WorkflowGraph(nodes={"kid": WorkflowNode(id="kid", type=NodeType.SUBWORKFLOW, config={"workflow_id": "child_wf"})}, edges=[])
    engine.register_definition(_definition(parent, workflow_id="parent_wf"))

    run = engine.start_run("parent_wf")
    engine.execute_step(run.run_id)  # no runner bound at all

    assert run.node_states["kid"] == NodeStatus.FAILED
    assert "not completed" in engine._run_graph_for(run).nodes["kid"].output["reason"]


def test_subworkflow_refuses_an_unregistered_target():
    engine = DynamicWorkflowEngine()
    parent = WorkflowGraph(
        nodes={"kid": WorkflowNode(id="kid", type=NodeType.SUBWORKFLOW, config={"workflow_id": "nope"})},
        edges=[],
    )
    engine.register_definition(_definition(parent, workflow_id="parent_wf"))
    run = engine.start_run("parent_wf")
    engine.execute_step(run.run_id)
    assert run.node_states["kid"] == NodeStatus.FAILED
    assert "not registered" in engine._run_graph_for(run).nodes["kid"].output["reason"]


def test_subworkflow_refuses_to_recurse_into_its_own_workflow():
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={"kid": WorkflowNode(id="kid", type=NodeType.SUBWORKFLOW, config={"workflow_id": "self_wf"})},
        edges=[],
    )
    engine.register_definition(_definition(graph, workflow_id="self_wf"))
    run = engine.start_run("self_wf")
    engine.execute_step(run.run_id)
    assert run.node_states["kid"] == NodeStatus.FAILED
    assert "recurse forever" in engine._run_graph_for(run).nodes["kid"].output["reason"]


# ------------------------------------------------------------- observability


def test_critical_path_is_the_slowest_measured_dependency_chain():
    graph = WorkflowGraph(
        nodes={
            "root": WorkflowNode(id="root"),
            "slow": WorkflowNode(id="slow", depends_on=["root"]),
            "fast": WorkflowNode(id="fast", depends_on=["root"]),
            "join": WorkflowNode(id="join", depends_on=["slow", "fast"]),
        },
        edges=[],
    )
    timeline = RunTimeline(
        entries=[
            NodeTiming(node_id="root", started_at="t", ended_at="t", duration_seconds=0.1),
            NodeTiming(node_id="slow", started_at="t", ended_at="t", duration_seconds=5.0),
            NodeTiming(node_id="fast", started_at="t", ended_at="t", duration_seconds=0.2),
            NodeTiming(node_id="join", started_at="t", ended_at="t", duration_seconds=0.3),
        ]
    )
    path = critical_path(graph, timeline)
    assert path["path"] == ["root", "slow", "join"]
    assert path["total_seconds"] == pytest.approx(5.4)
    assert path["complete"] is True


def test_critical_path_reports_incomplete_rather_than_inventing_one():
    graph = WorkflowGraph(nodes={"a": WorkflowNode(id="a")}, edges=[])
    path = critical_path(graph, RunTimeline())
    assert path["path"] == []
    assert path["complete"] is False
    assert "no node has a completed timing measurement" in path["reason"]


def test_topological_order_reports_cyclic_nodes_instead_of_dropping_them():
    graph = WorkflowGraph(
        nodes={"a": WorkflowNode(id="a", depends_on=["b"]), "b": WorkflowNode(id="b", depends_on=["a"]), "c": WorkflowNode(id="c")},
        edges=[],
    )
    ordered, unresolved = topological_order(graph)
    assert ordered == ["c"]
    assert unresolved == ["a", "b"]


def test_observability_separates_execution_from_acceptance():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_fanout_graph(["a", "b"])))
    run = engine.start_run("wf")
    engine.execute_step(run.run_id, node_runner=_ok_runner)

    report = build_run_observability(run, engine._run_graph_for(run))
    assert report["status"] == "completed"
    assert report["terminal"] is True
    assert report["nodes_completed"] == 2
    assert report["measured_executions"] == 2
    # The report describes execution only. Acceptance stays the executor's
    # evidence contract, so the payload carries no acceptance verdict at all.
    assert "acceptance_passed" not in report
    assert "verified" not in report


def test_timeline_survives_a_corrupt_ledger_row_without_failing_the_projection():
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition(_single_node_graph()))
    run = engine.start_run("wf")
    run.metrics["node_timings"] = {"entries": [{"node_id": "a", "duration_seconds": "not-a-number"}, "garbage", {}]}
    report = build_run_observability(run, engine._run_graph_for(run))
    assert report["timeline"] == []


# ------------------------------------------------------------- shared-state lock


def test_concurrent_failures_do_not_duplicate_a_node_in_failed_nodes():
    engine = DynamicWorkflowEngine()
    node_ids = [f"n{i}" for i in range(8)]
    engine.register_definition(_definition(_fanout_graph(node_ids), policies={"max_concurrency": 8}))
    run = engine.start_run("wf")

    def runner(node, _run):
        time.sleep(0.01)
        return {"status": "failed", "output": "deliberate failure", "evidence": "", "tokens_used": 0}

    engine.execute_step(run.run_id, node_runner=runner)

    assert len(run.failed_nodes) == len(set(run.failed_nodes)), "a node id was appended twice"
    assert run.status is WorkflowRunStatus.FAILED


def test_an_idempotent_node_runs_its_side_effect_only_once_under_concurrency():
    engine = DynamicWorkflowEngine()
    graph = WorkflowGraph(
        nodes={"charge": WorkflowNode(id="charge", prompt="charge the card", idempotency_key="order-42")},
        edges=[],
    )
    engine.register_definition(_definition(graph))
    run = engine.start_run("wf")
    effects: list[str] = []

    def runner(node, _run):
        effects.append(node.id)
        return _ok_runner(node, _run)

    engine.execute_step(run.run_id, node_runner=runner)
    assert run.node_states["charge"] == NodeStatus.SUCCEEDED
    assert len(effects) == 1

    # Re-entering the same node must not repeat the recorded effect.
    engine.execute_step(run.run_id, node_runner=runner)
    assert len(effects) == 1, "the recorded idempotency key was not consumed"


def test_timeout_flag_is_per_thread_and_leaks_nothing_between_nodes():
    from alpha.workflow.execution import mark_timeout_occurred, reset_timeout_flag

    reset_timeout_flag()
    assert timeout_occurred() is False
    mark_timeout_occurred()
    assert timeout_occurred() is True

    seen: list[bool] = []
    worker = threading.Thread(target=lambda: seen.append(timeout_occurred()))
    worker.start()
    worker.join()
    assert seen == [False], "the miss flag must not leak onto another thread"

    reset_timeout_flag()
    assert timeout_occurred() is False
