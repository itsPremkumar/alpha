"""Pre-parse body-size ceiling for the unauthenticated peer-network ingress.

Why a middleware and not a route check: FastAPI materializes a Pydantic body
model (``await request.body()``) *before* it resolves any route dependency, so a
guard inside a handler runs after the entire request body has already been
buffered in memory. The only place that can stop the buffering is the ASGI
``receive`` channel, which is what this middleware wraps.

Why it exists: ``routers/peer_network._check_public_body_size`` reads the
caller-supplied ``Content-Length`` header. That header is attacker-controlled
and is simply absent for ``Transfer-Encoding: chunked`` requests and for
ordinary HTTP/2 request bodies, so the declared-length guard was bypassable
while the two routes stayed on the unauthenticated, CSRF-exempt public plane
(``auth_middleware._PUBLIC_EXACT_PATHS`` and
``csrf_middleware._CSRF_EXEMPT_EXACT_PATHS``). A single chunked request of
arbitrary size was buffered whole before the peer token was ever checked.

The ceiling and the exact path set are imported from
``routers/peer_network`` so the documented 512 KiB limit and the enforced one
cannot drift, and the path set stays exact - a prefix would silently extend the
ceiling to future routes under the same namespace.
"""

from __future__ import annotations

from starlette._utils import get_route_path
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Only body-carrying verbs are limited; a GET has no body to buffer.
_LIMITED_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_TOO_LARGE_DETAIL = "Peer network request exceeds the 512 KiB limit"


def public_peer_body_limit_middleware(max_body_bytes: int, exact_paths: frozenset[str]) -> type:
    """Build a pure-ASGI middleware enforcing *max_body_bytes* on *exact_paths*.

    A factory rather than module-level configuration so the limit and the path
    set are supplied by the module that documents them
    (``app.gateway.routers.peer_network``) rather than duplicated here.
    """

    class PublicBodyLimitMiddleware:
        """Reject an oversized body on the exact paths, before it is buffered."""

        def __init__(self, app: ASGIApp) -> None:
            self.app = app
            self._max_body_bytes = max_body_bytes
            self._exact_paths = exact_paths

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http" or scope.get("method") not in _LIMITED_METHODS:
                await self.app(scope, receive, send)
                return

            if get_route_path(scope).rstrip("/") not in self._exact_paths:
                await self.app(scope, receive, send)
                return

            received = 0

            async def bounded_receive() -> Message:
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b"") or b"")
                    if received > self._max_body_bytes:
                        # A real HTTPException, not a bespoke error: Starlette's
                        # ExceptionMiddleware (which sits below every user
                        # middleware) renders it as the 413 it is, and FastAPI's
                        # body-parse wrapper re-raises HTTPException unchanged
                        # instead of flattening it into a misleading 400
                        # "There was an error parsing the body".
                        raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)
                return message

            await self.app(scope, bounded_receive, send)

    return PublicBodyLimitMiddleware
