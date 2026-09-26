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
  ``Shift+Delete``, ``Ctrl+Alt+Delete``) are blocked. The only way past the
  blacklist is a **single-use, combo-bound confirmation token issued by an
  operator** through :meth:`SentinelGuard.arm_destructive_confirmation`. A bare
  boolean (``confirmed=True``) used to be enough, which meant the *model* could
  lift the blacklist by writing one more argument into its own tool call; there
  is deliberately no longer any boolean that a model-facing tool can set.

All checks are pure in-memory Python under a lock: no OS calls, no file I/O,
no network. Optional input backends live in :mod:`alpha.computer_use.dispatcher`;
this module never imports them.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import time
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

#: How long an operator-issued destructive-hotkey confirmation stays armed.
CONFIRMATION_TTL_SECONDS = 300.0

#: Bare executable names the sentinel will spawn without an explicit allowlist.
#: Anything containing a path separator, a ``..`` component, or an argv-looking
#: fragment is refused: a bare name resolves through the OS search path, while a
#: caller-supplied absolute path is arbitrary-program execution.
LAUNCH_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$"
_LAUNCH_NAME_RE = re.compile(LAUNCH_NAME_PATTERN)


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
        # token -> {keys, armed_by, expires_at, consumed}
        self._destructive_confirmations: dict[str, dict[str, Any]] = {}
        # exact executable (lowercased, resolved) -> reason; empty = bare names only
        self._launch_allowlist: dict[str, str] = {}

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
                "armed_destructive_confirmations": self._live_confirmation_count(),
                "launch_allowlist": sorted(self._launch_allowlist),
            }

    def reset(self) -> dict[str, Any]:
        """Operator re-arm: clear the halt, drop every lease, release the window lock.

        Reset never resurrects revoked leases -- it re-opens the perimeter so a
        fresh, deliberate session can start. Armed destructive-hotkey
        confirmations are dropped too: re-arming the perimeter must not silently
        hand back a live authorisation for a blacklisted combo.
        """
        with self._lock:
            self._halted = False
            self._halt_reason = None
            self._leases.clear()
            self._window_bounds = None
            self._window_label = ""
            self._destructive_confirmations.clear()
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
            # A kill-switch must not leave a live authorisation behind either.
            confirmations_revoked = len(self._destructive_confirmations)
            self._destructive_confirmations.clear()
            hooks = list(self._panic_hooks) if first_trigger else []
        if revoked:
            logger.warning("panic halt revoked %d execution lease(s): %s", len(revoked), reason)
        for hook in hooks:
            try:
                hook(reason)
            except Exception:  # noqa: BLE001 - external revokers must never break the halt path
                logger.exception("panic hook failed (halt still engaged): %s", reason)
        return {
            "halted": True,
            "panic": True,
            "reason": reason,
            "revoked_leases": revoked,
            "revoked_confirmations": confirmations_revoked,
            "first_trigger": first_trigger,
        }

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

    def check_hotkey(self, keys: str | Sequence[str], *, confirmation_token: str | None = None) -> dict[str, Any]:
        """Blacklist check for key combos.

        A destructive combo passes only with a valid, matching, single-use
        confirmation token that an **operator** armed out of band. There is no
        boolean parameter: a caller that a model can influence must not be able
        to lift the blacklist by setting a flag.
        """
        verdict = self._halt_verdict("hotkey")
        if verdict is not None:
            return verdict
        normalized = normalize_hotkey(keys)
        pressed = frozenset(normalized)
        for combo, description in BLACKLISTED_HOTKEYS.items():
            if combo <= pressed:
                consumed = self.consume_destructive_confirmation(normalized, confirmation_token)
                if consumed["valid"]:
                    return _verdict(
                        True,
                        "hotkey",
                        f"blacklisted combo allowed by an operator confirmation token: {description}",
                        keys=list(normalized),
                        confirmed=True,
                    )
                return _verdict(
                    False,
                    "hotkey",
                    f"blocked destructive hotkey: {description} ({consumed['reason']})",
                    keys=list(normalized),
                    blocked_by="hotkey_blacklist",
                )
        return _verdict(True, "hotkey", keys=list(normalized))

    # ------------------------------------------------------------------
    # Operator-issued destructive-hotkey confirmations
    # ------------------------------------------------------------------
    @staticmethod
    def _confirmation_key(normalized: Sequence[str]) -> str:
        return "+".join(sorted(normalized))

    def _live_confirmation_count(self) -> int:
        now = time.monotonic()
        return sum(1 for record in self._destructive_confirmations.values() if record["expires_at"] > now)

    def arm_destructive_confirmation(self, keys: str | Sequence[str], *, armed_by: str = "operator") -> dict[str, Any]:
        """Issue a single-use token that unlocks exactly one blacklisted combo.

        This is an **operator** surface, not a model-facing tool argument: no
        ``desktop_*`` tool exposes it, so a model cannot arm its own permission.
        The token is bound to the normalised key set, expires, and is consumed by
        the first matching :meth:`check_hotkey`.
        """
        normalized = normalize_hotkey(keys)
        if not normalized:
            return {"armed": False, "reason": "a confirmation needs at least one key"}
        pressed = frozenset(normalized)
        matched = next((description for combo, description in BLACKLISTED_HOTKEYS.items() if combo <= pressed), None)
        if matched is None:
            return {
                "armed": False,
                "keys": list(normalized),
                "reason": f"{self._confirmation_key(normalized)} is not a blacklisted combo; no confirmation is needed or issued",
            }
        with self._lock:
            if self._halted:
                return {"armed": False, "keys": list(normalized), "reason": f"automation halted: {self._halt_reason}"}
            token = secrets.token_urlsafe(24)
            self._destructive_confirmations[token] = {
                "keys": self._confirmation_key(normalized),
                "armed_by": str(armed_by or "operator"),
                "expires_at": time.monotonic() + CONFIRMATION_TTL_SECONDS,
            }
        logger.warning("operator armed a single-use confirmation for blacklisted hotkey %s (%s)", self._confirmation_key(normalized), matched)
        return {
            "armed": True,
            "confirmation_token": token,
            "keys": list(normalized),
            "combo": matched,
            "armed_by": str(armed_by or "operator"),
            "single_use": True,
            "ttl_seconds": CONFIRMATION_TTL_SECONDS,
        }

    def disarm_destructive_confirmation(self, confirmation_token: str | None = None) -> dict[str, Any]:
        """Revoke one armed confirmation, or all of them when no token is given."""
        with self._lock:
            if confirmation_token:
                removed = self._destructive_confirmations.pop(str(confirmation_token), None) is not None
                return {"revoked": [str(confirmation_token)] if removed else [], "revoked_count": 1 if removed else 0}
            count = len(self._destructive_confirmations)
            self._destructive_confirmations.clear()
            return {"revoked_count": count}

    def consume_destructive_confirmation(self, keys: str | Sequence[str], confirmation_token: str | None) -> dict[str, Any]:
        """Validate and burn a confirmation token for *keys* (single use)."""
        if not confirmation_token:
            return {"valid": False, "reason": "an operator confirmation token is required to run a destructive hotkey"}
        wanted = self._confirmation_key(normalize_hotkey(keys))
        with self._lock:
            record = self._destructive_confirmations.get(str(confirmation_token))
            if record is None:
                return {"valid": False, "reason": "no operator confirmation is armed for this hotkey"}
            if record["keys"] != wanted:
                return {"valid": False, "reason": f"the armed confirmation covers {record['keys']}, not {wanted}"}
            if record["expires_at"] <= time.monotonic():
                self._destructive_confirmations.pop(str(confirmation_token), None)
                return {"valid": False, "reason": "the operator confirmation expired"}
            # Single use: burn it whatever happens next.
            self._destructive_confirmations.pop(str(confirmation_token), None)
            armed_by = record["armed_by"]
        logger.warning("consumed operator confirmation armed by %s for %s", armed_by, wanted)
        return {"valid": True, "reason": "operator confirmation consumed", "armed_by": armed_by}

    # ------------------------------------------------------------------
    # Application-launch scope
    # ------------------------------------------------------------------
    def set_launch_allowlist(self, executables: Sequence[str]) -> dict[str, Any]:
        """Operator allowlist of exact executables that may be spawned by path.

        Empty (the default) means "bare executable names only": a name with no
        path separator still resolves through the OS search path, but a
        caller-supplied absolute path is refused as arbitrary-program execution.
        """
        cleaned: dict[str, str] = {}
        for item in executables or ():
            text = str(item).strip()
            if text:
                cleaned[os.path.normcase(text)] = text
        with self._lock:
            self._launch_allowlist = cleaned
        return {"allowlist": sorted(cleaned.values()), "count": len(cleaned)}

    def check_launch(self, app: str) -> dict[str, Any]:
        """Scope check for :func:`alpha.computer_use.dispatcher.launch_application`."""
        verdict = self._halt_verdict("launch_application")
        if verdict is not None:
            return verdict
        cleaned = str(app or "").strip()
        if not cleaned:
            return _verdict(False, "launch_application", "an executable name or path is required")
        if len(cleaned) > 1024:
            return _verdict(False, "launch_application", "executable name exceeds 1024 characters")
        with self._lock:
            allowlist = dict(self._launch_allowlist)
        key = os.path.normcase(cleaned)
        if key in allowlist:
            return _verdict(True, "launch_application", f"{cleaned} is on the operator launch allowlist", app=cleaned, allowlisted=True)
        if not _LAUNCH_NAME_RE.fullmatch(cleaned):
            return _verdict(
                False,
                "launch_application",
                (
                    f"refused to spawn {cleaned!r}: only a bare executable name matching {LAUNCH_NAME_PATTERN} "
                    "is allowed by default, because a caller-supplied path is arbitrary-program execution. "
                    "An operator can allow an exact executable with SentinelGuard.set_launch_allowlist([...])."
                ),
                blocked_by="launch_scope",
                app=cleaned,
            )
        if os.path.sep in cleaned or (os.path.altsep and os.path.altsep in cleaned) or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
            return _verdict(False, "launch_application", f"refused to spawn path-like executable {cleaned!r} (launch scope)", blocked_by="launch_scope", app=cleaned)
        return _verdict(True, "launch_application", f"{cleaned} is a bare executable name", app=cleaned, allowlisted=False)

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
