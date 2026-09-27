"""Security audit: the OpenAI-compatible surface must keep the same admission
contract as the thread-scoped routes it delegates to.

``routers/openai_compat.py`` is pure translation over ``threads.create`` +
``runs.wait`` (its own module docstring). It calls the *decorated* route
functions directly, so every ``@require_permission`` layer on them still runs.
This file pins that the delegation actually reaches the run lifecycle with the
caller's thread - a silently-dead delegation is an availability bug, and a
delegation that dropped its owner check would be a cross-tenant read.
"""

from __future__ import annotations

from typing import Any

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import openai_compat


class _RecordingThreadStore:
    """Minimal thread-meta store: seeded rows map to their owning user id."""

    def __init__(self, rows: dict[str, str | None]) -> None:
        self.rows = rows
        self.get_calls: list[str] = []
        self.check_access_calls: list[tuple[str, str]] = []

    async def get(self, thread_id: str) -> dict[str, Any] | None:
        self.get_calls.append(thread_id)
        owner = self.rows.get(thread_id, "__missing__")
        if owner == "__missing__":
            return None
        return {"thread_id": thread_id, "user_id": owner}

    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        self.check_access_calls.append((thread_id, user_id))
        if thread_id not in self.rows:
            return not require_existing
        return self.rows[thread_id] in (user_id, None)

    async def create(self, thread_id: str, **_kwargs: Any) -> dict[str, Any]:
        self.rows.setdefault(thread_id, None)
        return {"thread_id": thread_id, "user_id": None}

    async def touch(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _build_app(store: _RecordingThreadStore):
    app = make_authed_test_app()
    app.state.thread_store = store
    app.include_router(openai_compat.router)
    return app


def test_compat_chat_completions_reaches_the_run_lifecycle(monkeypatch):
    """A complete compat request must reach ``wait_run``, not 500."""
    store = _RecordingThreadStore({"mine": None})
    app = _build_app(store)

    seen: dict[str, Any] = {}

    async def _fake_wait(thread_id, body, request, **_kwargs):
        seen["thread_id"] = thread_id
        seen["input"] = body.input
        return {"messages": [{"type": "ai", "content": "hello"}]}

    monkeypatch.setattr(openai_compat, "wait_run", _fake_wait)

    with TestClient(app) as client:
        response = client.post(
            "/api/compat/openai/chat/completions",
            json={"model": "x", "thread_id": "mine", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200, response.text
    assert seen["thread_id"] == "mine"
    assert seen["input"] == {"messages": [{"role": "user", "content": "hi"}]}


def test_compat_chat_completions_enforces_thread_ownership():
    """A compat request naming another user's thread must be refused (404).

    ``openai_compat`` only checks that the thread *exists*; the ownership
    admission is delegated to ``wait_run``'s ``owner_check``. This pins that
    the delegation is genuinely reached and genuinely denies.
    """
    store = _RecordingThreadStore({"victim-thread": "someone-else"})
    app = _build_app(store)

    with TestClient(app) as client:
        response = client.post(
            "/api/compat/openai/chat/completions",
            json={
                "model": "x",
                "thread_id": "victim-thread",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert response.status_code == 404, response.text
    assert any(call[0] == "victim-thread" for call in store.check_access_calls)


def test_require_permission_owner_check_reads_a_positional_thread_id():
    """Direct seam: the owner check must resolve a positionally-passed thread id.

    ``require_permission(..., owner_check=True)`` reads ``thread_id`` from
    ``kwargs``. In-process callers pass it positionally, so the check must also
    consult the signature binding. Without that it raises ``ValueError`` and the
    route 500s instead of admitting or denying.
    """
    import inspect

    from app.gateway import authz
    from app.gateway.authz import require_permission

    calls: list[tuple[str, str]] = []

    class _Store:
        async def check_access(self, thread_id, user_id, *, require_existing=False):
            calls.append((thread_id, user_id))
            return True

    @require_permission("threads", "read", owner_check=True)
    async def handler(thread_id: str, request) -> dict:
        return {"thread_id": thread_id}

    import asyncio
    from types import SimpleNamespace

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(thread_store=_Store())),
        state=SimpleNamespace(auth=authz.AuthContext(user=SimpleNamespace(id="u1", system_role="user"), permissions=["threads:read"])),
        cookies={},
    )

    result = asyncio.run(handler("positional-thread", request))
    assert result == {"thread_id": "positional-thread"}
    assert calls == [("positional-thread", "u1")]
    assert "thread_id" in inspect.signature(authz.require_permission("threads", "read", owner_check=True)(handler)).parameters
