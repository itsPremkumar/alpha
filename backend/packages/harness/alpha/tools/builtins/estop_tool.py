"""Built-in Emergency Stop (ESTOP) tool inspired by Hermes Agent."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.runtime.estop import get_estop_manager


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
        sentinel = manager.engage(reason=reason)
        return f"ESTOP successfully engaged at '{sentinel}'. Fleet execution paused."
    elif act == "disengage":
        success = manager.disengage()
        if success:
            return "ESTOP successfully disengaged. Fleet operations resumed."
        return "Failed to disengage ESTOP (filesystem error)."

    return f"Unknown action '{action}'. Use 'status', 'engage', or 'disengage'."
