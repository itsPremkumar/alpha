"""Memory persistence nudges: remind long-running agents to save what they learned.

Hermes-style periodic nudges, cooldown-guarded so they fire at most once per
window and never interrupt an active exchange — the nudge is consumed at the
next natural turn boundary by the caller.
"""

from __future__ import annotations

import time

DEFAULT_IDLE_HOURS = 2.0
DEFAULT_COOLDOWN_HOURS = 24.0


def should_nudge(
    last_activity_ts: float | None,
    last_nudge_ts: float | None,
    *,
    now: float | None = None,
    idle_hours: float = DEFAULT_IDLE_HOURS,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
) -> bool:
    """Nudge when the thread has been idle long enough and the cooldown elapsed."""
    moment = now if now is not None else time.time()
    if last_activity_ts is None:
        return False
    if (moment - last_activity_ts) < idle_hours * 3600.0:
        return False
    if last_nudge_ts is not None and (moment - last_nudge_ts) < cooldown_hours * 3600.0:
        return False
    return True


def build_memory_nudge(thread_hint: str = "") -> str:
    """The persistence reminder injected at the next turn boundary."""
    scope = f" (thread: {thread_hint})" if thread_hint else ""
    return (
        "Memory checkpoint: before continuing, persist anything durable you learned"
        f"{scope} — user preferences, project facts, reusable procedures — via memory tools. "
        "One concise write per fact; skip anything already stored or purely ephemeral. "
        "If nothing is worth keeping, say so briefly and move on."
    )
