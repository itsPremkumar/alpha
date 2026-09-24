"""Built-in OS computer-use tools: ``desktop_*`` (Module C, invariant 2).

Exports exactly five model-facing tools:

- ``desktop_screenshot``          -> alpha.computer_use.screen
- ``desktop_inspect_ui_tree``     -> alpha.computer_use.accessibility
- ``desktop_mouse_action``        -> alpha.computer_use.dispatcher
- ``desktop_keyboard_action``     -> alpha.computer_use.dispatcher
- ``desktop_window_manage``       -> accessibility + dispatcher + guard

Contract notes:
- Every ``@tool`` takes ``runtime: Runtime`` as a bare required first parameter
  (never a union/default) so pydantic can schema-generate the tool list.
- Every tool call passes :mod:`alpha.computer_use.guard` first: halt gate,
  panic-corner, window-boundary lock, hotkey blacklist -- deterministic
  enforcement, not advice. Side-effecting actions additionally hold a
  per-thread execution lease that the panic corner revokes.
- All blocking OS work runs in a worker thread (``asyncio.to_thread``); the
  event loop is never blocked (invariant 3).
- Missing optional dependencies produce honest ``status: "unavailable"``
  failure payloads naming the real package -- never fabricated output.

NOTE: deliberately no ``from __future__ import annotations`` -- LangChain's
injected-argument detection inspects the live ``runtime: Runtime`` annotation
object (see ``alpha.tools.builtins.agent_message_tool``).
"""

import asyncio
from typing import Any

from langchain.tools import tool

from alpha.computer_use import accessibility, dispatcher, screen
from alpha.computer_use.guard import get_sentinel_guard
from alpha.tools.types import Runtime

MAX_TYPE_CHARS = 10000
MAX_ELEMENTS = accessibility.MAX_ELEMENTS_LIMIT
LEASE_PREFIX = "os-computer"


def _thread_id(runtime: Any) -> str:
    """Best-effort current thread id from the injected runtime (never raises)."""
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        thread_id = context.get("thread_id")
        if thread_id:
            return str(thread_id)
    return "default_thread"


def _enrich(payload: dict[str, Any], thread_id: str) -> dict[str, Any]:
    payload["thread_id"] = thread_id
    return payload


def _begin_side_effect(runtime: Any, action: str) -> tuple[str, dict[str, Any] | None]:
    """Halt-gate + acquire the per-thread execution lease.

    Returns ``(thread_id, blocked_payload)``; ``blocked_payload`` is ``None``
    when the action may proceed.
    """
    thread_id = _thread_id(runtime)
    guard = get_sentinel_guard()
    verdict = guard.check_action(action)
    if not verdict["allowed"]:
        return thread_id, {
            "ok": False,
            "status": "blocked",
            "reason": str(verdict["reason"]),
            "guard": "halted",
            "action": action,
        }
    lease = guard.acquire_lease(f"{LEASE_PREFIX}:{thread_id}", owner=f"desktop_{action}")
    if not lease["granted"]:
        return thread_id, {
            "ok": False,
            "status": "blocked",
            "reason": str(lease.get("reason")),
            "guard": "halted",
            "action": action,
        }
    return thread_id, None


def _halt_gated(action: str) -> dict[str, Any] | None:
    """Read-only halt gate (perception stops when the sentinel halts too)."""
    verdict = get_sentinel_guard().check_action(action)
    if verdict["allowed"]:
        return None
    return {"ok": False, "status": "blocked", "reason": str(verdict["reason"]), "guard": "halted", "action": action}


@tool("desktop_screenshot", parse_docstring=True)
async def desktop_screenshot_tool(
    runtime: Runtime,
    window_title: str = "",
) -> dict[str, Any]:
    """Capture a free local screenshot of the desktop or one window (PNG, base64).

    Uses the mss backend (target <10ms) -- no cloud vision API, no tokens. When
    the optional mss dependency is missing the payload is an honest
    status="unavailable" failure naming it, never a fabricated image. With
    window_title set, the crop rectangle comes from the OS accessibility API
    (pywinauto); if that dependency is missing the call fails honestly instead
    of silently capturing the whole desktop.

    Args:
        runtime: Injected harness runtime (automatic; not model-facing).
        window_title: Optional substring of the window title to capture. Empty captures the primary monitor.
    """
    blocked = _halt_gated("screenshot")
    if blocked is not None:
        return _enrich(blocked, _thread_id(runtime))
    query = window_title.strip()
    if query:
        resolved = await asyncio.to_thread(accessibility.find_window_bbox, query)
        if not resolved.get("found"):
            return _enrich(resolved, _thread_id(runtime))
        payload = await asyncio.to_thread(screen.capture_screenshot, 1, resolved.get("bbox"))
    else:
        payload = await asyncio.to_thread(screen.capture_screenshot, 1, None)
    return _enrich(payload, _thread_id(runtime))


@tool("desktop_inspect_ui_tree", parse_docstring=True)
async def desktop_inspect_ui_tree_tool(
    runtime: Runtime,
    window_title: str = "",
    max_elements: int = 200,
) -> dict[str, Any]:
    """Inspect a window's clickable buttons, inputs, and checkboxes via the OS accessibility tree -- zero tokens.

    Returns each element as {name, type, bbox: [left, top, right, bottom],
    center: (x, y), window} plus scan metadata. A failed probe reports
    status="unavailable"/"failed" with scanned=false and an empty element list;
    an empty list with scanned=true means the walk really ran and found nothing.
    When pywinauto is not installed the payload names it honestly instead of
    pretending the screen has no UI.

    Args:
        runtime: Injected harness runtime (automatic; not model-facing).
        window_title: Optional substring selecting which window(s) to inspect; empty scans all top-level windows.
        max_elements: Maximum elements to return (1-500).
    """
    blocked = _halt_gated("inspect_ui_tree")
    if blocked is not None:
        return _enrich(blocked, _thread_id(runtime))
    payload = await asyncio.to_thread(accessibility.inspect_ui_tree, window_title.strip(), int(max_elements))
    return _enrich(payload, _thread_id(runtime))


@tool("desktop_mouse_action", parse_docstring=True)
async def desktop_mouse_action_tool(
    runtime: Runtime,
    action: str,
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    end_x: int = 0,
    end_y: int = 0,
    direction: str = "down",
) -> dict[str, Any]:
    """Dispatch a guarded mouse click, move, drag, or scroll on the desktop.

    Every call passes the sentinel guard before any backend loads: the
    panic-corner kill-switch, the window-boundary lock (coordinates outside a
    locked target window are blocked, never re-targeted; moves are clamped),
    then the optional input backend (pyautogui/pynput). Missing backends fail
    honestly with status="unavailable".

    Args:
        runtime: Injected harness runtime (automatic; not model-facing).
        action: One of 'click', 'move', 'drag', 'scroll'.
        x: Target X pixel coordinate (drag: start; scroll: unused; move: destination).
        y: Target Y pixel coordinate (drag: start; scroll: unused; move: destination).
        button: Mouse button for click/drag: 'left', 'right', or 'middle'.
        clicks: Click count for 'click', or scroll amount for 'scroll' (>= 1).
        end_x: Drag end X coordinate (action='drag' only).
        end_y: Drag end Y coordinate (action='drag' only).
        direction: Scroll direction for 'scroll': 'up' or 'down'.
    """
    normalized = action.strip().lower()
    if normalized not in {"click", "move", "drag", "scroll"}:
        return _enrich({"ok": False, "status": "invalid", "reason": f"unknown mouse action {action!r}; expected click, move, drag, or scroll", "action": normalized}, _thread_id(runtime))
    thread_id, blocked = _begin_side_effect(runtime, f"mouse_{normalized}")
    if blocked is not None:
        return _enrich(blocked, thread_id)
    if normalized == "click":
        payload = await asyncio.to_thread(dispatcher.mouse_click, int(x), int(y), button, int(clicks))
    elif normalized == "move":
        payload = await asyncio.to_thread(dispatcher.mouse_move, int(x), int(y))
    elif normalized == "drag":
        payload = await asyncio.to_thread(dispatcher.mouse_drag, int(x), int(y), int(end_x), int(end_y), button)
    else:
        payload = await asyncio.to_thread(dispatcher.mouse_scroll, int(clicks), direction)
    return _enrich(payload, thread_id)


@tool("desktop_keyboard_action", parse_docstring=True)
async def desktop_keyboard_action_tool(
    runtime: Runtime,
    action: str,
    text: str = "",
    hotkey: str = "",
    key: str = "",
    interval_ms: int = 20,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Type text, press a key, or execute a keyboard hotkey -- sentinel-guard checked.

    Destructive hotkey combinations (Win+L, desktop Alt+F4, Shift+Delete,
    Ctrl+Alt+Delete) are blocked by the blacklist before any input backend is
    loaded unless confirmed=true is passed explicitly. Missing input backends
    fail honestly with status="unavailable" -- confirmation never fabricates a
    successful dispatch.

    Args:
        runtime: Injected harness runtime (automatic; not model-facing).
        action: One of 'type', 'press', 'hotkey'.
        text: Text to type (action='type'; max 10000 characters).
        hotkey: Key combo like 'ctrl+s' or 'alt+tab' (action='hotkey').
        key: Single key like 'enter', 'escape', 'tab' (action='press').
        interval_ms: Per-keystroke delay for 'type' (0-1000 ms).
        confirmed: Explicit operator/agent confirmation to run a blacklisted hotkey.
    """
    normalized = action.strip().lower()
    if normalized not in {"type", "press", "hotkey"}:
        return _enrich({"ok": False, "status": "invalid", "reason": f"unknown keyboard action {action!r}; expected type, press, or hotkey", "action": normalized}, _thread_id(runtime))
    if normalized == "type" and not text:
        return _enrich({"ok": False, "status": "invalid", "reason": "action 'type' requires non-empty text", "action": "type"}, _thread_id(runtime))
    if normalized == "type" and len(text) > MAX_TYPE_CHARS:
        return _enrich({"ok": False, "status": "invalid", "reason": f"text too long: {len(text)} chars (max {MAX_TYPE_CHARS})", "action": "type"}, _thread_id(runtime))
    if normalized == "press" and not key.strip():
        return _enrich({"ok": False, "status": "invalid", "reason": "action 'press' requires a non-empty key", "action": "press"}, _thread_id(runtime))
    if normalized == "hotkey" and not hotkey.strip():
        return _enrich({"ok": False, "status": "invalid", "reason": "action 'hotkey' requires a non-empty hotkey like 'ctrl+s'", "action": "hotkey"}, _thread_id(runtime))
    thread_id, blocked = _begin_side_effect(runtime, f"keyboard_{normalized}")
    if blocked is not None:
        return _enrich(blocked, thread_id)
    if normalized == "type":
        payload = await asyncio.to_thread(dispatcher.keyboard_type, text, int(interval_ms))
    elif normalized == "press":
        payload = await asyncio.to_thread(dispatcher.keyboard_press, key)
    else:
        payload = await asyncio.to_thread(dispatcher.keyboard_hotkey, hotkey, bool(confirmed))
    return _enrich(payload, thread_id)


@tool("desktop_window_manage", parse_docstring=True)
async def desktop_window_manage_tool(
    runtime: Runtime,
    action: str,
    window_title: str = "",
    app: str = "",
    bbox: list[int] | None = None,
) -> dict[str, Any]:
    """List or focus windows, launch desktop software, or bind/unbind the window-boundary lock.

    Actions:
      list    - enumerate visible windows with bounding boxes (needs pywinauto; honest-unavailable without it).
      focus   - bring the window matching window_title to the foreground (needs pywinauto).
      launch  - start an executable by name/path (stdlib subprocess, shell=False).
      bind    - lock all pointer actions inside a window: pass bbox=[left, top, right, bottom] (works with no optional dependencies) or a window_title resolved via the accessibility API.
      unbind  - release the window-boundary lock.

    Args:
        runtime: Injected harness runtime (automatic; not model-facing).
        action: One of 'list', 'focus', 'launch', 'bind', 'unbind'.
        window_title: Substring selecting the target window (list/focus/bind-by-title).
        app: Executable name or path to start (action='launch').
        bbox: Explicit [left, top, right, bottom] lock rectangle (action='bind').
    """
    normalized = action.strip().lower()
    if normalized not in {"list", "focus", "launch", "bind", "unbind"}:
        return _enrich({"ok": False, "status": "invalid", "reason": f"unknown window action {action!r}; expected list, focus, launch, bind, or unbind", "action": normalized}, _thread_id(runtime))
    if normalized == "list":
        blocked = _halt_gated("window_list")
        if blocked is not None:
            return _enrich(blocked, _thread_id(runtime))
        payload = await asyncio.to_thread(accessibility.list_active_windows, window_title.strip())
        return _enrich(payload, _thread_id(runtime))
    thread_id, blocked = _begin_side_effect(runtime, f"window_{normalized}")
    if blocked is not None:
        return _enrich(blocked, thread_id)
    if normalized == "focus":
        payload = await asyncio.to_thread(accessibility.focus_window, window_title.strip())
    elif normalized == "launch":
        payload = await asyncio.to_thread(dispatcher.launch_application, app, None)
    elif normalized == "unbind":
        result = get_sentinel_guard().clear_window_bounds()
        payload = {"ok": True, "status": "ok", "locked": result["locked"], "bounds": None, "reason": "window-boundary lock released"}
    elif bbox is not None:
        result = get_sentinel_guard().set_window_bounds(bbox, label=window_title.strip())
        if result.get("locked"):
            payload = {"ok": True, "status": "ok", "locked": True, "bounds": result["bounds"], "label": result.get("label") or "", "reason": "pointer actions are now locked to this bounding box"}
        else:
            payload = {"ok": False, "status": "invalid", "locked": False, "bounds": None, "reason": str(result.get("reason"))}
    elif window_title.strip():
        resolved = await asyncio.to_thread(accessibility.find_window_bbox, window_title.strip())
        if not resolved.get("found"):
            payload = _enrich(resolved, thread_id)
            return payload
        result = get_sentinel_guard().set_window_bounds(resolved.get("bbox") or (), label=str(resolved.get("window") or window_title.strip()))
        payload = {
            "ok": bool(result.get("locked")),
            "status": "ok" if result.get("locked") else "invalid",
            "locked": bool(result.get("locked")),
            "bounds": result.get("bounds"),
            "window": resolved.get("window"),
            "reason": str(result.get("reason") or "window-boundary lock bound to the resolved window rectangle"),
        }
    else:
        payload = {"ok": False, "status": "invalid", "locked": False, "bounds": None, "reason": "bind requires an explicit bbox=[left, top, right, bottom] or a window_title to resolve"}
    return _enrich(payload, thread_id)
