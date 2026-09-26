"""Persistence nudge, turn-counter complement: honest cadence over turns.

Owns the turn-counting gate; the reminder TEXT is NOT duplicated here —
:func:`alpha.learning.nudges.build_memory_nudge` is imported and reused
verbatim by :func:`build_persistence_nudge` (one canonical prompt string,
never a second divergent copy). ``learning/**`` is a read-only dependency.

Semantics mirror Hermes ``turn_context.py:709-718`` (``_tick_memory_nudge``):
the counter only advances when the capability is actually present
(``interval > 0`` AND the memory tool is available AND a store exists to
write to); reaching ``interval`` resets the counter to 0 and fires exactly
once. Resume mirrors Hermes ``turn_context.py:701-706`` (``prior % interval``).
Autonomous forks never nudge — :func:`should_suppress` is the single seam.
"""

from __future__ import annotations

from alpha.learning.nudges import build_memory_nudge

__all__ = [
    "build_persistence_nudge",
    "hydrate",
    "should_suppress",
    "tick",
]


def tick(turns_since: int, interval: int, *, memory_tool_available: bool, has_store: bool) -> tuple[int, bool]:
    """Advance the turn counter; returns ``(new_counter, fired)``.

    Fires ONLY when the capability is actually present: with no store, an
    unavailable tool, or a disabled interval the counter is returned
    untouched and ``fired`` is False — a nudge that could not be acted on
    is never emitted. On fire the counter resets to 0 (one nudge per
    cadence) and the REAL counter value is always the first tuple element.
    """
    if interval > 0 and memory_tool_available and has_store:
        counter = max(int(turns_since), 0) + 1
        if counter >= interval:
            return 0, True
        return counter, False
    return int(turns_since), False


def hydrate(prior_user_turn_count: int, interval: int) -> int:
    """Resume the counter from persisted history (Hermes ``turn_context.py:701-706``).

    ``prior % interval`` reconstructs the in-cycle position; non-positive
    history or a disabled interval resumes at 0 — never invented progress.
    """
    if interval <= 0 or prior_user_turn_count <= 0:
        return 0
    return int(prior_user_turn_count) % int(interval)


def should_suppress(is_autonomous_fork: bool) -> bool:
    """Autonomous forks never nudge (no human-in-the-loop benefit in a fork)."""
    return bool(is_autonomous_fork)


def build_persistence_nudge(
    thread_hint: str = "",
    *,
    turns_since: int | None = None,
    stored_memory_count: int | None = None,
) -> str:
    """The nudge text: canonical ``build_memory_nudge`` + honest counter lines.

    The reminder body is the imported canonical string, verbatim. The
    optional counter line prints the REAL ``turns_since`` value; the
    stored-memory line prints the REAL count when one was provided and the
    honest phrase "evidence unknown" when it was not — never a fabricated
    count.
    """
    parts = [build_memory_nudge(thread_hint)]
    if turns_since is not None:
        parts.append(f"Turns since the last persistence checkpoint: {int(turns_since)}.")
    if stored_memory_count is None:
        parts.append("evidence unknown: no stored-memory count was provided.")
    else:
        parts.append(f"Stored-memory records on hand: {int(stored_memory_count)}.")
    return " ".join(parts)
