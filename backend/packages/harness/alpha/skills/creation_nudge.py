"""Skill-creation nudge: cadence-gated cue to review the skill library.

Port of Hermes ``turn_finalizer.py:675-682`` semantics (read-only reference):
the review cue fires only when ``interval > 0 AND iters_since_skill >=
interval AND the skill tool is actually available``; firing resets the
counter so the next cue needs a full cadence again. Resume mirrors Hermes
``turn_context.py:701-706``: a persisted user-turn count hydrates the
counter by modulo (``prior % interval``), never inventing progress that the
history does not contain.

The prompt built by :func:`build_skill_review_prompt` carries REAL counts
only (``len(experience_refs)`` and per-skill ``uses`` counts read from the
real :class:`alpha.skills.usage.SkillUsageTracker` stats) or the literal
honest phrase "no usage evidence recorded" when no telemetry rows are
supplied. It never claims an improvement — generation has not measured one.

No wiring, router, or manifest changes: this module is a pure seam the
turn finalizer can import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "SkillNudgeState",
    "build_skill_review_prompt",
    "hydrate",
    "should_review_skills",
]


def should_review_skills(interval: int, iters_since_skill: int, *, skill_tool_available: bool) -> bool:
    """Hermes ``turn_finalizer.py:675-682`` gate: all three conditions required.

    Disabled cadence (``interval <= 0``) never fires, and a missing skill
    tool never fires (the agent could not act on the cue anyway).
    """
    if interval <= 0:
        return False
    if not skill_tool_available:
        return False
    return iters_since_skill >= interval


@dataclass
class SkillNudgeState:
    """Tool-iteration counter for the skill-review cue (one per session).

    ``skill_tool_available`` is part of the state's environment (mirrors
    Hermes reading ``valid_tool_names`` at finalizer time); it can be
    re-evaluated between turns, and a count accumulated while the tool was
    unavailable fires as soon as the tool returns — Hermes only resets the
    counter on fire, never on a suppressed check.
    """

    interval: int = 0
    iters_since_skill: int = 0
    skill_tool_available: bool = True

    def tick(self, tool_iterations: int) -> bool:
        """Advance by this turn's tool iterations; True when the cue fires.

        The counter resets to 0 on fire (Hermes zeroes exactly when the cue
        fires; a surplus past the interval is not carried over). When the
        cue is disabled or the tool is unavailable the count is KEPT, never
        silently zeroed. Non-positive increments are ignored — tool
        iterations cannot run backwards.
        """
        if tool_iterations > 0:
            self.iters_since_skill += int(tool_iterations)
        if should_review_skills(self.interval, self.iters_since_skill, skill_tool_available=self.skill_tool_available):
            self.iters_since_skill = 0
            return True
        return False

    def reset(self) -> None:
        """Drop accumulated iterations without firing (explicit manual reset)."""
        self.iters_since_skill = 0


def hydrate(prior_user_turn_count: int, interval: int) -> SkillNudgeState:
    """Resume a state from persisted history (Hermes ``turn_context.py:701-706``).

    ``prior % interval`` reconstructs the position inside the current
    cadence cycle (a fire is assumed at each multiple, matching the
    reset-on-fire behavior); non-positive history or a disabled interval
    resumes at 0 — never at an invented progress value.
    """
    if interval <= 0:
        return SkillNudgeState(interval=int(interval))
    if prior_user_turn_count <= 0:
        return SkillNudgeState(interval=int(interval))
    return SkillNudgeState(interval=int(interval), iters_since_skill=int(prior_user_turn_count) % int(interval))


def _usage_pairs(usage_stats: Any) -> list[tuple[str, int]]:
    """Normalize real tracker stats into ``(name, uses)`` pairs.

    Accepts ``None``, a single row (``SkillUsage`` from ``tracker.stats()``),
    a sequence of rows (``tracker.all_stats()``), or a mapping of
    ``name -> uses``. A row missing ``name``/``uses`` raises instead of
    being silently dropped — honest fail-closed: no invented and no hidden
    numbers.
    """
    if usage_stats is None:
        return []
    if isinstance(usage_stats, Mapping):
        return [(str(name), int(uses)) for name, uses in usage_stats.items()]
    if hasattr(usage_stats, "name") and hasattr(usage_stats, "uses"):
        usage_stats = [usage_stats]
    pairs: list[tuple[str, int]] = []
    for row in usage_stats:
        name = getattr(row, "name", None)
        uses = getattr(row, "uses", None)
        if name is None or uses is None:
            raise ValueError(f"usage stats rows must carry real 'name' and 'uses' fields, got {row!r}.")
        pairs.append((str(name), int(uses)))
    return pairs


def build_skill_review_prompt(experience_refs: list[str], usage_stats: Any = None) -> str:
    """The review cue text: real counts only, honest-unknown when empty.

    The ONLY numbers that can appear are ``len(experience_refs)`` and the
    per-skill ``uses`` counts from the supplied real tracker stats; with no
    stats the literal phrase "no usage evidence recorded" is emitted instead
    of any fabricated number. No improvement is ever claimed.
    """
    refs = list(experience_refs) if experience_refs else []
    parts: list[str] = []
    if refs:
        parts.append(f"Skill review: {len(refs)} experience reference(s) attached.")
    else:
        parts.append("Skill review: no experience references attached.")
    pairs = _usage_pairs(usage_stats)
    if pairs:
        parts.append("Recorded skill usage (real tracker counts):")
        parts.extend(f"- {name}: {uses} recorded use(s)" for name, uses in pairs)
    else:
        parts.append("no usage evidence recorded")
    parts.append("Update the skill library only from the evidence above; report honestly when nothing is worth changing.")
    return " ".join(parts)
