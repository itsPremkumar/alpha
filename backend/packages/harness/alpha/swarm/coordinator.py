"""Master Swarm Coordinator: Manages swarm lifecycle, state, and persistence."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.estimator import SwarmBenefitEstimator
from alpha.swarm.models import SwarmDecision, SwarmEvent, SwarmMode, SwarmPlan, TaskNodeState
from alpha.swarm.scheduler import SwarmScheduler

logger = logging.getLogger(__name__)

_GLOBAL_COORDINATOR: SwarmCoordinator | None = None


def get_swarm_coordinator() -> SwarmCoordinator:
    """Returns the singleton instance of SwarmCoordinator."""
    global _GLOBAL_COORDINATOR
    if _GLOBAL_COORDINATOR is None:
        _GLOBAL_COORDINATOR = SwarmCoordinator()
    return _GLOBAL_COORDINATOR


class SwarmCoordinator:
    """Coordinates lifecycle, state, scheduling, and aggregation of autonomous agent swarms."""

    def __init__(self, storage_dir: Path | str | None = None):
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            base = os.environ.get("AGENT_WORKSPACE_HOME", "~/.agent-workspace")
            self.storage_dir = Path(os.path.expanduser(base)) / "swarms"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._swarms: dict[str, SwarmPlan] = {}
        self._events: dict[str, list[SwarmEvent]] = {}
        self._load_persisted_swarms()

    def _load_persisted_swarms(self) -> None:
        if not self.storage_dir.exists():
            return
        for file in self.storage_dir.glob("*.json"):
            try:
                with open(file, encoding="utf-8") as f:
                    data = json.load(f)
                    plan = SwarmPlan.from_dict(data)
                    self._swarms[plan.swarm_id] = plan
            except Exception as e:
                logger.warning(f"Failed to load swarm checkpoint {file}: {e}")

    def evaluate_intent(self, goal: str, items: Sequence[str] | None = None) -> SwarmDecision:
        """Evaluates whether a goal warrants a swarm."""
        return SwarmBenefitEstimator.estimate(goal, items=items)

    def create_swarm(
        self,
        goal: str,
        mode: SwarmMode = SwarmMode.AUTO,
        items: Sequence[str] | None = None,
        max_concurrency: int = 8,
    ) -> SwarmPlan:
        """Constructs, decomposes, and registers a new SwarmPlan."""
        plan = SwarmTaskDecomposer.decompose(
            goal=goal,
            mode=mode,
            items=items,
            max_concurrency=max_concurrency,
        )
        plan.status = "running"
        self._swarms[plan.swarm_id] = plan
        self._events[plan.swarm_id] = []

        self.append_event(
            plan.swarm_id,
            "SWARM_CREATED",
            details={
                "goal": goal,
                "mode": plan.mode.value,
                "tasks_count": len(plan.tasks),
                "estimated_speedup": plan.estimated_speedup,
                "critical_path_seconds": plan.critical_path_seconds,
            },
        )
        self.checkpoint(plan.swarm_id)
        return plan

    def get_swarm(self, swarm_id: str) -> SwarmPlan | None:
        return self._swarms.get(swarm_id)

    def list_swarms(self, limit: int = 20) -> list[SwarmPlan]:
        plans = list(self._swarms.values())
        plans.sort(key=lambda p: p.created_at, reverse=True)
        return plans[:limit]

    def pause_swarm(self, swarm_id: str) -> bool:
        plan = self._swarms.get(swarm_id)
        if not plan or plan.status in ("completed", "failed", "cancelled"):
            return False
        plan.status = "paused"
        self.append_event(swarm_id, "SWARM_PAUSED")
        self.checkpoint(swarm_id)
        return True

    def resume_swarm(self, swarm_id: str) -> bool:
        plan = self._swarms.get(swarm_id)
        if not plan or plan.status != "paused":
            return False
        plan.status = "running"
        self.append_event(swarm_id, "SWARM_RESUMED")
        self.checkpoint(swarm_id)
        return True

    def cancel_swarm(self, swarm_id: str, reason: str = "") -> bool:
        plan = self._swarms.get(swarm_id)
        if not plan:
            return False
        plan.status = "cancelled"
        for task in plan.tasks.values():
            if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED, TaskNodeState.RUNNING):
                task.state = TaskNodeState.CANCELLED
        self.append_event(swarm_id, "SWARM_CANCELLED", details={"reason": reason})
        self.checkpoint(swarm_id)
        return True

    def step(self, swarm_id: str) -> dict[str, Any]:
        """Advances swarm execution: dispatches ready tasks, checks completion, runs aggregation."""
        from alpha.swarm.watchdog import SwarmWatchdog

        plan = self._swarms.get(swarm_id)
        if not plan or plan.status != "running":
            return {"status": plan.status if plan else "not_found", "dispatched": []}

        # 0. Reconcile liveness, stragglers, and expired leases
        watchdog_report = SwarmWatchdog.check_and_reconcile(plan)
        for tid in watchdog_report.get("speculative_backups_spawned", []):
            self.append_event(swarm_id, "SPECULATIVE_BACKUP_LAUNCHED", task_id=tid)

        scheduler = SwarmScheduler(plan)

        # 0.5 Reconcile a deadlocked DAG before the finished check: if nothing
        # is ready, nothing is running (the watchdog above already requeued or
        # failed expired external leases), yet the plan is not finished, the
        # remaining PENDING tasks are unreachable — a dependency failed or was
        # cancelled, or the graph has a cycle. Without this, status stayed
        # "running" forever and every subsequent step returned empty work.
        ready_now = scheduler.get_ready_tasks()
        running_now = sum(1 for t in plan.tasks.values() if t.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING))
        if not ready_now and running_now == 0 and not scheduler.is_swarm_finished():
            stranded = scheduler.fail_unrunnable_tasks()
            if stranded:
                self.append_event(
                    swarm_id,
                    "SWARM_STRANDED_TASKS_FAILED",
                    details={"task_ids": [t.task_id for t in stranded]},
                )

        # 1. Check if finished
        if scheduler.is_swarm_finished():
            plan.status = "aggregating"
            self.append_event(swarm_id, "SWARM_AGGREGATING")
            agg_result = SwarmAggregator.aggregate(plan)
            plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            final_event = "SWARM_COMPLETED" if plan.status == "completed" else "SWARM_FAILED" if plan.status == "failed" else "SWARM_PARTIAL_SUCCESS"
            self.append_event(
                swarm_id,
                final_event,
                details=agg_result,
            )
            self.checkpoint(swarm_id)
            return {"status": plan.status, "aggregated": True, "result": agg_result}

        # 2. Dispatch ready tasks
        dispatched = scheduler.dispatch_ready_tasks()
        for task in dispatched:
            self.append_event(
                swarm_id,
                "TASK_DISPATCHED",
                task_id=task.task_id,
                worker=task.assigned_worker or "ephemeral-worker",
                details={"objective": task.objective},
            )

        self.checkpoint(swarm_id)
        return {
            "status": plan.status,
            "dispatched": [t.task_id for t in dispatched],
            "running_count": sum(1 for t in plan.tasks.values() if t.state == TaskNodeState.RUNNING),
        }

    def append_event(
        self,
        swarm_id: str,
        event_type: str,
        task_id: str | None = None,
        worker: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> SwarmEvent:
        evt = SwarmEvent(
            swarm_id=swarm_id,
            event_type=event_type,
            task_id=task_id,
            worker=worker,
            details=details or {},
        )
        if swarm_id not in self._events:
            self._events[swarm_id] = []
        self._events[swarm_id].append(evt)
        return evt

    def get_events(self, swarm_id: str, limit: int = 50) -> list[SwarmEvent]:
        events = self._events.get(swarm_id, [])
        return events[-limit:]

    def checkpoint(self, swarm_id: str) -> None:
        plan = self._swarms.get(swarm_id)
        if not plan:
            return
        target = self.storage_dir / f"{swarm_id}.json"
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(plan.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to checkpoint swarm {swarm_id}: {e}")

    def dynamic_expand(
        self,
        swarm_id: str,
        new_tasks: list[dict[str, Any]],
        parent_task_id: str | None = None,
    ) -> list[str]:
        """Dynamically injects new tasks into an active swarm DAG mid-flight."""
        import uuid

        from alpha.swarm.models import SwarmTaskNode, TaskNodeState

        plan = self._swarms.get(swarm_id)
        if not plan or plan.status in ("completed", "failed", "cancelled"):
            return []

        added_ids = []
        for raw in new_tasks:
            tid = raw.get("task_id") or f"task-dyn-{uuid.uuid4().hex[:6]}"
            deps = list(raw.get("dependencies", []))
            if parent_task_id and parent_task_id not in deps:
                deps.append(parent_task_id)

            node = SwarmTaskNode(
                task_id=tid,
                objective=raw.get("objective", "Dynamically discovered subtask"),
                dependencies=deps,
                assigned_worker=raw.get("assigned_worker"),
                worker_type=raw.get("worker_type", "ephemeral"),
                model_override=raw.get("model_override"),
                worktree_path=raw.get("worktree_path"),
                input_artifacts=raw.get("input_artifacts", []),
                output_artifacts=raw.get("output_artifacts", []),
                state=TaskNodeState.PENDING,
            )
            plan.tasks[tid] = node
            added_ids.append(tid)

            # Ensure final aggregator node waits for this task
            for agg_id in ("task-reduce", "task-judge", "task-integrate", "task-evaluator"):
                if agg_id in plan.tasks and agg_id != tid and tid not in plan.tasks[agg_id].dependencies:
                    plan.tasks[agg_id].dependencies.append(tid)

        # Recalculate critical path
        SwarmTaskDecomposer._compute_critical_path_and_speedup(plan)

        self.append_event(
            swarm_id,
            "SWARM_EXPANDED",
            details={
                "parent_task_id": parent_task_id,
                "added_task_ids": added_ids,
                "new_critical_path": plan.critical_path_seconds,
            },
        )
        self.checkpoint(swarm_id)
        return added_ids

    def start_async(self, swarm_id: str):
        """Starts the swarm in background async loop."""
        from alpha.swarm.runner import AsyncSwarmRunner

        runner = AsyncSwarmRunner(self)
        return runner.start_background_swarm(swarm_id)
