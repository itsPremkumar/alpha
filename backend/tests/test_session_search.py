"""Tests for cross-thread message content search (H2 session recall)."""

import asyncio
from types import SimpleNamespace

import pytest

from agent_workspace.runtime.events.search import (
    clamp_limit,
    escape_like,
    extract_searchable_text,
    make_snippet,
    message_rank_group,
    normalize_query,
    sanitize_fts_query,
)
from agent_workspace.runtime.events.store.memory import MemoryRunEventStore

pytestmark = pytest.mark.anyio


async def _seed_two_threads(store) -> None:
    await store.put(
        thread_id="t1",
        run_id="r1",
        event_type="llm.human.input",
        category="message",
        content={"content": "how do I reset the router password?", "type": "human", "id": "m1"},
    )
    await store.put(
        thread_id="t1",
        run_id="r1",
        event_type="llm.ai.response",
        category="message",
        content={"content": "Press the reset button for ten seconds.", "type": "ai", "id": "m2"},
    )
    await store.put(
        thread_id="t1",
        run_id="r1",
        event_type="run.end",
        category="run",
        content="router finished",
    )
    await store.put(
        thread_id="t2",
        run_id="r9",
        event_type="llm.tool.result",
        category="message",
        content={"content": "router manual page 42", "type": "tool", "id": "m3"},
    )
    await store.put(
        thread_id="t2",
        run_id="r9",
        event_type="llm.human.input",
        category="message",
        content={"content": "unrelated weather question", "type": "human", "id": "m4"},
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def test_extract_searchable_text_shapes():
    assert extract_searchable_text("plain") == "plain"
    assert extract_searchable_text({"content": "hi", "type": "human"}) == "hi"
    assert extract_searchable_text({"content": [{"type": "text", "text": "a"}, "b"], "type": "ai"}) == "a\nb"
    assert extract_searchable_text({"content": [{"type": "tool_call", "name": "bash"}], "type": "ai"}) == "tool call bash"
    assert extract_searchable_text({"type": "ai"}) == ""
    assert extract_searchable_text(None) == ""
    assert extract_searchable_text(42) == ""


def test_message_rank_group():
    assert message_rank_group({"type": "human"}) == 0
    assert message_rank_group({"type": "ai"}) == 0
    assert message_rank_group("plain string") == 0
    assert message_rank_group({"type": "tool"}) == 1
    assert message_rank_group({"type": "other"}) == 1
    assert message_rank_group(None) == 1


def test_sanitize_fts_query_quotes_operators():
    assert sanitize_fts_query("router password") == '"router" "password"'
    assert sanitize_fts_query('a OR "b" *') == '"a" "OR" """b""" "*"'
    assert sanitize_fts_query("   ") == ""
    assert len(sanitize_fts_query("w " * 50).split()) == 10


def test_normalize_and_clamp():
    assert normalize_query("") == []
    assert clamp_limit(0) == 1
    assert clamp_limit(500) == 100
    assert clamp_limit("nope") == 20
    assert escape_like("100%_x\\y") == "100\\%\\_x\\\\y"


def test_make_snippet_windows_and_caps():
    text = "start " + "filler " * 100 + "needle here " + "tail " * 100
    snippet = make_snippet(text, "needle", 60)
    assert "needle" in snippet
    assert len(snippet) <= 62
    assert make_snippet("short text", "missing", 300) == "short text"


# ---------------------------------------------------------------------------
# Memory backend
# ---------------------------------------------------------------------------


async def test_memory_search_ranks_human_ai_above_tool():
    store = MemoryRunEventStore()
    await _seed_two_threads(store)
    hits = await store.search_message_content("router", user_id="u1")
    # t1/human matches; t2/tool matches (ranked after human/ai); the run
    # event and the weather turn never match. Seq restarts per thread.
    assert [(h["thread_id"], h["seq"]) for h in hits] == [("t1", 1), ("t2", 1)]
    assert all(h["event_type"] != "run.end" for h in hits)


async def test_memory_search_requires_all_tokens():
    store = MemoryRunEventStore()
    await _seed_two_threads(store)
    hits = await store.search_message_content("router password", user_id="u1")
    assert [(h["thread_id"], h["seq"]) for h in hits] == [("t1", 1)]


async def test_memory_search_blank_scope_and_limit():
    store = MemoryRunEventStore()
    await _seed_two_threads(store)
    assert await store.search_message_content("   ", user_id="u1") == []
    scoped = await store.search_message_content("router", user_id="u1", thread_id="t2")
    assert {h["thread_id"] for h in scoped} == {"t2"}
    limited = await store.search_message_content("router", user_id="u1", limit=1)
    assert len(limited) == 1


async def test_memory_search_snippet_bounded_and_shaped():
    store = MemoryRunEventStore()
    await store.put(
        thread_id="t1",
        run_id="r1",
        event_type="llm.human.input",
        category="message",
        content={"content": "x" * 500 + " needle " + "y" * 500, "type": "human", "id": "m"},
    )
    hits = await store.search_message_content("needle", user_id="u1", snippet_chars=60)
    assert len(hits) == 1
    assert "needle" in hits[0]["snippet"]
    assert len(hits[0]["snippet"]) <= 62
    assert set(hits[0]) == {"thread_id", "run_id", "seq", "event_type", "snippet", "created_at"}


# ---------------------------------------------------------------------------
# JSONL backend
# ---------------------------------------------------------------------------


async def test_jsonl_search_matches_memory_contract(tmp_path):
    from agent_workspace.runtime.events.store.jsonl import JsonlRunEventStore

    store = JsonlRunEventStore(base_dir=tmp_path)
    await _seed_two_threads(store)
    hits = await store.search_message_content("router password", user_id="u1")
    assert [(h["thread_id"], h["seq"]) for h in hits] == [("t1", 1)]
    scoped = await store.search_message_content("router", user_id="u1", thread_id="t2")
    assert {h["thread_id"] for h in scoped} == {"t2"}
    assert await store.search_message_content("", user_id="u1") == []


# ---------------------------------------------------------------------------
# SQLite backend (FTS path + LIKE fallback + owner isolation)
# ---------------------------------------------------------------------------


async def _sqlite_store(tmp_path):
    from agent_workspace.persistence.engine import get_session_factory, init_engine
    from agent_workspace.runtime.events.store.db import DbRunEventStore

    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    return DbRunEventStore(get_session_factory())


async def _put_as(store, user_id, **kwargs):
    from agent_workspace.runtime.user_context import reset_current_user, set_current_user

    token = set_current_user(SimpleNamespace(id=user_id))
    try:
        return await store.put(**kwargs)
    finally:
        reset_current_user(token)


async def test_db_search_enforces_owner_isolation(tmp_path):
    from agent_workspace.persistence.engine import close_engine

    store = await _sqlite_store(tmp_path)
    try:
        await _put_as(
            store,
            "owner-a",
            thread_id="t1",
            run_id="r1",
            event_type="llm.human.input",
            category="message",
            content={"content": "alpha shared keyword", "type": "human", "id": "m1"},
        )
        await _put_as(
            store,
            "owner-b",
            thread_id="t2",
            run_id="r2",
            event_type="llm.human.input",
            category="message",
            content={"content": "alpha other owner", "type": "human", "id": "m2"},
        )
        hits_a = await store.search_message_content("alpha", user_id="owner-a")
        hits_b = await store.search_message_content("alpha", user_id="owner-b")
        assert [(h["thread_id"], h["seq"]) for h in hits_a] == [("t1", 1)]
        assert [(h["thread_id"], h["seq"]) for h in hits_b] == [("t2", 1)]
    finally:
        await close_engine()


async def test_db_search_ranks_and_snippets(tmp_path):
    from agent_workspace.persistence.engine import close_engine

    store = await _sqlite_store(tmp_path)
    try:
        await _put_as(
            store,
            "u1",
            thread_id="t1",
            run_id="r1",
            event_type="llm.tool.result",
            category="message",
            content={"content": "firewall diagnostics output", "type": "tool", "id": "m1"},
        )
        await _put_as(
            store,
            "u1",
            thread_id="t1",
            run_id="r1",
            event_type="llm.human.input",
            category="message",
            content={"content": "why is the firewall slow?", "type": "human", "id": "m2"},
        )
        hits = await store.search_message_content("firewall", user_id="u1")
        assert [h["seq"] for h in hits] == [2, 1]
        assert all(h["snippet"] for h in hits)
        assert set(hits[0]) == {"thread_id", "run_id", "seq", "event_type", "snippet", "created_at"}
    finally:
        await close_engine()


async def test_db_search_like_fallback_stays_correct(tmp_path):
    from agent_workspace.persistence.engine import close_engine

    store = await _sqlite_store(tmp_path)
    try:
        await _put_as(
            store,
            "u1",
            thread_id="t1",
            run_id="r1",
            event_type="llm.human.input",
            category="message",
            content={"content": "like fallback keyword here", "type": "human", "id": "m1"},
        )
        # Force the LIKE path even though FTS objects exist.
        store._fts_available = False
        hits = await store.search_message_content("fallback keyword", user_id="u1")
        assert [(h["thread_id"], h["seq"]) for h in hits] == [("t1", 1)]
        store._fts_available = None
        hits_fts = await store.search_message_content("fallback keyword", user_id="u1")
        assert [(h["thread_id"], h["seq"]) for h in hits_fts] == [("t1", 1)]
    finally:
        await close_engine()


async def test_db_search_blank_query_returns_without_storage(tmp_path):
    from agent_workspace.persistence.engine import close_engine

    store = await _sqlite_store(tmp_path)
    try:
        assert await store.search_message_content("  ", user_id="u1") == []
    finally:
        await close_engine()


# ---------------------------------------------------------------------------
# session_search tool
# ---------------------------------------------------------------------------


def _tool_runtime(store, user_id="u1"):
    return SimpleNamespace(
        state={},
        context={"user_id": user_id, "thread_id": "t1", "__run_event_store": store},
        config={"metadata": {}},
    )


async def _call_tool(**kwargs):
    from agent_workspace.tools.builtins.session_search_tool import session_search_tool

    coroutine = getattr(session_search_tool, "coroutine", None)
    if coroutine is not None:
        return await coroutine(**kwargs)
    return session_search_tool.func(**kwargs)


async def test_tool_discovery_formats_hits_with_hint():
    store = MemoryRunEventStore()
    await _seed_two_threads(store)
    out = await _call_tool(runtime=_tool_runtime(store), query="router password")
    assert "t1" in out and "seq 1" in out
    assert "after_seq=" in out


async def test_tool_blank_without_session_is_usage_error():
    store = MemoryRunEventStore()
    out = await _call_tool(runtime=_tool_runtime(store), query="   ")
    assert out.startswith("Error: provide a keyword")


async def test_tool_scroll_reads_thread_history():
    store = MemoryRunEventStore()
    await _seed_two_threads(store)
    out = await _call_tool(runtime=_tool_runtime(store), session_id="t1")
    assert "[seq 1] human:" in out
    assert "[seq 2] ai:" in out
    forward = await _call_tool(runtime=_tool_runtime(store), session_id="t1", after_seq=1)
    assert "[seq 1]" not in forward
    assert "[seq 2]" in forward


async def test_tool_scroll_unknown_thread_reports_empty():
    store = MemoryRunEventStore()
    out = await _call_tool(runtime=_tool_runtime(store), session_id="ghost")
    assert "No messages" in out


async def test_tool_store_unavailable_is_actionable(monkeypatch):
    runtime = SimpleNamespace(state={}, context={"user_id": "u1"}, config={"metadata": {}})

    def _boom():
        raise FileNotFoundError("no config anywhere")

    monkeypatch.setattr("agent_workspace.config.get_app_config", _boom)
    out = await _call_tool(runtime=runtime, query="anything")
    assert out.startswith("Error: message store unavailable")


# ---------------------------------------------------------------------------
# Gateway endpoint
# ---------------------------------------------------------------------------


def _recording_store():
    store = MemoryRunEventStore()
    calls: dict = {}

    async def _search(query, *, user_id=None, thread_id=None, limit=20, snippet_chars=300):
        calls.update(query=query, user_id=user_id, thread_id=thread_id, limit=limit)
        return await MemoryRunEventStore.search_message_content(store, query, user_id=user_id, thread_id=thread_id, limit=limit, snippet_chars=snippet_chars)

    store.search_message_content = _search  # type: ignore[method-assign]
    return store, calls


def _content_app(store):
    from _router_auth_helpers import make_authed_test_app
    from fastapi.testclient import TestClient

    from app.gateway.routers import threads as threads_router

    app = make_authed_test_app()
    app.state.run_event_store = store
    app.include_router(threads_router.router)
    return TestClient(app)


async def test_endpoint_returns_hits_and_forwards_identity_async():
    store, calls = _recording_store()
    await _seed_two_threads(store)
    client_holder: dict = {}

    def _client():
        client_holder["client"] = _content_app(store)
        return client_holder["client"]

    client = await asyncio.to_thread(_client)
    response = await asyncio.to_thread(client.post, "/api/threads/search/content", json={"query": "router password"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(h["thread_id"], h["seq"]) for h in body] == [("t1", 1)]
    assert set(body[0]) == {"thread_id", "run_id", "seq", "event_type", "snippet", "created_at"}
    assert isinstance(calls["user_id"], str) and calls["user_id"]
    assert calls["limit"] == 20
    assert calls["thread_id"] is None


async def test_endpoint_validates_and_scopes():
    store, calls = _recording_store()
    await _seed_two_threads(store)
    client = await asyncio.to_thread(_content_app, store)
    missing = await asyncio.to_thread(client.post, "/api/threads/search/content", json={})
    assert missing.status_code == 422
    scoped = await asyncio.to_thread(
        client.post,
        "/api/threads/search/content",
        json={"query": "router", "thread_id": "t2", "limit": 5},
    )
    assert scoped.status_code == 200
    assert calls["thread_id"] == "t2"
    assert calls["limit"] == 5
    assert {h["thread_id"] for h in scoped.json()} == {"t2"}
    blank = await asyncio.to_thread(client.post, "/api/threads/search/content", json={"query": "   "})
    assert blank.status_code == 200
    assert blank.json() == []
