"""Swarm Task Scheduler: Dependency-aware DAG execution and concurrency management."""

from __future__ import annotations

import time
from collections.abc import Sequence

from agent_workspace.swarm.models import SwarmPlan, SwarmTaskNode, TaskNodeState


class SwarmScheduler:
    """Schedules tasks in a SwarmPlan adhering to DAG dependencies and concurrency limits."""

    def __init__(self, plan: SwarmPlan):
        self.plan = plan

    def get_ready_tasks(self) -> list[SwarmTaskNode]:
        """Returns pending tasks whose dependencies are all successfully completed."""
        ready: list[SwarmTaskNode] = []
        for task in self.plan.tasks.values():
            if task.state != TaskNodeState.PENDING:
                continue

            # Check if all dependencies are completed
            deps_satisfied = True
            for dep_id in task.dependencies:
                dep_node = self.plan.tasks.get(dep_id)
                if not dep_node or dep_node.state != TaskNodeState.COMPLETED:
                    deps_satisfied = False
                    break

            if deps_satisfied:
                ready.append(task)
        return ready

    def dispatch_ready_tasks(self, lease_seconds: float = 60.0) -> list[SwarmTaskNode]:
        """Dispatches ready tasks into RUNNING state up to max_concurrency limit."""
        running_count = sum(1 for t in self.plan.tasks.values() if t.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING))
        available_slots = max(0, self.plan.max_concurrency - running_count)

        if available_slots <= 0:
            return []

        ready = self.get_ready_tasks()
        dispatched: list[SwarmTaskNode] = []

        now = time.time()
        for task in ready[:available_slots]:
            task.state = TaskNodeState.RUNNING
            task.started_at = now
            task.lease_expires_at = now + lease_seconds
            task.attempts += 1
            dispatched.append(task)

        return dispatched

    def mark_completed(
        self,
        task_id: str,
        result_summary: str,
        evidence: Sequence[dict] | None = None,
        output_artifacts: Sequence[str] | None = None,
    ) -> SwarmTaskNode | None:
        """Marks a task as completed and records outcome."""
        task = self.plan.tasks.get(task_id)
        if not task:
            return None

        now = time.time()
        task.state = TaskNodeState.COMPLETED
        task.completed_at = now
        task.duration_seconds = (now - task.started_at) if task.started_at else 0.0
        task.result_summary = result_summary
        if evidence:
            task.evidence.extend(evidence)
        if output_artifacts:
            task.output_artifacts.extend(output_artifacts)
        return task

    def mark_failed(self, task_id: str, error_message: str) -> SwarmTaskNode | None:
        """Marks a task as failed and checks retry eligibility."""
        task = self.plan.tasks.get(task_id)
        if not task:
            return None

        task.error_message = error_message
        if task.attempts < task.max_attempts:
            # Requeue for retry
            task.state = TaskNodeState.PENDING
            task.lease_expires_at = None
        else:
            task.state = TaskNodeState.FAILED
            task.completed_at = time.time()
        return task

    def is_swarm_finished(self) -> bool:
        """Checks if all tasks in the swarm have completed or failed."""
        if not self.plan.tasks:
            return True
        return all(t.state in (TaskNodeState.COMPLETED, TaskNodeState.FAILED, TaskNodeState.CANCELLED) for t in self.plan.tasks.values())

    def has_active_tasks(self) -> bool:
        """Checks if there are currently running or pending tasks."""
        return any(t.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED, TaskNodeState.RUNNING, TaskNodeState.STRAGGLING) for t in self.plan.tasks.values())
