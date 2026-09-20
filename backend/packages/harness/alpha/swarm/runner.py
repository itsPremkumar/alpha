"""Autonomous Async Swarm Runner.

Manages background non-blocking execution of Swarm DAGs,
concurrency control via asyncio semaphores, real-time watchdog checks,
worker dispatching, and automated fan-in aggregation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.models import SwarmPlan, TaskNodeState
from alpha.swarm.scheduler import SwarmScheduler
from alpha.swarm.watchdog import SwarmWatchdog
from alpha.swarm.worker import (
    CodingWorktreeWorker,
    EphemeralSubagentWorker,
    HermesBotWorker,
    SpecialistBotWorker,
)

logger = logging.getLogger(__name__)


class AsyncSwarmRunner:
    """Asynchronous background execution engine for SwarmPlans."""

    def __init__(self, coordinator: Any, poll_interval: float = 0.2):
        self.coordinator = coordinator
        self.poll_interval = poll_interval
        self._active_tasks: dict[str, asyncio.Task] = {}

    def is_running(self, swarm_id: str) -> bool:
        task = self._active_tasks.get(swarm_id)
        return task is not None and not task.done()

    async def run_swarm_async(self, swarm_id: str) -> dict[str, Any]:
        """Runs the swarm DAG to completion in the background."""
        plan: SwarmPlan | None = self.coordinator.get_swarm(swarm_id)
        if not plan:
            return {"status": "not_found", "error": f"Swarm '{swarm_id}' not found."}

        plan.status = "running"
        self.coordinator.checkpoint(swarm_id)
        self.coordinator.append_event(swarm_id, "SWARM_STARTED")

        semaphore = asyncio.Semaphore(plan.max_concurrency)
        scheduler = SwarmScheduler(plan)

        async def execute_node(task_node) -> None:
            async with semaphore:
                # Select worker
                if task_node.worker_type == "permanent_bot" and task_node.assigned_worker:
                    worker = SpecialistBotWorker(task_node.assigned_worker)
                elif task_node.worktree_path:
                    worker = CodingWorktreeWorker(repo_root=".", branch_name=f"wt-{task_node.task_id}")
                else:
                    worker = EphemeralSubagentWorker(task_node.task_id, model=task_node.model_override)

                try:
                    # Offload execution to thread or coroutine
                    outcome = await asyncio.to_thread(worker.execute_task, task_node, plan)
                    scheduler.mark_completed(
                        task_node.task_id,
                        result_summary=outcome.get("summary", "Completed successfully."),
                        evidence=outcome.get("evidence"),
                        output_artifacts=outcome.get("artifacts"),
                    )
                    self.coordinator.append_event(
                        swarm_id,
                        "TASK_COMPLETED",
                        task_id=task_node.task_id,
                        worker=task_node.assigned_worker or "ephemeral-worker",
                        details={"summary": outcome.get("summary")},
                    )
                except Exception as exc:
                    logger.error(f"Error executing task {task_node.task_id} in swarm {swarm_id}: {exc}")
                    scheduler.mark_failed(task_node.task_id, str(exc))
                    self.coordinator.append_event(
                        swarm_id,
                        "TASK_FAILED",
                        task_id=task_node.task_id,
                        details={"error": str(exc)},
                    )
                finally:
                    self.coordinator.checkpoint(swarm_id)

        # Main DAG execution loop
        while plan.status == "running":
            if scheduler.is_swarm_finished():
                break

            # 1. Check watchdog for stragglers or stalled leases
            watchdog_report = SwarmWatchdog.check_and_reconcile(plan)
            for tid in watchdog_report.get("speculative_backups_spawned", []):
                self.coordinator.append_event(swarm_id, "SPECULATIVE_BACKUP_LAUNCHED", task_id=tid)

            # 2. Dispatch ready tasks
            ready_tasks = scheduler.dispatch_ready_tasks()
            if ready_tasks:
                for t in ready_tasks:
                    self.coordinator.append_event(
                        swarm_id,
                        "TASK_DISPATCHED",
                        task_id=t.task_id,
                        worker=t.assigned_worker or "ephemeral-worker",
                    )
                # Run dispatched wave concurrently
                await asyncio.gather(*(execute_node(t) for t in ready_tasks))

            if scheduler.is_swarm_finished():
                break

            # If no tasks are ready and no tasks running, deadlock or complete
            running_count = sum(1 for t in plan.tasks.values() if t.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING))
            if not ready_tasks and running_count == 0:
                break

            await asyncio.sleep(self.poll_interval)

        # 3. Aggregation & Quality Gate Verification
        if plan.status == "running" and scheduler.is_swarm_finished():
            plan.status = "aggregating"
            self.coordinator.append_event(swarm_id, "SWARM_AGGREGATING")
            agg_result = SwarmAggregator.aggregate(plan)
            plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.coordinator.append_event(
                swarm_id,
                "SWARM_COMPLETED" if plan.status == "completed" else "SWARM_PARTIAL_SUCCESS",
                details=agg_result,
            )
            self.coordinator.checkpoint(swarm_id)
            return {"status": plan.status, "aggregated": True, "result": agg_result}

        self.coordinator.checkpoint(swarm_id)
        return {"status": plan.status, "tasks_completed": sum(1 for t in plan.tasks.values() if t.state == TaskNodeState.COMPLETED)}

    def start_background_swarm(self, swarm_id: str) -> asyncio.Task:
        """Launches run_swarm_async in an independent background asyncio Task."""
        task = asyncio.create_task(self.run_swarm_async(swarm_id))
        self._active_tasks[swarm_id] = task
        return task
