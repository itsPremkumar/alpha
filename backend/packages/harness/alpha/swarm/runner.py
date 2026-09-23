"""Autonomous Async Swarm Runner.

Manages background non-blocking execution of Swarm DAGs,
concurrency control via asyncio semaphores, real-time watchdog checks,
worker dispatching, and automated fan-in aggregation.

Correctness invariants enforced here (each was a real defect before):

- A plan never stays ``status="running"`` once this coroutine returns: a
  dependency failure (or a dependency cycle) that strands downstream tasks in
  PENDING is reconciled via ``SwarmScheduler.fail_unrunnable_tasks`` so the
  aggregation gate can run and produce a terminal status.
- Pause suspends dispatching but keeps this runner alive, so ``resume_swarm``
  actually continues the run (the old ``while status == "running"`` loop exited
  on pause and nothing ever restarted the background task).
- The watchdog runs every poll tick, including mid-wave. The old loop awaited
  the whole wave with ``asyncio.gather`` first, so lease/straggler logic only
  ever ran between waves when no task was RUNNING — it never fired.
- ``SPECULATIVE_BACKUP_LAUNCHED`` is now truthful: a real backup execution is
  launched alongside the straggler; first terminal result wins thanks to the
  scheduler's terminal-state guards.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.models import SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.scheduler import SwarmScheduler
from alpha.swarm.watchdog import SwarmWatchdog
from alpha.swarm.worker import (
    CodingWorktreeWorker,
    EphemeralSubagentWorker,
    HermesBotWorker,
    SpecialistBotWorker,
)

logger = logging.getLogger(__name__)

# Module-level registry of live background runs. Must be module-level (not a
# per-instance dict): every ``coordinator.start_async`` call builds a fresh
# AsyncSwarmRunner, so an instance-level registry never saw earlier runs and a
# double-click executed two loops over one plan (duplicate dispatch/events).
_ACTIVE_SWARM_TASKS: dict[str, asyncio.Task] = {}

# Statuses that must never be flipped back to "running" by run_swarm_async.
# Matches the terminal sets used by coordinator pause/cancel/expand guards —
# "partial_success" is deliberately NOT terminal here because dynamic_expand
# may add tasks to a partially successful plan and re-running must pick them up.
_TERMINAL_PLAN_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _final_event_for(status: str) -> str:
    if status == "completed":
        return "SWARM_COMPLETED"
    if status == "failed":
        return "SWARM_FAILED"
    return "SWARM_PARTIAL_SUCCESS"


class AsyncSwarmRunner:
    """Asynchronous background execution engine for SwarmPlans."""

    def __init__(self, coordinator: Any, poll_interval: float = 0.2):
        self.coordinator = coordinator
        self.poll_interval = poll_interval
        self._active_tasks: dict[str, asyncio.Task] = {}

    def is_running(self, swarm_id: str) -> bool:
        task = _ACTIVE_SWARM_TASKS.get(swarm_id) or self._active_tasks.get(swarm_id)
        return task is not None and not task.done()

    async def run_swarm_async(self, swarm_id: str) -> dict[str, Any]:
        """Runs the swarm DAG to completion in the background."""
        plan: SwarmPlan | None = self.coordinator.get_swarm(swarm_id)
        if not plan:
            return {"status": "not_found", "error": f"Swarm '{swarm_id}' not found."}

        completed_count = sum(1 for t in plan.tasks.values() if t.state == TaskNodeState.COMPLETED)
        if plan.status in _TERMINAL_PLAN_STATUSES:
            # Never resurrect a terminal swarm: the old code unconditionally
            # flipped status back to "running", silently re-running finished work.
            return {"status": plan.status, "tasks_completed": completed_count}

        plan.status = "running"
        self.coordinator.checkpoint(swarm_id)
        self.coordinator.append_event(swarm_id, "SWARM_STARTED")

        semaphore = asyncio.Semaphore(plan.max_concurrency)
        scheduler = SwarmScheduler(plan)
        active: set[asyncio.Task] = set()

        async def execute_node(task_node: SwarmTaskNode, *, backup: bool = False) -> None:
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
                        details={"summary": outcome.get("summary"), "backup": backup},
                    )
                except Exception as exc:
                    logger.error(f"Error executing task {task_node.task_id} in swarm {swarm_id}: {exc}")
                    if backup:
                        # A speculative backup failing must not demote the
                        # original execution, which is still running and may
                        # yet succeed — record the miss and leave state alone.
                        self.coordinator.append_event(
                            swarm_id,
                            "SPECULATIVE_BACKUP_FAILED",
                            task_id=task_node.task_id,
                            details={"error": str(exc)},
                        )
                    else:
                        scheduler.mark_failed(task_node.task_id, str(exc))
                        self.coordinator.append_event(
                            swarm_id,
                            "TASK_FAILED",
                            task_id=task_node.task_id,
                            details={"error": str(exc)},
                        )
                finally:
                    self.coordinator.checkpoint(swarm_id)

        def launch(task_node: SwarmTaskNode, *, backup: bool = False) -> None:
            task = asyncio.create_task(execute_node(task_node, backup=backup))

            def _reap(done: asyncio.Task) -> None:
                active.discard(done)
                if not done.cancelled() and done.exception() is not None:
                    logger.warning("swarm %s node %s task crashed: %s", swarm_id, task_node.task_id, done.exception())

            task.add_done_callback(_reap)
            active.add(task)

        try:
            # Main DAG execution loop — also parks while paused so that a
            # resume continues THIS run instead of orphaning it.
            while plan.status in ("running", "paused"):
                if plan.status == "paused":
                    await asyncio.sleep(self.poll_interval)
                    continue

                if scheduler.is_swarm_finished() and not active:
                    break

                # 1. Watchdog every tick, including mid-wave (the old
                # gather-based loop could only run it between waves, when no
                # task was RUNNING, so lease/straggler logic never fired).
                watchdog_report = SwarmWatchdog.check_and_reconcile(plan)
                for tid in watchdog_report.get("speculative_backups_spawned", []):
                    node = plan.tasks.get(tid)
                    if node is None or node.state not in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                        continue
                    # Launch a real backup race (first terminal result wins via
                    # the scheduler's terminal-state guards) — the event used
                    # to be emitted with nothing behind it.
                    self.coordinator.append_event(swarm_id, "SPECULATIVE_BACKUP_LAUNCHED", task_id=tid)
                    launch(node, backup=True)

                # 2. Dispatch ready tasks (no-op at the concurrency cap)
                ready_tasks = scheduler.dispatch_ready_tasks()
                for t in ready_tasks:
                    self.coordinator.append_event(
                        swarm_id,
                        "TASK_DISPATCHED",
                        task_id=t.task_id,
                        worker=t.assigned_worker or "ephemeral-worker",
                    )
                    launch(t)

                if not active:
                    # Idle but unfinished: no ready task, no live execution,
                    # yet not all tasks terminal. Either a dependency failed or
                    # was cancelled (dependents can never become ready) or the
                    # dependency graph has a cycle. Reconcile instead of the
                    # old behavior — break out and leave status "running" forever.
                    if not scheduler.is_swarm_finished():
                        stranded = scheduler.fail_unrunnable_tasks()
                        if stranded:
                            self.coordinator.append_event(
                                swarm_id,
                                "SWARM_STRANDED_TASKS_FAILED",
                                details={"task_ids": [t.task_id for t in stranded]},
                            )
                    if not scheduler.is_swarm_finished():
                        # Only unreachable RUNNING/STRAGGLING orphans can still
                        # block terminality now (their execution vanished while
                        # the swarm was idle) — fail them rather than spin.
                        for t in plan.tasks.values():
                            if t.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                                t.state = TaskNodeState.FAILED
                                t.error_message = "orphaned: execution disappeared while the swarm was idle"
                                t.completed_at = time.time()
                                self.coordinator.append_event(
                                    swarm_id,
                                    "TASK_FAILED",
                                    task_id=t.task_id,
                                    details={"error": t.error_message},
                                )
                    break

                # 3. Progress: wait for any node to finish, bounded by one tick
                # so the watchdog above keeps firing while a wave is in flight.
                await asyncio.wait(set(active), timeout=self.poll_interval, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Cancel leftover executions when leaving the loop for a terminal
            # status (swarm cancelled, gateway shutting down). Detached worker
            # threads may still finish, but the scheduler's terminal-state
            # guards drop their late results instead of resurrecting tasks.
            if active:
                for t in list(active):
                    t.cancel()
                await asyncio.gather(*active, return_exceptions=True)

        # 4. Aggregation & Quality Gate Verification
        if plan.status == "running" and scheduler.is_swarm_finished():
            plan.status = "aggregating"
            self.coordinator.append_event(swarm_id, "SWARM_AGGREGATING")
            agg_result = SwarmAggregator.aggregate(plan)
            plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.coordinator.append_event(
                swarm_id,
                _final_event_for(plan.status),
                details=agg_result,
            )
            self.coordinator.checkpoint(swarm_id)
            return {"status": plan.status, "aggregated": True, "result": agg_result}

        self.coordinator.checkpoint(swarm_id)
        return {"status": plan.status, "tasks_completed": sum(1 for t in plan.tasks.values() if t.state == TaskNodeState.COMPLETED)}

    def start_background_swarm(self, swarm_id: str) -> asyncio.Task:
        """Launches run_swarm_async in an independent background asyncio Task.

        Idempotent per swarm via the module-level registry: repeated start
        requests (double-click, tool + endpoint) return the live task instead
        of running a second loop over the same plan.
        """
        existing = _ACTIVE_SWARM_TASKS.get(swarm_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(self.run_swarm_async(swarm_id))
        _ACTIVE_SWARM_TASKS[swarm_id] = task
        self._active_tasks[swarm_id] = task

        def _cleanup(done: asyncio.Task, sid: str = swarm_id) -> None:
            if _ACTIVE_SWARM_TASKS.get(sid) is done:
                _ACTIVE_SWARM_TASKS.pop(sid, None)

        task.add_done_callback(_cleanup)
        return task
