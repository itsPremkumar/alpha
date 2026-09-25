"""Lease expiry, straggler detection, and bounded speculative backup policy."""

from __future__ import annotations

import time
from typing import Any

from alpha.swarm.models import SwarmPlan, TaskNodeState


class SwarmWatchdog:
    """Reconciles task liveness without pretending a backup succeeded."""

    @classmethod
    def check_and_reconcile(
        cls,
        plan: SwarmPlan,
        *,
        now: float | None = None,
        lease_seconds: float = 60.0,
        retry_backoff_seconds: float = 0.0,
    ) -> dict[str, Any]:
        now = float(now if now is not None else time.time())
        stalled: list[str] = []
        retried: list[str] = []
        failed: list[str] = []
        stragglers: list[str] = []
        speculative_spawned: list[str] = []
        completed_durations = [task.duration_seconds for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED and task.duration_seconds > 0]
        avg_duration = (sum(completed_durations) / len(completed_durations)) if completed_durations else 15.0

        for task in plan.tasks.values():
            if task.state not in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                continue

            if task.lease_expires_at is not None and now > task.lease_expires_at:
                stalled.append(task.task_id)
                task.error_message = f"lease expired at {task.lease_expires_at:.3f}"
                task.lease_id = None
                task.lease_owner = None
                task.lease_expires_at = None
                if task.attempts < task.max_attempts:
                    task.state = TaskNodeState.PENDING
                    task.next_attempt_at = now + max(0.0, float(retry_backoff_seconds))
                    retried.append(task.task_id)
                else:
                    task.state = TaskNodeState.FAILED
                    task.completed_at = now
                    task.next_attempt_at = None
                    failed.append(task.task_id)
                continue

            elapsed = max(0.0, now - task.started_at) if task.started_at else 0.0
            if elapsed > (avg_duration * 2.5):
                task.state = TaskNodeState.STRAGGLING
                stragglers.append(task.task_id)
                if not task.backup_worker_launched:
                    task.backup_worker_launched = True
                    # Keep the original lease alive for the race.  The backup
                    # carries the same task id; the first terminal result wins.
                    task.lease_expires_at = now + max(1.0, float(lease_seconds))
                    speculative_spawned.append(task.task_id)

        return {
            "stalled_tasks": stalled,
            "retried_tasks": retried,
            "failed_tasks": failed,
            "stragglers": stragglers,
            "speculative_backups_spawned": speculative_spawned,
            "average_completed_duration_seconds": avg_duration,
        }
