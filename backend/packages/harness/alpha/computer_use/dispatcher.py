"""OS input dispatcher: mouse, keyboard, and application launch (Module C).

Wraps ``pyautogui`` (preferred) or ``pynput`` behind optional imports with an
honest-unavailable contract -- when neither backend is installed every action
returns ``{"ok": false, "status": "unavailable", "dispatched": false, "reason":
"dependency not installed: ..."}`` and *never* a fabricated success.

Safety: every action passes :mod:`alpha.computer_use.guard` first (halt gate ->
action-specific checks: panic corner / window-boundary lock / hotkey blacklist /
launch scope -> backend availability -> physical pointer panic probe ->
dispatch), so the sentinel enforces its boundaries deterministically even if a
caller bypasses the desktop_* tools. The hotkey blacklist is lifted only by a
single-use, combo-bound **operator** confirmation token, and ``launch_application``
refuses a caller-supplied path unless an operator allowlisted that exact
executable. ``launch_application`` uses stdlib ``subprocess`` with ``shell=False``
(no optional dependency).

Blocking OS calls made through this module must be invoked from worker threads
(the desktop_* tools do this via ``asyncio.to_thread``) -- never directly on an
asyncio event loop. Nothing in this module has an internal deadline: the
deadline lives at the tool boundary (``_run_os`` in
:mod:`alpha.tools.builtins.os_computer_tool`), because a thread cannot cancel a
wedged OS call in flight.
"""

from __future__ import annotations

import importlib
import subprocess
import time
from collections.abc import Sequence
from typing import Any

from alpha.computer_use.guard import SentinelGuard, get_sentinel_guard, normalize_hotkey

INPUT_BACKENDS = ("pyautogui", "pynput")
MOUSE_BUTTONS = ("left", "right", "middle")
SCROLL_DIRECTIONS = ("up", "down")


def _optional_import(name: str) -> tuple[Any | None, str | None]:
    """Lazily import ``name``; return ``(module, None)`` or ``(None, honest reason)``.

    Tests replace this seam (and inject fake modules) so no real mouse or
    keyboard event can ever be produced by the unit-test suite.
    """
    try:
        return importlib.import_module(name), None
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None, f"dependency not installed: {name}"
        return None, f"dependency import failed for {name}: missing {exc.name}: {exc}"
    except ImportError as exc:
        return None, f"dependency import failed for {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a backend may raise anything at import time
        return None, f"dependency failed to load: {name}: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Payload builders (uniform ok/status vocabulary across the package)
# ---------------------------------------------------------------------------


def _blocked(reason: str, action: str, *, halted: bool = False, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "status": "blocked", "dispatched": False, "action": action, "reason": reason}
    if halted:
        payload["halted"] = True
    payload.update(extra)
    return payload


def _unavailable(reason: str) -> dict[str, Any]:
    return {"ok": False, "status": "unavailable", "dispatched": False, "backend": None, "reason": reason}


def _invalid(reason: str, action: str) -> dict[str, Any]:
    return {"ok": False, "status": "invalid", "dispatched": False, "action": action, "reason": reason}


def _failed(reason: str, action: str) -> dict[str, Any]:
    return {"ok": False, "status": "failed", "dispatched": False, "action": action, "reason": reason}


def _ok(action: str, backend: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "status": "ok", "dispatched": True, "action": action, "backend": backend}
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class _Backend:
    """Minimal input-backend protocol. Implementations must not run guard checks."""

    name = "backend"

    def pointer_position(self) -> tuple[int, int] | None:
        """Current physical pointer position, or None when the backend cannot read it."""
        return None

    def click(self, x: int, y: int, button: str, clicks: int) -> None:
        raise NotImplementedError

    def move(self, x: int, y: int, smooth: bool) -> None:
        raise NotImplementedError

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, button: str, duration: float) -> None:
        raise NotImplementedError

    def scroll(self, clicks: int, direction: str) -> None:
        raise NotImplementedError

    def type_text(self, text: str, interval_ms: int) -> None:
        raise NotImplementedError

    def hotkey(self, keys: Sequence[str]) -> None:
        raise NotImplementedError

    def press(self, key: str) -> None:
        raise NotImplementedError


class _PyAutoGuiBackend(_Backend):
    """pyautogui adapter (preferred backend: smooth moves, hotkeys, scroll)."""

    name = "pyautogui"

    def __init__(self, module: Any) -> None:
        self._pyautogui = module

    def pointer_position(self) -> tuple[int, int] | None:
        try:
            pos = self._pyautogui.position()
        except Exception:  # noqa: BLE001 - an unreadable pointer must not crash dispatch
            return None
        try:
            return (int(pos[0]), int(pos[1]))
        except (TypeError, ValueError, IndexError):
            return None

    def click(self, x: int, y: int, button: str, clicks: int) -> None:
        self._pyautogui.click(x=x, y=y, button=button, clicks=clicks)

    def move(self, x: int, y: int, smooth: bool) -> None:
        self._pyautogui.moveTo(x, y, duration=0.15 if smooth else 0)

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, button: str, duration: float) -> None:
        self._pyautogui.moveTo(start_x, start_y, duration=0)
        self._pyautogui.dragTo(end_x, end_y, duration=duration, button=button)

    def scroll(self, clicks: int, direction: str) -> None:
        amount = clicks if direction == "up" else -clicks
        self._pyautogui.scroll(amount)

    def type_text(self, text: str, interval_ms: int) -> None:
        self._pyautogui.write(text, interval=max(0, interval_ms) / 1000.0)

    def hotkey(self, keys: Sequence[str]) -> None:
        self._pyautogui.hotkey(*keys)

    def press(self, key: str) -> None:
        self._pyautogui.press(key)


class _PynputBackend(_Backend):
    """pynput fallback adapter (plain instant positioning; ``smooth`` is ignored)."""

    name = "pynput"

    def __init__(self, module: Any) -> None:
        mouse_mod = importlib.import_module("pynput.mouse")
        keyboard_mod = importlib.import_module("pynput.keyboard")
        self._mouse = mouse_mod.Controller()
        self._keyboard = keyboard_mod.Controller()
        self._button = mouse_mod.Button
        self._key = keyboard_mod.Key

    def _resolve_button(self, button: str) -> Any:
        return getattr(self._button, button, self._button.left)

    def _resolve_key(self, token: str) -> Any:
        if len(token) == 1:
            return token
        named = getattr(self._key, token, None)
        if named is None and token == "win":
            named = getattr(self._key, "cmd", None)
        return named if named is not None else token

    def pointer_position(self) -> tuple[int, int] | None:
        try:
            pos = self._mouse.position
            return (int(pos[0]), int(pos[1]))
        except Exception:  # noqa: BLE001 - unreadable pointer is not fatal
            return None

    def click(self, x: int, y: int, button: str, clicks: int) -> None:
        self._mouse.position = (x, y)
        for _ in range(max(1, clicks)):
            self._mouse.click(self._resolve_button(button), 1)

    def move(self, x: int, y: int, smooth: bool) -> None:
        del smooth  # pynput positions the pointer instantly; documented limitation
        self._mouse.position = (x, y)

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int, button: str, duration: float) -> None:
        del duration
        resolved = self._resolve_button(button)
        self._mouse.position = (start_x, start_y)
        self._mouse.press(resolved, 1)
        self._mouse.position = (end_x, end_y)
        self._mouse.release(resolved, 1)

    def scroll(self, clicks: int, direction: str) -> None:
        self._mouse.scroll(0, clicks if direction == "up" else -clicks)

    def type_text(self, text: str, interval_ms: int) -> None:
        delay = max(0, interval_ms) / 1000.0
        for character in text:
            self._keyboard.type(character)
            if delay:
                time.sleep(delay)

    def hotkey(self, keys: Sequence[str]) -> None:
        resolved = [self._resolve_key(token) for token in keys]
        for item in resolved:
            self._keyboard.press(item)
        for item in reversed(resolved):
            self._keyboard.release(item)

    def press(self, key: str) -> None:
        resolved = self._resolve_key(key)
        self._keyboard.press(resolved)
        self._keyboard.release(resolved)


def _load_backend() -> tuple[_Backend | None, str | None]:
    """Return the first usable input backend, or ``(None, honest reason)``."""
    failures: list[str] = []
    for backend_name, factory in (("pyautogui", _PyAutoGuiBackend), ("pynput", _PynputBackend)):
        module, reason = _optional_import(backend_name)
        if module is None:
            failures.append(str(reason or f"dependency not installed: {backend_name}"))
            continue
        try:
            return factory(module), None
        except Exception as exc:  # noqa: BLE001 - report and fall through to the next backend
            failures.append(f"{backend_name} backend failed to initialise: {type(exc).__name__}: {exc}")
    return None, "; ".join(failures)


def availability() -> dict[str, Any]:
    """Probe input backends without dispatching a single event."""
    backend, reason = _load_backend()
    if backend is None:
        return {"available": False, "backend": None, "reason": reason, "missing_packages": list(INPUT_BACKENDS)}
    return {"available": True, "backend": backend.name, "reason": None}


def _probe_pointer(guard: SentinelGuard, backend: _Backend) -> dict[str, Any] | None:
    """Feed the physical pointer position into the panic-corner check (best-effort)."""
    position = backend.pointer_position()
    if position is None:
        return None
    return guard.observe_pointer(int(position[0]), int(position[1]))


# ---------------------------------------------------------------------------
# Mouse actions (each: guard -> validate -> backend -> panic probe -> dispatch)
# ---------------------------------------------------------------------------


def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
    """Guard-checked click at ``(x, y)``."""
    guard = get_sentinel_guard()
    verdict = guard.check_click(x, y)
    if not verdict["allowed"]:
        return _blocked(str(verdict["reason"]), "click", halted=bool(verdict.get("halted")))
    if button not in MOUSE_BUTTONS:
        return _invalid(f"unknown mouse button {button!r}; expected one of {list(MOUSE_BUTTONS)}", "click")
    if int(clicks) < 1:
        return _invalid("clicks must be >= 1", "click")
    backend, reason = _load_backend()
    if backend is None:
        return _unavailable(str(reason))
    probe = _probe_pointer(guard, backend)
    if probe is not None and probe.get("panic"):
        return _blocked(str(probe.get("reason")), "click", halted=True)
    try:
        backend.click(int(x), int(y), button, int(clicks))
    except Exception as exc:  # noqa: BLE001 - honest dispatch failure
        return _failed(f"mouse click dispatch failed: {type(exc).__name__}: {exc}", "click")
    return _ok("click", backend.name, x=int(x), y=int(y), button=button, clicks=int(clicks))


def mouse_move(x: int, y: int, smooth: bool = True) -> dict[str, Any]:
    """Guard-checked move; coordinates are clamped inside a window lock."""
    guard = get_sentinel_guard()
    verdict = guard.check_move(x, y)
    if not verdict["allowed"]:
        return _blocked(str(verdict["reason"]), "move", halted=bool(verdict.get("halted")))
    backend, reason = _load_backend()
    if backend is None:
        return _unavailable(str(reason))
    probe = _probe_pointer(guard, backend)
    if probe is not None and probe.get("panic"):
        return _blocked(str(probe.get("reason")), "move", halted=True)
    target_x, target_y = int(verdict["x"]), int(verdict["y"])
    try:
        backend.move(target_x, target_y, bool(smooth))
    except Exception as exc:  # noqa: BLE001 - honest dispatch failure
        return _failed(f"mouse move dispatch failed: {type(exc).__name__}: {exc}", "move")
    return _ok("move", backend.name, x=target_x, y=target_y, clamped=bool(verdict.get("clamped")), smooth=bool(smooth))


def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left", duration: float = 0.2) -> dict[str, Any]:
    """Guard-checked drag; both endpoints must pass the window-boundary lock."""
    guard = get_sentinel_guard()
    start_verdict = guard.check_click(start_x, start_y)
    if not start_verdict["allowed"]:
        return _blocked(str(start_verdict["reason"]), "drag", halted=bool(start_verdict.get("halted")))
    end_verdict = guard.check_click(end_x, end_y)
    if not end_verdict["allowed"]:
        return _blocked(str(end_verdict["reason"]), "drag", halted=bool(end_verdict.get("halted")))
    if button not in MOUSE_BUTTONS:
        return _invalid(f"unknown mouse button {button!r}; expected one of {list(MOUSE_BUTTONS)}", "drag")
    backend, reason = _load_backend()
    if backend is None:
        return _unavailable(str(reason))
    probe = _probe_pointer(guard, backend)
    if probe is not None and probe.get("panic"):
        return _blocked(str(probe.get("reason")), "drag", halted=True)
    try:
        backend.drag(int(start_x), int(start_y), int(end_x), int(end_y), button, max(0.0, float(duration)))
    except Exception as exc:  # noqa: BLE001 - honest dispatch failure
        return _failed(f"mouse drag dispatch failed: {type(exc).__name__}: {exc}", "drag")
    return _ok("drag", backend.name, start_x=int(start_x), start_y=int(start_y), end_x=int(end_x), end_y=int(end_y), button=button)


def mouse_scroll(clicks: int, direction: str = "down") -> dict[str, Any]:
    """Guard-checked scroll at the current pointer; pointer position must stay inside a window lock."""
    guard = get_sentinel_guard()
    verdict = guard.check_action("scroll")
    if not verdict["allowed"]:
        return _blocked(str(verdict["reason"]), "scroll", halted=True)
    normalized_direction = str(direction).strip().lower()
    if normalized_direction not in SCROLL_DIRECTIONS:
        return _invalid(f"unknown scroll direction {direction!r}; expected one of {list(SCROLL_DIRECTIONS)}", "scroll")
    if int(clicks) < 1:
        return _invalid("scroll clicks must be >= 1", "scroll")
    backend, reason = _load_backend()
    if backend is None:
        return _unavailable(str(reason))
    position = backend.pointer_position()
    if position is not None:
        probe = guard.observe_pointer(int(position[0]), int(position[1]))
        if probe.get("panic"):
            return _blocked(str(probe.get("reason")), "scroll", halted=True)
        # Containment: a scroll emits at the pointer, so a locked window also
        # locks where the scroll may land.
        lock_verdict = guard.check_click(int(position[0]), int(position[1]))
        if not lock_verdict["allowed"]:
            return _blocked(f"scroll refused: {lock_verdict['reason']}", "scroll", halted=bool(lock_verdict.get("halted")))
    try:
        backend.scroll(int(clicks), normalized_direction)
    except Exception as exc:  # noqa: BLE001 - honest dispatch failure
        return _failed(f"mouse scroll dispatch failed: {type(exc).__name__}: {exc}", "scroll")
    return _ok("scroll", backend.name, clicks=int(clicks), direction=normalized_direction)


# ---------------------------------------------------------------------------
# Keyboard actions
# ---------------------------------------------------------------------------


def keyboard_type(text: str, interval_ms: int = 20) -> dict[str, Any]:
    """Guard-checked typing of ``text`` with per-keystroke interval."""
    guard = get_sentinel_guard()
    verdict = guard.check_action("type")
    if not verdict["allowed"]:
        return _blocked(str(verdict["reason"]), "type", halted=True)
    if not text:
        return _invalid("action 'type' requires non-empty text", "type")
    if int(interval_ms) < 0:
        return _invalid("interval_ms must be >= 0", "type")
    backend, reason = _load_backend()
    if backend is None:
        return _unavailable(str(reason))
    probe = _probe_pointer(guard, backend)
    if probe is not None and probe.get("panic"):
        return _blocked(str(probe.get("reason")), "type", halted=True)
    try:
        backend.type_text(text, int(interval_ms))
    except Exception as exc:  # noqa: BLE001 - honest dispatch failure (e.g. non-typable characters)
        return _failed(f"keyboard type dispatch failed: {type(exc).__name__}: {exc}", "type")
    return _ok("type", backend.name, length=len(text), interval_ms=int(interval_ms))


def keyboard_hotkey(keys: str | Sequence[str], *, confirmation_token: str | None = None) -> dict[str, Any]:
    """Guard-checked hotkey; the destructive-combo blacklist runs before any backend is loaded.

    A blacklisted combo needs a single-use, combo-bound **operator** confirmation
    token. There is deliberately no ``confirmed`` boolean here: this function is
    called from model-facing tools, so a boolean a model can set is not a
    confirmation.
    """
    guard = get_sentinel_guard()
    verdict = guard.check_hotkey(keys, confirmation_token=confirmation_token)
    if not verdict["allowed"]:
        return _blocked(str(verdict["reason"]), "hotkey", halted=bool(verdict.get("halted")), blocked_by=str(verdict.get("blocked_by") or "guard"), keys=list(verdict.get("keys") or ()))
    normalized = list(verdict.get("keys") or ())
    if not normalized:
        return _invalid("hotkey requires at least one key", "hotkey")
    backend, reason = _load_backend()
    if backend is None:
        return _unavailable(str(reason))
    probe = _probe_pointer(guard, backend)
    if probe is not None and probe.get("panic"):
        return _blocked(str(probe.get("reason")), "hotkey", halted=True)
    try:
        backend.hotkey(normalized)
    except Exception as exc:  # noqa: BLE001 - honest dispatch failure
        return _failed(f"hotkey dispatch failed: {type(exc).__name__}: {exc}", "hotkey")
    payload = _ok("hotkey", backend.name, keys=normalized)
    if verdict.get("confirmed"):
        payload["confirmed"] = True
        payload["confirmation_note"] = str(verdict.get("reason"))
    return payload


def keyboard_press(key: str) -> dict[str, Any]:
    """Guard-checked single key press (``enter``, ``escape``, ``tab``, ...)."""
    guard = get_sentinel_guard()
    verdict = guard.check_action("press")
    if not verdict["allowed"]:
        return _blocked(str(verdict["reason"]), "press", halted=True)
    cleaned = str(key or "").strip()
    if not cleaned:
        return _invalid("action 'press' requires a non-empty key", "press")
    # ``press`` is a single-key API. Treat combo-looking input as a hotkey so
    # the destructive-combo blacklist cannot be bypassed by changing the
    # call-site verb from ``hotkey`` to ``press``.
    normalized = normalize_hotkey(cleaned)
    if len(normalized) != 1:
        return _invalid("action 'press' accepts one key; use the hotkey action for combinations", "press")
    backend, reason = _load_backend()
    if backend is None:
        return _unavailable(str(reason))
    probe = _probe_pointer(guard, backend)
    if probe is not None and probe.get("panic"):
        return _blocked(str(probe.get("reason")), "press", halted=True)
    try:
        # Keep the caller's original single-key spelling for backend
        # compatibility; normalization above is only the combo safety check.
        backend.press(cleaned)
    except Exception as exc:  # noqa: BLE001 - honest dispatch failure
        return _failed(f"key press dispatch failed: {type(exc).__name__}: {exc}", "press")
    return _ok("press", backend.name, key=cleaned)


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


def _spawn_process(command: list[str]) -> Any:
    """Process-spawn seam (tests replace this). stdlib only, ``shell=False``."""
    return subprocess.Popen(command, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def launch_application(app: str, args: Sequence[str] | None = None) -> dict[str, Any]:
    """Launch a desktop application (halt-gated, launch-scoped, ``shell=False``).

    ``shell=False`` already blocks shell metacharacters, but it does **not** stop
    arbitrary-program execution: any executable name or absolute path used to be
    accepted. The sentinel's launch scope therefore runs first and refuses a
    caller-supplied path unless an operator allowlisted that exact executable.
    """
    guard = get_sentinel_guard()
    verdict = guard.check_launch(app)
    if not verdict["allowed"]:
        return _blocked(str(verdict["reason"]), "launch_application", halted=bool(verdict.get("halted")), blocked_by=str(verdict.get("blocked_by") or "guard"))
    cleaned = str(app or "").strip()
    if not cleaned:
        return _invalid("app executable name or path is required", "launch_application")
    if not args:
        command = [cleaned]
    else:
        command = [cleaned, *[str(arg) for arg in args]]
    if len(command) > 256:
        return _invalid("launch accepts at most 256 argv entries", "launch_application")
    for arg in command[1:]:
        if len(str(arg)) > 4096:
            return _invalid("a launch argument exceeds 4096 characters", "launch_application")
    try:
        process = _spawn_process(command)
    except FileNotFoundError as exc:
        return _failed(f"launch failed: executable not found: {exc}", "launch_application")
    except Exception as exc:  # noqa: BLE001 - honest launch failure
        return _failed(f"launch failed: {type(exc).__name__}: {exc}", "launch_application")
    return _ok("launch_application", "subprocess", launched=True, pid=int(getattr(process, "pid", 0)), command=command)
