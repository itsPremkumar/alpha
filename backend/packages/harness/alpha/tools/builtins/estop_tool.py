"""Built-in Emergency Stop (ESTOP) tool inspired by Hermes Agent."""

from __future__ import annotations

import json
import time

from langchain.tools import tool

from alpha.runtime.estop import get_estop_manager

_LEDGER_OWNER = "runtime"


def _record_action(tool_name: str, args: dict[str, object], act: str) -> tuple[str | None, float]:
    """Write a real action intent before the ESTOP mutation runs.

    Returns ``(intent_id, started_at)``; ``(None, started_at)`` when the
    side-channel itself is unavailable — the action must still run, and the
    failure is never hidden.

    The start timestamp is taken AFTER the intent row lands: the ledger
    rejects a receipt whose ``started_at`` precedes ``intent.created_at``, so
    sampling the clock first would make every receipt unwritable.
    """
    try:
        from alpha.ledger.store import default_action_ledger

        intent = default_action_ledger().record_intent(_LEDGER_OWNER, tool_name, args)
        return intent.id, time.time()
    except Exception:
        return None, time.time()


def _record_receipt(
    intent_id: str | None,
    started_at: float,
    outcome: str,
    *,
    error_category: str | None = None,
    exit_ref: str | None = None,
) -> None:
    """Write the real receipt for a completed ESTOP action (fail-safe)."""
    if intent_id is None:
        return
    try:
        from alpha.ledger.store import default_action_ledger

        default_action_ledger().record_receipt(
            intent_id,
            outcome,  # type: ignore[arg-type]  # one of OUTCOMES by construction
            owner_id=_LEDGER_OWNER,
            error_category=error_category,  # type: ignore[arg-type]  # one of ERROR_CATEGORIES
            started_at=started_at,
            completed_at=time.time(),
            exit_ref=exit_ref,
        )
    except Exception:
        # A receipt failure must not turn a successful ESTOP action into an
        # error, but it must NOT be silent either: the reason is logged at
        # warning level so a broken ledger is visible in the logs.
        import logging

        logging.getLogger(__name__).warning(
            "ESTOP action receipt could not be recorded (the action itself stands)",
            exc_info=True,
        )
        return


@tool("emergency_stop_manage", parse_docstring=True)
def emergency_stop_manage(
    action: str = "status",
    reason: str = "",
) -> str:
    """Manage the runtime Emergency Stop (ESTOP) global pause state.

    Allows operators or supervisor agents to immediately suspend all autonomous background tasks,
    crons, subagents, and goal loops with zero state corruption.

    Args:
        action: Operational action: 'status' (inspect active state), 'engage' (trigger pause), or 'disengage' (resume operations).
        reason: Justification when engaging the emergency stop.
    """
    manager = get_estop_manager()
    act = action.strip().lower()

    if act == "status":
        return json.dumps(manager.get_status(), indent=2)
    elif act == "engage":
        intent_id, started = _record_action(
            "emergency_stop_manage", {"action": act, "reason": reason}, act
        )
        try:
            sentinel = manager.engage(reason=reason)
        except Exception:
            _record_receipt(intent_id, started, "failed", error_category="internal")
            raise
        _record_receipt(intent_id, started, "succeeded", exit_ref=str(sentinel))
        return f"ESTOP successfully engaged at '{sentinel}'. Fleet execution paused."
    elif act == "disengage":
        intent_id, started = _record_action(
            "emergency_stop_manage", {"action": act, "reason": reason}, act
        )
        try:
            success = manager.disengage()
        except Exception:
            _record_receipt(intent_id, started, "failed", error_category="internal")
            raise
        if success:
            _record_receipt(intent_id, started, "succeeded")
            return "ESTOP successfully disengaged. Fleet operations resumed."
        _record_receipt(intent_id, started, "failed", error_category="internal")
        return "Failed to disengage ESTOP (filesystem error)."

    return f"Unknown action '{action}'. Use 'status', 'engage', or 'disengage'."
