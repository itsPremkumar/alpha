"""Connectivity waits: the run state that says "alive, parked, link down".

Properties pinned here:

* no bound probe means an honest node failure, never a wait on nothing;
* reachable is a fresh MEASUREMENT on every dispatch — the node never
  completes on a stale reading, and its evidence says which one happened
  (probe vs signal), because those are different truths;
* the sweep releases a wait the probe measures reachable and un-parks the
  RUN, not just the node — the scheduler refuses a parked run, so a recovered
  link must not leave the run reading as still waiting;
* a wait whose deadline passes fails with the measured age, so nothing parks
  forever.
"""

from __future__ import annotations

import time
from pathlib import Path

from alpha.workflow.connectivity import (
    DEFAULT_RELEASE_EVENT,
    MAX_CONNECTIVITY_WAIT_SECONDS,
    connectivity_evidence,
    parse_deadline_seconds,
    release_event_for,
)
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRunStatus,
)
from alpha.workflow.runtime import DynamicWorkflowEngine

# --------------------------------------------------------------------- policy


def test_deadline_takes_the_first_usable_candidate():
    assert parse_deadline_seconds(12, 99) == 12.0
    assert parse_deadline_seconds(None, 30) == 30.0
    assert parse_deadline_seconds("bad", 7.5) == 7.5
    assert parse_deadline_seconds(True, 5) == 5.0  # bool is not a deadline


def test_deadline_refuses_negative_and_non_numeric():
    assert parse_deadline_seconds(-1) is None
    assert parse_deadline_seconds(None) is None
    assert parse_deadline_seconds("soon") is None


def test_deadline_is_clamped_to_the_ceiling():
    assert parse_deadline_seconds(10**9) == MAX_CONNECTIVITY_WAIT_SECONDS


def test_release_event_default_and_override():
    assert release_event_for(None) == DEFAULT_RELEASE_EVENT
    assert release_event_for({"release_event": " net.back "}) == "net.back"


def test_evidence_distinguishes_measured_from_asserted():
    measured = connectivity_evidence(reachable=True, target="api.example.com", release="connectivity.restored")
    asserted = connectivity_evidence(reachable=False, target="api.example.com", release="net.back")
    assert "measured reachable" in measured
    assert "asserted, not measured" in asserted
    assert "net.back" in asserted


# --------------------------------------------------------------------- engine


def _definition(*, timeout_seconds: object = 5, target: str = "api.example.com") -> WorkflowDefinition:
    graph = WorkflowGraph(
        nodes={
            "wait_net": WorkflowNode(
                id="wait_net",
                type=NodeType.CONNECTIVITY_WAIT,
                config={"target": target, "timeout_seconds": timeout_seconds},
            ),
        },
        edges=[],
    )
    return WorkflowDefinition(id="wf_net", name="connectivity fixture", graph=graph, budget=100000)


def _engine(tmp_path: Path) -> DynamicWorkflowEngine:
    engine = DynamicWorkflowEngine()
    engine.register_definition(_definition())
    return engine


def test_no_probe_bound_fails_the_node_honestly():
    engine = _engine(Path("."))
    run = engine.start_run("wf_net")
    run = engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.FAILED
    failed = [e for e in engine.events.get_events(run.run_id) if e.event_type == "node_failed"]
    assert any("no connectivity_probe is bound" in str(event.payload) for event in failed)


def test_unbounded_wait_is_refused():
    engine = _engine(Path("."))
    engine.register_definition(_definition(timeout_seconds=None), allow_replace=True)
    engine.connectivity_probe = lambda: False
    run = engine.start_run("wf_net")
    run = engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.FAILED
    failed = [e for e in engine.events.get_events(run.run_id) if e.event_type == "node_failed"]
    assert any("unbounded connectivity wait is refused" in str(event.payload) for event in failed)


def test_probe_measures_reachable_and_completes_immediately():
    engine = _engine(Path("."))
    engine.connectivity_probe = lambda: True
    run = engine.start_run("wf_net")
    run = engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.COMPLETED
    node = engine._run_graphs[run.run_id].nodes["wait_net"]
    assert node.output["released_by"] == "probe"
    assert "measured reachable" in node.evidence[-1]


def test_probe_down_parks_the_run_in_its_own_state():
    engine = _engine(Path("."))
    engine.connectivity_probe = lambda: False
    run = engine.start_run("wf_net")
    run = engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.WAITING_CONNECTIVITY
    assert run.node_states["wait_net"] == NodeStatus.WAITING
    types = [e.event_type for e in engine.events.get_events(run.run_id)]
    assert "connectivity_wait_registered" in types
    assert "connectivity_wait_parked" in types
    # A parked run is not dispatchable: stepping again must not advance work
    # the engine is holding.
    again = engine.execute_step(run.run_id)
    assert again.status is WorkflowRunStatus.WAITING_CONNECTIVITY


def test_signal_release_is_journalled_as_asserted_not_measured():
    engine = _engine(Path("."))
    state = {"value": False}
    engine.connectivity_probe = lambda: state["value"]
    run = engine.start_run("wf_net")
    engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.WAITING_CONNECTIVITY
    engine.signal_event(run.run_id, DEFAULT_RELEASE_EVENT, payload={"source": "operator"})
    assert engine.get_run(run.run_id).node_states["wait_net"] == NodeStatus.READY
    run = engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.COMPLETED
    node = engine._run_graphs[run.run_id].nodes["wait_net"]
    assert node.output["released_by"] == "signal"
    assert "asserted, not measured" in node.evidence[-1]
    assert run.state["wait_net_event_payload"] == {"source": "operator"}


def test_sweep_releases_a_measured_link_and_unparks_the_run():
    engine = _engine(Path("."))
    state = {"up": False}
    engine.connectivity_probe = lambda: state["up"]
    run = engine.start_run("wf_net")
    engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.WAITING_CONNECTIVITY
    state["up"] = True
    swept = engine.sweep_expired_waits(run.run_id)
    # The RUN un-parks, not only the node: the scheduler refuses a parked run,
    # so a recovered link must not leave the run reading as still waiting.
    assert swept.status is WorkflowRunStatus.RUNNING
    assert swept.node_states["wait_net"] == NodeStatus.READY
    released = [e for e in engine.events.get_events(run.run_id) if e.event_type == "connectivity_wait_released"]
    assert released
    run = engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.COMPLETED
    node = engine._run_graphs[run.run_id].nodes["wait_net"]
    # The completing dispatch re-measures; the evidence names the probe.
    assert node.output["released_by"] == "probe"


def test_sweep_fails_a_wait_whose_deadline_passed():
    engine = _engine(Path("."))
    engine.register_definition(_definition(timeout_seconds=0.05), allow_replace=True)
    engine.connectivity_probe = lambda: False
    run = engine.start_run("wf_net")
    engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.WAITING_CONNECTIVITY
    # A 0.05s deadline, aged past by real time.
    time.sleep(0.06)
    swept = engine.sweep_expired_waits(run.run_id)
    assert swept.status is WorkflowRunStatus.FAILED
    failed = [e for e in engine.events.get_events(run.run_id) if e.event_type == "node_failed"]
    assert any("connectivity wait on node 'wait_net' expired" in str(event.payload) for event in failed)


def test_probe_that_raises_fails_the_node_with_its_reason():
    engine = _engine(Path("."))

    def _broken() -> bool:
        raise OSError("probe socket refused")

    engine.connectivity_probe = _broken
    run = engine.start_run("wf_net")
    run = engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.FAILED
    failed = [e for e in engine.events.get_events(run.run_id) if e.event_type == "node_failed"]
    assert any("OSError: probe socket refused" in str(event.payload) for event in failed)


def test_a_raised_probe_during_a_sweep_holds_the_wait():
    engine = _engine(Path("."))  # the fixture declares a 5s deadline
    state = {"mode": "down"}

    def _probe() -> bool:
        if state["mode"] == "down":
            return False
        raise OSError("probe socket refused")

    engine.connectivity_probe = _probe
    run = engine.start_run("wf_net")
    engine.execute_step(run.run_id)
    assert run.status is WorkflowRunStatus.WAITING_CONNECTIVITY
    state["mode"] = "broken"
    swept = engine.sweep_expired_waits(run.run_id)
    # A broken probe is not a reachable link: the wait is HELD and the failure
    # is journalled, so a broken probe never fabricates a release.  The
    # deadline-as-backstop is pinned by the expiry test above; here the point
    # is that a raising probe holds rather than resolves.
    assert swept.status is WorkflowRunStatus.WAITING_CONNECTIVITY
    types = [e.event_type for e in engine.events.get_events(run.run_id)]
    assert "connectivity_probe_failed" in types
    assert "connectivity_wait_released" not in types
