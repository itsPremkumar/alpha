"""System-1 reflex hook in the DWE loop path (Master Mission B wiring).

The reflex layer is ADVISORY; the deterministic runtime decides. These pins
stub ONLY the reflex CALL (``alpha.system1.pruning.evaluate_loop_termination``)
so everything that actually decides — the deterministic evidence agreement,
the journaled ``loop_termination_considered`` event, the completion path and
its ``stopped_by`` provenance — runs for real in every branch. One test runs
the REAL local reflex classifier to prove the wiring works end-to-end.
"""

from __future__ import annotations

import pytest

import alpha.system1.pruning as pruning_module
import alpha.workflow.runtime as runtime_module
from alpha.system1.pruning import LoopTermination
from alpha.workflow.models import WorkflowDefinition, WorkflowGraph
from alpha.workflow.runtime import DynamicWorkflowEngine


def _loop_engine(*, max_iterations: int = 5) -> DynamicWorkflowEngine:
    engine = DynamicWorkflowEngine()
    engine.register_definition(
        WorkflowDefinition(
            id="wf_loop",
            name="loop",
            description="reflex hook fixture",
            graph=WorkflowGraph(
                version=1,
                nodes={
                    "loop1": {
                        "id": "loop1",
                        "type": "loop",
                        "prompt": "iterate until every check passes",
                        "loop_policy": {"max_iterations": max_iterations},
                    }
                },
                edges=[],
            ),
        )
    )
    return engine


def _counting_runner():
    calls = {"n": 0}

    def runner(node, run):
        calls["n"] += 1
        return {
            # The engine's runner contract: "completed" (anything else is a
            # failure) plus real evidence — a completion without evidence is
            # refused fail-closed.
            "status": "completed",
            "output": {"attempt": calls["n"]},
            "evidence": f"real evidence from attempt {calls['n']}",
        }

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _considered_events(engine: DynamicWorkflowEngine, run_id: str) -> list[dict]:
    return [
        event.payload
        for event in engine.events.get_events(run_id)
        if event.event_type == "loop_termination_considered"
    ]


@pytest.fixture(autouse=True)
def _isolated_dispatcher(monkeypatch):
    """A fresh in-memory dispatcher per test: no cross-test event bleed."""
    from alpha.workflow.events import WorkflowEventDispatcher

    monkeypatch.setattr(runtime_module, "get_event_dispatcher", lambda: WorkflowEventDispatcher())


def test_reflex_stops_the_loop_only_with_agreeing_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        pruning_module,
        "evaluate_loop_termination",
        lambda context, *args, **kwargs: LoopTermination(terminate=True, probability=0.99, reason="stubbed proposal"),
    )
    engine = _loop_engine()
    run = engine.start_run("wf_loop")
    runner = _counting_runner()

    # Iteration 1: the reflex proposes a stop, but the node has no evidence
    # yet — the deterministic agreement fails, so the loop MUST keep running.
    engine.execute_step(run.run_id, node_runner=runner)
    considered = _considered_events(engine, run.run_id)
    assert considered[-1]["terminate"] is True
    assert considered[-1]["evidence_agreement"] is False
    assert run.status.value == "running"
    assert runner.calls["n"] == 1

    # Iteration 2: real evidence now exists and nothing failed, so the same
    # proposal is actionable — and the completion says WHY it stopped.
    engine.execute_step(run.run_id, node_runner=runner)
    considered = _considered_events(engine, run.run_id)
    assert considered[-1]["evidence_agreement"] is True
    assert "loop1" in run.completed_nodes
    completions = [
        event.payload
        for event in engine.events.get_events(run.run_id)
        if event.event_type == "node_completed"
    ]
    assert completions[-1]["stopped_by"] == "system1_reflex_agreement"
    assert completions[-1]["evidence"]  # real evidence travels with the completion


def test_reflex_refusal_never_blocks_the_loop(monkeypatch) -> None:
    monkeypatch.setattr(
        pruning_module,
        "evaluate_loop_termination",
        lambda context, *args, **kwargs: LoopTermination(terminate=False, probability=0.1, reason="objective not met"),
    )
    engine = _loop_engine()
    run = engine.start_run("wf_loop")
    runner = _counting_runner()

    for _ in range(2):
        engine.execute_step(run.run_id, node_runner=runner)

    considered = _considered_events(engine, run.run_id)
    assert len(considered) == 2
    assert all(item["terminate"] is False for item in considered)
    assert runner.calls["n"] == 2
    assert run.status.value == "running"  # uncertainty keeps the loop alive


def test_failed_node_blocks_the_reflex_agreement(monkeypatch) -> None:
    monkeypatch.setattr(
        pruning_module,
        "evaluate_loop_termination",
        lambda context, *args, **kwargs: LoopTermination(terminate=True, probability=0.99, reason="stubbed proposal"),
    )
    engine = _loop_engine()
    run = engine.start_run("wf_loop")
    runner = _counting_runner()
    engine.execute_step(run.run_id, node_runner=runner)  # real evidence accrued
    run.failed_nodes.append("something_else")

    engine.execute_step(run.run_id, node_runner=runner)

    considered = _considered_events(engine, run.run_id)
    assert considered[-1]["terminate"] is True
    assert considered[-1]["evidence_agreement"] is False  # a failure vetoes the stop
    assert "loop1" not in run.completed_nodes
    assert runner.calls["n"] == 2


def test_reflex_layer_error_is_disclosed_and_never_fatal(monkeypatch) -> None:
    def _boom(context, *args, **kwargs):
        raise RuntimeError("reflex backend offline")

    monkeypatch.setattr(pruning_module, "evaluate_loop_termination", _boom)
    engine = _loop_engine()
    run = engine.start_run("wf_loop")
    runner = _counting_runner()

    engine.execute_step(run.run_id, node_runner=runner)  # must not raise

    considered = _considered_events(engine, run.run_id)
    assert considered[-1]["terminate"] is False
    assert "system1_unavailable" in considered[-1]["reason"]
    assert "RuntimeError" in considered[-1]["reason"]
    assert runner.calls["n"] == 1
    assert run.status.value == "running"


def test_real_local_reflex_is_consulted_and_journaled() -> None:
    """No stub: the REAL local (free, CPU-only) reflex answers and is journaled."""
    engine = _loop_engine()
    run = engine.start_run("wf_loop")
    runner = _counting_runner()

    engine.execute_step(run.run_id, node_runner=runner)

    considered = _considered_events(engine, run.run_id)
    assert len(considered) == 1
    payload = considered[0]
    assert isinstance(payload["probability"], float)
    # Whatever the local reflex decided, the reason must name the real engine
    # and the loop only stops when the proposal AND evidence agree.
    assert payload["reason"]
    if payload["terminate"] and payload["evidence_agreement"]:
        assert "loop1" in run.completed_nodes
    else:
        assert runner.calls["n"] == 1
