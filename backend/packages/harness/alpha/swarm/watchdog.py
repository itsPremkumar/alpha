"""Swarm Watchdog: Heartbeat tracking, straggler detection, and speculative backup."""

from __future__ import annotations

import time
from typing import Any

from alpha.swarm.models import SwarmPlan, TaskNodeState


class SwarmWatchdog:
    """Monitors task liveness, flags stragglers, and spawns speculative backup workers."""

    @classmethod
    def check_and_reconcile(cls, plan: SwarmPlan) -> dict[str, Any]:
        now = time.time()
        stalled: list[str] = []
        stragglers: list[str] = []
        speculative_spawned: list[str] = []

        completed_durations = [t.duration_seconds for t in plan.tasks.values() if t.state == TaskNodeState.COMPLETED and t.duration_seconds > 0]
        avg_duration = (sum(completed_durations) / len(completed_durations)) if completed_durations else 15.0

        for tid, task in plan.tasks.items():
            if task.state not in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                continue

            # 1. Lease expiration
            if task.lease_expires_at and now > task.lease_expires_at:
                stalled.append(tid)
                # Fail or requeue
                if task.attempts < task.max_attempts:
                    task.state = TaskNodeState.PENDING
                    task.lease_expires_at = None
                else:
                    task.state = TaskNodeState.FAILED
                continue

            # 2. Straggler detection (running > 2.5x avg duration of peers)
            elapsed = (now - task.started_at) if task.started_at else 0.0
            if elapsed > (avg_duration * 2.5):
                task.state = TaskNodeState.STRAGGLING
                stragglers.append(tid)

                # 3. Speculative backup launch
                if not task.backup_worker_launched:
                    task.backup_worker_launched = True
                    # Extend lease for the backup worker
                    task.lease_expires_at = now + 45.0
                    speculative_spawned.append(tid)

        return {
            "stalled_tasks": stalled,
            "stragglers": stragglers,
            "speculative_backups_spawned": speculative_spawned,
        }
