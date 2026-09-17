"""Autonomous team runs: one objective in, coordinated work out.

Covers run lifecycle (start -> background execution -> terminal record),
no-model fail-fast with a room receipt, cancellation, and Gateway mounting.
Model execution itself is covered by the subagent suites; here the model
layer is stubbed so these tests stay offline and deterministic.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.gateway.app import create_app
from app.gateway.routers import groups

pytestmark = pytest.mark.asyncio


class _NoModelConfig:
    models: list = []


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    import agent_workspace.bots.registry as bot_reg
    import agent_workspace.groups.runner as runner
    import agent_workspace.groups.service as grp_svc

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    monkeypatch.setattr(runner, "_global_runner", None)
    monkeypatch.setattr(runner, "_global_runner_path", None)
    # Deterministic: no chat models, so background execution fails fast with
    # a clear room receipt instead of calling providers.
    import agent_workspace.config as deerflow_config

    monkeypatch.setattr(deerflow_config, "get_app_config", lambda: _NoModelConfig())
    yield


async def _wait_for_terminal(run_id: str, timeout_seconds: float = 30.0):
    from agent_workspace.groups.runner import get_group_run_service

    async def _poll():
        while True:
            run = get_group_run_service().get_run(run_id)
            assert run is not None
            if run.status != "running":
                return run
            await asyncio.sleep(0.2)

    return await asyncio.wait_for(_poll(), timeout=timeout_seconds)


async def test_gateway_mounts_group_run_routes() -> None:
    paths = {route.path for route in create_app().routes}
    assert "/api/groups/{name}/runs" in paths
    assert "/api/groups/{name}/runs/{run_id}" in paths
    assert "/api/groups/{name}/runs/{run_id}/cancel" in paths


async def test_start_run_announces_and_fails_fast_without_models() -> None:
    from agent_workspace.groups.runner import get_group_run_service
    from agent_workspace.groups.service import get_group_chat_service

    svc = get_group_run_service()
    run = svc.start_run("alpha", "Research the topic", members=["architect", "coder"])
    assert run.status == "running"
    assert run.members == ["architect", "coder"]

    terminal = await _wait_for_terminal(run.run_id)
    assert terminal.status == "failed"
    assert "No chat models" in (terminal.error or "")

    room = get_group_chat_service().get_room("alpha")
    assert room is not None
    phases = [m.metadata.get("phase") for m in room.log]
    assert "started" in phases
    assert "failed" in phases


async def test_cancel_running_run() -> None:
    from agent_workspace.groups.runner import get_group_run_service

    svc = get_group_run_service()
    run = svc.start_run("beta", "Do things", members=["architect"])
    assert svc.cancel_run(run.run_id) is True
    # Second cancel on a terminal run is a no-op.
    terminal = await _wait_for_terminal(run.run_id)
    assert terminal.status in {"cancelled", "failed", "succeeded"}
    assert svc.cancel_run(run.run_id) is False


async def test_list_runs_filters_by_room() -> None:
    from agent_workspace.groups.runner import get_group_run_service

    svc = get_group_run_service()
    first = svc.start_run("gamma", "Objective one", members=["architect"])
    second = svc.start_run("delta", "Objective two", members=["coder"])
    try:
        gamma_runs = svc.list_runs(room_name="gamma")
        assert [r.run_id for r in gamma_runs] == [first.run_id]
        assert second.run_id not in [r.run_id for r in svc.list_runs(room_name="gamma")]
    finally:
        svc.cancel_run(first.run_id)
        svc.cancel_run(second.run_id)


async def test_router_start_run_returns_202_record() -> None:
    from agent_workspace.groups.runner import get_group_run_service

    body = groups.GroupRunRequest(objective="Ship the feature", members=["architect"])
    record = await groups.start_group_run("omega", body)
    assert record["room_name"] == "omega"
    assert record["status"] == "running"
    try:
        fetched = await groups.get_group_run("omega", record["run_id"])
        assert fetched["objective"] == "Ship the feature"
        listed = await groups.list_group_runs("omega")
        assert listed["count"] >= 1
    finally:
        get_group_run_service().cancel_run(record["run_id"])


async def test_router_rejects_empty_members_and_unknown_run() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await groups.start_group_run("empty", groups.GroupRunRequest(objective="x", members=[]))
    assert excinfo.value.status_code == 422
    with pytest.raises(HTTPException) as excinfo:
        await groups.get_group_run("empty", "grun_missing")
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException) as excinfo:
        await groups.cancel_group_run("empty", "grun_missing")
    assert excinfo.value.status_code == 404
