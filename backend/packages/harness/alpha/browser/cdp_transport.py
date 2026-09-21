"""A real Chrome DevTools Protocol transport, over one websocket.

This is the layer `browser-harness` supplies to ``browser-use/jev-ultrafast``:
one websocket to Chrome, and a ``send(method, **params)`` call on top of it.
Alpha needs the same layer for the same reason — so the browser loop can drive a
real, JavaScript-capable page without Playwright, which is neither installed here
nor testable in this environment.

Why a Protocol rather than a concrete class everywhere: a transport that can be
swapped for a fake is the only way to test the behaviour that actually matters.
A stale target, a covered control, a control that vanished mid-run — none of
those can be produced against a real browser on demand, and all of them are the
reason the guards exist. ``WebSocketCdpTransport`` is the real one; the tests
inject a scripted one.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Default Chrome remote-debugging endpoint. Chrome serves ``/json/version`` and
#: ``/json/list`` here once started with ``--remote-debugging-port=9222``.
DEFAULT_ENDPOINT = "http://127.0.0.1:9222"

#: Per-call ceiling. CDP is local, so anything slower than this is a hang rather
#: than a slow page, and a hang must not consume the run's whole time budget.
DEFAULT_TIMEOUT = 15.0


class CdpError(RuntimeError):
    """A CDP call failed, or the connection did not come up.

    Raised for a protocol-level ``error`` response and for a dead socket. A
    caller that gets this has learned nothing about the page — which is why it
    must never be translated into "the action had no effect".
    """

    def __init__(self, message: str, *, code: int | None = None, data: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@runtime_checkable
class CdpTransport(Protocol):
    """One CDP connection. Injectable so the executor is testable offline."""

    async def send(self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None) -> dict[str, Any]:
        """Send one CDP command and return its ``result`` object.

        Raises :class:`CdpError` on a protocol error or a dead connection.
        """

    async def aclose(self) -> None:
        """Close the connection. Safe to call twice."""


class WebSocketCdpTransport:
    """A single websocket to Chrome, speaking CDP directly.

    Chrome interleaves *events* (``{"method": ...}`` with no ``id``) with command
    *responses* (``{"id": n, "result": ...}``) on one stream, so a response is
    read by skipping anything that is not the id we are waiting for. A lock
    serialises sends: two concurrent commands would otherwise race for whichever
    reply arrived first, and the pairing is by id only.
    """

    def __init__(self, ws_url: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.ws_url = ws_url
        self.timeout = timeout
        self._ws: Any = None
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()
        #: Every CDP command sent, in order — the run's protocol trace. Kept
        #: because "why did it do that" is otherwise unanswerable after the fact.
        self.sent: list[str] = []

    async def connect(self) -> None:
        if self._ws is not None:
            return
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise CdpError("the 'websockets' package is required for the CDP transport") from exc
        try:
            self._ws = await asyncio.wait_for(connect(self.ws_url, max_size=None), timeout=self.timeout)
        except Exception as exc:
            raise CdpError(f"could not connect to {self.ws_url}: {exc}") from exc

    async def send(self, method: str, params: dict[str, Any] | None = None, session_id: str | None = None) -> dict[str, Any]:
        await self.connect()
        message: dict[str, Any] = {"id": next(self._ids), "method": method}
        if params:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id
        self.sent.append(method)
        async with self._lock:
            try:
                await asyncio.wait_for(self._ws.send(json.dumps(message)), timeout=self.timeout)
                response = await self._await_response(message["id"])
            except CdpError:
                raise
            except Exception as exc:
                raise CdpError(f"{method} failed: {exc}") from exc
        if "error" in response:
            error = response["error"] or {}
            raise CdpError(
                f"{method} failed: {error.get('message', 'unknown error')}",
                code=error.get("code"),
                data=str(error.get("data", "")),
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def _await_response(self, message_id: int) -> dict[str, Any]:
        """Read until the reply to `message_id` arrives, skipping events."""
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get("id") != message_id:
                # An event, or a reply to a command we are not waiting on.
                continue
            return payload

    async def aclose(self) -> None:
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:
            logger.debug("CDP socket close failed; ignoring.", exc_info=True)


async def discover_ws_url(endpoint: str = DEFAULT_ENDPOINT, *, timeout: float = 5.0) -> str:
    """Resolve the browser-level websocket URL from a Chrome debugging endpoint.

    Chrome serves ``/json/version`` with a ``webSocketDebuggerUrl`` that accepts
    browser-level commands (``Target.createTarget`` and friends). A user who
    started Chrome with ``--remote-debugging-port`` has nothing else to do; the
    URL is never something a model should be asked to guess.
    """
    import httpx

    url = f"{endpoint.rstrip('/')}/json/version"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise CdpError(f"Chrome is not reachable at {endpoint}: {exc}") from exc
    ws_url = str(payload.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise CdpError(f"{url} returned no webSocketDebuggerUrl")
    return ws_url
