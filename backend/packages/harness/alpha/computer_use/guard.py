"""Meta Muse-style sentinel safety guard for OS computer use (Module C).

Deterministic Python enforcement of the safety boundaries (mission invariant 5) --
never advisory, never "best effort":

* **Panic-corner kill-switch** -- :meth:`SentinelGuard.observe_pointer` sees the
  pointer at ``(0, 0)`` (either the physical pointer probed through the input
  backend, or a requested mouse target) and immediately halts all automation and
  revokes every execution lease. While halted, *every* guard check denies.
* **Window-boundary lock** -- with bounds set, clicks (or scroll pointer
  positions) outside the target window are blocked -- never silently re-targeted
  to a different control -- and moves are clamped inside the bounds.
* **Hotkey blacklist** -- destructive combos (``Win+L``, desktop ``Alt+F4``,
  ``Shift+Delete``, ``Ctrl+Alt+Delete``) are blocked unless the caller passes an
  explicit ``confirmed=True``.

All checks are pure in-memory Python under a lock: no OS calls, no file I/O,
no network. Optional input backends live in :mod:`alpha.computer_use.dispatcher`;
this module never imports them.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any

from alpha.computer_use.accessibility import bbox_contains, clamp_to_bbox

logger = logging.getLogger(__name__)

# Destructive hotkey combos blocked unless explicitly confirmed. Matching is by
# subset: every key in the combo must be present in the pressed set, so
# ``ctrl+shift+delete`` is caught by the ``shift+delete`` entry too.
BLACKLISTED_HOTKEYS: dict[frozenset[str], str] = {
    frozenset({"win", "l"}): "locks the workstation (Win+L)",
    frozenset({"ctrl", "alt", "delete"}): "secure-attention sequence (Ctrl+Alt+Delete)",
    frozenset({"alt", "f4"}): "closes the foreground window (desktop Alt+F4 is destructive to unsaved work)",
    frozenset({"shift", "delete"}): "permanently deletes without the recycle bin (Shift+Delete)",
}

# Token aliases so the blacklist cannot be dodged with alternate spellings.
_HOTKEY_ALIASES: dict[str, str] = {
    "control": "ctrl",
    "ctl": "ctrl",
    "ctrl": "ctrl",
    "super": "win",
    "meta": "win",
    "cmd": "win",
    "command": "win",
    "windows": "win",
    "win": "win",
    "winleft": "win",
    "winright": "win",
    "alt": "alt",
    "option": "alt",
    "shift": "shift",
    "del": "delete",
    "delete": "delete",
}


def normalize_hotkey(keys: str | Sequence[str]) -> tuple[str, ...]:
    """Normalize a hotkey spec (``"Win+L"`` or ``["super", "l"]``) to canonical tokens."""
    if isinstance(keys, str):
        parts = keys.split("+")
    else:
        parts = [str(part) for part in keys]
    normalized: list[str] = []
    for raw in parts:
        token = raw.strip().lower()
        if not token:
            continue
        normalized.append(_HOTKEY_ALIASES.get(token, token))
    return tuple(normalized)


def _verdict(allowed: bool, action: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"allowed": allowed, "action": action, "reason": reason}
    payload.update(extra)
    return payload


class SentinelGuard:
    """Process-wide sentinel: halt gate, panic corner, window lock, hotkey blacklist."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._halted = False
        self._halt_reason: str | None = None
        self._leases: dict[str, str] = {}
        self._window_bounds: tuple[int, int, int, int] | None = None
        self._window_label = ""
        self._panic_hooks: list[Callable[[str], None]] = []

    # ------------------------------------------------------------------
    # Status & lifecycle
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Snapshot of guard state (halt, leases, window lock)."""
        with self._lock:
            return {
                "halted": self._halted,
                "halt_reason": self._halt_reason,
                "leases": sorted(self._leases),
                "window_bounds": list(self._window_bounds) if self._window_bounds is not None else None,
                "window_label": self._window_label,
            }

    def reset(self) -> dict[str, Any]:
        """Operator re-arm: clear the halt, drop every lease, release the window lock.

        Reset never resurrects revoked leases -- it re-opens the perimeter so a
        fresh, deliberate session can start.
        """
        with self._lock:
            self._halted = False
            self._halt_reason = None
            self._leases.clear()
            self._window_bounds = None
            self._window_label = ""
        return self.status()

    # ------------------------------------------------------------------
    # Execution leases
    # ------------------------------------------------------------------
    def acquire_lease(self, lease_id: str, *, owner: str = "") -> dict[str, Any]:
        """Acquire/refresh an execution lease. Refused while automation is halted."""
        with self._lock:
            if self._halted:
                return {"granted": False, "lease": lease_id, "owner": owner, "reason": f"automation halted: {self._halt_reason}"}
            self._leases[lease_id] = owner
            return {"granted": True, "lease": lease_id, "owner": owner}

    def revoke_lease(self, lease_id: str) -> bool:
        """Drop one lease. Returns True when the lease existed."""
        with self._lock:
            return self._leases.pop(lease_id, None) is not None

    def active_leases(self) -> list[str]:
        """Sorted ids of every live execution lease."""
        with self._lock:
            return sorted(self._leases)

    def revoke_all_leases(self, reason: str) -> list[str]:
        """Drop every lease (deterministic bulk revocation). Returns the revoked ids."""
        with self._lock:
            revoked = sorted(self._leases)
            self._leases.clear()
        if revoked:
            logger.info("sentinel revoked %d execution lease(s): %s", len(revoked), reason)
        return revoked

    # ------------------------------------------------------------------
    # Panic corner
    # ------------------------------------------------------------------
    def add_panic_hook(self, hook: Callable[[str], None]) -> None:
        """Register an external revoker invoked once, on the first panic trigger."""
        with self._lock:
            if hook not in self._panic_hooks:
                self._panic_hooks.append(hook)

    def trigger_panic(self, reason: str) -> dict[str, Any]:
        """Halt automation immediately and revoke every execution lease."""
        with self._lock:
            first_trigger = not self._halted
            self._halted = True
            self._halt_reason = reason
            revoked = sorted(self._leases)
            self._leases.clear()
            hooks = list(self._panic_hooks) if first_trigger else []
        if revoked:
            logger.warning("panic halt revoked %d execution lease(s): %s", len(revoked), reason)
        for hook in hooks:
            try:
                hook(reason)
            except Exception:  # noqa: BLE001 - external revokers must never break the halt path
                logger.exception("panic hook failed (halt still engaged): %s", reason)
        return {"halted": True, "panic": True, "reason": reason, "revoked_leases": revoked, "first_trigger": first_trigger}

    def observe_pointer(self, x: int, y: int) -> dict[str, Any]:
        """Panic-corner probe: a pointer at ``(0, 0)`` halts automation at once.

        Called before every dispatched input action with the probed physical
        pointer position, and by the click/move checks with the requested
        target coordinates (moving/clicking into the corner is treated as
        engaging the kill-switch).
        """
        with self._lock:
            if self._halted:
                return {"halted": True, "panic": False, "reason": self._halt_reason}
        if int(x) == 0 and int(y) == 0:
            result = self.trigger_panic("panic corner: pointer at (0, 0)")
            return {"halted": True, "panic": True, "reason": result["reason"], "revoked_leases": result["revoked_leases"]}
        return {"halted": False, "panic": False, "reason": None}

    # ------------------------------------------------------------------
    # Pre-execution checks
    # ------------------------------------------------------------------
    def _halt_verdict(self, action: str) -> dict[str, Any] | None:
        with self._lock:
            if not self._halted:
                return None
            reason = f"automation halted: {self._halt_reason}"
        return _verdict(False, action, reason, halted=True)

    def check_action(self, action: str) -> dict[str, Any]:
        """Global halt gate -- every tool call must pass this first."""
        verdict = self._halt_verdict(action)
        if verdict is not None:
            return verdict
        return _verdict(True, action)

    def check_click(self, x: int, y: int) -> dict[str, Any]:
        """Window-boundary lock for clicks: outside coordinates are blocked, not re-targeted."""
        verdict = self._halt_verdict("click")
        if verdict is not None:
            return verdict
        observed = self.observe_pointer(x, y)
        if observed.get("panic"):
            return _verdict(False, "click", str(observed.get("reason")), halted=True, panic=True)
        with self._lock:
            bounds = self._window_bounds
        if bounds is not None and not bbox_contains(bounds, x, y):
            return _verdict(False, "click", f"coordinate ({x}, {y}) is outside the locked window bounds {list(bounds)} (window-boundary lock)")
        return _verdict(True, "click", x=int(x), y=int(y))

    def check_move(self, x: int, y: int) -> dict[str, Any]:
        """Moves are clamped inside the window lock; the final target still feeds panic-corner."""
        verdict = self._halt_verdict("move")
        if verdict is not None:
            return verdict
        target_x, target_y = int(x), int(y)
        clamped = False
        with self._lock:
            bounds = self._window_bounds
        if bounds is not None:
            new_x, new_y = clamp_to_bbox(bounds, target_x, target_y)
            clamped = (new_x, new_y) != (target_x, target_y)
            target_x, target_y = new_x, new_y
        observed = self.observe_pointer(target_x, target_y)
        if observed.get("panic"):
            return _verdict(False, "move", str(observed.get("reason")), halted=True, panic=True)
        return _verdict(True, "move", x=target_x, y=target_y, clamped=clamped)

    def check_hotkey(self, keys: str | Sequence[str], *, confirmed: bool = False) -> dict[str, Any]:
        """Blacklist check for key combos; destructive combos pass only with ``confirmed=True``."""
        verdict = self._halt_verdict("hotkey")
        if verdict is not None:
            return verdict
        normalized = normalize_hotkey(keys)
        pressed = frozenset(normalized)
        for combo, description in BLACKLISTED_HOTKEYS.items():
            if combo <= pressed:
                if confirmed:
                    return _verdict(True, "hotkey", f"blacklisted combo explicitly confirmed by the caller: {description}", keys=list(normalized), confirmed=True)
                return _verdict(False, "hotkey", f"blocked destructive hotkey: {description} (pass confirmed=true to override)", keys=list(normalized), blocked_by="hotkey_blacklist")
        return _verdict(True, "hotkey", keys=list(normalized))

    # ------------------------------------------------------------------
    # Window-boundary lock
    # ------------------------------------------------------------------
    def set_window_bounds(self, bbox: Sequence[int], label: str = "") -> dict[str, Any]:
        """Lock all pointer actions to ``bbox = [left, top, right, bottom]`` (half-open)."""
        try:
            values = tuple(int(v) for v in bbox)
        except (TypeError, ValueError):
            values = ()
        if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
            return {"locked": False, "bounds": None, "reason": f"invalid bbox {bbox!r}: expected [left, top, right, bottom] with right > left and bottom > top"}
        with self._lock:
            self._window_bounds = values  # type: ignore[assignment]
            self._window_label = str(label or "")
        return {"locked": True, "bounds": list(values), "label": self._window_label}

    def clear_window_bounds(self) -> dict[str, Any]:
        """Release the window-boundary lock."""
        with self._lock:
            self._window_bounds = None
            self._window_label = ""
        return {"locked": False, "bounds": None}

    def window_bounds(self) -> dict[str, Any]:
        """Current lock state."""
        with self._lock:
            return {"locked": self._window_bounds is not None, "bounds": list(self._window_bounds) if self._window_bounds is not None else None, "label": self._window_label}


_GUARD: SentinelGuard | None = None
_GUARD_LOCK = threading.Lock()


def get_sentinel_guard() -> SentinelGuard:
    """Process-wide sentinel guard singleton."""
    global _GUARD
    if _GUARD is None:
        with _GUARD_LOCK:
            if _GUARD is None:
                _GUARD = SentinelGuard()
    return _GUARD
