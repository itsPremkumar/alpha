"""Thread-safe Persistent Store for Continuous Autonomous Goals."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from agent_workspace.harness.continuous.models import (
    Goal,
    GoalStatus,
    Milestone,
    MilestoneStatus,
    VerificationCheck,
    _now,
)

logger = logging.getLogger(__name__)

_DEFAULT_GOAL_DIR = ".deerflow/goals"


class GoalStore:
    """Thread-safe persistent store for continuous goals and milestones."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = (
            Path(storage_path).resolve()
            if storage_path
            else Path.cwd() / _DEFAULT_GOAL_DIR / "goals.json"
        )
        self._goals: dict[str, Goal] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("goals", []):
                goal = Goal.from_dict(item)
                self._goals[goal.goal_id.lower()] = goal
        except Exception:
            pass

    def _save(self) -> None:
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
        except Exception:
            pass

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
