"""Kibitzer Resident Memory Tool.

Allows configuring long-term project knowledge and querying resident memory hints.
"""

from __future__ import annotations

from langchain.tools import tool

from alpha.memory.kibitzer import KibitzerMemoryBank, KibitzerObserver

_GLOBAL_BANK = KibitzerMemoryBank()
_GLOBAL_OBSERVER = KibitzerObserver(memory_bank=_GLOBAL_BANK)


@tool
def kibitzer_nudge_manage(
    action: str,
    entry_id: str | None = None,
    topic: str | None = None,
    keywords: list[str] | None = None,
    hint: str | None = None,
    details: str | None = None,
    prompt: str | None = None,
    tool_name: str | None = None,
    tool_result: str | None = None,
) -> str:
    """Manage resident memory hints (Kibitzer sidecar). Actions: 'add_memory', 'observe', 'reset'."""
    if action == "add_memory":
        if not entry_id or not keywords or not hint:
            return "Error: 'entry_id', 'keywords', and 'hint' are required."
        _GLOBAL_BANK.add_entry(
            entry_id=entry_id,
            topic=topic or entry_id,
            keywords=keywords,
            hint=hint,
            details=details or "",
        )
        return f"Added memory hint '{entry_id}' with keywords {keywords}."

    elif action == "observe":
        nudges = _GLOBAL_OBSERVER.observe(
            prompt=prompt or "",
            tool_name=tool_name or "",
            tool_result=tool_result or "",
        )
        if not nudges:
            return "No memory nudges triggered for this activity."
        return "\n".join(nudges)

    elif action == "reset":
        _GLOBAL_OBSERVER.reset_delivered()
        return "Reset delivered memory cache for session."

    return f"Error: unknown action '{action}'."
