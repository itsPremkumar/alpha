"""W-N1 durability at the REST layer: journaled events, projection, hydration, plans.

The real handlers are called directly (same pattern as
``test_dynamic_workflow_router``), and ``ALPHA_HOME`` is redirected
per test, so the durable store is always a temp dir. The gateway's sink is
attached to the GLOBAL event dispatcher at router import, so events emitted by
a real run through the real kernel are journaled for real — these tests assert
the journal, not a mock.

Every disk path is offloaded with ``asyncio.to_thread`` inside the handlers, so
the event loop is never blocked; the endpoints' own correctness (honest
refusals, degraded reports) is what is pinned here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from alpha.orchestrator.executors import DIGEST_EXECUTOR
from app.gateway.routers.workflows import (
    WorkflowCreateRequest,
    WorkflowRunCreateRequest,
    _store,
    get_durable_workflow_events,
    get_workflow_engine,
    get_workflow_run,
    hydrate_workflow_engine,
    list_workflow_plans,
    project_workflow_run,
    record_workflow_plan,
    recover_workflow_run,
    register_workflow,
    start_workflow_run,
    step_workflow_run,
    workflow_durability_status,
)


@pytest.fixture(autouse=True)
def _isolate_alpha(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))


async def _register_and_start(workflow_id: str = "wf_rest"):
    req = MagicMock()
    await register_workflow(
        WorkflowCreateRequest(
            id=workflow_id,
            name="REST durability",
            description="journaled through the real sink",
            graph={
                "version": 1,
                "nodes": {"only": {"id": "only", "prompt": "persist me"}},
                "edges": [],
            },
        ),
        req,
    )
    run = await start_workflow_run(workflow_id, WorkflowRunCreateRequest(initial_state={"k": "v"}), req)
    return req, run["run_id"]


@pytest.mark.asyncio
async def test_durability_status_reports_real_store_and_sink() -> None:
    req = MagicMock()
    payload = await workflow_durability_status(req)
    assert payload["dispatcher"]["attached"] is True
    assert payload["store"]["writable"] is True
    assert payload["store"]["writable_detail"] == "writable"
    assert payload["store"]["persisted_run_count"] == 0
    assert payload["store"]["store_dir"].endswith("workflow_store")


@pytest.mark.asyncio
async def test_real_run_events_are_journaled_durably() -> None:
    req, run_id = await _register_and_start("wf_journal")

    payload = await get_durable_workflow_events(run_id, req)
    assert payload["count"] >= 1
    assert payload["corrupt_tail"] == []
    types = [event["event_type"] for event in payload["events"]]
    assert "workflow_started" in types
    # Sequence numbers are writer-assigned and monotonic, never gaps.
    seqs = [event["seq"] for event in payload["events"]]
    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))


@pytest.mark.asyncio
async def test_terminal_event_automatically_projects_current_run() -> None:
    """A normal terminal wave leaves a restart-readable projection without /project."""
    req = MagicMock()
    await register_workflow(
        WorkflowCreateRequest(
            id="wf_auto_projection",
            name="Automatic projection",
            graph={
                "version": 1,
                "nodes": {
                    "only": {
                        "id": "only",
                        "prompt": "project me",
                        "executor": DIGEST_EXECUTOR,
                    }
                },
                "edges": [],
            },
        ),
        req,
    )
    started = await start_workflow_run("wf_auto_projection", WorkflowRunCreateRequest(), req)
    completed = await step_workflow_run(started["run_id"], req)
    assert completed["status"] == "completed"

    snapshot = _store().load_snapshot(started["run_id"])
    assert snapshot is not None
    assert snapshot.run.status.value == "completed"
    assert snapshot.last_seq == _store().last_seq(started["run_id"])
    assert "wf_auto_projection:v1" in snapshot.graphs


@pytest.mark.asyncio
async def test_project_then_hydrate_restores_the_run() -> None:
    req, run_id = await _register_and_start("wf_project")

    projection = await project_workflow_run(run_id, req)
    assert projection["run_id"] == run_id
    assert projection["last_seq"] >= 1
    assert projection["definition_recorded"] is True
    assert projection["graph_versions"] == ["wf_project:v1"]

    # Simulate a fresh process: the in-memory engine forgets the run while the
    # durable store keeps the truth.
    get_workflow_engine().runs.clear()
    assert get_workflow_engine().get_run(run_id) is None

    report = await hydrate_workflow_engine(req)
    assert report["status"] in {"ok", "degraded"}
    assert run_id in report["hydrated_runs"]
    restored = await get_workflow_run(run_id, req)
    assert restored["state"] == {"k": "v"}


@pytest.mark.asyncio
async def test_projection_refuses_a_corrupt_tail_with_409() -> None:
    req, run_id = await _register_and_start("wf_corrupt")
    with _store().event_log_path(run_id).open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")

    with pytest.raises(HTTPException) as exc_info:
        await project_workflow_run(run_id, req)
    assert exc_info.value.status_code == 409
    assert "corrupt tail" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_hydrate_reports_stale_projection_as_degraded() -> None:
    req, run_id = await _register_and_start("wf_stale_rest")
    await project_workflow_run(run_id, req)
    # The run journals another event AFTER the projection was taken.
    get_workflow_engine().events.emit("node_started", run_id, node_id="only")

    get_workflow_engine().runs.clear()
    report = await hydrate_workflow_engine(req)
    assert report["status"] == "degraded"
    assert report["hydrated_runs"] == []
    assert report["stale_projections"][0]["run_id"] == run_id


@pytest.mark.asyncio
async def test_stale_run_is_not_served_after_refused_hydration() -> None:
    req, run_id = await _register_and_start("wf_stale_404")
    await project_workflow_run(run_id, req)
    get_workflow_engine().events.emit("node_started", run_id, node_id="only")
    get_workflow_engine().runs.clear()
    await hydrate_workflow_engine(req)

    with pytest.raises(HTTPException) as exc_info:
        await get_workflow_run(run_id, req)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_recover_rebuilds_a_stale_run_and_frees_its_orphaned_node() -> None:
    """The explicit way back from a crash the hydration gate refused.

    A mid-node crash advances the journal past the projection, so hydrate
    correctly refuses the run and it becomes unreachable. Recovery folds the
    real log back in, frees the node a dead worker left RUNNING, and
    re-materializes the projection so the next startup does not refuse again.
    """
    req, run_id = await _register_and_start("wf_recover")
    await project_workflow_run(run_id, req)
    # The crash: a node_started the projection never saw.
    get_workflow_engine().events.emit("node_started", run_id, node_id="only")
    get_workflow_engine().runs.clear()
    await hydrate_workflow_engine(req)

    with pytest.raises(HTTPException) as refused:
        await get_workflow_run(run_id, req)
    assert refused.value.status_code == 404, "the fail-closed refusal must still hold"

    report = await recover_workflow_run(run_id, req)

    assert report["events_folded"] >= 2
    assert report["run_status"] == "running"
    reconciled = report["reconciled"]
    assert reconciled, "a node left RUNNING by a dead worker must be reported"
    assert reconciled[0]["action"] == "failed"
    assert reconciled[0]["failure_class"] == "worker_lost"
    assert "no longer present" in reconciled[0]["reason"]

    restored = await get_workflow_run(run_id, req)
    assert restored["run_id"] == run_id

    # The projection now matches the journal, including the recovery events
    # reconciliation itself appended.
    get_workflow_engine().runs.clear()
    again = await hydrate_workflow_engine(req)
    assert again["stale_projections"] == []
    assert run_id in again["hydrated_runs"]


@pytest.mark.asyncio
async def test_recovering_an_unknown_run_is_a_disclosed_404() -> None:
    req = MagicMock()
    with pytest.raises(HTTPException) as exc_info:
        await recover_workflow_run("run_never_journaled", req)
    assert exc_info.value.status_code == 404
    assert "cannot be recovered" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_recover_never_reports_the_rebuilt_run_as_verified() -> None:
    req, run_id = await _register_and_start("wf_recover_honest")
    await project_workflow_run(run_id, req)
    get_workflow_engine().events.emit("node_started", run_id, node_id="only")
    get_workflow_engine().runs.clear()
    await hydrate_workflow_engine(req)

    report = await recover_workflow_run(run_id, req)

    assert "verified" not in report
    assert "acceptance" not in report
    assert report["projection"]["last_seq"] > 0


@pytest.mark.asyncio
async def test_plan_recording_is_append_only_and_history_is_readable() -> None:
    req, _ = await _register_and_start("wf_plans")

    first = await record_workflow_plan("wf_plans", req)
    assert first["version"] == 1
    assert first["source"] == "manual"

    with pytest.raises(HTTPException) as exc_info:
        await record_workflow_plan("wf_plans", req)
    assert exc_info.value.status_code == 409
    assert "already exists" in str(exc_info.value.detail)

    history = await list_workflow_plans("wf_plans", req)
    assert history["versions"] == [1]
    assert history["count"] == 1
    assert history["latest_source"] == "manual"


@pytest.mark.asyncio
async def test_plan_history_of_unknown_workflow_is_empty_not_fabricated() -> None:
    req = MagicMock()
    history = await list_workflow_plans("wf_never_seen", req)
    assert history == {
        "workflow_id": "wf_never_seen",
        "versions": [],
        "count": 0,
        "latest_source": None,
    }
