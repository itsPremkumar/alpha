"""Tests for the Dynamic Workflow Gateway observability / control routes.

Covers the endpoints added for the runtime-correctness wave:

- ``GET  /runs/{id}/history``  — ordered, replayable timeline with stable 1-based
  indexes that a fork can quote back.
- ``GET  /runs/{id}/report``   — measured execution report, and the explicit
  absence of any acceptance verdict.
- ``POST /runs/{id}/fork``     — branch a new run without mutating the source.
- ``POST /simulate``           — side-effect-free dry run, clearly labelled.
- ``POST /runs/{id}/suspend`` / ``resume`` — park and release a live run.
- ``POST /runs/{id}/signals``  — deliver an external event to a parked wait.
- ``POST /runs/{id}/sweep-waits`` — fail a wait nobody satisfied.
- ``GET  /system/executors``   — what is actually bound, and the opt-in nature
  of the domain executors made visible.

The honesty rules are asserted at the REST boundary too: a bad fork point is a
400 with the real reason, a fork of an unknown run is a 404, resuming a run that
is not suspended is a 409, and a dry run never claims acceptance.
"""

from __future__ import annotations

import itertools
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import alpha.orchestrator.executors as executors_module
from alpha.orchestrator.executors import DIGEST_EXECUTOR, ExecutorRegistry
from app.gateway.routers.workflows import (
    WorkflowCreateRequest,
    WorkflowForkRequest,
    WorkflowRunCreateRequest,
    WorkflowSignalRequest,
    WorkflowSimulateRequest,
    fork_workflow_run,
    get_workflow_engine,
    get_workflow_run_history,
    get_workflow_run_report,
    list_workflow_executors,
    resume_workflow_run,
    signal_workflow_run,
    simulate_workflow,
    start_workflow_run,
    step_workflow_run,
    suspend_workflow_run,
    sweep_workflow_waits,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolate_agent_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _bind_digest_registry(monkeypatch):
    registry = ExecutorRegistry()
    monkeypatch.setattr(executors_module, "_REGISTRY", registry)
    registry.register(DIGEST_EXECUTOR, executors_module.local_digest_executor)
    return registry


def _request() -> MagicMock:
    """A request double; the router treats a non-Starlette request as unscoped."""
    return MagicMock()


_WORKFLOW_SEQ = itertools.count(1)


async def _register_two_step(request: MagicMock) -> tuple[str, str]:
    """Register a two-step workflow and start a run of it.

    Returns ``(workflow_id, run_id)``.  The id is generated per call because the
    router owns ONE module-global engine: a reused id would collide with a
    definition a previous test left registered instead of being reset between
    tests.
    """
    from app.gateway.routers.workflows import register_workflow

    workflow_id = f"obs_wf_{next(_WORKFLOW_SEQ)}"
    await register_workflow(
        WorkflowCreateRequest(
            id=workflow_id,
            name="Observability workflow",
            graph={
                "version": 1,
                "nodes": {
                    "step1": {"id": "step1", "prompt": "first", "executor": DIGEST_EXECUTOR},
                    "step2": {"id": "step2", "prompt": "second", "executor": DIGEST_EXECUTOR, "depends_on": ["step1"]},
                },
                "edges": [],
            },
        ),
        request,
    )
    run = await start_workflow_run(workflow_id, WorkflowRunCreateRequest(), request)
    return workflow_id, str(run["run_id"])


# ------------------------------------------------------------------- history


async def test_history_returns_ordered_events():
    request = _request()
    _, run_id = await _register_two_step(request)
    await step_workflow_run(run_id, request)

    payload = await get_workflow_run_history(run_id, request)

    assert payload["run_id"] == run_id
    assert payload["count"] == len(payload["events"])
    assert payload["events"][0]["event_type"] == "workflow_started"
    indexes = [entry["index"] for entry in payload["events"]]
    assert indexes == list(range(1, len(indexes) + 1)), "indexes must be stable and 1-based"
    assert any(entry["node_id"] == "step1" for entry in payload["events"])


async def test_history_of_an_unknown_run_is_404():
    with pytest.raises(HTTPException) as excinfo:
        await get_workflow_run_history("run_nope", _request())
    assert excinfo.value.status_code == 404


# -------------------------------------------------------------------- report


async def test_report_carries_measurements_and_no_acceptance_verdict():
    request = _request()
    _, run_id = await _register_two_step(request)
    await step_workflow_run(run_id, request)

    report = await get_workflow_run_report(run_id, request)

    observability = report["observability"]
    assert observability["run_id"] == run_id
    assert observability["timeline_source"] == "event_log"
    assert observability["measured_executions"] == 1
    assert observability["timeline"], "a stepped node must have a measured window"
    assert observability["critical_path"]["path"]
    assert observability["critical_path"]["complete"] is True
    # Execution reported; acceptance never claimed.
    assert "acceptance_passed" not in observability
    assert "verified" not in observability


async def test_report_of_an_unknown_run_is_404():
    with pytest.raises(HTTPException) as excinfo:
        await get_workflow_run_report("run_nope", _request())
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------- fork


async def test_fork_creates_a_new_run_and_leaves_the_source_alone():
    request = _request()
    _, run_id = await _register_two_step(request)
    await step_workflow_run(run_id, request)
    source = get_workflow_engine().get_run(run_id)
    before_completed = list(source.completed_nodes)
    before_history = len(get_workflow_engine().events.get_events(run_id))

    forked = await fork_workflow_run(run_id, WorkflowForkRequest(), request)

    assert forked["run_id"] != run_id
    assert forked["source_run_id"] == run_id
    assert forked["inherited_completed_nodes"] == before_completed
    assert forked["status"] == "running"
    # The source is untouched: same completions, same log depth.
    assert source.completed_nodes == before_completed
    assert len(get_workflow_engine().events.get_events(run_id)) == before_history


async def test_fork_from_a_named_event_inherits_only_prior_work():
    request = _request()
    _, run_id = await _register_two_step(request)
    await step_workflow_run(run_id, request)

    history = await get_workflow_run_history(run_id, request)
    fork_event = next(entry for entry in history["events"] if entry["event_type"] == "node_completed")

    forked = await fork_workflow_run(run_id, WorkflowForkRequest(at_event_id=fork_event["event_id"]), request)

    assert forked["forked_at_event_id"] == fork_event["event_id"]
    assert forked["inherited_completed_nodes"] == ["step1"]


async def test_fork_with_an_unknown_event_id_is_400_with_the_real_reason():
    request = _request()
    _, run_id = await _register_two_step(request)
    with pytest.raises(HTTPException) as excinfo:
        await fork_workflow_run(run_id, WorkflowForkRequest(at_event_id="nope"), request)
    assert excinfo.value.status_code == 400
    assert "not in the source run" in str(excinfo.value.detail)


async def test_fork_of_an_unknown_run_is_404():
    with pytest.raises(HTTPException) as excinfo:
        await fork_workflow_run("run_nope", WorkflowForkRequest(), _request())
    assert excinfo.value.status_code == 404


async def test_reset_completed_nodes_is_disclosed_as_dangerous():
    request = _request()
    _, run_id = await _register_two_step(request)
    await step_workflow_run(run_id, request)

    forked = await fork_workflow_run(run_id, WorkflowForkRequest(reset_completed_nodes=True), request)

    assert forked["inherited_completed_nodes"] == []
    assert any("idempotency keys cannot protect them" in note for note in forked["notes"])


# ----------------------------------------------------------------- simulate


async def test_simulate_is_labelled_and_creates_no_live_run():
    request = _request()
    workflow_id, _ = await _register_two_step(request)
    engine = get_workflow_engine()
    runs_before = set(engine.runs)

    payload = await simulate_workflow(WorkflowSimulateRequest(workflow_id=workflow_id), request)

    assert payload["simulated"] is True
    assert payload["execution_label"] == "dry_run_simulation"
    assert payload["status"] == "completed"
    assert payload["nodes_visited"] == ["step1", "step2"]
    assert "acceptance_passed" not in payload
    assert set(engine.runs) == runs_before, "a dry run must not add a live run"


async def test_simulate_of_an_unknown_workflow_is_404():
    with pytest.raises(HTTPException) as excinfo:
        await simulate_workflow(WorkflowSimulateRequest(workflow_id="ghost"), _request())
    assert excinfo.value.status_code == 404


async def test_simulate_rejects_a_hostile_wave_ceiling_at_the_boundary():
    """The bound is enforced by the request model, before the handler runs.

    A client cannot reach the handler with ``max_waves: 0`` at all, which is the
    stronger guarantee: there is no code path where an unbounded wave count is
    accepted and only clamped later.
    """
    import pydantic

    workflow_id, _ = await _register_two_step(_request())
    with pytest.raises(pydantic.ValidationError):
        WorkflowSimulateRequest(workflow_id=workflow_id, max_waves=0)


# --------------------------------------------------------- suspend and resume


async def test_suspend_parks_a_run_and_resume_releases_it():
    request = _request()
    _, run_id = await _register_two_step(request)

    suspended = await suspend_workflow_run(run_id, request, reason="operator hold")
    assert suspended["status"] == "suspended"

    # A step while suspended must not perform work.
    stepped = await step_workflow_run(run_id, request)
    assert stepped["status"] == "suspended"
    assert stepped["completed_nodes"] == []

    resumed = await resume_workflow_run(run_id, request)
    assert resumed["status"] == "running"
    stepped = await step_workflow_run(run_id, request)
    assert stepped["completed_nodes"] == ["step1"]


async def test_resuming_a_run_that_is_not_suspended_is_409():
    request = _request()
    _, run_id = await _register_two_step(request)
    with pytest.raises(HTTPException) as excinfo:
        await resume_workflow_run(run_id, request)
    assert excinfo.value.status_code == 409
    assert "not suspended" in str(excinfo.value.detail)


async def test_suspend_of_an_unknown_run_is_404():
    with pytest.raises(HTTPException) as excinfo:
        await suspend_workflow_run("run_nope", _request())
    assert excinfo.value.status_code == 404


# ------------------------------------------------------------------- signals


async def test_signal_releases_a_parked_event_wait():
    request = _request()
    from app.gateway.routers.workflows import register_workflow

    await register_workflow(
        WorkflowCreateRequest(
            id="signal_wf_a",
            name="Signal workflow",
            graph={
                "version": 1,
                "nodes": {
                    "waiter": {"id": "waiter", "type": "event_wait", "config": {"event": "deploy.ok"}},
                    "after": {"id": "after", "prompt": "continue", "executor": DIGEST_EXECUTOR, "depends_on": ["waiter"]},
                },
                "edges": [],
            },
        ),
        request,
    )
    run = await start_workflow_run("signal_wf_a", WorkflowRunCreateRequest(), request)
    run_id = run["run_id"]

    parked = await step_workflow_run(run_id, request)
    assert parked["status"] == "waiting_event"

    signalled = await signal_workflow_run(run_id, WorkflowSignalRequest(event="deploy.ok", payload={"by": "prem"}), request)
    assert signalled["matched_nodes"] == ["waiter"]
    assert signalled["released_nodes"] == ["waiter"]
    assert signalled["unmatched"] is False
    assert signalled["run"]["status"] == "running"

    stepped = await step_workflow_run(run_id, request)
    assert stepped["node_states"]["waiter"] == "succeeded"
    assert stepped["state"]["waiter_event_payload"] == {"by": "prem"}


async def test_an_unmatched_signal_advances_nothing():
    request = _request()
    from app.gateway.routers.workflows import register_workflow

    await register_workflow(
        WorkflowCreateRequest(
            id="signal_wf_b",
            name="Signal workflow",
            graph={
                "version": 1,
                "nodes": {"waiter": {"id": "waiter", "type": "event_wait", "config": {"event": "real.event"}}},
                "edges": [],
            },
        ),
        request,
    )
    run_id = (await start_workflow_run("signal_wf_b", WorkflowRunCreateRequest(), request))["run_id"]
    await step_workflow_run(run_id, request)

    signalled = await signal_workflow_run(run_id, WorkflowSignalRequest(event="typo.event"), request)

    assert signalled["matched_nodes"] == []
    assert signalled["unmatched"] is True
    assert signalled["run"]["status"] == "waiting_event"
    assert signalled["run"]["node_states"]["waiter"] == "waiting"


async def test_sweep_fails_a_wait_nobody_satisfied():
    import time

    request = _request()
    from app.gateway.routers.workflows import register_workflow

    await register_workflow(
        WorkflowCreateRequest(
            id="sweep_wf_a",
            name="Sweep workflow",
            graph={
                "version": 1,
                "nodes": {
                    "waiter": {
                        "id": "waiter",
                        "type": "event_wait",
                        "config": {"event": "never", "timeout_seconds": 0.0},
                    }
                },
                "edges": [],
            },
        ),
        request,
    )
    run_id = (await start_workflow_run("sweep_wf_a", WorkflowRunCreateRequest(), request))["run_id"]
    await step_workflow_run(run_id, request)
    time.sleep(0.02)

    swept = await sweep_workflow_waits(run_id, request)

    assert swept["node_states"]["waiter"] == "failed"
    assert swept["status"] == "failed"


# ----------------------------------------------------------------- executors


async def test_executor_listing_makes_the_opt_in_nature_visible():
    payload = await list_workflow_executors(_request())

    assert DIGEST_EXECUTOR in payload["bound"]
    assert "alpha.local.model" in payload["domain_executors"]
    # Not bound by default: importing a module must never start spending tokens.
    assert payload["domain_bound"] == []
    assert "not a claim that any" in payload["note"]
