"""Gateway routes for the v2 dynamic-workflow surfaces.

Route-layer properties pinned here (the engine/store behaviour behind them is
pinned by ``test_workflow_triggers.py``, ``test_workflow_quarantine.py``,
``test_workflow_drift.py``, ``test_workflow_worktrees.py`` and
``test_workflow_connectivity.py``):

* ``/triggers/due`` is declared BEFORE ``/triggers/{trigger_id}`` — the same
  Starlette registration-order trap the skills and system routes document, so
  this test fails if the order is ever flipped;
* an unrunnable schedule is refused at creation with the real reason, so
  nothing is stored that could never fire;
* a fire reports the started run and advances the schedule exactly once; a
  disabled trigger refuses without consuming anything;
* a replay applies a REAL ``retry_node`` patch through the engine's own apply
  path, and a refused replay leaves the record open rather than closed;
* drift and worktree reads are honest about their absence (``no_goal``, empty
  claims, 400 for a node with no worktree) instead of inventing a payload.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.gateway.routers.workflows import (
    QuarantineDiscardRequest,
    QuarantineReplayRequest,
    WorkflowCreateRequest,
    WorkflowTriggerCreateRequest,
    arm_workflow_trigger,
    create_workflow_trigger,
    delete_workflow_trigger,
    disarm_workflow_trigger,
    discard_quarantined_node,
    due_workflow_triggers,
    fire_workflow_trigger,
    get_quarantined_node,
    get_workflow_engine,
    get_workflow_run_drift,
    get_workflow_trigger,
    get_workflow_trigger_store,
    list_quarantined_nodes,
    list_run_worktrees,
    list_workflow_triggers,
    register_workflow,
    release_run_worktree,
    replay_quarantined_node,
)


@pytest.fixture(autouse=True)
def _isolate_alpha(tmp_path, monkeypatch):
    """ALPHA_HOME points at a per-test temp dir (process-global env)."""
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))


async def _register(workflow_id: str) -> str:
    """Register one workflow under a caller-chosen id.

    ``get_workflow_engine()`` is a process-global singleton, so the engine's
    definitions outlive a single test. Each test therefore registers its own
    distinct id rather than reusing ``wf_new_1``; re-registering the same id
    after a run has advanced the definition's graph projection would be a real
    409, not a fixture artifact. Callers thread the returned id into every
    trigger, run and quarantine record they create.
    """
    request = MagicMock()
    body = WorkflowCreateRequest(
        id=workflow_id,
        name="v2 fixture",
        graph={
            "version": 1,
            "nodes": {"only": {"id": "only", "prompt": "do the thing", "executor": "alpha.local.digest"}},
            "edges": [],
        },
    )
    await register_workflow(body, request)
    return workflow_id


def _trigger_body(workflow_id: str, **overrides) -> WorkflowTriggerCreateRequest:
    fields = {
        "workflow_id": workflow_id,
        "kind": "interval",
        "interval_seconds": 3600.0,
        "input_state": {"objective": "nightly audit"},
    }
    fields.update(overrides)
    return WorkflowTriggerCreateRequest(**fields)


# --------------------------------------------------------------------- triggers


@pytest.mark.asyncio
async def test_trigger_crud_round_trip():
    workflow_id = await _register("wf_crud")
    request = MagicMock()
    created = await create_workflow_trigger(_trigger_body(workflow_id), request)
    assert created["kind"] == "interval"
    assert created["enabled"] is True
    assert created["next_fire_at"] is not None

    listed = await list_workflow_triggers(request)
    assert listed["count"] == 1
    assert listed["owner_scoped"] is False  # a MagicMock request resolves no owner

    fetched = await get_workflow_trigger(created["trigger_id"], request)
    assert fetched["trigger_id"] == created["trigger_id"]
    assert fetched["input_state"] == {"objective": "nightly audit"}


@pytest.mark.asyncio
async def test_due_route_is_declared_before_the_id_catch_all():
    """``/triggers/due`` must resolve as itself, not as a trigger id.

    The route table is static, so this asserts through the registrar's own
    ordering rather than a live HTTP call: if ``/triggers/{trigger_id}`` were
    declared first, ``due`` would be read as an id and 404 — the trap the
    skills routes pin.
    """
    from app.gateway.routers.workflows import router

    paths = [(route.path, getattr(route.endpoint, "__name__", "")) for route in router.routes]
    due_at = next(index for index, (path, _name) in enumerate(paths) if path == "/api/workflows/triggers/due")
    id_at = next(index for index, (path, _name) in enumerate(paths) if path == "/api/workflows/triggers/{trigger_id}")
    assert due_at < id_at, "/triggers/due must be declared before /triggers/{trigger_id}"
    listed = await due_workflow_triggers(MagicMock())
    assert listed == {"due": [], "count": 0, "evaluated_at": listed["evaluated_at"]}


@pytest.mark.asyncio
async def test_unknown_workflow_is_refused_at_creation():
    request = MagicMock()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await create_workflow_trigger(_trigger_body(workflow_id="nope"), request)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unsatisfiable_cron_is_refused_with_the_real_reason():
    workflow_id = await _register("wf_cron")
    request = MagicMock()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await create_workflow_trigger(_trigger_body(workflow_id, kind="cron", expression="0 0 30 2 *", interval_seconds=None), request)
    assert exc.value.status_code == 400
    assert "never fire" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_arm_disarm_and_delete():
    workflow_id = await _register("wf_arm")
    request = MagicMock()
    created = await create_workflow_trigger(_trigger_body(workflow_id), request)
    disarmed = await disarm_workflow_trigger(created["trigger_id"], request, reason="hold for review")
    assert disarmed["enabled"] is False
    assert disarmed["disabled_reason"] == "hold for review"
    armed = await arm_workflow_trigger(created["trigger_id"], request)
    assert armed["enabled"] is True
    deleted = await delete_workflow_trigger(created["trigger_id"], request)
    assert deleted["deleted"] is True
    assert get_workflow_trigger_store().get(created["trigger_id"]) is None


@pytest.mark.asyncio
async def test_fire_starts_one_run_and_advances_once():
    workflow_id = await _register("wf_fire")
    request = MagicMock()
    created = await create_workflow_trigger(_trigger_body(workflow_id), request)
    # Make it due now.
    store = get_workflow_trigger_store()
    trigger = store.get(created["trigger_id"])
    trigger.next_fire_at = 0.0
    result = await fire_workflow_trigger(created["trigger_id"], request)
    assert result["fired"] is True
    assert result["run_id"].startswith("run_")
    assert result["fire_count"] == 1
    stored = store.get(created["trigger_id"])
    assert stored.fire_count == 1
    # The run really exists on the engine.
    assert get_workflow_engine().get_run(result["run_id"]) is not None


@pytest.mark.asyncio
async def test_fire_refuses_a_disabled_trigger_without_consuming_it():
    workflow_id = await _register("wf_fire_disabled")
    request = MagicMock()
    created = await create_workflow_trigger(_trigger_body(workflow_id), request)
    await disarm_workflow_trigger(created["trigger_id"], request, reason="hold")
    result = await fire_workflow_trigger(created["trigger_id"], request)
    assert result["fired"] is False
    assert "disabled" in result["reason"]
    assert get_workflow_trigger_store().get(created["trigger_id"]).fire_count == 0


@pytest.mark.asyncio
async def test_fire_unknown_trigger_is_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await fire_workflow_trigger("trg_missing", MagicMock())
    assert exc.value.status_code == 404


# ------------------------------------------------------------------ quarantine


@pytest.mark.asyncio
async def test_quarantine_list_replay_and_discard():
    workflow_id = await _register("wf_quar_list")
    request = MagicMock()
    engine = get_workflow_engine()
    from alpha.workflow.quarantine import QuarantineTrigger

    record = engine.quarantine.admit(
        run_id="run_absent",
        workflow_id=workflow_id,
        node_id="only",
        reason="provider returned 503 after 4 attempts",
        trigger=QuarantineTrigger.RECOVERY_EXHAUSTED,
        failure_class="provider_error",
        signature="provider returned 5xx",
        attempts=4,
    )
    listed = await list_quarantined_nodes(request)
    assert listed["count"] == 1
    assert listed["records"][0]["record_id"] == record.record_id

    fetched = await get_quarantined_node(record.record_id, request)
    assert fetched["node_id"] == "only"

    # Replay refuses honestly when the source run is not resident — and the
    # record stays OPEN with the refusal recorded, because closing it would
    # lose the work.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await replay_quarantined_node(record.record_id, QuarantineReplayRequest(note="retry"), request)
    assert exc.value.status_code == 409
    held = engine.quarantine.get(record.record_id)
    assert held.status.value == "quarantined"
    assert "not resident" in held.replay_refusal

    discarded = await discard_quarantined_node(record.record_id, QuarantineDiscardRequest(note="superseded by manual fix"), request)
    assert discarded["status"] == "discarded"


@pytest.mark.asyncio
async def test_quarantine_replay_on_a_live_run_applies_a_real_patch():
    workflow_id = await _register("wf_quar_replay")
    request = MagicMock()
    engine = get_workflow_engine()
    from alpha.workflow.quarantine import QuarantineTrigger

    run = engine.start_run(workflow_id)
    base_version = run.graph_version
    record = engine.quarantine.admit(
        run_id=run.run_id,
        workflow_id=workflow_id,
        node_id="only",
        reason="transient upstream failure",
        trigger=QuarantineTrigger.RECOVERY_EXHAUSTED,
        attempts=3,
        graph_version=run.graph_version,
    )
    outcome = await replay_quarantined_node(record.record_id, QuarantineReplayRequest(note="retry after the outage"), request)
    assert outcome["replayed_node"] == "only"
    assert outcome["graph_version"] > base_version
    stored = engine.quarantine.get(record.record_id)
    assert stored.status.value == "replayed"
    assert stored.replay_graph_version == outcome["graph_version"]
    # The node is READY again in the same run — a real patch, not a new run.
    assert engine.get_run(run.run_id).node_states["only"].value == "ready"


# ---------------------------------------------------------- drift and worktrees


@pytest.mark.asyncio
async def test_drift_route_reports_no_goal_honestly():
    workflow_id = await _register("wf_drift")
    request = MagicMock()
    engine = get_workflow_engine()
    run = engine.start_run(workflow_id)
    report = await get_workflow_run_drift(run.run_id, request)
    assert report["has_goal"] is False
    assert report["verdict"] == "no_goal"


@pytest.mark.asyncio
async def test_drift_route_404s_an_unknown_run():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_workflow_run_drift("run_missing", MagicMock())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_worktree_routes_are_honest_about_absence():
    workflow_id = await _register("wf_worktree")
    request = MagicMock()
    engine = get_workflow_engine()
    run = engine.start_run(workflow_id)
    listed = await list_run_worktrees(run.run_id, request)
    assert listed["claims"] == []
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await release_run_worktree(run.run_id, "only", request)
    assert exc.value.status_code == 400
