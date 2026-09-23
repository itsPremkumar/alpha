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


class _ModelConfig:
    """Truthy model layer: enough to get past the no-models fail-fast gate.

    Model resolution itself is stubbed inside the retry test below, so no
    provider is ever contacted.
    """

    models: list = ["stub-model"]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    import alpha.bots.registry as bot_reg
    import alpha.groups.runner as runner
    import alpha.groups.service as grp_svc

    monkeypatch.setattr(bot_reg, "_global_registry", None)
    monkeypatch.setattr(bot_reg, "_global_registry_path", None)
    monkeypatch.setattr(grp_svc, "_global_groups", None)
    monkeypatch.setattr(grp_svc, "_global_groups_path", None)
    monkeypatch.setattr(runner, "_global_runner", None)
    monkeypatch.setattr(runner, "_global_runner_path", None)
    # Deterministic: no chat models, so background execution fails fast with
    # a clear room receipt instead of calling providers.
    import alpha.config as agent_workspace_config

    monkeypatch.setattr(agent_workspace_config, "get_app_config", lambda: _NoModelConfig())
    yield


async def _wait_for_terminal(run_id: str, timeout_seconds: float = 30.0):
    from alpha.groups.runner import get_group_run_service

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
    from alpha.groups.runner import get_group_run_service
    from alpha.groups.service import get_group_chat_service

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
    from alpha.groups.runner import get_group_run_service

    svc = get_group_run_service()
    run = svc.start_run("beta", "Do things", members=["architect"])
    assert svc.cancel_run(run.run_id) is True
    # Second cancel on a terminal run is a no-op.
    terminal = await _wait_for_terminal(run.run_id)
    assert terminal.status in {"cancelled", "failed", "succeeded"}
    assert svc.cancel_run(run.run_id) is False


async def test_list_runs_filters_by_room() -> None:
    from alpha.groups.runner import get_group_run_service

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
    from alpha.groups.runner import get_group_run_service

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


async def test_member_failure_is_retried_before_being_recorded(monkeypatch) -> None:
    """A FAILED member execution must feed run.max_retries.

    Before the fix only infrastructure exceptions retried, so the commonest
    failure mode — the model call itself failing — was recorded on the first
    hit with no retry despite max_retries=2. Teeth: with the old code the
    member output is the first-failure message and retry_counts stays empty.
    """
    import alpha.config as cfg
    import alpha.utils.assembly_io as assembly_io

    monkeypatch.setattr(cfg, "get_app_config", lambda: _ModelConfig())

    from alpha.subagents import config as subagent_config
    from alpha.subagents import executor as executor_mod

    # Model-name resolution and the tool assembly both touch provider/MCP
    # layers; stub them at source (imported inside _execute on each call).
    monkeypatch.setattr(subagent_config, "resolve_subagent_model_name", lambda *args, **kwargs: "stub-model")

    async def _fake_assembly(*args, **kwargs):
        return []

    monkeypatch.setattr(assembly_io, "run_assembly", _fake_assembly)

    class _FakeExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None, **kwargs):
            # Execution id == task_id: unique per attempt + synthesis.
            return task_id

    # NOTE: tests/conftest.py replaces alpha.subagents.executor in sys.modules
    # with a mock (the real module has a circular import; see conftest:29-41),
    # where SubagentStatus is the bare MagicMock class. Bind a real enum the
    # runner can identity-compare against, and plain namespaces for results —
    # the runner only reads .status/.result/.error.
    from enum import Enum
    from types import SimpleNamespace

    class _Status(Enum):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"
        TIMED_OUT = "timed_out"

        @property
        def is_terminal(self) -> bool:
            return self in {
                type(self).COMPLETED,
                type(self).FAILED,
                type(self).CANCELLED,
                type(self).TIMED_OUT,
            }

    monkeypatch.setattr(executor_mod, "SubagentStatus", _Status)

    def _fake_result(execution_id):
        if execution_id.endswith(":synthesis"):
            return SimpleNamespace(status=_Status.COMPLETED, result="Merged final deliverable.", error=None)
        if ":attempt1" in execution_id:
            return SimpleNamespace(status=_Status.FAILED, result=None, error="model call exploded")
        return SimpleNamespace(status=_Status.COMPLETED, result="final member output", error=None)

    monkeypatch.setattr(executor_mod, "SubagentExecutor", _FakeExecutor)
    monkeypatch.setattr(executor_mod, "get_background_task_result", _fake_result)

    from alpha.groups.runner import get_group_run_service

    svc = get_group_run_service()
    run = svc.start_run("epsilon", "Deliver despite a flaky model", members=["architect"])

    terminal = await _wait_for_terminal(run.run_id, timeout_seconds=45)
    assert terminal.status == "succeeded"
    assert terminal.retry_counts.get("architect") == 1, "first FAILED must be retried, not recorded"
    member_entry = terminal.member_results.get("architect", {})
    assert member_entry.get("output") == "final member output"
    assert terminal.synthesis == "Merged final deliverable."
