"""A browser backend that drives a real Chrome over CDP.

This is the piece Alpha was missing. ``SupervisorExecutor`` is a stub, and
``PlaywrightExecutor`` needs Playwright — which is not installed, and which is
not testable in this environment either. ``HtmlExecutor`` can reach a real page
but only as far as a plain HTTP request goes: no JavaScript, no client-side
state, no canvas.

This backend takes the route ``browser-use/jev-ultrafast`` takes: talk CDP to a
Chrome the user already has, address elements by *identity* rather than by
description, and let the page hold the references. Three consequences worth
stating plainly:

* **Model output never becomes a selector, a coordinate, or JavaScript.** The
  policy picks an index; the index maps to an :class:`Element`; the element
  carries a ``node`` id the page issued. Only the page can resolve that id, and
  it resolves to the element it was issued for.
* **Geometry is resolved at input time, not observation time.** A snapshot is
  stale the moment it is taken, so the click is hit-tested against
  ``document.elementFromPoint`` immediately before the event is dispatched. A
  control that moved, was covered, or went away is refused rather than clicked
  blind.
* **A password field's value is never read.** The field stays a target so a login
  can be filled; only the value is withheld.

The transport is injected, so all of the interesting behaviour — a stale target,
a covered control, a control that vanished — is testable with no browser at all.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from alpha.browser.cdp_transport import (
    DEFAULT_ENDPOINT,
    CdpError,
    CdpTransport,
    WebSocketCdpTransport,
    discover_ws_url,
)
from alpha.browser.element_table import CLICK, DONE, SCROLL_DOWN, SCROLL_UP, SELECT, TYPE_TEXT, WAIT, Element

logger = logging.getLogger(__name__)

#: Placeholders substituted into the JS below. Chosen so they cannot collide with
#: anything in the script itself — a bare word like "NODE" would be one edit away
#: from being replaced in the wrong place.
_NODE_SLOT = "__NODE__"
_ARGS_SLOT = "__ARGS__"

#: Where the scroll wheel is dispatched. Anywhere in the viewport works; a point
#: in the middle is the least likely to land on a fixed header or a floating
#: control that would swallow the event.
_SCROLL_X = 550
_SCROLL_Y = 650
_SCROLL_DELTA = 560

#: JS: resolve a node id to the live element and its current click point, or
#: null. Every rejection here is one the reference implementation makes, and each
#: one is a click that would otherwise land somewhere unintended.
_RESOLVE_JS = """
((node) => {
  const c = window.__alphaNodes;
  const e = c && c.nodes.get(node);
  if (!e) return null;
  if (!e.isConnected) return null;
  if (e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]')) return null;
  if (!e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return null;
  const r = e.getBoundingClientRect();
  const x = r.x + r.width / 2, y = r.y + r.height / 2;
  if (!r.width || !r.height) return null;
  if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return null;
  if (!e.contains(document.elementFromPoint(x, y))) return null;
  return {x: x, y: y, tag: e.tagName, type: e.type || '', readOnly: e.readOnly === true};
})(""" + _NODE_SLOT + """)
"""

#: JS: set a native select's value and fire the events a framework listens for.
#: Assigning `.value` alone changes the DOM and not the application state.
_SELECT_JS = """
((args) => {
  const c = window.__alphaNodes;
  const e = c && c.nodes.get(args.node);
  if (!e || e.tagName !== 'SELECT') return null;
  const wanted = String(args.value);
  const option = [...e.options].find((o) => o.value === wanted);
  if (!option || option.disabled || option.closest('optgroup[disabled]')) return null;
  e.value = option.value;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return e.value;
})(""" + _ARGS_SLOT + """)
"""

#: JS: how many nodes the page is still holding. Diagnostic only — a registry
#: that grows without bound is a leak, and this is how a caller can see it.
_REGISTRY_SIZE_JS = "(() => { const c = window.__alphaNodes; return c ? c.nodes.size : 0; })()"


def _resolve_js(node: int) -> str:
    return _RESOLVE_JS.replace(_NODE_SLOT, str(int(node)))


def _select_js(node: int, value: str) -> str:
    return _SELECT_JS.replace(_ARGS_SLOT, json.dumps({"node": int(node), "value": value}))


class CdpExecutor:
    """Drive a real Chrome page over the DevTools Protocol.

    Args:
        url: The page to open.
        transport: An injected :class:`CdpTransport`. When omitted, a websocket
            is opened to `ws_url` (discovered from `endpoint` if not given).
        endpoint: Chrome's HTTP debugging endpoint, used only to discover the
            websocket URL.
        url_policy: Optional ``Callable[[str], bool]`` checked before navigating
            and before following a link. Not set by default: this drives the
            user's own browser, where the risk is acting on an authenticated
            session rather than server-side request forgery. ``CDPSecurityPolicy
            ().is_url_permitted`` can be passed, with the caveat that it matches
            substrings — its default list blocks any URL containing "bank".
        settle_timeout: How long to wait for ``document.readyState`` to reach
            ``complete`` after a navigation.
    """

    #: A live page can be re-read, so "did this change anything" is answerable.
    tracks_changes = True

    def __init__(
        self,
        url: str,
        *,
        transport: CdpTransport | None = None,
        endpoint: str = DEFAULT_ENDPOINT,
        ws_url: str = "",
        url_policy: Callable[[str], bool] | None = None,
        settle_timeout: float = 15.0,
    ) -> None:
        self.url = url
        self._transport = transport
        self._endpoint = endpoint
        self._ws_url = ws_url
        self._url_policy = url_policy
        self._settle_timeout = settle_timeout
        self._session: str | None = None
        self._target: str | None = None
        self._state: dict[str, Any] = {"url": url, "title": "", "text": "", "elements": []}
        #: Every CDP command issued, in order. The run's protocol trace.
        self.commands: list[str] = []
        #: How many times the page was read. One step costs at least two.
        self.reads = 0

    # -- lifecycle --------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        """The attached CDP session, once there is one."""
        return self._session

    async def _send(self, method: str, **params: Any) -> dict[str, Any]:
        transport = await self._transport_or_create()
        self.commands.append(method)
        return await transport.send(method, params or None, self._session)

    async def _transport_or_create(self) -> CdpTransport:
        if self._transport is None:
            ws_url = self._ws_url or await discover_ws_url(self._endpoint)
            self._transport = WebSocketCdpTransport(ws_url)
        return self._transport

    async def _ensure_session(self) -> None:
        """Open a background tab, attach, and navigate. Idempotent."""
        if self._session is not None:
            return
        created = await self._send("Target.createTarget", url="about:blank", background=True)
        self._target = str(created.get("targetId") or "")
        if not self._target:
            raise CdpError("Target.createTarget returned no targetId")
        attached = await self._send("Target.attachToTarget", targetId=self._target, flatten=True)
        self._session = str(attached.get("sessionId") or "")
        if not self._session:
            raise CdpError("Target.attachToTarget returned no sessionId")
        # A fixed viewport makes geometry reproducible, and focus emulation keeps
        # requestAnimationFrame running in a tab that is never activated — menus
        # and transitions would otherwise stall in a background tab.
        await self._send("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        await self._send("Emulation.setFocusEmulationEnabled", enabled=True)
        await self._navigate(self.url)
        await self._settle()

    async def _navigate(self, url: str) -> None:
        if self._url_policy is not None and not self._url_policy(url):
            raise CdpError(f"navigation to {url!r} refused by the url policy")
        await self._send("Page.navigate", url=url)

    async def _settle(self) -> None:
        """Wait for the document to finish loading. Best effort, never fatal."""
        import asyncio

        deadline = asyncio.get_running_loop().time() + self._settle_timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                if await self._evaluate("document.readyState") == "complete":
                    return
            except CdpError:
                return
            await asyncio.sleep(0.05)

    async def aclose(self) -> None:
        """Close the tab this executor opened. Never closes the user's browser."""
        if self._target:
            try:
                await self._send("Target.closeTarget", targetId=self._target)
            except CdpError:
                logger.debug("Could not close the CDP target; ignoring.", exc_info=True)
            self._target = None
        self._session = None
        if self._transport is not None:
            await self._transport.aclose()

    # -- observing --------------------------------------------------------

    async def _evaluate(self, expression: str, *, await_promise: bool = False) -> Any:
        params: dict[str, Any] = {"expression": expression, "returnByValue": True}
        if await_promise:
            params["awaitPromise"] = True
        response = await self._send("Runtime.evaluate", **params)
        if response.get("exceptionDetails"):
            detail = response["exceptionDetails"].get("text") or "evaluation failed"
            raise CdpError(str(detail))
        return (response.get("result") or {}).get("value")

    async def observe(self) -> dict[str, Any]:
        """Read the page. A failed read keeps the last known state.

        Reporting an empty page instead would look like the page had been
        emptied, and the freshness check downstream would read it as a change.
        """
        from alpha.browser.cdp_snapshot import CDP_SNAPSHOT_JS, page_state_from_cdp

        try:
            await self._ensure_session()
        except CdpError as exc:
            logger.debug("CDP session unavailable: %s", exc)
            return dict(self._state)

        self.reads += 1
        try:
            payload = await self._evaluate(CDP_SNAPSHOT_JS)
        except CdpError as exc:
            # A navigation in flight is the common case, and it is not a failure.
            logger.debug("Snapshot failed, keeping last known state: %s", exc)
            return dict(self._state)

        if payload is None:
            # `document.body` was absent: the document is still navigating.
            return dict(self._state)
        state = page_state_from_cdp(payload)
        if state["elements"] or state["text"]:
            self._state = state
        elif not self._state.get("elements"):
            self._state = state
        return dict(self._state)

    async def fingerprint(self) -> str | None:
        """A fingerprint of the live page, or None when it cannot be read."""
        from alpha.browser.freshness import page_fingerprint

        try:
            state = await self.observe()
        except Exception as exc:
            logger.debug("Could not fingerprint the CDP page: %s", exc)
            return None
        if not state.get("elements") and not state.get("text"):
            return None
        return page_fingerprint(state)

    # -- acting -----------------------------------------------------------

    async def act(self, operation: str, target: str | None, text: str, element: Element | None) -> dict[str, Any]:
        if operation in (DONE, WAIT, "BLOCKED"):
            return {"ok": True, "detail": f"{operation.lower()} (no-op)"}
        if operation in (SCROLL_UP, SCROLL_DOWN):
            return await self._scroll(-_SCROLL_DELTA if operation == SCROLL_UP else _SCROLL_DELTA)
        if element is None:
            return {"ok": False, "detail": "no element for this operation"}
        node = element.node
        if not isinstance(node, int):
            # A backend with a node registry must never fall back to a selector:
            # that is exactly the round trip the registry exists to avoid.
            return {"ok": False, "detail": f"'{element.label}' has no live node id; observe again"}
        try:
            await self._ensure_session()
        except CdpError as exc:
            return {"ok": False, "detail": f"no CDP session: {exc}"}

        if operation == CLICK:
            return await self._click(node, element)
        if operation == TYPE_TEXT:
            return await self._type(node, element, text)
        if operation == SELECT:
            return await self._select(node, element, target)
        return {"ok": False, "detail": f"unsupported operation {operation}"}

    async def _resolve(self, node: int) -> dict[str, Any] | None:
        """The node's current click point, or None if it is not clickable now."""
        try:
            resolved = await self._evaluate(_resolve_js(node))
        except CdpError:
            return None
        return resolved if isinstance(resolved, dict) else None

    async def _mouse_click(self, x: float, y: float) -> None:
        for event in ("mousePressed", "mouseReleased"):
            await self._send("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)

    async def _click(self, node: int, element: Element) -> dict[str, Any]:
        point = await self._resolve(node)
        if point is None:
            return {
                "ok": False,
                "detail": f"'{element.label}' is gone, covered, or off-screen; observe again",
            }
        try:
            await self._mouse_click(point["x"], point["y"])
        except CdpError as exc:
            return {"ok": False, "detail": f"click failed: {exc}"}
        return {"ok": True, "detail": f"clicked {element.label}"}

    async def _type(self, node: int, element: Element, text: str) -> dict[str, Any]:
        if not text:
            return {"ok": False, "detail": f"no value to type into '{element.label}'"}
        point = await self._resolve(node)
        if point is None:
            return {"ok": False, "detail": f"'{element.label}' is gone, covered, or off-screen; observe again"}
        if point.get("readOnly"):
            return {"ok": False, "detail": f"'{element.label}' is read-only"}
        import sys

        try:
            # Click to focus, select what is there, then replace it. Typing
            # character by character would be slower and would append to a value
            # the goal may be trying to overwrite.
            await self._mouse_click(point["x"], point["y"])
            modifier = 4 if sys.platform == "darwin" else 2
            for event in ("keyDown", "keyUp"):
                await self._send(
                    "Input.dispatchKeyEvent",
                    type=event,
                    key="a",
                    code="KeyA",
                    modifiers=modifier,
                    **({"commands": ["selectAll"]} if event == "keyDown" else {}),
                )
            await self._send("Input.insertText", text=text)
        except CdpError as exc:
            return {"ok": False, "detail": f"typing failed: {exc}"}
        return {"ok": True, "detail": f"typed into {element.label}"}

    async def _select(self, node: int, element: Element, target: str | None) -> dict[str, Any]:
        # The action space addresses an option as "<element>:<offset>", so the
        # target names the option — the same addressing the HTTP backend uses,
        # for the same reason.
        option = next((o for o in element.options if str(o.get("index")) == str(target)), None)
        if option is None:
            # Defensive: an element built without option indices still carries a
            # positional offset in the target's tail.
            try:
                offset = int(str(target or "").split(":")[-1])
            except (TypeError, ValueError):
                offset = 0
            if 1 <= offset <= len(element.options):
                option = element.options[offset - 1]
        if option is None:
            return {"ok": False, "detail": f"unknown option {target!r} for '{element.label}'"}
        value = str(option.get("value") or option.get("label") or "")
        if value == "":
            return {"ok": False, "detail": f"option {target!r} on '{element.label}' has no value"}
        try:
            applied = await self._evaluate(_select_js(node, value))
        except CdpError as exc:
            return {"ok": False, "detail": f"select failed: {exc}"}
        if applied is None:
            # The element is no longer a live <select>, or the option vanished.
            return {"ok": False, "detail": f"'{element.label}' could not be set to {value!r}; observe again"}
        return {"ok": True, "detail": f"selected {value!r} on {element.label}"}

    async def _scroll(self, delta: int) -> dict[str, Any]:
        try:
            await self._ensure_session()
            await self._send(
                "Input.dispatchMouseEvent",
                type="mouseWheel",
                x=_SCROLL_X,
                y=_SCROLL_Y,
                deltaX=0,
                deltaY=delta,
            )
        except CdpError as exc:
            return {"ok": False, "detail": f"scroll failed: {exc}"}
        return {"ok": True, "detail": f"scrolled {'up' if delta < 0 else 'down'}"}

    async def registry_size(self) -> int:
        """How many nodes the page is still holding. Diagnostic."""
        try:
            await self._ensure_session()
            size = await self._evaluate(_REGISTRY_SIZE_JS)
        except CdpError:
            return 0
        return int(size) if isinstance(size, (int, float)) else 0
