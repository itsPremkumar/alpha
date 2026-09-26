"""Module C: OS computer use -- geometry, sentinel guard, honest-unavailable, mocked dispatch.

ABSOLUTE SAFETY RULE: this suite must never move a real mouse, type real keys,
capture the real desktop, or spawn a real process. The autouse fixture forces
every optional OS-dependency seam to "unavailable" (tests that need a backend
inject fake modules/fake backends instead) and replaces process spawning with a
hard AssertionError, so a missing mock fails loudly instead of touching the
host. A real-hardware smoke run is deferred to the lead's restart window.
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import inspect
import sys
import types
from types import SimpleNamespace

import pytest
from langchain_core.tools import BaseTool

from alpha.capabilities.catalog import CAPABILITY_CATALOG
from alpha.computer_use import LaptopController, accessibility, dispatcher, get_laptop_controller, screen
from alpha.computer_use.guard import (
    BLACKLISTED_HOTKEYS,
    SentinelGuard,
    get_sentinel_guard,
    normalize_hotkey,
)
from alpha.tools.builtins import os_computer_tool
from alpha.tools.builtins.computer_system_one_tool import desktop_system_one_action_tool
from alpha.tools.builtins.os_computer_tool import (
    desktop_inspect_ui_tree_tool,
    desktop_keyboard_action_tool,
    desktop_mouse_action_tool,
    desktop_screenshot_tool,
    desktop_window_manage_tool,
)
from alpha.tools.types import Runtime

TOOL_NAMES = {
    "desktop_screenshot",
    "desktop_inspect_ui_tree",
    "desktop_mouse_action",
    "desktop_keyboard_action",
    "desktop_window_manage",
}

# Module-level variable names of the five tool objects (registries import these).
OBJECT_NAMES = {f"{name}_tool" for name in TOOL_NAMES}

# Captured at import time so fixtures can restore the real probes where a test
# needs to observe the actual venv state (headless-safe probes only).
_REAL_ACCESSIBILITY_IMPORT = accessibility._optional_import
_REAL_SCREEN_IMPORT = screen._optional_import


def _runtime(thread_id: str = "thread-7") -> SimpleNamespace:
    """Minimal injected runtime (ToolRuntime-shaped enough for these tools)."""
    return SimpleNamespace(context={"thread_id": thread_id}, state={}, config={})


class _FakeBackend:
    """Mocked OS input layer: records every dispatch, performs nothing."""

    name = "fake-backend"

    def __init__(self, position: tuple[int, int] = (123, 45)) -> None:
        self._position = position
        self.calls: list[tuple] = []

    def pointer_position(self) -> tuple[int, int]:
        return self._position

    def click(self, x, y, button, clicks) -> None:
        self.calls.append(("click", x, y, button, clicks))

    def move(self, x, y, smooth) -> None:
        self.calls.append(("move", x, y, smooth))

    def drag(self, start_x, start_y, end_x, end_y, button, duration) -> None:
        self.calls.append(("drag", start_x, start_y, end_x, end_y, button, duration))

    def scroll(self, clicks, direction) -> None:
        self.calls.append(("scroll", clicks, direction))

    def type_text(self, text, interval_ms) -> None:
        self.calls.append(("type", text, interval_ms))

    def hotkey(self, keys) -> None:
        self.calls.append(("hotkey", tuple(keys)))

    def press(self, key) -> None:
        self.calls.append(("press", key))


@pytest.fixture(autouse=True)
def _no_real_os(monkeypatch):
    """Fail loudly rather than ever touching the host OS from tests."""

    def _deny(name: str):
        return None, f"dependency not installed: {name}"

    def _deny_screen(name: str):
        if name.startswith("PIL"):
            # Pillow draws on synthetic in-memory buffers only -- headless-safe.
            return _REAL_SCREEN_IMPORT(name)
        return _deny(name)

    def _deny_spawn(command):
        raise AssertionError(f"test attempted to spawn a real process: {command!r}")

    monkeypatch.setattr(accessibility, "_optional_import", _deny)
    monkeypatch.setattr(dispatcher, "_optional_import", _deny)
    monkeypatch.setattr(screen, "_optional_import", _deny_screen)
    monkeypatch.setattr(dispatcher, "_spawn_process", _deny_spawn)

    guard = get_sentinel_guard()
    guard.reset()
    yield
    guard.reset()


# ---------------------------------------------------------------------------
# Bounding-box / center-click math
# ---------------------------------------------------------------------------


def test_bbox_center_and_containment_math():
    assert accessibility.center_of([0, 0, 10, 20]) == (5, 10)
    assert accessibility.center_of([0, 0, 11, 21]) == (5, 10)
    assert accessibility.center_of([100, 50, 300, 150]) == (200, 100)

    box = [0, 0, 100, 100]
    assert accessibility.bbox_contains(box, 0, 0) is True
    assert accessibility.bbox_contains(box, 99, 99) is True
    # Half-open: right/bottom edges belong to the next box.
    assert accessibility.bbox_contains(box, 100, 100) is False
    assert accessibility.bbox_contains(box, -1, 0) is False
    assert accessibility.bbox_contains(box, 0, -1) is False


def test_clamp_to_bbox_keeps_points_inside():
    box = [10, 20, 50, 60]
    assert accessibility.clamp_to_bbox(box, 30, 40) == (30, 40)
    assert accessibility.clamp_to_bbox(box, 0, 999) == (10, 59)
    assert accessibility.clamp_to_bbox(box, 1000, -50) == (49, 20)
    # Clamped result is always inside the box.
    for candidate in [(0, 0), (1000, 1000), (-7, 43)]:
        clamped = accessibility.clamp_to_bbox(box, *candidate)
        assert accessibility.bbox_contains(box, *clamped) is True


# ---------------------------------------------------------------------------
# Honest-unavailable contract (never an empty scan presented as success)
# ---------------------------------------------------------------------------


def test_inspect_ui_tree_never_reports_empty_tree_as_successful_scan():
    payload = accessibility.inspect_ui_tree("Notepad")
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["available"] is False
    assert payload["scanned"] is False  # distinguishes "no scan" from "clean scan"
    assert payload["elements"] == []
    assert payload["count"] == 0
    assert "pywinauto" in payload["reason"]
    assert payload["reason"].startswith("dependency not installed:")


def test_list_active_windows_honest_unavailable():
    payload = accessibility.list_active_windows("")
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["windows"] == []
    assert "pywinauto" in payload["reason"]


def test_availability_claims_match_installed_dependencies():
    """Never claim available without the dependency really being importable."""
    monkeypatched_real = _REAL_ACCESSIBILITY_IMPORT("pywinauto")[0] is not None
    spec = importlib.util.find_spec("pywinauto")
    assert monkeypatched_real == (spec is not None)

    # Probe through the real seam regardless of the suite-wide denial fixture.
    module, reason = _REAL_ACCESSIBILITY_IMPORT("pywinauto")
    if spec is None:
        assert module is None
        assert reason is not None and "pywinauto" in reason
    else:
        assert module is not None


def test_dispatcher_availability_names_both_missing_backends():
    probe = dispatcher.availability()
    assert probe["available"] is False
    assert probe["backend"] is None
    assert "pyautogui" in probe["reason"]
    assert "pynput" in probe["reason"]
    assert probe["missing_packages"] == ["pyautogui", "pynput"]


def test_screenshot_capture_honest_unavailable_without_mss():
    payload = screen.capture_screenshot(1)
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["captured"] is False
    assert payload["png_base64"] is None
    assert "mss" in payload["reason"]
    assert payload["reason"].startswith("dependency not installed:")


def test_unavailable_dispatch_never_reports_success():
    payload = dispatcher.mouse_click(10, 10)
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["dispatched"] is False
    assert "pyautogui" in payload["reason"] and "pynput" in payload["reason"]


# ---------------------------------------------------------------------------
# Sentinel guard: panic corner, window lock, hotkey blacklist
# ---------------------------------------------------------------------------


def test_panic_corner_halts_and_revokes_execution_leases():
    guard = SentinelGuard()
    lease = guard.acquire_lease("os-computer:thread-1", owner="desktop_mouse_click")
    assert lease["granted"] is True
    assert guard.active_leases() == ["os-computer:thread-1"]

    observed = guard.observe_pointer(0, 0)
    assert observed["panic"] is True
    assert observed["halted"] is True
    assert "panic corner" in observed["reason"]

    # Execution leases are revoked, not merely hidden.
    assert guard.active_leases() == []
    status = guard.status()
    assert status["halted"] is True
    assert "panic corner" in status["halt_reason"]

    # Every subsequent check denies; leases cannot be re-acquired while halted.
    assert guard.check_action("mouse_click")["allowed"] is False
    assert guard.check_click(5, 5)["allowed"] is False
    assert guard.check_move(5, 5)["allowed"] is False
    assert guard.check_hotkey("ctrl+s")["allowed"] is False
    assert guard.acquire_lease("os-computer:thread-1")["granted"] is False

    # Operator reset re-arms the perimeter (fresh leases only).
    guard.reset()
    assert guard.status()["halted"] is False
    assert guard.check_action("mouse_click")["allowed"] is True
    assert guard.acquire_lease("os-computer:thread-1")["granted"] is True


def test_panic_hooks_fire_once_and_cannot_break_the_halt():
    guard = SentinelGuard()
    seen: list[str] = []
    guard.add_panic_hook(seen.append)
    guard.trigger_panic("first")
    guard.trigger_panic("second")
    assert seen == ["first"]

    exploding = SentinelGuard()
    exploding.add_panic_hook(lambda reason: (_ for _ in ()).throw(RuntimeError("boom")))
    result = exploding.trigger_panic("halt anyway")
    assert result["halted"] is True
    assert exploding.status()["halted"] is True


def test_window_lock_blocks_outside_clicks_and_clamps_moves():
    guard = SentinelGuard()
    assert guard.set_window_bounds([100, 100, 300, 300], label="target")["locked"] is True

    inside = guard.check_click(150, 150)
    assert inside["allowed"] is True

    outside = guard.check_click(50, 50)
    assert outside["allowed"] is False
    assert "outside the locked window bounds" in outside["reason"]
    # Half-open edge: the right/bottom edge belongs to the next box.
    assert guard.check_click(300, 300)["allowed"] is False

    # Moves are clamped inside the lock, never allowed out.
    clamped = guard.check_move(1000, 500)
    assert clamped["allowed"] is True
    assert clamped["clamped"] is True
    assert (clamped["x"], clamped["y"]) == (299, 299)
    inside_move = guard.check_move(150, 150)
    assert inside_move["allowed"] is True
    assert inside_move["clamped"] is False

    guard.clear_window_bounds()
    assert guard.window_bounds()["locked"] is False
    assert guard.check_click(50, 50)["allowed"] is True


def test_window_lock_rejects_invalid_bounding_boxes():
    guard = SentinelGuard()
    for bad in ([1, 2, 3], [10, 10, 10, 20], [50, 0, 10, 10], "nonsense"):
        result = guard.set_window_bounds(bad)
        assert result["locked"] is False
        assert "invalid bbox" in result["reason"]
    assert guard.window_bounds()["locked"] is False


def test_hotkey_blacklist_blocks_destructive_combos():
    guard = SentinelGuard()
    assert set(BLACKLISTED_HOTKEYS) >= {frozenset({"win", "l"}), frozenset({"alt", "f4"}), frozenset({"shift", "delete"}), frozenset({"ctrl", "alt", "delete"})}

    for combo in ("win+l", "alt+f4", "shift+delete", "ctrl+alt+delete", "Win+L", "shift+del", "super+l", "ctrl+shift+delete"):
        verdict = guard.check_hotkey(combo)
        assert verdict["allowed"] is False, combo
        assert verdict["blocked_by"] == "hotkey_blacklist"
        assert "operator confirmation" in verdict["reason"]

    assert guard.check_hotkey(["win", "l"])["allowed"] is False
    assert guard.check_hotkey("ctrl+s")["allowed"] is True
    assert guard.check_hotkey("alt+tab")["allowed"] is True
    assert guard.check_hotkey("")["allowed"] is True  # empty resolves to no keys; tool-level validation rejects it


def test_blacklist_cannot_be_lifted_by_a_bare_boolean_or_a_self_minted_token():
    """A confirmation a caller can supply is not a confirmation.

    ``check_hotkey`` takes no boolean override, and an invented token is
    refused. This is the regression that keeps the destructive-combo blacklist
    a real control rather than a prompt suggestion.
    """

    guard = SentinelGuard()
    assert "confirmed" not in inspect.signature(guard.check_hotkey).parameters

    with pytest.raises(TypeError):
        guard.check_hotkey("win+l", confirmed=True)  # type: ignore[call-arg]

    invented = guard.check_hotkey("win+l", confirmation_token="not-a-real-token")
    assert invented["allowed"] is False
    assert invented["blocked_by"] == "hotkey_blacklist"
    assert "no operator confirmation" in invented["reason"]


def test_operator_confirmation_is_single_use_combo_bound_and_expiring():
    guard = SentinelGuard()

    # Nothing to arm for a combo that is not blacklisted.
    assert guard.arm_destructive_confirmation("ctrl+s")["armed"] is False

    armed = guard.arm_destructive_confirmation("win+l", armed_by="test-operator")
    assert armed["armed"] is True
    assert armed["single_use"] is True
    token = armed["confirmation_token"]
    assert isinstance(token, str) and token

    # A token for win+l does not unlock a different blacklisted combo.
    other = guard.check_hotkey("shift+delete", confirmation_token=token)
    assert other["allowed"] is False
    assert "covers" in other["reason"]

    allowed = guard.check_hotkey("Win+L", confirmation_token=token)
    assert allowed["allowed"] is True
    assert allowed["confirmed"] is True

    # Single use: the same token is dead after one success.
    replay = guard.check_hotkey("win+l", confirmation_token=token)
    assert replay["allowed"] is False
    assert "no operator confirmation" in replay["reason"]


def test_panic_and_reset_revoke_armed_destructive_confirmations():
    guard = SentinelGuard()
    guard.arm_destructive_confirmation("ctrl+alt+delete")
    assert guard.status()["armed_destructive_confirmations"] == 1
    guard.trigger_panic("test panic")
    assert guard.status()["armed_destructive_confirmations"] == 0

    guard.reset()
    guard.arm_destructive_confirmation("alt+f4")
    assert guard.status()["armed_destructive_confirmations"] == 1
    guard.reset()
    assert guard.status()["armed_destructive_confirmations"] == 0
    assert guard.check_hotkey("alt+f4")["allowed"] is False


def test_normalize_hotkey_aliases():
    assert normalize_hotkey("Control+Shift+Del") == ("ctrl", "shift", "delete")
    assert normalize_hotkey(["Super", "L"]) == ("win", "l")
    assert normalize_hotkey("ALT + F4") == ("alt", "f4")


# ---------------------------------------------------------------------------
# Dispatcher against a mocked OS layer (no real pyautogui/pynput imports)
# ---------------------------------------------------------------------------


def test_mouse_click_dispatches_through_mocked_backend(monkeypatch):
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))
    payload = dispatcher.mouse_click(10, 20, "left", 1)
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["dispatched"] is True
    assert payload["backend"] == "fake-backend"
    assert fake.calls == [("click", 10, 20, "left", 1)]


def test_mouse_click_outside_window_lock_never_reaches_backend(monkeypatch):
    guard = get_sentinel_guard()
    guard.set_window_bounds([100, 100, 300, 300], label="target")
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    payload = dispatcher.mouse_click(10, 10)
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "outside the locked window bounds" in payload["reason"]
    assert fake.calls == []


def test_physical_pointer_in_panic_corner_halts_dispatch(monkeypatch):
    guard = get_sentinel_guard()
    assert guard.acquire_lease("os-computer:thread-9")["granted"] is True
    fake = _FakeBackend(position=(0, 0))  # physical pointer parked in the corner
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    payload = dispatcher.mouse_click(10, 10)
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["halted"] is True
    assert "panic corner" in payload["reason"]
    assert fake.calls == []
    assert guard.status()["halted"] is True
    assert guard.active_leases() == []


def test_click_target_in_panic_corner_engages_kill_switch(monkeypatch):
    guard = get_sentinel_guard()
    assert guard.acquire_lease("os-computer:thread-9")["granted"] is True
    fake = _FakeBackend(position=(500, 500))
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    payload = dispatcher.mouse_click(0, 0)
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["halted"] is True
    assert "panic corner" in payload["reason"]
    assert fake.calls == []
    assert guard.active_leases() == []


def test_keyboard_hotkey_blacklist_enforced_before_backend(monkeypatch):
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    blocked = dispatcher.keyboard_hotkey("shift+delete")
    assert blocked["ok"] is False
    assert blocked["status"] == "blocked"
    assert blocked["blocked_by"] == "hotkey_blacklist"
    assert fake.calls == []

    allowed = dispatcher.keyboard_hotkey("ctrl+s")
    assert allowed["ok"] is True
    assert fake.calls == [("hotkey", ("ctrl", "s"))]


def test_operator_confirmed_blacklisted_hotkey_dispatches_on_fake_backend(monkeypatch):
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))
    guard = get_sentinel_guard()
    token = guard.arm_destructive_confirmation("win+l", armed_by="test-operator")["confirmation_token"]
    payload = dispatcher.keyboard_hotkey("win+l", confirmation_token=token)
    assert payload["ok"] is True
    assert payload["confirmed"] is True
    assert fake.calls == [("hotkey", ("win", "l"))]


def test_operator_confirmed_blacklisted_hotkey_is_still_honest_without_backend():
    """Confirmation must never fabricate a successful dispatch."""
    guard = get_sentinel_guard()
    token = guard.arm_destructive_confirmation("win+l", armed_by="test-operator")["confirmation_token"]
    payload = dispatcher.keyboard_hotkey("win+l", confirmation_token=token)
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["dispatched"] is False
    assert "pyautogui" in payload["reason"]


def test_dispatcher_hotkey_takes_no_self_supplied_confirmation_boolean():
    assert "confirmed" not in inspect.signature(dispatcher.keyboard_hotkey).parameters
    with pytest.raises(TypeError):
        dispatcher.keyboard_hotkey("win+l", confirmed=True)  # type: ignore[call-arg]


def test_keyboard_type_press_and_validation_through_fake_backend(monkeypatch):
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    typed = dispatcher.keyboard_type("hello", 5)
    assert typed["ok"] is True
    pressed = dispatcher.keyboard_press("enter")
    assert pressed["ok"] is True
    assert fake.calls == [("type", "hello", 5), ("press", "enter")]

    assert dispatcher.keyboard_type("")["status"] == "invalid"
    assert dispatcher.keyboard_press("")["status"] == "invalid"
    assert dispatcher.mouse_click(1, 1, button="side", clicks=1)["status"] == "invalid"
    assert dispatcher.mouse_scroll(0)["status"] == "invalid"
    assert dispatcher.mouse_scroll(1, direction="sideways")["status"] == "invalid"


def test_move_drag_scroll_through_fake_backend(monkeypatch):
    fake = _FakeBackend(position=(150, 150))
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    assert dispatcher.mouse_move(40, 50, smooth=False)["ok"] is True
    assert dispatcher.mouse_drag(1, 2, 3, 4, "left", 0.0)["ok"] is True
    assert dispatcher.mouse_scroll(3, "down")["ok"] is True
    assert ("move", 40, 50, False) in fake.calls
    assert ("drag", 1, 2, 3, 4, "left", 0.0) in fake.calls
    assert ("scroll", 3, "down") in fake.calls


def test_scroll_refused_when_pointer_escapes_window_lock(monkeypatch):
    guard = get_sentinel_guard()
    guard.set_window_bounds([100, 100, 300, 300], label="target")
    fake = _FakeBackend(position=(500, 500))  # pointer outside the locked window
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    payload = dispatcher.mouse_scroll(3, "down")
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "scroll refused" in payload["reason"]
    assert ("scroll", 3, "down") not in fake.calls


def test_launch_application_success_failure_and_halt(monkeypatch):
    spawned: list[list[str]] = []

    def _fake_spawn(command):
        spawned.append(list(command))
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(dispatcher, "_spawn_process", _fake_spawn)
    payload = dispatcher.launch_application("notepad")
    assert payload["ok"] is True
    assert payload["launched"] is True
    assert payload["pid"] == 4242
    assert payload["command"] == ["notepad"]
    assert spawned == [["notepad"]]

    def _missing(command):
        raise FileNotFoundError(f"no such file: {command[0]}")

    monkeypatch.setattr(dispatcher, "_spawn_process", _missing)
    failed = dispatcher.launch_application("definitely-not-installed-xyz")
    assert failed["ok"] is False
    assert failed["status"] == "failed"
    assert failed.get("launched") is None or failed["ok"] is False
    assert "executable not found" in failed["reason"]

    get_sentinel_guard().trigger_panic("test halt")
    blocked = dispatcher.launch_application("notepad")
    assert blocked["ok"] is False
    assert blocked["status"] == "blocked"
    assert blocked["halted"] is True
    assert spawned == [["notepad"]]


# ---------------------------------------------------------------------------
# PyAutoGUI / pynput adapter mapping against fake modules (sys.modules-injected)
# ---------------------------------------------------------------------------


def test_pyautogui_adapter_mapping_with_fake_module(monkeypatch):
    module = types.ModuleType("pyautogui")
    calls: list[tuple] = []
    module.position = lambda: (7, 8)
    module.click = lambda **kwargs: calls.append(("click", kwargs))
    module.moveTo = lambda x, y, duration=0: calls.append(("moveTo", x, y, duration))
    module.dragTo = lambda x, y, duration=0.0, button="left": calls.append(("dragTo", x, y, duration, button))
    module.scroll = lambda amount: calls.append(("scroll", amount))
    module.write = lambda text, interval=0.0: calls.append(("write", text, interval))
    module.hotkey = lambda *keys: calls.append(("hotkey", keys))
    module.press = lambda key: calls.append(("press", key))
    monkeypatch.setitem(sys.modules, "pyautogui", module)
    monkeypatch.setattr(
        dispatcher,
        "_optional_import",
        lambda name: (module, None) if name == "pyautogui" else (None, f"dependency not installed: {name}"),
    )

    backend, reason = dispatcher._load_backend()
    assert reason is None
    assert backend is not None and backend.name == "pyautogui"

    assert dispatcher.mouse_click(3, 4, "right", 2)["ok"] is True
    assert dispatcher.mouse_move(10, 10, smooth=True)["ok"] is True
    assert dispatcher.mouse_drag(1, 2, 3, 4, "left", 0.0)["ok"] is True
    assert dispatcher.mouse_scroll(2, "up")["ok"] is True
    assert dispatcher.keyboard_type("Hi", 5)["ok"] is True
    assert dispatcher.keyboard_hotkey("ctrl+s")["ok"] is True
    assert dispatcher.keyboard_press("enter")["ok"] is True

    assert ("click", {"x": 3, "y": 4, "button": "right", "clicks": 2}) in calls
    assert ("moveTo", 10, 10, 0.15) in calls
    assert ("dragTo", 3, 4, 0.0, "left") in calls
    assert ("scroll", 2) in calls
    assert ("write", "Hi", 0.005) in calls
    assert ("hotkey", ("ctrl", "s")) in calls
    assert ("press", "enter") in calls


def test_pynput_adapter_mapping_with_fake_modules(monkeypatch):
    class _FakeMouse:
        def __init__(self):
            self._position = (1, 1)  # non-zero: (0,0) would trip the panic-corner probe
            self.calls: list[tuple] = []

        def _get_position(self):
            return self._position

        def _set_position(self, value):
            self._position = value
            self.calls.append(("position", tuple(value)))

        # One property, two distinctly-named accessors: the AST dup gate
        # flags a getter/setter pair that redefines the same function name.
        position = property(_get_position, _set_position)

        def click(self, button, count):
            self.calls.append(("click", button, count))

        def press(self, button, count):
            self.calls.append(("press_button", button, count))

        def release(self, button, count):
            self.calls.append(("release_button", button, count))

        def scroll(self, dx, dy):
            self.calls.append(("scroll", dx, dy))

    class _FakeKeyboard:
        def __init__(self):
            self.calls: list[tuple] = []

        def press(self, key):
            self.calls.append(("press", key))

        def release(self, key):
            self.calls.append(("release", key))

        def type(self, text):
            self.calls.append(("type", text))

    mouse = _FakeMouse()
    keyboard = _FakeKeyboard()
    mouse_mod = types.ModuleType("pynput.mouse")
    mouse_mod.Controller = lambda: mouse
    mouse_mod.Button = types.SimpleNamespace(left="BTN_LEFT", right="BTN_RIGHT", middle="BTN_MIDDLE")
    keyboard_mod = types.ModuleType("pynput.keyboard")
    keyboard_mod.Controller = lambda: keyboard
    keyboard_mod.Key = types.SimpleNamespace(ctrl="KEY_CTRL", alt="KEY_ALT", shift="KEY_SHIFT", delete="KEY_DELETE", cmd="KEY_CMD", enter="KEY_ENTER", f4="KEY_F4")
    package = types.ModuleType("pynput")
    package.mouse = mouse_mod
    package.keyboard = keyboard_mod
    for name, injected in (("pynput", package), ("pynput.mouse", mouse_mod), ("pynput.keyboard", keyboard_mod)):
        monkeypatch.setitem(sys.modules, name, injected)
    monkeypatch.setattr(
        dispatcher,
        "_optional_import",
        lambda name: (package, None) if name == "pynput" else (None, f"dependency not installed: {name}"),
    )

    backend, reason = dispatcher._load_backend()
    assert reason is None
    assert backend is not None and backend.name == "pynput"

    assert dispatcher.mouse_click(5, 6, "left", 1)["ok"] is True
    assert ("position", (5, 6)) in mouse.calls
    assert ("click", "BTN_LEFT", 1) in mouse.calls

    assert dispatcher.keyboard_hotkey("ctrl+s")["ok"] is True
    assert keyboard.calls[:2] == [("press", "KEY_CTRL"), ("press", "s")]
    assert keyboard.calls[-2:] == [("release", "s"), ("release", "KEY_CTRL")]

    assert dispatcher.keyboard_press("enter")["ok"] is True
    assert ("press", "KEY_ENTER") in keyboard.calls
    assert dispatcher.mouse_scroll(2, "up")["ok"] is True
    assert ("scroll", 0, 2) in mouse.calls


# ---------------------------------------------------------------------------
# screen.py: fake mss capture + Set-of-Marks annotator hook
# ---------------------------------------------------------------------------


def _install_fake_mss(monkeypatch):
    class _FakeShot:
        width = 4
        height = 2
        rgb = bytes(24)

    class _FakeSct:
        monitors = [{"left": 0, "top": 0, "width": 4, "height": 2}, {"left": 0, "top": 0, "width": 4, "height": 2}]

        def __init__(self):
            self.grabbed = None

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def grab(self, area):
            self.grabbed = dict(area) if isinstance(area, dict) else area
            return _FakeShot()

    holder = {"sct": None}

    def _factory():
        holder["sct"] = _FakeSct()
        return holder["sct"]

    package = types.ModuleType("mss")
    package.mss = _factory
    tools_mod = types.ModuleType("mss.tools")
    tools_mod.to_png = lambda data, size: b"\x89PNG-fake" + bytes(data[:4])
    package.tools = tools_mod
    monkeypatch.setitem(sys.modules, "mss", package)
    monkeypatch.setitem(sys.modules, "mss.tools", tools_mod)
    monkeypatch.setattr(
        screen,
        "_optional_import",
        lambda name: (sys.modules.get(name), None) if name in ("mss", "mss.tools") else (None, f"dependency not installed: {name}"),
    )
    return holder


def test_capture_with_fake_mss_module(monkeypatch):
    holder = _install_fake_mss(monkeypatch)
    payload = screen.capture_screenshot(1)
    assert payload["ok"] is True
    assert payload["captured"] is True
    assert payload["width"] == 4 and payload["height"] == 2
    assert payload["elapsed_ms"] >= 0
    assert base64.b64decode(payload["png_base64"]).startswith(b"\x89PNG-fake")
    assert holder["sct"].grabbed == {"left": 0, "top": 0, "width": 4, "height": 2}

    cropped = screen.capture_screenshot(1, region=[1, 1, 4, 3])
    assert cropped["ok"] is True
    assert cropped["region"] == [1, 1, 4, 3]
    assert holder["sct"].grabbed == {"left": 1, "top": 1, "width": 3, "height": 2}


def test_capture_monitor_out_of_range_is_invalid(monkeypatch):
    _install_fake_mss(monkeypatch)
    payload = screen.capture_screenshot(9)
    assert payload["ok"] is False
    assert payload["status"] == "invalid"
    assert payload["captured"] is False
    assert "out of range" in payload["reason"]


def _synthetic_png_b64() -> str:
    pytest.importorskip("PIL", reason="Pillow optional annotator dependency is not installed")
    import io as _io

    from PIL import Image

    buffer = _io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_set_of_marks_annotation_draws_marks_with_pillow():
    elements = [
        {"name": "Run", "bbox": [2, 2, 10, 10]},
        {"name": "Save", "bbox": [10, 10, 18, 18]},
        {"name": "no bbox"},
        {"name": "degenerate", "bbox": [5, 5, 5, 9]},
    ]
    payload = screen.annotate_set_of_marks(_synthetic_png_b64(), elements)
    assert payload["ok"] is True
    assert payload["annotated"] is True
    assert payload["count"] == 2
    assert [mark["mark"] for mark in payload["marks"]] == [1, 2]
    assert payload["marks"][0]["name"] == "Run"
    assert payload["marks"][0]["bbox"] == [2, 2, 10, 10]
    assert payload["marks"][0]["center"] == (6, 6)
    assert base64.b64decode(payload["png_base64"]).startswith(b"\x89PNG")


def test_set_of_marks_honest_when_pillow_missing(monkeypatch):
    def _deny_pil(name: str):
        if name.startswith("PIL"):
            return None, f"dependency not installed: {name}"
        return _REAL_SCREEN_IMPORT(name)

    monkeypatch.setattr(screen, "_optional_import", _deny_pil)
    elements = [{"name": "Run", "bbox": [2, 2, 10, 10]}, {"name": "Save", "bbox": [10, 10, 18, 18]}]
    payload = screen.annotate_set_of_marks(_synthetic_png_b64(), elements)
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["annotated"] is False
    assert payload["missing_package"] == "Pillow"
    assert payload["png_base64"] is None
    # Pure-data marks are still honest output, not a fake annotated image.
    assert len(payload["marks"]) == 2


def test_set_of_marks_rejects_invalid_input():
    empty = screen.annotate_set_of_marks("", [])
    assert empty["ok"] is False
    assert empty["annotated"] is False
    assert "no elements" in empty["reason"]

    bad_b64 = screen.annotate_set_of_marks("!!!not-base64!!!", [{"name": "x", "bbox": [0, 0, 5, 5]}])
    assert bad_b64["ok"] is False
    assert bad_b64["annotated"] is False
    assert "base64" in bad_b64["reason"]


# ---------------------------------------------------------------------------
# Tool contracts: bare `runtime: Runtime`, guard pass-through, leases
# ---------------------------------------------------------------------------

_FIVE_TOOLS = (
    desktop_screenshot_tool,
    desktop_inspect_ui_tree_tool,
    desktop_mouse_action_tool,
    desktop_keyboard_action_tool,
    desktop_window_manage_tool,
)


def test_module_exports_exactly_the_five_desktop_tools():
    exported = {name for name, value in vars(os_computer_tool).items() if isinstance(value, BaseTool)}
    assert exported == OBJECT_NAMES


def test_tool_signatures_carry_bare_runtime_first_param():
    for tool_obj in _FIVE_TOOLS:
        assert tool_obj.name in TOOL_NAMES
        coroutine = tool_obj.coroutine
        assert coroutine is not None, tool_obj.name
        parameters = list(inspect.signature(coroutine).parameters.values())
        first = parameters[0]
        assert first.name == "runtime", tool_obj.name
        assert first.default is inspect.Parameter.empty, f"{tool_obj.name}: runtime must be required"
        assert first.annotation == Runtime, f"{tool_obj.name}: runtime must be the bare Runtime alias (got {first.annotation!r})"
        # Model-facing schema must generate and must not expose runtime internals.
        schema = tool_obj.tool_call_schema
        assert "runtime" not in schema.model_fields, tool_obj.name
        schema.model_json_schema()


@pytest.mark.asyncio
async def test_desktop_inspect_ui_tree_tool_honest_unavailable():
    payload = await desktop_inspect_ui_tree_tool.coroutine(runtime=_runtime(), window_title="Notepad")
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["scanned"] is False
    assert payload["elements"] == []
    assert "pywinauto" in payload["reason"]
    assert payload["thread_id"] == "thread-7"


@pytest.mark.asyncio
async def test_desktop_screenshot_tool_honest_paths():
    full = await desktop_screenshot_tool.coroutine(runtime=_runtime())
    assert full["ok"] is False
    assert full["status"] == "unavailable"
    assert full["captured"] is False
    assert "mss" in full["reason"]

    windowed = await desktop_screenshot_tool.coroutine(runtime=_runtime(), window_title="Notepad")
    assert windowed["ok"] is False
    assert windowed["status"] == "unavailable"
    assert windowed["found"] is False
    assert "pywinauto" in windowed["reason"]


@pytest.mark.asyncio
async def test_desktop_screenshot_tool_captures_through_fake_mss(monkeypatch):
    _install_fake_mss(monkeypatch)
    payload = await desktop_screenshot_tool.coroutine(runtime=_runtime("thread-3"))
    assert payload["ok"] is True
    assert payload["captured"] is True
    assert payload["thread_id"] == "thread-3"
    assert base64.b64decode(payload["png_base64"]).startswith(b"\x89PNG-fake")


@pytest.mark.asyncio
async def test_desktop_mouse_action_blocked_outside_window_lock(monkeypatch):
    guard = get_sentinel_guard()
    guard.set_window_bounds([100, 100, 300, 300], label="target")
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    payload = await desktop_mouse_action_tool.coroutine(runtime=_runtime(), action="click", x=10, y=10)
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "outside the locked window bounds" in payload["reason"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_desktop_mouse_action_panic_corner_halts_via_tool(monkeypatch):
    guard = get_sentinel_guard()
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))

    payload = await desktop_mouse_action_tool.coroutine(runtime=_runtime(), action="click", x=0, y=0)
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["halted"] is True
    assert "panic corner" in payload["reason"]
    assert fake.calls == []
    assert guard.status()["halted"] is True
    assert guard.active_leases() == []

    # Everything is denied while halted -- even read-only perception.
    follow_up = await desktop_keyboard_action_tool.coroutine(runtime=_runtime(), action="press", key="enter")
    assert follow_up["ok"] is False
    assert follow_up["status"] == "blocked"
    assert "automation halted" in follow_up["reason"]
    inspect_blocked = await desktop_inspect_ui_tree_tool.coroutine(runtime=_runtime())
    assert inspect_blocked["status"] == "blocked"


@pytest.mark.asyncio
async def test_desktop_mouse_action_success_holds_thread_lease(monkeypatch):
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))
    guard = get_sentinel_guard()

    payload = await desktop_mouse_action_tool.coroutine(runtime=_runtime("thread-7"), action="click", x=50, y=60)
    assert payload["ok"] is True
    assert payload["dispatched"] is True
    assert payload["thread_id"] == "thread-7"
    assert fake.calls == [("click", 50, 60, "left", 1)]
    assert guard.active_leases() == ["os-computer:thread-7"]


@pytest.mark.asyncio
async def test_desktop_keyboard_action_blacklist_is_unreachable_from_the_tool(monkeypatch):
    fake = _FakeBackend()
    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (fake, None))
    guard = get_sentinel_guard()

    blocked = await desktop_keyboard_action_tool.coroutine(runtime=_runtime(), action="hotkey", hotkey="win+l")
    assert blocked["ok"] is False
    assert blocked["status"] == "blocked"
    assert blocked["blocked_by"] == "hotkey_blacklist"
    assert fake.calls == []

    blocked2 = await desktop_keyboard_action_tool.coroutine(runtime=_runtime(), action="hotkey", hotkey="shift+delete")
    assert blocked2["ok"] is False
    assert blocked2["status"] == "blocked"

    # Even with an operator token armed, the tool exposes no way to present it,
    # and there is no boolean to set instead.
    guard.arm_destructive_confirmation("win+l", armed_by="test-operator")
    still_blocked = await desktop_keyboard_action_tool.coroutine(runtime=_runtime(), action="hotkey", hotkey="win+l")
    assert still_blocked["ok"] is False
    assert still_blocked["blocked_by"] == "hotkey_blacklist"
    assert fake.calls == []

    with pytest.raises(TypeError):
        await desktop_keyboard_action_tool.coroutine(runtime=_runtime(), action="hotkey", hotkey="win+l", confirmed=True)  # type: ignore[call-arg]
    assert fake.calls == []
    assert guard.status()["halted"] is False


def test_no_model_facing_desktop_tool_advertises_a_confirmation_argument():
    """The schema is what the LLM sees; a confirmation must not be in it."""

    for tool in (
        desktop_screenshot_tool,
        desktop_inspect_ui_tree_tool,
        desktop_mouse_action_tool,
        desktop_keyboard_action_tool,
        desktop_window_manage_tool,
        desktop_system_one_action_tool,
    ):
        properties = set(tool.tool_call_schema.model_json_schema().get("properties", {}))
        assert "confirmed" not in properties, tool.name
        assert "confirmation_token" not in properties, tool.name
        assert "approval_token" not in properties, tool.name
        assert "approval_id" not in properties, tool.name


@pytest.mark.asyncio
async def test_desktop_keyboard_action_invalid_inputs_never_leak_leases():
    guard = get_sentinel_guard()
    invalid = await desktop_keyboard_action_tool.coroutine(runtime=_runtime(), action="frobnicate")
    assert invalid["ok"] is False
    assert invalid["status"] == "invalid"
    assert guard.active_leases() == []

    empty_type = await desktop_keyboard_action_tool.coroutine(runtime=_runtime(), action="type", text="")
    assert empty_type["status"] == "invalid"
    assert guard.active_leases() == []

    empty_hotkey = await desktop_keyboard_action_tool.coroutine(runtime=_runtime(), action="hotkey", hotkey="")
    assert empty_hotkey["status"] == "invalid"
    assert guard.active_leases() == []


@pytest.mark.asyncio
async def test_desktop_window_manage_bind_unbind_and_honest_list():
    guard = get_sentinel_guard()
    bound = await desktop_window_manage_tool.coroutine(runtime=_runtime(), action="bind", bbox=[0, 0, 200, 100], window_title="Target")
    assert bound["ok"] is True
    assert bound["locked"] is True
    assert guard.window_bounds()["locked"] is True

    outside = await desktop_mouse_action_tool.coroutine(runtime=_runtime(), action="click", x=250, y=50)
    assert outside["status"] == "blocked"

    unbound = await desktop_window_manage_tool.coroutine(runtime=_runtime(), action="unbind")
    assert unbound["ok"] is True
    assert unbound["locked"] is False
    assert guard.window_bounds()["locked"] is False

    listed = await desktop_window_manage_tool.coroutine(runtime=_runtime(), action="list")
    assert listed["ok"] is False
    assert listed["status"] == "unavailable"
    assert listed["windows"] == []
    assert "pywinauto" in listed["reason"]

    invalid = await desktop_window_manage_tool.coroutine(runtime=_runtime(), action="bind")
    assert invalid["ok"] is False
    assert invalid["status"] == "invalid"
    assert "bbox" in invalid["reason"]


@pytest.mark.asyncio
async def test_desktop_window_manage_launch_uses_injected_spawn_only(monkeypatch):
    spawned: list[list[str]] = []

    def _fake_spawn(command):
        spawned.append(list(command))
        return SimpleNamespace(pid=777)

    monkeypatch.setattr(dispatcher, "_spawn_process", _fake_spawn)
    payload = await desktop_window_manage_tool.coroutine(runtime=_runtime("thread-5"), action="launch", app="notepad")
    assert payload["ok"] is True
    assert payload["launched"] is True
    assert payload["pid"] == 777
    assert payload["command"] == ["notepad"]
    assert spawned == [["notepad"]]
    assert get_sentinel_guard().active_leases() == ["os-computer:thread-5"]


# ---------------------------------------------------------------------------
# Registration: builtins exports, BUILTIN_TOOLS, capability catalog
# ---------------------------------------------------------------------------


def test_desktop_tools_are_registered_and_catalogued():
    from alpha.tools.builtins import __all__ as builtins_all
    from alpha.tools.tools import BUILTIN_TOOLS

    assert OBJECT_NAMES <= set(builtins_all)
    registered = {tool.name for tool in BUILTIN_TOOLS}
    assert TOOL_NAMES <= registered

    spec = CAPABILITY_CATALOG["os_computer_use"]
    assert spec.module == "alpha.computer_use"
    assert spec.target == "LaptopController"
    module = importlib.import_module(spec.module)
    assert getattr(module, spec.target) is LaptopController


def test_laptop_controller_availability_facade():
    controller = get_laptop_controller()
    assert controller is get_laptop_controller()
    assert isinstance(controller, LaptopController)

    report = controller.availability()
    assert set(report["systems"]) == {"accessibility", "dispatcher", "screenshot", "set_of_marks"}
    assert report["systems"]["accessibility"]["available"] is False
    assert "pywinauto" in report["systems"]["accessibility"]["reason"]
    assert report["systems"]["dispatcher"]["available"] is False
    assert report["systems"]["screenshot"]["available"] is False
    assert report["available"] is False
    assert report["reason"]  # reasons aggregated, never a silent False
    assert controller.guard is get_sentinel_guard()
