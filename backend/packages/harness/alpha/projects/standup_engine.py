"""Multi-Agent Async Standup & Blocker Detection Engine.

Aggregates asynchronous coordination state across active bot memberships,
file locks, task contracts, and event streams. Identifies deadlocks, dependency
bottlenecks, and stagnant tasks to generate actionable team standup briefings.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from alpha.projects.contracts import get_contract_gatekeeper
from alpha.projects.events import get_event_bus
from alpha.projects.locks import get_lock_manager
from alpha.projects.membership import get_membership_store

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class StagnantTaskAlert:
    """Warning for tasks with zero forward progress over an extended threshold."""

    task_id: str
    assignee_bot: str
    minutes_inactive: int
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StandupReport:
    """Consolidated asynchronous team standup briefing."""

    project_id: str
    timestamp: str
    active_bots: list[str] = field(default_factory=list)
    completed_tasks: list[dict[str, Any]] = field(default_factory=list)
    in_progress_tasks: list[dict[str, Any]] = field(default_factory=list)
    active_locks: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    stagnant_alerts: list[StagnantTaskAlert] = field(default_factory=list)
    executive_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stagnant_alerts"] = [a.to_dict() for a in self.stagnant_alerts]
        return data

    def render_markdown(self) -> str:
        """Render standup briefing in human-readable Markdown format."""
        lines = [
            f"# Async Standup Briefing - Project `{self.project_id}`",
            f"\n> **Generated At**: {self.timestamp} | **Active Agents**: {len(self.active_bots)}",
            f"\n### Executive Summary\n{self.executive_summary}",
            "\n## 1. Completed Deliverables",
        ]

        if self.completed_tasks:
            for t in self.completed_tasks:
                lines.append(f"- [x] **{t['task_id']}**: {t['title']} (Delivered by @{t['assignee_bot']})")
        else:
            lines.append("- _No tasks completed in this reporting cycle._")

        lines.append("\n## 2. In Progress Work & Active Worktrees")
        if self.in_progress_tasks:
            for t in self.in_progress_tasks:
                lines.append(f"- [ ] **{t['task_id']}**: {t['title']} (@{t['assignee_bot']})")
        else:
            lines.append("- _No tasks currently in progress._")

        lines.append("\n## 3. Active Resource Locks")
        if self.active_locks:
            for l in self.active_locks:
                lines.append(f"- 🔒 `{l['scope']}:{l['path']}` held by @{l['owner_bot']}")
        else:
            lines.append("- _No active resource locks._")

        lines.append("\n## 4. Blockers & Stagnation Alerts")
        if self.blockers or self.stagnant_alerts:
            for b in self.blockers:
                lines.append(f"- ⚠️ **Blocker**: {b}")
            for s in self.stagnant_alerts:
                lines.append(f"- ⏳ **Stagnant Task {s.task_id}** (@{s.assignee_bot}): Inactive for {s.minutes_inactive}m. _{s.recommendation}_")
        else:
            lines.append("- ✅ _Zero blockers detected; team velocity is nominal._")

        return "\n".join(lines) + "\n"


class StandupEngine:
    """Orchestrates asynchronous standup briefings and deadlock diagnostics."""

    def __init__(self, project_id: str, gatekeeper: Any = None):
        self.project_id = project_id
        self._gk = gatekeeper
        self._lock = threading.Lock()

    def generate_standup(self, *, stagnation_threshold_minutes: int = 60, gatekeeper: Any = None) -> StandupReport:
        """Assemble a multi-agent standup briefing from live project subsystems."""
        with self._lock:
            # 1. Active Bots
            members = get_membership_store().presence(self.project_id)
            active_bot_names = [m.bot_name for m in members if m.status == "active"] or ["architect", "coder", "reviewer"]

            # 2. Contracts State
            gk = gatekeeper or self._gk or get_contract_gatekeeper(self.project_id)
            contracts = gk.list_contracts()
            completed = [c.to_dict() for c in contracts if c.status == "done"]
            in_progress = [c.to_dict() for c in contracts if c.status in ("in_progress", "under_review")]

            # 3. Active Locks & Pending Requests
            lock_mgr = get_lock_manager()
            locks = [l.to_dict() for l in lock_mgr.list_locks(self.project_id)]
            pending_requests = lock_mgr.list_requests(self.project_id, pending_only=True)

            blockers: list[str] = []
            for req in pending_requests:
                blockers.append(
                    f"@{req.requester_bot} blocked waiting for lock on {req.scope}:{req.path}"
                )

            # 4. Stagnation Detection
            stagnant_alerts: list[StagnantTaskAlert] = []
            now_dt = datetime.now(UTC)
            for c in contracts:
                if c.status == "in_progress":
                    try:
                        up_dt = datetime.fromisoformat(c.updated_at)
                        elapsed_mins = int((now_dt - up_dt).total_seconds() / 60)
                        if elapsed_mins >= stagnation_threshold_minutes:
                            stagnant_alerts.append(
                                StagnantTaskAlert(
                                    task_id=c.task_id,
                                    assignee_bot=c.assignee_bot,
                                    minutes_inactive=elapsed_mins,
                                    recommendation="Recommend reassigning to available bot or splitting into sub-tasks.",
                                )
                            )
                    except Exception:
                        pass

            # 5. Executive Summary
            summary = (
                f"Team has {len(completed)} completed tasks and {len(in_progress)} active work streams. "
                f"Detected {len(blockers)} resource lock contention(s) and {len(stagnant_alerts)} stagnant task(s)."
            )

            report = StandupReport(
                project_id=self.project_id,
                timestamp=_now(),
                active_bots=active_bot_names,
                completed_tasks=completed,
                in_progress_tasks=in_progress,
                active_locks=locks,
                blockers=blockers,
                stagnant_alerts=stagnant_alerts,
                executive_summary=summary,
            )

            get_event_bus(self.project_id).emit(
                "phase_changed",
                "system",
                {"standup_summary": summary, "completed_count": len(completed)},
            )

            return report


def get_standup_engine(project_id: str) -> StandupEngine:
    return StandupEngine(project_id)
