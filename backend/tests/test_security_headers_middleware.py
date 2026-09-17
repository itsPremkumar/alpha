"""Unit tests for the gateway security-headers middleware."""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.gateway.security_headers_middleware import SecurityHeadersMiddleware


def _app_with_middleware(inner=None) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> PlainTextResponse:
        return PlainTextResponse("pong")

    @app.get("/custom-framing")
    def custom_framing() -> PlainTextResponse:
        return PlainTextResponse("pong", headers={"X-Frame-Options": "DENY"})

    if inner is not None:
        app.add_middleware(inner)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


class _RejectAll:
    """Inner middleware that rejects every request with 401 (stands in for AuthMiddleware)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"detail": "Authentication required"}'})


def test_security_headers_present_on_normal_response() -> None:
    with TestClient(_app_with_middleware()) as client:
        response = client.get("/ping")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["permissions-policy"] == "camera=(), geolocation=()"
    assert "strict-transport-security" not in response.headers


def test_hsts_sent_for_direct_https() -> None:
    with TestClient(_app_with_middleware(), base_url="https://testserver") as client:
        response = client.get("/ping")
    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_hsts_sent_for_forwarded_https_behind_tls_proxy() -> None:
    with TestClient(_app_with_middleware()) as client:
        response = client.get("/ping", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_existing_header_values_are_not_overwritten() -> None:
    with TestClient(_app_with_middleware()) as client:
        response = client.get("/custom-framing")
    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_rejection_from_inner_middleware_still_carries_headers() -> None:
    """Pins outermost placement: inner-layer rejections must carry the headers too."""
    with TestClient(_app_with_middleware(inner=_RejectAll)) as client:
        response = client.get("/ping")
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"


def test_rejection_over_https_carries_hsts() -> None:
    with TestClient(_app_with_middleware(inner=_RejectAll), base_url="https://testserver") as client:
        response = client.get("/ping")
    assert response.status_code == 401
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_non_http_scope_passes_through() -> None:
    """Lifespan and other non-HTTP scopes must reach the inner app untouched."""
    seen: list[str] = []

    async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])
        if scope["type"] == "lifespan":
            await send({"type": "lifespan.startup.complete"})

    middleware = SecurityHeadersMiddleware(inner_app)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    import anyio

    anyio.run(middleware, {"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    assert seen == ["lifespan"]
    assert sent == [{"type": "lifespan.startup.complete"}]
