"""Render a durable mission into the compact anchor injected every turn.

Why this exists
--------------
The durable stack is only useful if the model *re-reads* the parts that prevent
drift on every turn. Dumping the whole stack would bloat the context -- the very
thing compaction exists to shrink. So the anchor is a deliberately terse
projection: the frozen objective, the hard constraints, the "done when" bar, the
one active milestone and where it sits in the plan, the latest status lines, and
the tail of the scratchpad. It is bounded to a character budget and it is what
the middleware wraps in a hidden ``<system_memory>`` block.

The anchor carries **no verdict**. It never says "the mission is on track" or
"this milestone passes"; it restates the target and the current, checkable step.
Whether a milestone holds is decided by :mod:`alpha.runtime.missions.milestones`
from a measured result, not by this projection.
"""

from __future__ import annotations

from typing import Final

from alpha.runtime.missions.checkpoint import render_resume_checkpoint
from alpha.runtime.missions.manager import MissionStack

__all__ = ["ANCHOR_HEADER", "render_anchor", "MISSION_NOTICE"]

#: Marker the middleware stamps on the injected message so it can recognise --
#: and not duplicate -- its own reminder across turns.
ANCHOR_HEADER: Final[str] = '<system_memory role="mission">'
MISSION_NOTICE: Final[str] = "</system_memory>"


def _bullets(lines: tuple[str, ...], *, limit: int) -> str:
    if not lines:
        return ""
    kept = lines[-limit:]
    return "".join(f"  - {line}\n" for line in kept)


def render_anchor(stack: MissionStack, *, status_tail: int = 4, scratch_tail: int = 2, max_chars: int = 4000) -> str:
    """Render the compact per-turn anchor for *stack*.

    Returns an empty string for an empty stack (nothing to anchor). The output
    is trimmed to *max_chars* from the front -- the objective is the
    least-droppable part -- rather than from the tail, so a budget overflow drops
    the tail (usually scratchpad) before it drops the target. ``[truncated]``
    marks the cut so the agent knows it is reading an abridged view.
    """
    if not stack.is_active():
        return ""

    parts: list[str] = [ANCHOR_HEADER + "\n"]

    if stack.spec_objective:
        parts.append(f"## Objective\n{stack.spec_objective}\n\n")
    constraints = _bullets(stack.spec_constraints, limit=8)
    if constraints:
        parts.append("## Hard constraints\n" + constraints + "\n")
    done = _bullets(stack.spec_done_when, limit=8)
    if done:
        parts.append("## Done when\n" + done + "\n")

    plan = stack.plan
    if plan is not None and plan.current is not None:
        current = plan.current
        position = f"{plan.active_index + 1}/{plan.total}"
        parts.append(f"## Current milestone ({position}): {current.title}\n")
        if current.acceptance:
            parts.append(f"acceptance: {current.acceptance}\n")
        if current.validation:
            parts.append(f"verification: {current.validation}\n")
        if plan.complete:
            parts.append("(all milestones verified)\n")
        parts.append("\n")
    elif plan is not None and plan.total == 0:
        parts.append("## Plan\n(no milestones yet)\n\n")

    steers = _bullets(stack.steers, limit=3)
    if steers:
        parts.append("## Operator steer\n" + steers + "\n")

    if stack.checkpoint is not None:
        rendered = render_resume_checkpoint(stack.checkpoint)
        if rendered:
            parts.append(rendered + "\n")

    if stack.rejected:
        parts.append("## Do not repeat\n" + _bullets(stack.rejected, limit=5) + "\n")

    if stack.deferred:
        parts.append("## Deferred (not now)\n" + _bullets(stack.deferred, limit=3) + "\n")

    if stack.status_lines:
        parts.append("## Recent status\n" + _bullets(stack.status_lines, limit=status_tail) + "\n")

    scratch = stack.scratchpad
    if scratch.count:
        head = "" if scratch.dropped == 0 else f"(+{scratch.dropped} earlier notes dropped)\n"
        parts.append("## Scratchpad\n" + head + _bullets(scratch.tail(scratch_tail), limit=scratch_tail) + "\n")

    parts.append(MISSION_NOTICE)
    text = "".join(parts)
    if len(text) > max_chars:
        text = text[: max(0, max_chars - len("\n[truncated]"))] + "\n[truncated]" + MISSION_NOTICE
    return text
