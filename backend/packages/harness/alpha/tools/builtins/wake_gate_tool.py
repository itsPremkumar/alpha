"""Built-in Await Task Event Tool.

Allows agents to register a wake gate and yield execution until background
tasks, subagents, or terminal commands complete, eliminating busy polling loops.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.scheduler.reactive_wake import WakeCondition, get_reactive_wake_registry


@tool("await_task_event", parse_docstring=True)
def await_task_event(
    task_ids_json: str,
    condition: str = "all_complete",
    timeout_seconds: float = 60.0,
    thread_id: str = "default",
) -> str:
    """Register an event wake gate for one or more asynchronous background tasks.

    Instead of repeatedly checking task status in a loop and wasting context tokens,
    use this tool to set a condition that wakes you when tasks complete or fail.

    Args:
        task_ids_json: JSON list of task IDs to monitor (e.g. '["task-1", "task-2"]').
        condition: Wake condition: 'all_complete', 'any_complete', 'on_failure', or 'on_exit'.
        timeout_seconds: Maximum wait duration before waking on timeout.
        thread_id: Current execution thread ID.
    """
    try:
        task_ids = json.loads(task_ids_json)
        if not isinstance(task_ids, list) or not task_ids:
            return "Error: task_ids_json must be a non-empty JSON list of task IDs."
    except Exception as exc:
        return f"Error parsing task_ids_json: {exc}"

    cond: WakeCondition = "all_complete"
    if condition in ("all_complete", "any_complete", "on_failure", "on_exit"):
        cond = condition  # type: ignore

    registry = get_reactive_wake_registry()
    gate = registry.register_gate(
        thread_id=thread_id,
        task_ids=task_ids,
        condition=cond,
        timeout_seconds=timeout_seconds,
    )

    return (
        f"[WAKE_GATE_REGISTERED]\n"
        f"Gate ID: {gate.gate_id}\n"
        f"Monitored Tasks: {list(gate.target_task_ids)}\n"
        f"Condition: {gate.condition}\n"
        f"Timeout: {gate.timeout_seconds}s\n"
        f"Status: Waiting for event triggers (notify_on_exit)."
    )
