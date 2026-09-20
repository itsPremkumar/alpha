"""Global Kill Switch and Human Override Safety Engine (Master Inventory #113, #114, #177).

Provides process-safe emergency stop capability across all autonomous bot operations and
per-bot pausing controls.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from alpha.bots.profile import _now

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_global_kill_switch_active = False
_global_kill_switch_reason = ""
_global_kill_switch_engaged_at: str | None = None
_paused_bots: dict[str, dict[str, str]] = {}


def set_global_kill_switch(active: bool, reason: str = "Operator emergency stop") -> dict[str, Any]:
    """Engage or disengage the fleet-wide global kill switch (Inventory #114)."""
    global _global_kill_switch_active, _global_kill_switch_reason, _global_kill_switch_engaged_at
    with _lock:
        _global_kill_switch_active = active
        _global_kill_switch_reason = reason if active else ""
        _global_kill_switch_engaged_at = _now() if active else None

    logger.warning("Global kill switch set to %s (reason: %s)", active, reason)

    try:
        from alpha.bots.events import log_org_event

        log_org_event(
            event_type="kill_switch_engaged" if active else "kill_switch_disengaged",
            actor="operator",
            details={"reason": reason, "active": active},
        )
    except Exception:
        logger.debug("Kill switch event logging failed", exc_info=True)

    return get_kill_switch_status()


def is_kill_switch_active() -> tuple[bool, str]:
    """Return whether the global kill switch is currently active and the reason."""
    with _lock:
        return _global_kill_switch_active, _global_kill_switch_reason


def pause_bot(bot_name: str, reason: str = "Operator paused") -> dict[str, Any]:
    """Pause execution for a specific bot (Inventory #113)."""
    key = bot_name.lower().strip()
    with _lock:
        _paused_bots[key] = {
            "reason": reason,
            "paused_at": _now(),
        }

    try:
        from alpha.bots.events import log_org_event

        log_org_event(
            event_type="bot_paused",
            actor="operator",
            target=key,
            details={"reason": reason},
        )
    except Exception:
        logger.debug("Pause bot event logging failed", exc_info=True)

    return {"bot_name": key, "is_paused": True, "reason": reason}


def resume_bot(bot_name: str) -> dict[str, Any]:
    """Resume execution for a previously paused bot."""
    key = bot_name.lower().strip()
    with _lock:
        removed = _paused_bots.pop(key, None)

    try:
        from alpha.bots.events import log_org_event

        log_org_event(
            event_type="bot_resumed",
            actor="operator",
            target=key,
            details={},
        )
    except Exception:
        logger.debug("Resume bot event logging failed", exc_info=True)

    return {"bot_name": key, "is_paused": False, "was_paused": removed is not None}


def is_bot_paused(bot_name: str) -> tuple[bool, str]:
    """Check if a specific bot is paused either individually or by the global kill switch."""
    key = bot_name.lower().strip()
    with _lock:
        if _global_kill_switch_active:
            return True, f"Global kill switch: {_global_kill_switch_reason}"
        if key in _paused_bots:
            return True, f"Bot paused: {_paused_bots[key]['reason']}"
        return False, ""


def get_kill_switch_status() -> dict[str, Any]:
    """Return comprehensive status of the kill switch and paused bots."""
    with _lock:
        return {
            "global_kill_switch_active": _global_kill_switch_active,
            "reason": _global_kill_switch_reason,
            "engaged_at": _global_kill_switch_engaged_at,
            "paused_bots": dict(_paused_bots),
            "paused_count": len(_paused_bots),
        }
