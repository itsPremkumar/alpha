"""Turn-end guard for kanban workers: end with a terminal call or keep going.

Some models narrate the next step and stop with no tool calls; treating that
as a clean exit strands the task. Policy-only: return a bounded synthetic
nudge so the loop continues instead of exiting.
"""

from __future__ import annotations

import os
from typing import Any

TERMINAL_KANBAN_TOOLS = frozenset({"kanban_complete", "kanban_block"})

DEFAULT_MAX_ATTEMPTS = 2


def stop_nudge_enabled(task_marker: str | None = None) -> bool:
    """On when a kanban task is active, unless explicitly disabled."""
    if (os.environ.get("AGENT_WORKSPACE_KANBAN_STOP_NUDGE") or "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    marker = task_marker if task_marker is not None else os.environ.get("AGENT_WORKSPACE_KANBAN_TASK", "")
    return bool((marker or "").strip())


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        fn = tool_call.get("function")
        return str((fn.get("name") if isinstance(fn, dict) else tool_call.get("name")) or "")
    fn = getattr(tool_call, "function", None)
    return str((getattr(fn, "name", "") if fn is not None else getattr(tool_call, "name", "")) or "")


def terminal_called(messages: list[dict[str, Any]] | None) -> bool:
    """True if this conversation already invoked a terminal kanban tool."""
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant" and any(_tool_call_name(tc) in TERMINAL_KANBAN_TOOLS for tc in msg.get("tool_calls") or []):
            return True
        if msg.get("role") == "tool" and str(msg.get("name") or "") in TERMINAL_KANBAN_TOOLS:
            return True
    return False


def build_stop_nudge(*, messages: list[dict[str, Any]] | None = None, attempts: int = 0, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> str | None:
    """Return a continuation nudge, or None when done (terminal called or budget spent)."""
    if terminal_called(messages):
        return None
    if attempts >= max_attempts:
        return None
    remaining = max_attempts - attempts
    return (
        f"You are a kanban worker with an unfinished task: end this turn with exactly one terminal call "
        f"(`kanban_complete` with evidence, or `kanban_block` with the blocker). Narration alone does not "
        f"advance the board. Attempts remaining: {remaining}."
    )
