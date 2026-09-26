"""Free local-first OS computer use & laptop automation (Module C).

Zero-cost grounding through OS accessibility trees instead of per-screenshot
vision tokens, dispatched through a deterministic sentinel guard. Subsystems:

- :mod:`alpha.computer_use.accessibility` -- zero-token UI-tree inspection
  (optional ``pywinauto``; honest-unavailable when missing).
- :mod:`alpha.computer_use.dispatcher` -- guarded mouse/keyboard/process
  dispatch (optional ``pyautogui``/``pynput``; honest-unavailable when missing).
- :mod:`alpha.computer_use.screen` -- fast ``mss`` screenshots plus a
  Set-of-Marks annotator hook for a local VLM (optional ``mss``/Pillow).
- :mod:`alpha.computer_use.guard` -- sentinel safety: panic-corner kill-switch,
  window-boundary lock, hotkey blacklist (deterministic Python enforcement).

Model-facing surface: the ``desktop_*`` tools in
:mod:`alpha.tools.builtins.os_computer_tool`. ``LaptopController`` is the
capability-catalog facade (``os_computer_use``) -- construction performs no OS
calls; probes run per :meth:`LaptopController.availability` call.
"""

import threading
from typing import Any

from alpha.computer_use import accessibility, dispatcher, guard, screen
from alpha.computer_use.accessibility import bbox_contains, center_of, clamp_to_bbox
from alpha.computer_use.guard import SentinelGuard, get_sentinel_guard

__all__ = [
    "LaptopController",
    "SentinelGuard",
    "accessibility",
    "bbox_contains",
    "center_of",
    "clamp_to_bbox",
    "dispatcher",
    "get_laptop_controller",
    "get_sentinel_guard",
    "guard",
    "screen",
]


class LaptopController:
    """Capability facade aggregating subsystem availability and the sentinel guard.

    Loading the ``os_computer_use`` capability returns this class; instantiating
    it is side-effect-free (no window enumerated, no pointer moved, no screen
    captured).
    """

    @property
    def guard(self) -> SentinelGuard:
        """The process-wide sentinel guard enforcing all safety boundaries."""
        return get_sentinel_guard()

    def availability(self) -> dict[str, Any]:
        """Probe every optional dependency without touching the OS."""
        probes: dict[str, dict[str, Any]] = {
            "accessibility": accessibility.availability(),
            "dispatcher": dispatcher.availability(),
            "screenshot": screen.availability(),
            "set_of_marks": screen.annotator_availability(),
        }
        reasons = [f"{name}: {probe['reason']}" for name, probe in probes.items() if probe.get("reason")]
        return {
            "available": all(bool(probe.get("available")) for probe in probes.values()),
            "systems": probes,
            "reason": "; ".join(reasons) if reasons else None,
            "guard": self.guard.status(),
        }


_CONTROLLER: LaptopController | None = None
_CONTROLLER_LOCK = threading.Lock()


def get_laptop_controller() -> LaptopController:
    """Process-wide :class:`LaptopController` singleton."""
    global _CONTROLLER
    if _CONTROLLER is None:
        with _CONTROLLER_LOCK:
            if _CONTROLLER is None:
                _CONTROLLER = LaptopController()
    return _CONTROLLER
