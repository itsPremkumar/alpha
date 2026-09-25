"""Bounded reflection and no-progress detection for swarm control loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alpha.swarm.models import SwarmPlan, TaskNodeState


@dataclass
class SwarmReflection:
    progress: dict[str, int]
    ready_task_ids: list[str] = field(default_factory=list)
    failed_task_ids: list[str] = field(default_factory=list)
    running_task_ids: list[str] = field(default_factory=list)
    no_progress_rounds: int = 0
    recommended_action: str = "continue"
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "progress": dict(self.progress),
            "ready_task_ids": list(self.ready_task_ids),
            "failed_task_ids": list(self.failed_task_ids),
            "running_task_ids": list(self.running_task_ids),
            "no_progress_rounds": self.no_progress_rounds,
            "recommended_action": self.recommended_action,
            "reason": self.reason,
            "metrics": dict(self.metrics),
        }


class SwarmReflector:
    """Turn task state into an explicit next-action recommendation.

    This is intentionally deterministic.  It is a control-plane guard, not a
    second LLM supervisor, and therefore cannot hallucinate progress or a
    recovery plan.
    """

    def __init__(self, *, max_no_progress_rounds: int = 2) -> None:
        if max_no_progress_rounds < 1:
            raise ValueError("max_no_progress_rounds must be at least 1")
        self.max_no_progress_rounds = int(max_no_progress_rounds)

    def reflect(
        self,
        plan: SwarmPlan,
        *,
        previous_completed: int | None = None,
        no_progress_rounds: int = 0,
    ) -> SwarmReflection:
        counts = {
            "total": len(plan.tasks),
            "completed": sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.COMPLETED),
            "failed": sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.FAILED),
            "cancelled": sum(1 for task in plan.tasks.values() if task.state == TaskNodeState.CANCELLED),
            "running": sum(1 for task in plan.tasks.values() if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING)),
            "pending": sum(1 for task in plan.tasks.values() if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED)),
        }
        ready = [task.task_id for task in plan.tasks.values() if task.state == TaskNodeState.PENDING and all(plan.tasks.get(dep) and plan.tasks[dep].state == TaskNodeState.COMPLETED for dep in task.dependencies)]
        failed_ids = [task.task_id for task in plan.tasks.values() if task.state == TaskNodeState.FAILED]
        running_ids = [task.task_id for task in plan.tasks.values() if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING)]
        if previous_completed is not None and counts["completed"] <= int(previous_completed):
            no_progress_rounds = int(no_progress_rounds) + 1
        elif previous_completed is not None:
            no_progress_rounds = 0

        if counts["completed"] == counts["total"]:
            action, reason = "aggregate", "all tasks reached a terminal successful state"
        elif failed_ids and plan.auto_replan and plan.replan_count < plan.budget.max_replans:
            # Prefer a bounded repair task over declaring the whole DAG
            # stranded.  The coordinator is responsible for redirecting
            # dependents and enforcing the task/replan budgets.
            action, reason = "replan", "failed tasks are eligible for a bounded replan"
        elif not ready and not running_ids and counts["pending"] > 0:
            action, reason = "fail_stranded", "no ready or running tasks remain while pending tasks are unreachable"
        elif no_progress_rounds >= self.max_no_progress_rounds and running_ids:
            action, reason = "stall", f"no task progress for {no_progress_rounds} reflection rounds"
        elif failed_ids:
            action, reason = "continue", "failed tasks are terminal; aggregation will report partial/failure honestly"
        elif ready:
            action, reason = "dispatch", "one or more dependency-ready tasks are available"
        else:
            action, reason = "wait", "tasks are running or waiting on dependencies"
        return SwarmReflection(
            progress=counts,
            ready_task_ids=ready,
            failed_task_ids=failed_ids,
            running_task_ids=running_ids,
            no_progress_rounds=no_progress_rounds,
            recommended_action=action,
            reason=reason,
            metrics={"completion_ratio": (counts["completed"] / counts["total"]) if counts["total"] else 1.0},
        )
