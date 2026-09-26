"""The peer status endpoint hands out this installation's INBOUND BEARER.

`GET /api/peer-network/status` is declared with
``@require_permission("threads", "read")`` and calls
``_service().status(include_pairing_code=True)``. That flag makes the response
carry ``pairing_code`` - the credential that authorises a peer to deliver
messages INTO this installation.

``threads:read`` is a deliberately low-privilege permission, so before this
gate existed any authenticated low-privilege caller could read the bearer that
opens the inbound plane. That is a read-side leak of a write-side capability,
and it is invisible in the response unless you know to look for it: the same
endpoint also returns harmless operational state.

The fix mirrors the pattern this router already uses for a sensitive operation
(`github/publish`): the permission decorator AND ``require_admin_user``. These
tests pin that boundary in both directions, because a gate that only proves the
happy path is not a gate.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import peer_network as peer_router

_PAIRING_CODE = "test-only-pairing-code-abcdefghijklmnop"
_STATUS = {
    "enabled": True,
    "identity": {"agent_id": "agent-under-test"},
    "discovery": {"mode": "local-first"},
    "transports": {"websocket": "ws://127.0.0.1:8743"},
    "persistence": {"backend": "sqlite"},
    "pairing_code": _PAIRING_CODE,
    "limits": {"max_peers": 32},
    "pairing": {"refused_attempts": 0},
}


def _make_user(system_role: str) -> User:
    return User(
        email="peer-status-test@example.com",
        password_hash="x",
        system_role=system_role,
        id=uuid4(),
    )


@pytest.fixture(autouse=True)
def stub_peer_service(monkeypatch):
    """Replace the peer service for EVERY test in this module.

    This must be ``autouse``: an earlier draft declared it as a plain fixture
    that no test requested, so ``_service()`` resolved to the REAL peer service
    and the admin-path assertion failed against live state. A stub that is not
    wired to the tests is worse than no stub, because it looks deliberate.
    """

    class _StubService:
        async def status(self, include_pairing_code: bool = False):
            assert include_pairing_code is True, (
                "the router must still request the pairing code; this stub "
                "asserts the production call shape has not drifted"
            )
            return dict(_STATUS)

        async def publish_github_card(self):
            return {"published": True, "result": "ok"}

        async def list_peers(self, skill: str | None = None, trust: str | None = None):
            return [{"agent_id": "peer-a", "trust": "paired"}]

    monkeypatch.setattr(peer_router, "_service", lambda: _StubService())


def _build(_unused: object, system_role: str) -> TestClient:
    app = make_authed_test_app(user_factory=lambda: _make_user(system_role))
    app.include_router(peer_router.router)
    return TestClient(app)


@pytest.mark.parametrize("system_role", ["user"])
def test_status_refuses_a_non_admin_even_though_it_holds_threads_read(system_role: str):
    """The regression: threads:read must not be enough to read the bearer.

    Only ``Literal["admin", "user"]`` is a valid ``system_role``
    (``app/gateway/auth/models.py:23``), so "user" is the single non-admin
    value. An earlier draft of this test also parametrized "viewer" and
    "member", which never reached the assertion at all - pydantic rejected the
    user object first, so two of the cases were silently vacuous.
    """

    client = _build(None, system_role)

    with client:
        response = client.get("/api/peer-network/status")

    assert response.status_code == 403, (
        f"system_role={system_role!r} was served the peer status endpoint; "
        f"body={response.text[:200]!r}"
    )
    # The whole point: the credential must not be anywhere in the body.
    assert _PAIRING_CODE not in response.text


def test_github_publish_passes_the_required_detail_argument():
    """Regression: `require_admin_user` has a REQUIRED keyword-only `detail`.

    Omitting it raised ``TypeError`` inside the route, so
    ``POST /api/peer-network/github/publish`` returned 500 for EVERY caller -
    including admins. No test exercised the route, which is why it survived on
    main. This asserts an admin gets a real authorisation decision rather than
    a crash.
    """

    class _PublishService:
        async def publish_github_card(self):
            return {"published": True, "result": "ok"}

    original = peer_router._service
    peer_router._service = lambda: _PublishService()
    try:
        client = _build(None, "admin")
        with client:
            response = client.post("/api/peer-network/github/publish", json={})
    finally:
        peer_router._service = original

    assert response.status_code == 200, (
        f"admin publish failed: {response.status_code} {response.text[:200]!r} "
        "(a TypeError here means the required `detail=` kwarg is missing again)"
    )
    assert response.json()["published"] is True


def test_status_allows_an_admin_and_returns_the_pairing_code():
    """The gate must not over-reject: an operator still needs this."""

    client = _build(None, "admin")

    with client:
        response = client.get("/api/peer-network/status")

    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["pairing_code"] == _PAIRING_CODE
    assert body["enabled"] is True


def test_the_refusal_does_not_leak_operational_state_either():
    """A 403 must be a 403, not a partially-populated body."""

    client = _build(None, "user")

    with client:
        response = client.get("/api/peer-network/status")

    assert response.status_code == 403
    payload = response.json()
    # FastAPI's HTTPException body is {"detail": ...}; assert no sibling keys
    # leaked alongside it.
    assert set(payload) <= {"detail", "code", "message"}
    assert "pairing_code" not in payload


def test_list_peers_remains_reachable_at_the_original_permission():
    """Only the credential-bearing route is tightened.

    ``/peers`` returns topology, not a bearer, and its declared permission is
    unchanged. If this ever fails because peers became admin-only, the change
    was too broad.

    The assertion is a strict 200 on purpose. An earlier draft allowed
    ``{200, 500}``, which meant a route erroring out counted as "unchanged" -
    the test could not distinguish "permission untouched" from "this route is
    broken". The stub implements ``list_peers`` so a 500 is a real failure.
    """

    client = _build(None, "user")

    with client:
        response = client.get("/api/peer-network/peers")

    assert response.status_code == 200, (
        f"/peers is no longer reachable for a plain user: {response.status_code} "
        f"{response.text[:200]!r}. Only the credential-bearing /status route "
        "should require admin."
    )
