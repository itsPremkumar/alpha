"""Transparent run resume: continue an interrupted run from its checkpoint.

Covers POST /api/threads/{thread_id}/runs/{run_id}/resume — the recovery half
of goal integrity. A run that never reached a durable final state (Gateway
restart, timeout, interrupt) continues from the thread head checkpoint with
``metadata.resumed_from_run_id`` instead of replaying the turn from scratch.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from agent_workspace.runtime import DisconnectMode, RunRecord, RunStatus
from agent_workspace.runtime.events.store.memory import MemoryRunEventStore
from agent_workspace.runtime.runs.manager import RunManager
from agent_workspace.runtime.runs.store.memory import MemoryRunStore

THREAD_ID = "thread-resume"
RUN_ID = "run-interrupted"
USER_ID = UUID("00000000-0000-0000-0000-000000000123")
USER_ID_STR = str(USER_ID)
RESUME_URL = f"/api/threads/{THREAD_ID}/runs/{RUN_ID}/resume"


def _user() -> User:
    return User(
        id=USER_ID,
        email="resume-test@example.com",
        password_hash="x",
        system_role="user",
    )


def _resume_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    head_checkpoint_id: str | None = "ckpt-head",
    captured: dict[str, Any] | None = None,
) -> tuple[TestClient, MemoryRunStore]:
    run_store = MemoryRunStore()
    event_store = MemoryRunEventStore()
    run_manager = RunManager(store=run_store)

    async def _head_checkpoint(request: Any, thread_id: str) -> str | None:
        return head_checkpoint_id

    async def _fake_start_run(body: Any, thread_id: str, request: Any, **kwargs: Any) -> RunRecord:
        if captured is not None:
            captured["body"] = body
            captured["thread_id"] = thread_id
            captured["kwargs"] = kwargs
        return RunRecord(
            run_id="run-resumed",
            thread_id=thread_id,
            assistant_id=getattr(body, "assistant_id", None),
            status=RunStatus.running,
            on_disconnect=DisconnectMode.cancel,
            created_at="2026-09-14T00:00:01+00:00",
            updated_at="2026-09-14T00:00:01+00:00",
        )

    monkeypatch.setattr(thread_runs, "_resolve_head_checkpoint_id", _head_checkpoint)
    monkeypatch.setattr(thread_runs, "start_run", _fake_start_run)

    app = make_authed_test_app(user_factory=_user)
    app.state.run_store = run_store
    app.state.run_event_store = event_store
    app.state.run_manager = run_manager
    app.include_router(thread_runs.router)
    return TestClient(app), run_store


def _put_run(run_store: MemoryRunStore, run_id: str, **overrides: Any) -> None:
    fields: dict[str, Any] = {
        "thread_id": THREAD_ID,
        "user_id": USER_ID_STR,
        "status": "error",
    }
    fields.update(overrides)
    asyncio.run(run_store.put(run_id, **fields))


def test_gateway_mounts_resume_route() -> None:
    from app.gateway.app import create_app

    paths = {route.path for route in create_app().routes}
    assert "/api/threads/{thread_id}/runs/{run_id}/resume" in paths


def test_resume_unknown_run_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _resume_app(monkeypatch)
    with client:
        response = client.post(RESUME_URL)
    assert response.status_code == 404


def test_resume_wrong_thread_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, run_store = _resume_app(monkeypatch)
    _put_run(run_store, RUN_ID, thread_id="thread-other")
    with client:
        response = client.post(RESUME_URL)
    assert response.status_code == 404


@pytest.mark.parametrize("status", ["pending", "running"])
def test_resume_active_run_is_409(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    client, run_store = _resume_app(monkeypatch)
    _put_run(run_store, RUN_ID, status=status)
    with client:
        response = client.post(RESUME_URL)
    assert response.status_code == 409
    assert "still active" in response.json()["detail"]


def test_resume_succeeded_run_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, run_store = _resume_app(monkeypatch)
    _put_run(run_store, RUN_ID, status="success")
    with client:
        response = client.post(RESUME_URL)
    assert response.status_code == 409
    assert "already completed" in response.json()["detail"]


@pytest.mark.parametrize("status", ["error", "timeout", "interrupted"])
def test_resume_without_checkpoint_is_409(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    client, run_store = _resume_app(monkeypatch, head_checkpoint_id=None)
    _put_run(run_store, RUN_ID, status=status)
    with client:
        response = client.post(RESUME_URL)
    assert response.status_code == 409
    assert "no resumable checkpoint" in response.json()["detail"]


def test_resume_after_thread_advanced_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    client, run_store = _resume_app(monkeypatch)
    _put_run(run_store, RUN_ID, status="error")
    run_store._runs[RUN_ID]["created_at"] = "2026-01-01T00:00:00+00:00"
    _put_run(run_store, "run-newer", status="success")
    with client:
        response = client.post(RESUME_URL)
    assert response.status_code == 409
    assert "no longer the latest turn" in response.json()["detail"]


@pytest.mark.parametrize("status", ["error", "timeout", "interrupted"])
def test_resume_continues_from_head_checkpoint(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    captured: dict[str, Any] = {}
    client, run_store = _resume_app(monkeypatch, captured=captured)
    _put_run(run_store, RUN_ID, status=status, assistant_id="agent-main")
    with client:
        response = client.post(RESUME_URL)
    assert response.status_code == 202, response.text
    body = captured["body"]
    assert body.checkpoint_id == "ckpt-head"
    assert body.input is None
    assert body.assistant_id == "agent-main"
    assert (body.metadata or {}).get("resumed_from_run_id") == RUN_ID
    assert captured["thread_id"] == THREAD_ID
    assert captured["kwargs"].get("require_existing_thread") is True
    payload = response.json()
    assert payload["run_id"] == "run-resumed"
    assert payload["status"] == "running"


def test_resume_forwards_start_run_admission_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    async def _conflict_start_run(body: Any, thread_id: str, request: Any, **kwargs: Any) -> RunRecord:
        raise HTTPException(status_code=409, detail="Another run is active on this thread")

    client, run_store = _resume_app(monkeypatch)
    _put_run(run_store, RUN_ID, status="interrupted")
    monkeypatch.setattr(thread_runs, "start_run", _conflict_start_run)
    with client:
        response = client.post(RESUME_URL)
    assert response.status_code == 409
    assert "Another run is active" in response.json()["detail"]


def test_resume_uses_unwrapped_handler_contract() -> None:
    # The route must stay a runs:create, thread-scoped endpoint: guard against
    # future edits accidentally widening it to a GET or dropping owner checks.
    route = next(r for r in thread_runs.router.routes if getattr(r, "path", "") == "/api/threads/{thread_id}/runs/{run_id}/resume")
    assert {"POST"} == set(route.methods or [])
    assert getattr(route, "response_model", None) is not None
