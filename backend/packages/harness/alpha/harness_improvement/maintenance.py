"""Whole-library maintenance for the experience store (guide §16 Phase 7).

Lessons are written one run at a time and superseded one pair at a time, so
the library accumulates exactly what that granularity cannot see: duplicate
lessons recorded against the same component, independent entries that
contradict each other, and entries whose ``expires_at`` has passed while their
stored metadata still says ``active``. Retrieval compensates by computing
staleness on the fly (``is_stale``); this pass is the store confronting its own
state instead of re-deriving it at every read.

Three rules keep it safe to run against a library someone still uses:

* **Analysis never mutates.** :func:`analyze_experiences` is pure and
  deterministic — the same entries with the same clock produce byte-identical
  plans, so a report means the same thing on a second run. With ``apply=False``
  the store is not written at all.
* **Nothing is deleted, only marked.** A duplicate becomes ``SUPERSEDED`` with
  a pointer to its keeper through the same pair-wise
  :func:`~alpha.harness_improvement.experiences.supersede_experience` write the
  reflection stage uses; an expired entry becomes ``STALE`` carrying a
  ``maintenance_notes`` entry naming its expiry. Both remain on disk and
  retrievable by id.
* **No evidence, no action.** Terminal records (``SUPERSEDED``/``RETRACTED``)
  are never rewritten; a duplicate group whose *newest* member is not current
  takes no action, because electing an older lesson over it is a human
  decision; entries that name no components are grouped by nothing; and
  without a clock nothing expires. Conflicts — a benefit and a regression
  recorded against the same component — are reported, never resolved: the
  information "it helped one task and hurt another" needs a human, not a
  tiebreak this module cannot justify.

The pass follows the store's single-process durability discipline: the caller
serializes it, and every write is the existing small atomic metadata write.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from alpha.harness_improvement.experiences import (
    ExperienceMetadata,
    ExperienceStatus,
    load_experience,
    save_experience,
    supersede_experience,
)
from alpha.harness_improvement.store import HarnessImprovementStore, utc_now

_CURRENT_STATUSES: tuple[ExperienceStatus, ...] = (ExperienceStatus.PROPOSED, ExperienceStatus.ACTIVE)
_GROUPABLE_STATUSES: tuple[ExperienceStatus, ...] = (ExperienceStatus.PROPOSED, ExperienceStatus.ACTIVE, ExperienceStatus.STALE)
_TERMINAL_STATUSES: tuple[ExperienceStatus, ...] = (ExperienceStatus.SUPERSEDED, ExperienceStatus.RETRACTED)


@dataclass(frozen=True, slots=True)
class MaintenanceAction:
    """One proposed change to one entry — never the record itself."""

    action: str  # "supersede" | "stale"
    experience_id: str
    reason: str
    target_id: str | None = None  # the keeper an entry is superseded by

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "experience_id": self.experience_id, "target_id": self.target_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class MaintenancePlan:
    """What a whole pass would do, and what it explicitly leaves alone."""

    reviewed: int
    actions: tuple[MaintenanceAction, ...] = ()
    duplicate_groups: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()
    current: tuple[str, ...] = ()  # untouched ids, sorted

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewed": self.reviewed,
            "actions": [row.to_dict() for row in self.actions],
            "duplicate_groups": [dict(row) for row in self.duplicate_groups],
            "conflicts": [dict(row) for row in self.conflicts],
            "current": list(self.current),
        }


def analyze_experiences(entries: Sequence[ExperienceMetadata], *, now: str | None = None) -> MaintenancePlan:
    """Compute the maintenance plan for a library snapshot.

    ``now=None`` disables expiry marking (no clock, no expiry decision); a
    caller that wants the expired entries acted on passes ``utc_now()``.
    """
    entries = list(entries)

    supersede_actions: list[MaintenanceAction] = []
    superseded_ids: set[str] = set()
    duplicate_rows: list[dict[str, Any]] = []
    groups: dict[tuple[str, tuple[str, ...]], list[ExperienceMetadata]] = {}
    for entry in entries:
        if entry.status not in _GROUPABLE_STATUSES or not entry.component_ids:
            continue
        key = (entry.finding, tuple(sorted(entry.component_ids)))
        groups.setdefault(key, []).append(entry)
    for (finding, components), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda e: (e.created_at, e.experience_id))
        newest = ordered[-1]
        if newest.status not in _CURRENT_STATUSES:
            duplicate_rows.append(
                {
                    "finding": finding,
                    "component_ids": list(components),
                    "kept_id": None,
                    "superseded_ids": [],
                    "note": "the newest entry of the group is not current (stale, superseded or retracted); electing an older lesson needs human review",
                }
            )
            continue
        defeated = tuple(entry for entry in ordered if entry.experience_id != newest.experience_id)
        duplicate_rows.append(
            {
                "finding": finding,
                "component_ids": list(components),
                "kept_id": newest.experience_id,
                "superseded_ids": [row.experience_id for row in defeated],
                "note": "",
            }
        )
        for entry in defeated:
            supersede_actions.append(
                MaintenanceAction(
                    action="supersede",
                    experience_id=entry.experience_id,
                    reason=f"duplicate {finding} lesson for {', '.join(components)}: newest entry {newest.experience_id} kept",
                    target_id=newest.experience_id,
                )
            )
            superseded_ids.add(entry.experience_id)

    stale_actions: list[MaintenanceAction] = []
    if now is not None:
        for entry in entries:
            if entry.status in _CURRENT_STATUSES and entry.expires_at and entry.expires_at <= now:
                if entry.experience_id in superseded_ids:
                    continue  # expiry is the weaker label; SUPERSEDED already implies staleness
                stale_actions.append(MaintenanceAction(action="stale", experience_id=entry.experience_id, reason=f"expired at {entry.expires_at}"))

    actions = sorted(
        (*supersede_actions, *stale_actions),
        key=lambda row: (row.experience_id, row.action),
    )
    actioned_ids = {row.experience_id for row in actions}

    conflicts: list[dict[str, Any]] = []
    candidates = [entry for entry in entries if entry.status in _CURRENT_STATUSES and entry.experience_id not in superseded_ids]
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            shared = sorted(set(left.component_ids) & set(right.component_ids))
            if shared and left.finding != right.finding:
                first, second = sorted((left, right), key=lambda e: e.experience_id)
                conflicts.append(
                    {
                        "left_id": first.experience_id,
                        "right_id": second.experience_id,
                        "component_ids": shared,
                        "reason": f"opposing findings recorded for {', '.join(shared)}: {first.finding} vs {second.finding}",
                    }
                )
    conflicts.sort(key=lambda row: (row["left_id"], row["right_id"]))
    return MaintenancePlan(
        reviewed=len(entries),
        actions=tuple(actions),
        duplicate_groups=tuple(duplicate_rows),
        conflicts=tuple(conflicts),
        current=tuple(sorted(entry.experience_id for entry in entries if entry.experience_id not in actioned_ids)),
    )


def apply_maintenance(store: HarnessImprovementStore, plan: MaintenancePlan, *, now: str | None = None) -> list[dict[str, Any]]:
    """Perform the plan's actions and report each one, refusals included.

    A supersede runs through :func:`supersede_experience`, so a target that
    vanished between analysis and apply is refused with the helper's own words
    instead of raising; a stale-mark re-reads and re-saves the entry atomically.
    """
    rows: list[dict[str, Any]] = []
    for action in plan.actions:
        if action.action == "supersede":
            if not action.target_id:
                rows.append({"experience_id": action.experience_id, "action": action.action, "status": "refused", "detail": "a supersede action carries no target"})
                continue
            changed, detail = supersede_experience(store, old_id=action.experience_id, new_id=action.target_id)
            rows.append(
                {
                    "experience_id": action.experience_id,
                    "action": action.action,
                    "status": "applied" if changed else "refused",
                    "detail": detail,
                }
            )
        elif action.action == "stale":
            entry = load_experience(store, action.experience_id)
            if entry is None:
                rows.append({"experience_id": action.experience_id, "action": action.action, "status": "refused", "detail": f"experience {action.experience_id!r} does not exist"})
                continue
            stamp = now or utc_now()
            entry.status = ExperienceStatus.STALE
            entry.last_reviewed_at = stamp
            entry.maintenance_notes = (*entry.maintenance_notes, f"{stamp} marked stale by library maintenance: {action.reason}")
            save_experience(store, entry)
            rows.append({"experience_id": action.experience_id, "action": action.action, "status": "applied", "detail": action.reason})
        else:
            rows.append({"experience_id": action.experience_id, "action": action.action, "status": "refused", "detail": f"unsupported maintenance action {action.action!r}"})
    return rows


def run_experience_maintenance(store: HarnessImprovementStore, *, apply: bool = False, now: str | None = None) -> dict[str, Any]:
    """List the library, plan, optionally apply, and report everything seen.

    ``unreadable`` carries the store's own rows for metadata files nobody could
    parse: those entries are not reviewed, not acted on, and not silently
    dropped. An absent store is an empty pass, not an error.
    """
    listing = store.list_experiences()
    plan = analyze_experiences([ExperienceMetadata.from_dict(row) for row in listing.items], now=now)
    applied = apply_maintenance(store, plan, now=now) if apply else []
    return {
        "generated_at": utc_now(),
        "apply": apply,
        "reviewed": plan.reviewed,
        "unreadable": list(listing.unreadable),
        "plan": plan.to_dict(),
        "applied": applied,
    }
