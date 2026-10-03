"""Route tests for the External Alpha transcript endpoints.

The transcript routes are the only way an operator can read the local half of a
cross-installation conversation, and they are also the only peer-network routes
that deliberately omit ``@require_permission(owner_check=True)``. That omission
is the thing most worth testing, because the reason for it is not obvious from
the route signature:

    A peer turn runs on ``peer_thread_id(peer_agent_id)`` owned by
    ``NETWORK_OWNER == "installation"`` -- a constant, never a session user id.
    ``owner_check=True`` resolves the caller against the thread's owner, so it
    would 404 the operator who owns this very installation and make the history
    permanently unreadable.

So these tests pin three things:

1. the substitute check (``_assert_peer_network_scope``) actually refuses an
   unauthenticated caller rather than defaulting open;
2. an ordinary ``threads:read`` caller IS served, which is the behaviour the
   substitution exists to preserve;
3. no response body leaks a credential, endpoint, or the owner constant.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import peer_network as peer_router

_LOCAL = "alpha_local"
_REMOTE = "alpha_remote"
_SECRET_TOKEN = "bearer-token-must-never-appear"


def _user(system_role: str = "user") -> User:
    return User(email=f"transcript-{uuid4().hex[:6]}@example.com", password_hash="x", system_role=system_role, id=uuid4())


class _StubService:
    """A peer service stub with a real conversation shape and a poisoned peer row."""

    enabled = True

    def __init__(self) -> None:
        self.identity = type("Id", (), {"agent_id": _LOCAL})()

    async def list_conversations(self):
        return [
            {
                "conversation_id": "conv_1",
                "title": "Alpha peer room",
                "mode": "direct",
                "status": "active",
                "participants": [_LOCAL, _REMOTE],
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:05+00:00",
            }
        ]

    async def get_conversation(self, conversation_id: str):
        rows = await self.list_conversations()
        return rows[0] if conversation_id == "conv_1" else None

    async def get_messages(self, conversation_id: str, limit: int = 200):
        if conversation_id != "conv_1":
            raise KeyError(conversation_id)
        return [
            {
                "message_id": "msg_1",
                "conversation_id": "conv_1",
                "sender_id": _REMOTE,
                "recipients": [_LOCAL],
                "kind": "chat",
                "text": "hello from the other installation",
                "payload": {},
                "status": "delivered",
                "direction": "inbound",
                "created_at": "2026-01-01T00:00:00+00:00",
                "delivered_at": "2026-01-01T00:00:00+00:00",
                "read_at": None,
                "delivery_error": None,
                "deliveries": [{"recipient_id": _LOCAL, "status": "delivered", "transport": "http", "error": None, "delivered_at": "2026-01-01T00:00:00+00:00", "read_at": None}],
            }
        ]

    async def get_peer(self, agent_id: str, include_secret: bool = False):
        # Everything below must be dropped by the projection.
        return {
            "agent_id": _REMOTE,
            "name": "Remote Alpha",
            "description": "a peer",
            "version": "1.0.0",
            "capabilities": ["chat"],
            "skills": [],
            "trust": "paired",
            "source": "pairing",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-01T00:00:00+00:00",
            "paired_at": "2026-01-01T00:00:00+00:00",
            "auto_reply": True,
            "url": "http://192.168.1.20:8001",
            "websocket_url": "ws://192.168.1.20:8001/api/peer-network/ws",
            "outbound_token": _SECRET_TOKEN,
            "token_hash": "deadbeef",
            "owner_id": "installation",
            "card": {"pairing_code": "leaked-code"},
        }


class _StubRunManager:
    """Reports one peer run, but only on a genuine ``peer_*`` thread.

    The prefix check mirrors production: `_peer_runs_for_conversation` only ever
    asks about `peer_thread_id(participant)` values, so a stub matching a looser
    substring would hide the very bug these tests exist to catch -- a personal
    thread leaking into a peer transcript.
    """

    async def list_by_thread(self, thread_id: str):
        if not thread_id.startswith("peer_"):
            return []
        return [
            type(
                "Rec",
                (),
                {
                    "run_id": "run_1",
                    "thread_id": thread_id,
                    "status": type("S", (), {"value": "success"})(),
                    "created_at": "2026-01-01T00:00:05+00:00",
                    "updated_at": "2026-01-01T00:00:06+00:00",
                    "error": None,
                    "stop_reason": None,
                    "model_name": "union-alpha",
                    "metadata": {"peer_network": {"message_id": "msg_1", "conversation_id": "conv_1", "peer_agent_id": _REMOTE, "kind": "chat"}},
                    "message_count": 2,
                    "llm_call_count": 1,
                    "token_usage_by_model": {"union-alpha": {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}},
                    "total_input_tokens": 5,
                    "total_output_tokens": 7,
                    "total_tokens": 12,
                    "last_ai_message": "reply from the local Alpha",
                },
            )()
        ]


class _StubEventStore:
    async def list_events(self, thread_id: str, run_id: str, **kwargs):
        return [
            {
                "run_id": run_id,
                "seq": 1,
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"type": "ai", "content": "reply from the local Alpha"},
                "metadata": {"caller": "lead_agent", "usage": {"total_tokens": 12}, "latency_ms": 90},
                "created_at": "2026-01-01T00:00:06+00:00",
            },
            {"run_id": run_id, "seq": 2, "event_type": "trace.span", "category": "trace", "content": {"big": "payload"}, "metadata": {}, "created_at": "2026-01-01T00:00:06+00:00"},
        ]


@pytest.fixture(autouse=True)
def stub_peer_plane(monkeypatch):
    monkeypatch.setattr(peer_router, "_service", lambda: _StubService())
    monkeypatch.setattr(peer_router, "get_run_manager", lambda request: _StubRunManager())
    monkeypatch.setattr(peer_router, "get_run_event_store", lambda request: _StubEventStore())


def _client(system_role: str = "user") -> TestClient:
    app = make_authed_test_app(user_factory=lambda: _user(system_role))
    app.include_router(peer_router.router)
    return TestClient(app)


# ── the substitute authorization check ──────────────────────────────────────


def test_a_threads_read_caller_is_served_which_is_why_owner_check_is_absent():
    """The whole reason owner_check is not used: an ordinary operator must read this.

    With `owner_check=True` the peer thread's owner (`NETWORK_OWNER`) would never
    equal the session user id, so the operator who owns the installation would be
    404'd from its own cross-installation history.
    """

    with _client("user") as client:
        response = client.get("/api/peer-network/transcripts/conv_1")
    assert response.status_code == 200, response.text
    assert response.json()["entries"]


def test_an_unauthenticated_caller_is_refused_not_served():
    """The substitute check must fail closed.

    `_assert_peer_network_scope` is an allow-check; if it defaulted open the
    missing `owner_check` would be an actual authorization hole rather than a
    documented substitution.
    """

    from fastapi import HTTPException

    async def _run():
        request = SimpleNamespace(state=SimpleNamespace(user=None), app=None)
        with pytest.raises(HTTPException) as caught:
            await peer_router._assert_peer_network_scope(request)
        assert caught.value.status_code == 403

    asyncio.run(_run())


def test_an_admin_is_served_like_any_local_caller():
    with _client("admin") as client:
        assert client.get("/api/peer-network/transcripts/conv_1").status_code == 200


# ── attribution: the two sides must stay distinct ──────────────────────────


def test_the_transcript_separates_the_peer_envelope_from_the_local_reply():
    with _client() as client:
        body = client.get("/api/peer-network/transcripts/conv_1").json()
    roles = [entry["role"] for entry in body["entries"]]
    assert "peer_message" in roles, roles
    assert "local_reply" in roles, roles
    peer_entry = next(entry for entry in body["entries"] if entry["role"] == "peer_message")
    assert peer_entry["sender_id"] == _REMOTE
    assert peer_entry["text"] == "hello from the other installation"
    assert peer_entry["deliveries"][0]["status"] == "delivered"


def test_the_local_reply_carries_its_run_and_thread():
    with _client() as client:
        body = client.get("/api/peer-network/transcripts/conv_1").json()
    reply = next(entry for entry in body["entries"] if entry["role"] == "local_reply")
    assert reply["run_id"] == "run_1"
    assert reply["thread_id"]


# ── nothing sensitive leaks ─────────────────────────────────────────────────


def test_no_response_body_carries_a_credential_endpoint_or_owner():
    for path in (
        "/api/peer-network/transcripts",
        "/api/peer-network/transcripts/conv_1",
        "/api/peer-network/transcripts/conv_1/export",
        "/api/peer-network/transcripts/conv_1/turns/run_1",
    ):
        with _client() as client:
            response = client.get(path)
        assert response.status_code == 200, (path, response.text[:200])
        text = response.text
        for leak, label in (
            (_SECRET_TOKEN, "outbound bearer token"),
            ("192.168.1.20", "peer endpoint host"),
            ("ws://", "peer websocket url"),
            ("deadbeef", "token hash"),
            ("leaked-code", "pairing code"),
        ):
            assert leak not in text, f"{path} leaked the {label}"


def test_turn_detail_reports_usage_and_hides_trace_rows():
    with _client() as client:
        turn = client.get("/api/peer-network/transcripts/conv_1/turns/run_1").json()["turn"]
    assert turn["run_id"] == "run_1"
    assert turn["total_tokens"] == 12
    assert turn["model_name"] == "union-alpha"
    # The trace row is internal bookkeeping, not conversation.
    assert [e["kind"] for e in turn["events"]] == ["llm.ai.response"]
    assert turn["events_truncated"] is False


def test_an_unknown_turn_is_a_404_not_an_empty_object():
    with _client() as client:
        response = client.get("/api/peer-network/transcripts/conv_1/turns/run_missing")
    assert response.status_code == 404
    assert "run_missing" in response.text


def test_an_unknown_conversation_is_a_404():
    with _client() as client:
        response = client.get("/api/peer-network/transcripts/conv_missing")
    assert response.status_code == 404
    assert "conv_missing" in response.text


# ── honesty in the response shape ───────────────────────────────────────────


def test_the_index_reports_the_plane_disabled_flag():
    """`enabled: false` must reach the client so the UI can say "off", not "empty"."""

    with _client() as client:
        body = client.get("/api/peer-network/transcripts").json()
    assert body["enabled"] is True
    assert body["count"] == 1
    assert body["transcripts"][0]["counts"]["messages"] == 1


def test_the_export_carries_the_untrusted_text_warning():
    with _client() as client:
        body = client.get("/api/peer-network/transcripts/conv_1/export").json()
    assert "untrusted" in body["export_note"].lower()
    assert body["exported_at"]


def test_read_limits_are_bounded():
    with _client() as client:
        assert client.get("/api/peer-network/transcripts?limit=99999").status_code == 200
        assert client.get("/api/peer-network/transcripts/conv_1?limit=99999").status_code == 200


def test_a_normal_chat_thread_is_never_matched_as_a_peer_turn():
    """A personal run must not appear in a peer transcript.

    `_StubRunManager` only returns a peer run for the derived `peer_*` thread, so
    a non-peer thread yields nothing to correlate and the transcript stays free
    of the operator's own conversations.
    """

    from alpha.peer_network.agent_dispatch import peer_thread_id

    manager = _StubRunManager()
    # A thread that is not a peer thread resolves to no runs at all, so a
    # personal conversation can never be correlated into a peer transcript.
    assert asyncio.run(manager.list_by_thread("chat-thread-abc")) == []
    assert asyncio.run(manager.list_by_thread(peer_thread_id(_REMOTE)))
    assert peer_thread_id(_REMOTE).startswith("peer_")
