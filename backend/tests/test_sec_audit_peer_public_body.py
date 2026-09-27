"""Security audit: the unauthenticated peer-network plane must bound the bytes it
buffers, not just trust a client-declared ``Content-Length``.

``routers/peer_network.py`` mounts two exact-path public POST routes
(``/api/peer-network/remote/pair`` and ``/api/peer-network/inbound/messages``)
that are exempt from both the auth and the CSRF middleware
(``auth_middleware._PUBLIC_EXACT_PATHS`` /
``csrf_middleware._CSRF_EXEMPT_EXACT_PATHS``). Their only size guard read the
caller-supplied ``Content-Length`` header, which is absent for
``Transfer-Encoding: chunked`` and for ordinary HTTP/2 bodies. FastAPI
materializes the Pydantic body model *before* the handler runs, so an absent
header meant the whole body was buffered with no ceiling at all - on the plane
where the peer token is the only thing between the internet and a parsed agent
message.

The enforcement point therefore has to be the ASGI receive channel, which is
``PublicBodyLimitMiddleware`` (registered by ``create_app()``).
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import peer_network

_CAP = peer_network._MAX_PUBLIC_BODY_BYTES


def _oversized_envelope() -> tuple[bytes, list[bytes]]:
    """Return (oversized JSON body, chunk generator that omits Content-Length)."""
    payload = {
        "kind": "chat",
        "sender_id": "attacker",
        "text": "hi",
        "payload": {"blob": "A" * (_CAP * 2)},
    }
    body = json.dumps(payload).encode("utf-8")
    return body, [body[i : i + 65536] for i in range(0, len(body), 65536)]


def _client() -> TestClient:
    app = FastAPI()
    # Registered exactly as ``create_app()`` does, so the test exercises the
    # shipped wiring rather than a reimplementation of it.
    app.add_middleware(peer_network.public_peer_body_limit_middleware())
    app.include_router(peer_network.public_router)
    return TestClient(app)


def test_public_pair_rejects_an_oversized_chunked_body() -> None:
    body, chunks = _oversized_envelope()
    with _client() as client:
        response = client.post(
            "/api/peer-network/remote/pair",
            # A generator body makes httpx use chunked framing: no Content-Length
            # header is sent, exactly like a chunked or HTTP/2 attacker.
            content=iter(chunks),
            headers={"content-type": "application/json"},
        )
    assert len(body) > _CAP
    assert response.status_code == 413, f"expected 413, got {response.status_code}: {response.text[:300]}"


def test_public_inbound_rejects_an_oversized_chunked_body() -> None:
    _, chunks = _oversized_envelope()
    with _client() as client:
        response = client.post(
            "/api/peer-network/inbound/messages",
            content=iter(chunks),
            headers={"content-type": "application/json", "x-alpha-peer-token": "forged"},
        )
    assert response.status_code == 413, f"expected 413, got {response.status_code}: {response.text[:300]}"


def test_public_declared_oversize_is_still_rejected() -> None:
    """The pre-existing Content-Length check must keep working."""
    body, _ = _oversized_envelope()
    with _client() as client:
        response = client.post(
            "/api/peer-network/remote/pair",
            content=body,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413, response.text


def test_public_still_accepts_a_body_under_the_cap() -> None:
    """Positive control: the cap must not reject legitimate-sized deliveries."""
    payload = {"kind": "chat", "sender_id": "peer", "text": "hello"}
    with _client() as client:
        response = client.post(
            "/api/peer-network/inbound/messages",
            json=payload,
            headers={"x-alpha-peer-token": "forged"},
        )
    # A forged token is rejected with 401 by the route's own token check -
    # the point is that the size gate did NOT turn this into a 413.
    assert response.status_code == 401, response.text


def test_the_limit_is_not_applied_to_other_routes() -> None:
    """The ceiling is exact-path: a large body elsewhere must not become a 413."""
    app = FastAPI()
    app.add_middleware(peer_network.public_peer_body_limit_middleware())

    from fastapi import APIRouter

    other = APIRouter()

    @other.post("/not-peer-network")
    async def _sink() -> dict:
        return {"ok": True}

    app.include_router(other)

    big = b"x" * (_CAP * 2)
    with TestClient(app) as client:
        response = client.post("/not-peer-network", content=big, headers={"content-type": "application/octet-stream"})
    assert response.status_code == 200, response.text
