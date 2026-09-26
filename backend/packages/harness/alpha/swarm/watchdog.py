"""Lease expiry, straggler detection, and bounded speculative backup policy."""

from __future__ import annotations

import time
from typing import Any

from alpha.swarm.models import SwarmPlan, TaskNodeState

# The disclosed planning default for a node's duration, shared with
# ``SwarmTaskNode.estimated_seconds``.  It is the straggler threshold basis
# before the plan has produced any measured sample.
_DEFAULT_DECLARED_SECONDS = 15.0


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
        # The straggler threshold is only as trustworthy as the sample behind
        # it.  ``avg_duration`` used to be the bare mean of measured
        # ``duration_seconds``, so ONE sub-millisecond completion redefined the
        # whole plan as "fast": every concurrent sibling immediately exceeded
        # ``2.5 * avg`` and was launched a second time by the speculative-backup
        # path below.  That is a real second provider execution of a healthy
        # node (and its measured cost was then discarded when the loser of the
        # race was fenced), not a liveness hedge.
        #
        # ``SwarmTaskNode.estimated_seconds`` is the plan's own declared
        # expectation and already defaults to 15s -- the same value this
        # projection used when nothing had completed yet -- so the threshold
        # basis is the LARGER of what was measured and what was declared for
        # the same completed cohort.  A measured mean only lowers the bar once
        # the plan has actually demonstrated it, and lease expiry remains the
        # independent backstop for a genuinely hung node.
        completed = [task for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED]
        measured_durations = [task.duration_seconds for task in completed if task.duration_seconds > 0]
        measured_avg = (sum(measured_durations) / len(measured_durations)) if measured_durations else 0.0
        declared = [float(task.estimated_seconds or 0.0) for task in (completed or list(plan.tasks.values()))]
        declared_avg = (sum(declared) / len(declared)) if declared else _DEFAULT_DECLARED_SECONDS
        avg_duration = max(measured_avg, declared_avg) or _DEFAULT_DECLARED_SECONDS

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
            # The straggler threshold basis.  Disclose measured and declared
            # separately so a reader can see which one held the bar up.
            "average_completed_duration_seconds": avg_duration,
            "measured_completed_duration_seconds": measured_avg,
            "declared_duration_seconds": declared_avg,
            "straggler_threshold_seconds": avg_duration * 2.5,
        }
