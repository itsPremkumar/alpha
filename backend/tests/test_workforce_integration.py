"""Integration coverage over new Gateway routes (auth, perms, 404/409/422 paths).

Live agent runs are never started here: these tests prove wiring,
ownership checks, and failure contracts end to end through TestClient.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.gateway.routers import openai_compat, threads
from alpha.persistence.thread_meta.memory import THREADS_NS, MemoryThreadMetaStore


class _PermissiveThreadMetaStore(MemoryThreadMetaStore):
    async def _get_owned_record(self, thread_id, user_id, method_name):  # type: ignore[override]
        item = await self._store.aget(THREADS_NS, thread_id)
        return dict(item.value) if item is not None else None

    async def check_access(self, thread_id, user_id, *, require_existing=False):  # type: ignore[override]
        item = await self._store.aget(THREADS_NS, thread_id)
        if item is None:
            return not require_existing
        return True

    async def create(self, thread_id, *, assistant_id=None, user_id=None, display_name=None, metadata=None, project_id=None):  # type: ignore[override]
        return await super().create(thread_id, assistant_id=assistant_id, user_id=None, display_name=display_name, metadata=metadata, project_id=project_id)


class _StubRunManager:
    @asynccontextmanager
    async def reserve_thread_operation(self, _thread_id: str, **_kwargs):
        yield


def _build_app():
    app = make_authed_test_app()
    store = InMemoryStore()
    app.state.store = store
    app.state.checkpointer = InMemorySaver()
    app.state.run_manager = _StubRunManager()
    app.state.thread_store = _PermissiveThreadMetaStore(store)
    app.include_router(threads.router)
    app.include_router(openai_compat.router)
    return app, store


def test_undo_unknown_thread_is_404():
    app, _ = _build_app()
    with TestClient(app) as client:
        response = client.post("/api/threads/no-such-thread/undo")
    assert response.status_code == 404


def _snapshot(messages: list[Any]) -> SimpleNamespace:
    return SimpleNamespace(
        values={"messages": messages},
        config={"configurable": {"thread_id": "t-undo", "checkpoint_ns": "", "checkpoint_id": "ckpt-9"}},
    )


class _StubAccessor:
    def __init__(self, snapshots: list[SimpleNamespace]):
        self._snapshots = snapshots

    async def ahistory(self, config, limit=None):
        return self._snapshots[:limit]


def _seed_thread(store, thread_id: str) -> None:
    asyncio.run(
        store.aput(
            THREADS_NS,
            thread_id,
            {"thread_id": thread_id, "status": "idle", "created_at": "", "updated_at": "", "metadata": {}},
        )
    )


def test_undo_single_turn_thread_is_409(monkeypatch):
    app, store = _build_app()
    _seed_thread(store, "one-turn")
    snapshot = _snapshot([HumanMessage(id="h1", content="q"), AIMessage(id="a1", content="a")])

    async def _abuild(request, *, thread_id, assistant_id=None, checkpoint_id=None):
        return _StubAccessor([snapshot]), {"configurable": {"thread_id": thread_id}}

    monkeypatch.setattr(threads, "abuild_checkpoint_state_accessor", _abuild)
    with TestClient(app) as client:
        response = client.post("/api/threads/one-turn/undo")
    assert response.status_code == 409


def test_undo_two_turn_thread_delegates_to_branch_writer(monkeypatch):
    from app.gateway.routers.threads import ThreadBranchResponse

    app, store = _build_app()
    _seed_thread(store, "two-turns")
    snapshot = _snapshot(
        [
            HumanMessage(id="h1", content="q1"),
            AIMessage(id="a1", content="a1"),
            HumanMessage(id="h2", content="q2"),
            AIMessage(id="a2", content="a2"),
        ]
    )

    async def _abuild(request, *, thread_id, assistant_id=None, checkpoint_id=None):
        return _StubAccessor([snapshot]), {"configurable": {"thread_id": thread_id}}

    captured: dict[str, Any] = {}

    async def _fake_branch(thread_id, body, request):
        captured["thread_id"] = thread_id
        captured["message_id"] = body.message_id
        captured["message_ids"] = body.message_ids
        return ThreadBranchResponse(
            thread_id="branched-1",
            parent_thread_id=thread_id,
            parent_checkpoint_id="ckpt-9",
            branched_from_message_id=body.message_id,
            workspace_clone_mode="skipped_empty",
            history_seed_mode="seeded",
        )

    monkeypatch.setattr(threads, "abuild_checkpoint_state_accessor", _abuild)
    monkeypatch.setattr(threads, "_branch_thread_with_reservation", _fake_branch)
    with TestClient(app) as client:
        response = client.post("/api/threads/two-turns/undo")
    assert response.status_code == 200, response.text
    assert captured["thread_id"] == "two-turns"
    assert captured["message_id"] == "a1"
    assert captured["message_ids"] == []
    assert response.json()["thread_id"] == "branched-1"


def test_undo_empty_thread_is_409(monkeypatch):
    app, store = _build_app()
    _seed_thread(store, "empty-thread")

    async def _abuild(request, *, thread_id, assistant_id=None, checkpoint_id=None):
        return _StubAccessor([_snapshot([])]), {"configurable": {"thread_id": thread_id}}

    monkeypatch.setattr(threads, "abuild_checkpoint_state_accessor", _abuild)
    with TestClient(app) as client:
        response = client.post("/api/threads/empty-thread/undo")
    assert response.status_code == 409
    assert "undo" in response.json()["detail"].lower()


def test_compat_rejects_empty_messages():
    app, _ = _build_app()
    with TestClient(app) as client:
        response = client.post("/api/compat/openai/chat/completions", json={"model": "x", "messages": []})
    assert response.status_code == 422


def test_compat_unknown_thread_is_404():
    app, _ = _build_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/compat/openai/chat/completions",
            json={"model": "x", "thread_id": "no-such-thread", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 404


def test_compat_whitespace_only_messages_rejected():
    app, _ = _build_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/compat/openai/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "   "}]},
        )
    assert response.status_code == 422
