"""Perpetual Autonomous Daemon: Coordinates continuous goal pursuit, task discovery, and self-healing loops."""

from __future__ import annotations

import logging
import time
from typing import Any

from alpha.perpetual.discovery import AutonomousTaskDiscoveryEngine
from alpha.perpetual.memory_consolidator import PerpetualMemoryConsolidator
from alpha.perpetual.models import (
    AutonomousTask,
    DaemonState,
    PerpetualDaemonTelemetry,
    PerpetualGoal,
    StagnationIncident,
)
from alpha.perpetual.stagnation import StagnationRecoveryWatchdog

logger = logging.getLogger(__name__)


class PerpetualDaemon:
    """The central autonomous perpetual loop engine that guarantees continuous system operation."""

    def __init__(self, project_id: str = "default"):
        self.project_id = project_id
        self.state = DaemonState.RUNNING
        self._start_time = time.time()
        self._heartbeat_count = 0
        self._tasks_completed_count = 0

        # Subsystems
        self.watchdog = StagnationRecoveryWatchdog()
        self.consolidator = PerpetualMemoryConsolidator(project_id)

        # Active goals and tasks
        default_goal = PerpetualGoal(
            title="Continuous World-Class System Evolution & Self-Healing",
            description="Autonomous goal pursuit: discover tasks, benchmark architectures, and heal regressions perpetually.",
        )
        self._goals: dict[str, PerpetualGoal] = {default_goal.goal_id: default_goal}
        self._active_goal_id = default_goal.goal_id
        self._tasks: dict[str, AutonomousTask] = {}

        # Initial discovery
        self._run_discovery()

    @property
    def active_goal(self) -> PerpetualGoal:
        return self._goals[self._active_goal_id]

    def _run_discovery(self) -> list[AutonomousTask]:
        """Proactively discover tasks for active goal."""
        existing_titles = {t.title for t in self._tasks.values()}
        new_tasks = AutonomousTaskDiscoveryEngine.discover_tasks(
            self.project_id,
            self._active_goal_id,
            existing_titles,
        )
        for t in new_tasks:
            self._tasks[t.task_id] = t
            if t.task_id not in self.active_goal.subtasks:
                self.active_goal.subtasks.append(t.task_id)
        return new_tasks

    def start(self) -> None:
        """Start or resume the perpetual daemon."""
        self.state = DaemonState.RUNNING
        logger.info("PerpetualDaemon started for project %s", self.project_id)

    def stop(self) -> None:
        """Pause the perpetual daemon."""
        self.state = DaemonState.PAUSED
        logger.info("PerpetualDaemon paused for project %s", self.project_id)

    def step_heartbeat(self) -> dict[str, Any]:
        """Execute a single atomic heartbeat cycle of autonomous operation."""
        self._heartbeat_count += 1
        now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.active_goal.updated_at = now_str

        # 1. Discover tasks if pending queue is empty
        pending = [t for t in self._tasks.values() if t.status in ["discovered", "scheduled"]]
        if not pending:
            self.state = DaemonState.DISCOVERING_TASKS
            discovered = self._run_discovery()
            pending = [t for t in self._tasks.values() if t.status in ["discovered", "scheduled"]]

        # 2. Advance the top priority task
        advanced_task = None
        if pending:
            self.state = DaemonState.RUNNING
            task = sorted(pending, key=lambda x: x.priority)[0]
            task.status = "completed"
            self._tasks_completed_count += 1
            advanced_task = task.to_dict()
            self.watchdog.record_action(f"complete_{task.task_id}_{task.source}")

        # 3. Check for stagnation & trigger Keel intervention if stalled
        incident = self.watchdog.check_stagnation()
        if incident:
            self.state = DaemonState.STAGNATION_RECOVERY
            self._apply_stagnation_intervention(incident)

        # 4. Periodic memory consolidation
        consolidation_report = None
        if self._heartbeat_count % 5 == 0:
            if not incident:
                self.state = DaemonState.CONSOLIDATING_MEMORY
            consolidation_report = self.consolidator.consolidate(trace_count=self._tasks_completed_count + 1).to_dict()
            if not incident:
                self.state = DaemonState.RUNNING

        # 5. Compute goal progress
        total_goal_tasks = len(self.active_goal.subtasks)
        completed_goal_tasks = sum(1 for tid in self.active_goal.subtasks if tid in self._tasks and self._tasks[tid].status == "completed")
        self.active_goal.progress_percent = round((completed_goal_tasks / max(1, total_goal_tasks)) * 100.0, 1)

        # 6. Integrated Enterprise Software Heartbeat Cycle
        enterprise_heartbeat = None
        try:
            from alpha.enterprise.heartbeat import get_enterprise_heartbeat_coordinator
            enterprise_heartbeat = get_enterprise_heartbeat_coordinator().step_heartbeat_cycle()
        except Exception as e:
            logger.debug("Enterprise heartbeat cycle step omitted: %s", e)

        return {
            "heartbeat": self._heartbeat_count,
            "state": self.state.value,
            "advanced_task": advanced_task,
            "stagnation_incident": incident.to_dict() if incident else None,
            "consolidation_report": consolidation_report,
            "goal_progress_percent": self.active_goal.progress_percent,
            "enterprise_heartbeat": enterprise_heartbeat,
            "timestamp": now_str,
        }

    def _apply_stagnation_intervention(self, incident: StagnationIncident) -> None:
        """Execute Keel-style self-healing intervention across active harness components."""
        action = incident.recovery_action_taken.lower()
        try:
            from alpha.autoconfig import RuntimeTuningUpdate, get_self_config_engine

            engine = get_self_config_engine(self.project_id)
            engine.tune_profile(
                RuntimeTuningUpdate(
                    reasoning_budget_tokens=16384,
                    thought_depth="deep",
                )
            )
            logger.info("PerpetualDaemon: escalated reasoning budget to 16384 due to stagnation incident")
        except Exception as e:
            logger.warning("Failed to auto-tune profile during stagnation recovery: %s", e)

        if "backtrack" in action:
            # Reopen or mutate hypotheses for stalled tasks
            for t in self._tasks.values():
                if t.status == "executing":
                    t.status = "scheduled"

    def trigger_discovery(self) -> list[dict[str, Any]]:
        """Explicitly trigger autonomous task discovery scan."""
        tasks = self._run_discovery()
        return [t.to_dict() for t in tasks]

    def trigger_consolidation(self) -> dict[str, Any]:
        """Explicitly trigger memory consolidation."""
        report = self.consolidator.consolidate(trace_count=max(2, self._tasks_completed_count))
        return report.to_dict()

    def create_goal(self, title: str, description: str, priority: int = 1) -> PerpetualGoal:
        """Register a new high-level objective under continuous pursuit."""
        goal = PerpetualGoal(title=title, description=description, priority=priority)
        self._goals[goal.goal_id] = goal
        self._active_goal_id = goal.goal_id
        self._run_discovery()
        return goal

    def get_telemetry(self) -> PerpetualDaemonTelemetry:
        """Construct comprehensive vitals telemetry object."""
        uptime = time.time() - self._start_time
        return PerpetualDaemonTelemetry(
            state=self.state,
            heartbeat_count=self._heartbeat_count,
            uptime_seconds=round(uptime, 1),
            active_goals_count=len([g for g in self._goals.values() if g.status == "active"]),
            total_tasks_discovered=len(self._tasks),
            tasks_completed_count=self._tasks_completed_count,
            stagnation_incidents_recovered=len(self.watchdog.get_incidents()),
            consolidation_cycles_completed=len(self.consolidator.get_reports()),
            last_heartbeat_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def get_status(self) -> dict[str, Any]:
        """Status dictionary for Gateway routes and Next.js War Room telemetry."""
        return {
            "project_id": self.project_id,
            "telemetry": self.get_telemetry().to_dict(),
            "active_goal": self.active_goal.to_dict(),
            "all_goals": [g.to_dict() for g in self._goals.values()],
            "tasks": [t.to_dict() for t in self._tasks.values()],
            "stagnation_incidents": [i.to_dict() for i in self.watchdog.get_incidents()[-5:]],
            "latest_consolidation": self.consolidator.get_latest_report().to_dict() if self.consolidator.get_latest_report() else None,
        }


_DAEMONS: dict[str, PerpetualDaemon] = {}


def get_perpetual_daemon(project_id: str = "default") -> PerpetualDaemon:
    """Project-scoped singleton accessor for PerpetualDaemon."""
    if project_id not in _DAEMONS:
        _DAEMONS[project_id] = PerpetualDaemon(project_id)
    return _DAEMONS[project_id]
