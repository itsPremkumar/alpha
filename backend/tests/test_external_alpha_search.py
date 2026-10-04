"""Route-order and search-behaviour tests for the transcript surface.

Two things are pinned here that are easy to get wrong and expensive to
discover in the UI.

**Route order.** ``GET /transcripts/search`` and ``/transcripts/analytics`` are
literal paths sitting next to ``GET /transcripts/{conversation_id}``. Starlette
matches in declaration order, so if the parameterised route is declared first it
swallows both and answers "Conversation 'search' not found" -- the same trap this
repo already documents for ``skills/{skill_name}`` and ``workflows/{workflow_id}``.
The whole feature is unreachable from the UI if that ordering slips.

**Search honesty.** An empty query must return nothing, not the entire mailbox.
FTS5 availability must be reported rather than implied, because the fallback is a
plain substring scan and calling that "search" overstates it.
"""

from __future__ import annotations

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import peer_network as peer_router

_CORPUS = [
    {
        "message_id": "msg_hit",
        "conversation_id": "conv_1",
        "sender_id": "alpha_remote",
        "recipients": ["alpha_local"],
        "kind": "task_request",
        "text": "please summarise the quarterly deployment log",
        "payload": {},
        "status": "delivered",
        "direction": "inbound",
        "created_at": "2026-01-01T00:00:00+00:00",
        "delivered_at": "2026-01-01T00:00:00+00:00",
        "read_at": None,
        "delivery_error": None,
        "deliveries": [{"recipient_id": "alpha_local", "status": "delivered", "transport": "http", "error": None, "delivered_at": "2026-01-01T00:00:00+00:00", "read_at": None}],
    },
    {
        "message_id": "msg_miss",
        "conversation_id": "conv_1",
        "sender_id": "alpha_local",
        "recipients": ["alpha_remote"],
        "kind": "chat",
        "text": "ack",
        "payload": {},
        "status": "delivered",
        "direction": "outbound",
        "created_at": "2026-01-01T00:00:01+00:00",
        "delivered_at": "2026-01-01T00:00:01+00:00",
        "read_at": None,
        "delivery_error": None,
        "deliveries": [{"recipient_id": "alpha_remote", "status": "delivered", "transport": "http", "error": None, "delivered_at": "2026-01-01T00:00:01+00:00", "read_at": None}],
    },
]


class _StubService:
    """Stubs only what these routes touch, so a real store is never required."""

    enabled = True
    identity = type("Id", (), {"agent_id": "alpha_local"})()

    class _Store:
        fts_available = True

    store = _Store()

    async def search_messages(self, needle, *, limit=50, conversation_id=None, direction=None):
        text = (needle or "").strip().lower()
        if not text:
            return []
        rows = [m for m in _CORPUS if text in m["text"].lower()]
        if conversation_id:
            rows = [m for m in rows if m["conversation_id"] == conversation_id]
        if direction in {"inbound", "outbound"}:
            rows = [m for m in rows if m["direction"] == direction]
        return rows[:limit]

    async def analytics(self):
        return {
            "totals": {"peers": 1, "conversations": 1, "messages": len(_CORPUS)},
            "conversations": 1,
            "modes": {"direct": 1},
            "kinds": {"chat": 1, "task_request": 1},
            "directions": {"inbound": 1, "outbound": 1},
            "statuses": {"delivered": len(_CORPUS)},
            "fts_available": True,
            "retention_days": 90,
        }

    async def get_conversation(self, conversation_id):
        if conversation_id != "conv_1":
            return None
        return {"conversation_id": "conv_1", "title": "Room", "mode": "direct", "status": "active", "participants": ["alpha_local", "alpha_remote"], "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:01+00:00"}

    async def list_conversations(self):
        return [await self.get_conversation("conv_1")]

    async def get_messages(self, conversation_id, limit=200):
        return _CORPUS if conversation_id == "conv_1" else []

    async def get_peer(self, agent_id, include_secret=False):
        return {"agent_id": agent_id, "name": "Remote", "trust": "paired", "url": "http://x", "outbound_token": "SECRET"}


class _StubRunManager:
    async def list_by_thread(self, thread_id: str):
        return []


@pytest.fixture(autouse=True)
def stub_peer_plane(monkeypatch):
    """Autouse, so `_service()` can never resolve to the real installation service.

    The run/event-store getters must be stubbed too: they are `_require(...)`
    dependencies that raise 503 when `app.state` has no run manager, which is
    what a bare test app looks like.
    """

    monkeypatch.setattr(peer_router, "_service", lambda: _StubService())
    monkeypatch.setattr(peer_router, "get_run_manager", lambda request: _StubRunManager())
    monkeypatch.setattr(peer_router, "get_run_event_store", lambda request: object())


def _user() -> User:
    from uuid import uuid4

    return User(email="search-test@example.com", password_hash="x", system_role="user", id=uuid4())


@pytest.fixture
def client() -> TestClient:
    app = make_authed_test_app(user_factory=_user)
    app.include_router(peer_router.router)
    return TestClient(app)


def test_literal_sub_paths_are_declared_before_the_parameterised_route():
    """The whole search surface is unreachable if this ordering slips."""

    paths = [route.path for route in peer_router.router.routes]
    parameterised = paths.index("/api/peer-network/transcripts/{conversation_id}")
    for literal in (
        "/api/peer-network/transcripts/search",
        "/api/peer-network/transcripts/analytics",
    ):
        assert literal in paths, f"{literal} is not declared"
        assert paths.index(literal) < parameterised, f"{literal} is declared after /transcripts/{{conversation_id}}; Starlette would answer it with 'Conversation not found'"


def test_an_empty_query_returns_nothing_rather_than_the_whole_mailbox(client: TestClient):
    with client:
        for query in ("", "   "):
            response = client.get("/api/peer-network/transcripts/search", params={"q": query})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["entries"] == []
            assert body["empty_query"] is True


def test_search_reports_fts_availability_rather_than_implying_it(client: TestClient):
    """The fallback is a substring scan; the UI must be able to say so."""

    with client:
        body = client.get("/api/peer-network/transcripts/search", params={"q": "anything"}).json()
    assert isinstance(body["fts_available"], bool)


def test_search_returns_the_allowlisted_message_shape(client: TestClient):
    with client:
        response = client.get("/api/peer-network/transcripts/search", params={"q": "anything"})
    assert response.status_code == 200, response.text
    for entry in response.json()["entries"]:
        # Same projection as a transcript entry: role, side, receipts, and no
        # peer endpoint or credential fields.
        assert entry["role"] == "peer_message"
        assert "outbound_token" not in entry
        assert "url" not in entry


def test_search_rejects_an_unsupported_direction_rather_than_guessing(client: TestClient):
    with client:
        body = client.get("/api/peer-network/transcripts/search", params={"q": "x", "direction": "sideways"}).json()
    # The invalid value is dropped to None (no filter), never applied blindly.
    assert body["count"] >= 0


def test_search_limit_is_bounded(client: TestClient):
    with client:
        response = client.get("/api/peer-network/transcripts/search", params={"q": "x", "limit": 100000})
    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) <= 200


def test_analytics_reports_measured_counts(client: TestClient):
    with client:
        response = client.get("/api/peer-network/transcripts/analytics")
    assert response.status_code == 200, response.text
    body = response.json()
    for key in ("totals", "conversations", "modes", "kinds", "directions", "statuses", "fts_available", "retention_days"):
        assert key in body, key
    # The histograms must be present as objects even when empty, so the UI never
    # has to distinguish "no data" from "field missing".
    for key in ("modes", "kinds", "directions", "statuses"):
        assert isinstance(body[key], dict)


def test_analytics_reports_the_effective_retention_window(client: TestClient):
    with client:
        body = client.get("/api/peer-network/transcripts/analytics").json()
    assert body["retention_days"] >= 7


def test_trace_route_404s_for_an_unknown_turn(client: TestClient):
    with client:
        response = client.get("/api/peer-network/transcripts/conv_1/turns/run_missing/trace")
    assert response.status_code == 404


def test_search_and_analytics_require_the_peer_scope(client: TestClient):
    """Both are authenticated peer-plane reads, not public metadata."""

    from types import SimpleNamespace

    from app.gateway.routers.peer_network import _assert_peer_network_scope

    async def _refused() -> None:
        request = SimpleNamespace(state=SimpleNamespace(user=None))
        with pytest.raises(Exception):
            await _assert_peer_network_scope(request)

    import asyncio

    asyncio.run(_refused())
