"""The structured resume checkpoint — the antidote to a compaction restart loop.

Why this exists
--------------
A documented failure of long-running coding agents (Codex issues #25900 / #25394)
is not that compaction fails -- it is that it *succeeds* and then resumes from the
wrong point: the agent re-reads files, re-runs exploration, re-attempts a path it
had already rejected, and the extra context triggers another compaction, in a
loop, until the quota runs out. The fix those threads converge on is a *structured*
continuation checkpoint the run re-reads after compaction, so it knows where it
actually is instead of inferring it from a prose summary.

:class:`ResumeCheckpoint` is that structured checkpoint, and
:func:`render_resume_checkpoint` projects it into the per-turn anchor. It is
deliberately terse and field-shaped rather than free-form, because the whole point
is that the "rejected paths / do-not-repeat" facts survive compaction as *facts*
rather than being smoothed back into a narrative that forgets them.

Honesty rules encoded here
--------------------------
* Every field is bounded and optional; an empty checkpoint renders nothing, so a
  first turn without one adds no noise.
* ``rejected_paths`` and ``do_not_repeat`` are rendered as an explicit "do not
  repeat" block. Turning a rejected path back into a live idea is precisely the
  drift this field exists to prevent, so it is never dropped from the view.
* The checkpoint records control-flow state, never a verdict of correctness. It
  says where the run *is*, not that the work is good -- that stays with the
  milestone evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["ResumeCheckpoint", "render_resume_checkpoint", "MAX_CHECKPOINT_FIELD_CHARS"]

#: Per-field bound shared with the tool boundary, so one rambling line cannot grow
#: the durable file without limit.
MAX_CHECKPOINT_FIELD_CHARS: Final[int] = 1200

_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("current_phase", "Current phase"),
    ("next_action", "Next action"),
    ("stop_condition", "Stop condition"),
    ("progress", "Progress"),
    ("confirmed_facts", "Confirmed facts"),
    ("rejected_paths", "Rejected paths"),
    ("do_not_repeat", "Do not repeat"),
)


def _clean(text: str) -> str:
    return " ".join((text or "").split())[:MAX_CHECKPOINT_FIELD_CHARS]


@dataclass(frozen=True, slots=True)
class ResumeCheckpoint:
    """A structured continuation checkpoint for a long-running mission.

    Fields are the minimum an agent needs to resume *from where it is* after a
    compaction or a restart, and never to start over. Only ``current_phase`` and
    ``next_action`` are the load-bearing pair; the rest sharpen the resume.
    """

    current_phase: str = ""
    next_action: str = ""
    stop_condition: str = ""
    progress: str = ""
    confirmed_facts: str = ""
    rejected_paths: str = ""
    do_not_repeat: str = ""

    def __post_init__(self) -> None:
        for name, _label in _ORDER:
            object.__setattr__(self, name, _clean(getattr(self, name)))

    def is_empty(self) -> bool:
        return not any(getattr(self, name).strip() for name, _ in _ORDER)

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name, _ in _ORDER}

    @classmethod
    def from_dict(cls, data: object) -> ResumeCheckpoint:
        if not isinstance(data, dict):
            return cls()
        return cls(**{name: str(data.get(name, "")) for name, _ in _ORDER})


def render_resume_checkpoint(checkpoint: ResumeCheckpoint) -> str:
    """Render the structured checkpoint as an anchor block.

    Returns an empty string for an empty checkpoint. Only fields with content are
    emitted, in the fixed :data:`_ORDER`, so the rendered shape is stable across
    turns and easy to re-read.
    """
    if checkpoint is None or checkpoint.is_empty():
        return ""
    lines = ["## Resume checkpoint"]
    for name, label in _ORDER:
        value = getattr(checkpoint, name)
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines) + "\n"
