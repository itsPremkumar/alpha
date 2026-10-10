"""Milestone plan: an objective decomposed into verifiable checkpoints.

Design
------
A long-horizon objective ("reduce p95 below 120ms", "reproduce the paper") is
not one prompt; it is a *sequence* of checkpoints, each small enough to finish
and *verify* in one loop. That segmentation is the whole reason a Multi-day run
stays coherent: the agent always has one active, testable target and never has to
hold the entire objective in working memory at once.

:class:`MilestonePlan` is that segmentation, expressed as an ordered tuple of
:class:`Milestone` with a single ``active_index``. It is a value object: every
mutation returns a new plan, so a checkpoint, an approval, and a crash-recovery
scan can all hold the same plan without racing.

Honesty rules encoded here
--------------------------
* **Evidence decides a milestone, not intent.** A milestone is only
  ``VERIFIED`` after :meth:`MilestonePlan.verify` records a measured result. The
  model may not declare a milestone done by describing it; the runbook must show
  the check.
* **Stop-and-fix is structural.** :meth:`MilestonePlan.advance` refuses to move
  the active pointer past a milestone whose status is not ``VERIFIED``. A
  ``FAILED`` milestone is not something the agent climbs over; it is something it
  repairs first.
* **The plan is refused, never clamped.** Bad input (empty objective, duplicate
  ids, too many milestones, an out-of-range index) raises
  :class:`InvalidMilestonePlan` naming the field, rather than being silently
  truncated to fit.
* ``PENDING``/``ACTIVE``/``VERIFIED``/``FAILED`` are the four terminal-ish
  states. There is deliberately no ``UNKNOWN``: an unverified milestone is
  ``ACTIVE``, never a shrug.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

__all__ = [
    "InvalidMilestonePlan",
    "Milestone",
    "MilestonePlan",
    "MilestoneStatus",
    "MAX_MILESTONES",
]


#: Hard ceiling on decomposition breadth. A 41st milestone is refused, not
#: dropped: silently keeping only the first 40 would hide the rest of the plan
#: behind a "complete" plan header.
MAX_MILESTONES: Final[int] = 40

#: Per-field bounds shared with the tool boundary. Long text fields are stored
#: trimmed to these so a single rambling entry cannot grow the file without
#: bound.
_MAX_TITLE_CHARS: Final[int] = 200
_MAX_TEXT_CHARS: Final[int] = 4000


class MilestoneStatus(StrEnum):
    """The verification state of a single milestone.

    The status is a property of the *evidence*, not of the agent's confidence.
    """

    PENDING = "pending"  # not reached yet
    ACTIVE = "active"  # being worked on now; unverified
    VERIFIED = "verified"  # a measured check held
    FAILED = "failed"  # a measured check did not hold; repair before advancing


class InvalidMilestonePlan(ValueError):
    """A milestone plan or one of its transitions is malformed.

    Raised rather than clamped or defaulted, so a decomposition bug is loud at
    the boundary that produced it instead of surfacing later as "the plan
    silently skipped half its milestones".
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _clean(text: str, *, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


@dataclass(frozen=True, slots=True)
class Milestone:
    """One verifiable checkpoint inside a :class:`MilestonePlan`.

    ``acceptance`` names what must hold; ``validation`` names *how it is
    checked* (a command, a file, a benchmark). An empty ``validation`` is legal
    but weak: the verifier records whatever the runbook produced, and a reviewer
    reading the plan should see at a glance that this milestone had no defined
    surface.
    """

    id: str
    title: str
    acceptance: str = ""
    validation: str = ""
    status: MilestoneStatus = MilestoneStatus.PENDING
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.id or not _clean(self.id, limit=120):
            raise InvalidMilestonePlan("milestone.id must be non-empty")
        if not _clean(self.title, limit=_MAX_TITLE_CHARS):
            raise InvalidMilestonePlan("milestone.title must be non-empty")
        # Normalize stored text once, at construction, so equality and the
        # rendered anchor do not depend on incidental whitespace.
        object.__setattr__(self, "id", _clean(self.id, limit=120))
        object.__setattr__(self, "title", _clean(self.title, limit=_MAX_TITLE_CHARS))
        object.__setattr__(self, "acceptance", _clean(self.acceptance, limit=_MAX_TEXT_CHARS))
        object.__setattr__(self, "validation", _clean(self.validation, limit=_MAX_TEXT_CHARS))
        object.__setattr__(self, "evidence", _clean(self.evidence, limit=_MAX_TEXT_CHARS))

    @property
    def is_terminal_ok(self) -> bool:
        return self.status is MilestoneStatus.VERIFIED

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "acceptance": self.acceptance,
            "validation": self.validation,
            "status": self.status.value,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: object) -> Milestone:
        if not isinstance(data, dict):
            raise InvalidMilestonePlan("milestone entry must be an object")
        raw_status = str(data.get("status", MilestoneStatus.PENDING.value))
        try:
            status = MilestoneStatus(raw_status)
        except ValueError as exc:  # unknown persisted status -> refuse, never guess
            raise InvalidMilestonePlan(f"unknown milestone status {raw_status!r}") from exc
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            acceptance=str(data.get("acceptance", "")),
            validation=str(data.get("validation", "")),
            status=status,
            evidence=str(data.get("evidence", "")),
        )


@dataclass(frozen=True, slots=True)
class MilestonePlan:
    """An objective segmented into verifiable, ordered checkpoints.

    Exactly one milestone is ``ACTIVE`` at a time and it is the one at
    ``active_index``. A plan with any ``VERIFIED`` milestone has advanced past the
    stop-and-fix gate for that milestone.
    """

    objective: str
    milestones: tuple[Milestone, ...] = ()
    active_index: int = 0

    def __post_init__(self) -> None:
        if not _clean(self.objective, limit=_MAX_TEXT_CHARS):
            raise InvalidMilestonePlan("milestone plan requires a non-empty objective")
        object.__setattr__(self, "objective", _clean(self.objective, limit=_MAX_TEXT_CHARS))
        if len(self.milestones) > MAX_MILESTONES:
            raise InvalidMilestonePlan(f"at most {MAX_MILESTONES} milestones per plan (got {len(self.milestones)})")
        ids = [m.id for m in self.milestones]
        if len(set(ids)) != len(ids):
            dups = sorted({i for i in ids if ids.count(i) > 1})
            raise InvalidMilestonePlan(f"milestone ids must be unique; duplicates: {dups}")
        if self.milestones and not (0 <= self.active_index < len(self.milestones)):
            raise InvalidMilestonePlan(f"active_index {self.active_index} out of range for {len(self.milestones)} milestones")
        if not self.milestones:
            object.__setattr__(self, "active_index", 0)

    # ---- construction ------------------------------------------------------------

    @classmethod
    def create(cls, objective: str, milestones: list[dict[str, str]] | tuple[dict[str, str], ...]) -> MilestonePlan:
        """Build a plan from a lightweight decomposition description.

        Each mapping is ``{"id": ..., "title": ..., "acceptance": ..., "validation": ...}``
        with only ``id``/``title`` required. The first milestone is ``ACTIVE``,
        the rest ``PENDING``; the agent cannot start from "everything active".
        """
        built: list[Milestone] = []
        for i, entry in enumerate(milestones):
            ms = Milestone.from_dict(entry)
            if i == 0:
                ms = replace(ms, status=MilestoneStatus.ACTIVE)
            built.append(ms)
        return cls(objective=objective, milestones=tuple(built), active_index=0)

    # ---- queries -----------------------------------------------------------------

    @property
    def current(self) -> Milestone | None:
        """The single active milestone, or ``None`` for an empty plan."""
        if not self.milestones:
            return None
        return self.milestones[self.active_index]

    @property
    def total(self) -> int:
        return len(self.milestones)

    @property
    def verified_count(self) -> int:
        return sum(1 for m in self.milestones if m.status is MilestoneStatus.VERIFIED)

    @property
    def complete(self) -> bool:
        """True only when every milestone is ``VERIFIED`` (and there is one)."""
        return bool(self.milestones) and all(m.status is MilestoneStatus.VERIFIED for m in self.milestones)

    def get(self, milestone_id: str) -> Milestone | None:
        return next((m for m in self.milestones if m.id == milestone_id), None)

    # ---- transitions -------------------------------------------------------------

    def _replace_milestone(self, milestone_id: str, updated: Milestone) -> MilestonePlan:
        if self.get(milestone_id) is None:
            raise InvalidMilestonePlan(f"unknown milestone id {milestone_id!r}")
        new = tuple(updated if m.id == milestone_id else m for m in self.milestones)
        return replace(self, milestones=new)

    def verify(self, milestone_id: str, *, passed: bool, evidence: str) -> MilestonePlan:
        """Record a measured result for one milestone.

        This is the only way a milestone becomes ``VERIFIED`` or ``FAILED``.
        Verification applies to any milestone by id, but advancing past the
        active one still requires the active one specifically to be ``VERIFIED``
        (see :meth:`advance`).
        """
        current = self.get(milestone_id)
        if current is None:
            raise InvalidMilestonePlan(f"unknown milestone id {milestone_id!r}")
        if current.status is MilestoneStatus.VERIFIED:
            # Already verified; a second confirmation neither strengthens nor
            # weakens it, so it is a no-op rather than an overwrite.
            return self
        status = MilestoneStatus.VERIFIED if passed else MilestoneStatus.FAILED
        return self._replace_milestone(milestone_id, replace(current, status=status, evidence=evidence))

    def advance(self) -> MilestonePlan:
        """Move the active pointer to the next milestone.

        Refuses unless the *current* milestone is ``VERIFIED``. That is the
        stop-and-fix rule: a ``FAILED`` (or still ``ACTIVE``) milestone must be
        repaired and verified before the plan moves forward, so the agent cannot
        quietly leave a broken checkpoint behind it.
        """
        if not self.milestones:
            raise InvalidMilestonePlan("cannot advance an empty plan")
        if self.complete:
            raise InvalidMilestonePlan("plan is already complete; nothing to advance to")
        current = self.current
        assert current is not None  # guarded by the empty check above
        if current.status is not MilestoneStatus.VERIFIED:
            raise InvalidMilestonePlan(f"stop-and-fix: milestone {current.id!r} is {current.status.value}, not verified; repair and verify it before advancing")
        next_index = self.active_index + 1
        milestones = list(self.milestones)
        milestones[next_index] = replace(milestones[next_index], status=MilestoneStatus.ACTIVE)
        return replace(self, milestones=tuple(milestones), active_index=next_index)

    # ---- persistence -------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "objective": self.objective,
            "active_index": self.active_index,
            "milestones": [m.to_dict() for m in self.milestones],
        }

    @classmethod
    def from_dict(cls, data: object) -> MilestonePlan:
        if not isinstance(data, dict):
            raise InvalidMilestonePlan("milestone plan must be an object")
        raw = data.get("milestones", [])
        if not isinstance(raw, list):
            raise InvalidMilestonePlan("milestone plan 'milestones' must be a list")
        return cls(
            objective=str(data.get("objective", "")),
            milestones=tuple(Milestone.from_dict(e) for e in raw),
            active_index=int(data.get("active_index", 0) or 0),
        )
