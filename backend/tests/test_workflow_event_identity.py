"""Event ids must be addresses: unique, and unresolvable when they are not.

``WorkflowEvent.event_id`` used to be a bare microsecond timestamp. That is not
unique -- ``node_attempt_started`` and the ``node_completed`` emitted right
after it routinely land in the same microsecond -- and an id that can repeat is
not an address. A caller that resolved a fork point by such an id got the first
of several matches, so the forked prefix ended early, no completed work was
inherited, and the fork repeated a side effect it had reported as inherited.

These tests pin both halves of the fix: ids are unique in practice, and an
ambiguous id is REFUSED by name rather than silently resolved to the wrong
event.
"""

from __future__ import annotations

import pytest

from alpha.workflow.events import WorkflowEvent, WorkflowEventDispatcher
from alpha.workflow.models import WorkflowDefinition, WorkflowEdge, WorkflowGraph, WorkflowNode
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.time_travel import ForkError, fork_run, run_history


def _ok(node, run):
    return {"status": "completed", "output": {"node": node.id}, "evidence": f"ran {node.id}", "tokens_used": 5}


def _engine_with_chain(length: int = 3) -> tuple[DynamicWorkflowEngine, str]:
    engine = DynamicWorkflowEngine()
    engine.events = WorkflowEventDispatcher(durable_sink=None)
    nodes = {f"n{i}": WorkflowNode(id=f"n{i}", prompt=f"step {i}") for i in range(length)}
    edges = [WorkflowEdge(source=f"n{i - 1}", target=f"n{i}") for i in range(1, length)]
    engine.register_definition(WorkflowDefinition(id="chain", name="chain", graph=WorkflowGraph(version=1, nodes=nodes, edges=edges), policies={}))
    return engine, "chain"


def test_event_ids_are_unique_even_when_emitted_in_the_same_microsecond(monkeypatch):
    """The exact failure mode: a frozen clock makes every id collide."""
    from datetime import UTC, datetime

    frozen = datetime.now(UTC)
    monkeypatch.setattr("alpha.workflow.events.datetime", type("Fake", (), {"now": staticmethod(lambda _tz: frozen), "UTC": UTC}))

    ids = [WorkflowEvent(workflow_run_id="r", event_type="node_attempt_started").event_id for _ in range(500)]
    assert len(set(ids)) == len(ids), "event ids collided under a frozen clock; a fork point could resolve to the wrong event"


def test_event_ids_sort_in_creation_order():
    """Padding is what keeps string order equal to insertion order."""
    ids = [WorkflowEvent(workflow_run_id="r", event_type="node_started").event_id for _ in range(200)]
    assert ids == sorted(ids), "zero-padded ids must sort chronologically, then by emission order"


def test_a_real_run_never_repeats_an_event_id():
    """The un-forced version of the same property, on a run that emits them back to back."""
    engine, wf_id = _engine_with_chain(3)
    run = engine.start_run(wf_id)
    engine.execute_step(run.run_id, node_runner=_ok)

    events = engine.events.get_events(run.run_id)
    ids = [event.event_id for event in events]
    assert len(set(ids)) == len(ids), f"duplicate event ids in one run's log: {ids}"


def test_a_fork_at_node_completed_inherits_the_completed_work():
    """The behaviour the duplicated id used to destroy, asserted directly.

    ``node_attempt_started`` and ``node_completed`` are emitted back to back, so
    this is the pair that collided and the pair that used to make the fork lose
    its inherited work.
    """
    engine, wf_id = _engine_with_chain(3)
    source = engine.start_run(wf_id)
    engine.execute_step(source.run_id, node_runner=_ok)
    assert source.completed_nodes == ["n0"]

    history = run_history(engine, source.run_id)
    fork_point = next(entry for entry in history if entry.event_type == "node_completed")
    result = fork_run(engine, source.run_id, at_event_id=fork_point.event_id)

    assert result.inherited_completed_nodes == ["n0"], "already-done work must be inherited, not repeated"
    assert result.run.completed_nodes == ["n0"]


def test_an_ambiguous_event_id_is_refused_by_name_not_silently_resolved(monkeypatch):
    """A duplicated id is a corrupt address: say so instead of guessing."""
    engine, wf_id = _engine_with_chain(3)
    source = engine.start_run(wf_id)
    engine.execute_step(source.run_id, node_runner=_ok)

    events = engine.events.get_events(source.run_id)
    history = run_history(engine, source.run_id)
    fork_point = next(entry for entry in history if entry.event_type == "node_completed")

    # Reproduce a pre-fix log: the completion shares its id with the attempt.
    target = next(event for event in events if event.event_type == "node_completed")
    target.event_id = fork_point.event_id
    preceding = next(event for event in events if event.event_type == "node_attempt_started")
    preceding.event_id = fork_point.event_id

    with pytest.raises(ForkError) as excinfo:
        fork_run(engine, source.run_id, at_event_id=fork_point.event_id)

    message = str(excinfo.value)
    assert "ambiguous" in message
    assert fork_point.event_id in message, "the refusal must name the id it could not resolve"
    assert "node_attempt_started" in message and "node_completed" in message, "and the events that collided"
