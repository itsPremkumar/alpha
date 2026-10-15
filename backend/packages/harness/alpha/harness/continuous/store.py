"""Thread-safe Persistent Store for Continuous Autonomous Goals."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from alpha.harness.continuous.models import (
    LEGACY_MIGRATION_GUIDANCE,
    LEGACY_RETIREMENT_REASON,
    Goal,
    GoalStatus,
    Milestone,
    MilestoneStatus,
    VerificationCheck,
    _now,
)
from alpha.persistence.storekit.atomic import atomic_write_bytes

logger = logging.getLogger(__name__)

_DEFAULT_GOAL_DIR = ".alpha/goals"


def describe_goal(goal: Goal) -> dict[str, object]:
    """Project one legacy record into what it can honestly be said to prove.

    The single owner of that projection. ``goal_engine`` (list/inspect),
    ``/loop:status`` and ``/loop:status <id>`` all read through here, because
    three hand-written renderers is three chances for one of them to print a
    stale ``executing`` row as live execution.

    Two fields are load-bearing:

    - ``status`` is always ``"retired"``. The *recorded* status is preserved
      beside it under ``recorded_status`` for forensics, because rewriting the
      stored value would destroy the evidence of how the row got that way.
    - ``verified`` is the string ``"never"``, never a boolean. A record that
      admits no ``verification_checks`` has not failed verification either — it
      was never verified, and ``False`` would be a claim nobody measured.
    """
    return {
        "goal_id": goal.goal_id,
        "title": goal.title,
        "description": goal.description,
        "status": "retired",
        "recorded_status": goal.status,
        "retired": True,
        "retirement_reason": goal.retired_reason or LEGACY_RETIREMENT_REASON,
        "retired_at": goal.retired_at,
        "migration": LEGACY_MIGRATION_GUIDANCE,
        "verified": "never",
        "verification_checks": [check.to_dict() for milestone in goal.milestones.values() for check in milestone.verification_checks],
        "iteration": goal.iteration,
        "max_iterations": goal.max_iterations,
        "current_milestone_id": goal.current_milestone_id,
        "created_at": goal.created_at,
        "updated_at": goal.updated_at,
        "heartbeat_at": goal.heartbeat_at,
        "milestones": [
            {
                "milestone_id": milestone.milestone_id,
                "title": milestone.title,
                "status": "unverified",
                "recorded_status": milestone.status,
                "dependencies": list(milestone.dependencies),
                "attempts": milestone.attempts,
            }
            for milestone in goal.milestones.values()
        ],
        "strategy_notes": list(goal.strategy_notes[-5:]),
    }


class GoalStore:
    """Thread-safe persistent store for continuous goals and milestones."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else Path.cwd() / _DEFAULT_GOAL_DIR / "goals.json"
        self._goals: dict[str, Goal] = {}
        self._lock = threading.Lock()
        # Why an unreadable / unwritable store is surfaced rather than absorbed:
        # `goal_engine action="list"` and `/loop:status` both answer from
        # `list_goals()`. With the parse error swallowed, a corrupt goals.json
        # answered "No active autonomous goals." — a model told there is no work
        # when the truth is that its work cannot be read. The same held for
        # writes: a disk-full save returned a Goal as if persisted, and
        # `/loop:resume` then reported success on state that never landed.
        self.load_error: str | None = None
        self.save_error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            self.load_error = None
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("goals", []):
                goal = Goal.from_dict(item)
                self._goals[goal.goal_id.lower()] = goal
        except Exception as exc:
            # A corrupt store must not read as an empty one. `goal_engine` and
            # the `/loop:*` commands both answer "no goals" from this dict, so
            # swallowing here told the model there was nothing to work on when
            # the truth was that state could not be read at all.
            self.load_error = str(exc)
            logger.warning("Continuous goal store unreadable; goals unavailable: %s", self.storage_path, exc_info=True)
            return
        self.load_error = None

    @property
    def is_degraded(self) -> bool:
        """Whether the on-disk store could not be read.

        Distinct from "there are no goals": the two lead to opposite decisions,
        so a caller that checks must not infer one from the other.
        """
        return self.load_error is not None

    @property
    def is_durable(self) -> bool:
        """Whether the most recent save actually reached disk.

        Read this after a mutating call before reporting success. A `Goal`
        handed back by `create_goal` / `update_goal_status` exists in memory
        either way; only this says whether it survives a restart.
        """
        return self.save_error is None

    def _save(self) -> bool:
        """Persist the store. Returns whether the write actually landed.

        The return value is the point: every caller used to hand back an
        in-memory object as though it were durable, so a full disk reported a
        successful resume and the work was gone on restart.
        """
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "goals": [g.to_dict() for g in self._goals.values()],
                "updated_at": _now(),
            }
            payload = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
            try:
                atomic_write_bytes(
                    self.storage_path,
                    payload.encode("utf-8"),
                    fsync="file_and_directory",
                )
            except Exception:
                # `atomic_write_bytes` already logs and leaves the previous
                # target intact; surface it so a caller that checks
                # `is_durable` answers honestly.
                self.save_error = "goal store save failed; goals are NOT durable"
                logger.exception("Continuous goal store save failed")
                return False
        except Exception as exc:
            logger.error("Continuous goal store save failed; goals are NOT durable: %s", self.storage_path, exc_info=True)
            self.save_error = str(exc)
            return False
        self.save_error = None
        return True

    def create_goal(
        self,
        title: str,
        description: str = "",
        max_iterations: int = 100,
        goal_id: str | None = None,
    ) -> Goal:
        gid = (goal_id or f"goal_{uuid4().hex[:8]}").lower()
        goal = Goal(
            goal_id=gid,
            title=title,
            description=description,
            max_iterations=max_iterations,
        )
        with self._lock:
            self._goals[gid] = goal
            self._save()
        return goal

    def get_goal(self, goal_id: str) -> Goal | None:
        with self._lock:
            return self._goals.get(goal_id.lower().strip())

    def list_goals(self) -> list[Goal]:
        with self._lock:
            return list(self._goals.values())

    def add_milestone(
        self,
        goal_id: str,
        title: str,
        description: str = "",
        dependencies: Sequence[str] | None = None,
    ) -> Milestone | None:
        goal = self.get_goal(goal_id)
        if not goal:
            return None

        mid = f"ms_{uuid4().hex[:6]}"
        milestone = Milestone(
            milestone_id=mid,
            goal_id=goal.goal_id,
            title=title,
            description=description,
            dependencies=list(dependencies or []),
        )
        with self._lock:
            goal.milestones[mid] = milestone
            goal.updated_at = _now()
            self._save()
        return milestone

    def update_milestone_status(
        self,
        goal_id: str,
        milestone_id: str,
        status: MilestoneStatus,
        evidence: str = "",
        error: str | None = None,
    ) -> Milestone | None:
        goal = self.get_goal(goal_id)
        if not goal or milestone_id not in goal.milestones:
            return None

        with self._lock:
            ms = goal.milestones[milestone_id]
            ms.status = status
            ms.updated_at = _now()
            if error:
                ms.last_error = error
            if evidence:
                check = VerificationCheck(
                    check_id=f"chk_{uuid4().hex[:6]}",
                    description="Verification acceptance check",
                    passed=(status == "verified"),
                    evidence=evidence,
                    checked_at=_now(),
                )
                ms.verification_checks.append(check)
            self._save()
            return ms

    def update_goal_status(
        self,
        goal_id: str,
        status: GoalStatus,
        strategy_note: str = "",
    ) -> Goal | None:
        goal = self.get_goal(goal_id)
        if not goal:
            return None

        with self._lock:
            goal.status = status
            goal.updated_at = _now()
            goal.heartbeat_at = _now()
            if strategy_note:
                goal.strategy_notes.append(f"[{_now()[:19]}] {strategy_note}")
            self._save()
            return goal

    def retire_all(self) -> dict[str, object]:
        """Stamp every stored record retired, in one additive write.

        This is a **label**, never a deletion: rows, milestones and strategy
        notes are all preserved, and the stamp only says "this record is not
        evidence of work". It exists because an operator who wants the on-disk
        file to stop looking like a fleet of running loops should be able to say
        so explicitly instead of hand-editing JSON.

        The read path retires records whether or not this ran
        (:func:`describe_goal`), so calling it is a labelling convenience, not a
        precondition for honesty. A degraded store is refused rather than
        rewritten from a partial in-memory view, which is the one outcome that
        *would* lose data.
        """
        if self.is_degraded:
            return {
                "retired": 0,
                "total": 0,
                "durable": False,
                "error": f"goal store at {self.storage_path} could not be read ({self.load_error}); nothing was rewritten",
            }
        with self._lock:
            stamped_at = _now()
            retired = 0
            for goal in self._goals.values():
                if not goal.retired:
                    goal.retired = True
                    goal.retired_at = stamped_at
                    goal.retired_reason = LEGACY_RETIREMENT_REASON
                    retired += 1
            landed = self._save()
            total = len(self._goals)
        return {
            "retired": retired,
            "total": total,
            "durable": landed,
            "error": None if landed else self.save_error,
        }


_global_goal_store = GoalStore()


def get_goal_store() -> GoalStore:
    return _global_goal_store
