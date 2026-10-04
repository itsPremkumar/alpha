"""Thread-safe Persistent Store for Continuous Autonomous Goals."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from alpha.harness.continuous.models import (
    Goal,
    GoalStatus,
    Milestone,
    MilestoneStatus,
    VerificationCheck,
    _now,
)

logger = logging.getLogger(__name__)

_DEFAULT_GOAL_DIR = ".alpha/goals"


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
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(self.storage_path)
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


_global_goal_store = GoalStore()


def get_goal_store() -> GoalStore:
    return _global_goal_store
