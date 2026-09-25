"""Dependency-aware, lease-fenced scheduling for Alpha swarm DAGs."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping, Sequence

from alpha.swarm.models import SwarmPlan, SwarmTaskLease, SwarmTaskNode, TaskNodeState


class SwarmPlanValidationError(ValueError):
    """Raised when a swarm plan would be ambiguous, unsafe, or non-executable."""


_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_OBJECTIVE_CHARS = 32_000
_MAX_DEPENDENCIES = 256
_MAX_RESULT_SUMMARY_CHARS = 50_000
_MAX_EVIDENCE_ITEMS = 128
_MAX_OUTPUT_ARTIFACTS = 128
_TERMINAL_STATES = frozenset({TaskNodeState.COMPLETED, TaskNodeState.FAILED, TaskNodeState.CANCELLED})


class SwarmScheduler:
    """Schedules tasks while enforcing DAG, concurrency, and lease invariants."""

    def __init__(self, plan: SwarmPlan):
        self.plan = plan

    def validate_graph(self) -> None:
        """Validate task ids, dependencies, and acyclicity before dispatch."""

        task_ids = set(self.plan.tasks)
        for task_id, task in self.plan.tasks.items():
            if task_id != task.task_id:
                raise SwarmPlanValidationError(f"task key {task_id!r} does not match task.task_id {task.task_id!r}")
            if not _TASK_ID_RE.fullmatch(task_id):
                raise SwarmPlanValidationError(f"invalid task id: {task_id!r}")
            if not str(task.objective or "").strip():
                raise SwarmPlanValidationError(f"task {task_id!r} has an empty objective")
            if len(str(task.objective)) > _MAX_OBJECTIVE_CHARS:
                raise SwarmPlanValidationError(f"task {task_id!r} objective exceeds {_MAX_OBJECTIVE_CHARS} characters")
            if len(task.dependencies) > _MAX_DEPENDENCIES:
                raise SwarmPlanValidationError(f"task {task_id!r} has too many dependencies")
            if len(task.dependencies) != len(set(task.dependencies)):
                raise SwarmPlanValidationError(f"task {task_id!r} has duplicate dependencies")
            for dependency in task.dependencies:
                if dependency not in task_ids:
                    raise SwarmPlanValidationError(f"task {task_id!r} depends on unknown task {dependency!r}")
                if dependency == task_id:
                    raise SwarmPlanValidationError(f"task {task_id!r} depends on itself")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise SwarmPlanValidationError("swarm dependency graph contains a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in self.plan.tasks[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in sorted(task_ids):
            visit(task_id)

    def get_ready_tasks(self, *, now: float | None = None) -> list[SwarmTaskNode]:
        """Return dependency-ready pending tasks in deterministic priority order."""

        now = float(now if now is not None else time.time())
        ready: list[SwarmTaskNode] = []
        for task in self.plan.tasks.values():
            if task.state != TaskNodeState.PENDING:
                continue
            if task.next_attempt_at is not None and task.next_attempt_at > now:
                continue
            if all((dependency in self.plan.tasks and self.plan.tasks[dependency].state == TaskNodeState.COMPLETED) for dependency in task.dependencies):
                ready.append(task)
        return sorted(ready, key=lambda task: (-int(task.priority), task.task_id))

    def dispatch_ready_tasks(
        self,
        lease_seconds: float = 60.0,
        *,
        lease_owner: str | None = None,
        now: float | None = None,
        max_dispatch: int | None = None,
    ) -> list[SwarmTaskNode]:
        """Atomically claim ready tasks up to the plan concurrency limit."""

        now = float(now if now is not None else time.time())
        lease_seconds = max(1.0, float(lease_seconds))
        running_count = sum(1 for task in self.plan.tasks.values() if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING))
        available_slots = max(0, int(self.plan.max_concurrency) - running_count)
        if max_dispatch is not None:
            available_slots = min(available_slots, max(0, int(max_dispatch)))
        if available_slots <= 0:
            return []

        dispatched: list[SwarmTaskNode] = []
        owner = lease_owner or f"swarm-runner:{uuid.uuid4().hex[:8]}"
        for task in self.get_ready_tasks(now=now)[:available_slots]:
            task.state = TaskNodeState.RUNNING
            task.started_at = now
            task.completed_at = None
            task.duration_seconds = 0.0
            task.lease_expires_at = now + lease_seconds
            task.lease_id = f"lease-{uuid.uuid4().hex[:16]}"
            task.lease_owner = owner
            task.next_attempt_at = None
            task.attempts += 1
            task.backup_worker_launched = False
            dispatched.append(task)
        return dispatched

    def claim_task(
        self,
        task_id: str,
        *,
        lease_owner: str,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> SwarmTaskLease | None:
        """Claim one specific ready task for an external worker."""

        lease_owner = str(lease_owner or "").strip()
        if not lease_owner:
            raise SwarmPlanValidationError("lease_owner must be non-empty")
        if len(lease_owner) > 128:
            raise SwarmPlanValidationError("lease_owner is limited to 128 characters")
        now = float(now if now is not None else time.time())
        task = self.plan.tasks.get(task_id)
        if task is None or task.state != TaskNodeState.PENDING or task.next_attempt_at and task.next_attempt_at > now:
            return None
        if not all((dependency in self.plan.tasks and self.plan.tasks[dependency].state == TaskNodeState.COMPLETED) for dependency in task.dependencies):
            return None
        task.state = TaskNodeState.RUNNING
        task.started_at = now
        task.completed_at = None
        task.duration_seconds = 0.0
        task.lease_expires_at = now + max(1.0, float(lease_seconds))
        task.lease_id = f"lease-{uuid.uuid4().hex[:16]}"
        task.lease_owner = lease_owner
        task.next_attempt_at = None
        task.backup_worker_launched = False
        task.attempts += 1
        return SwarmTaskLease(
            task_id=task.task_id,
            lease_id=task.lease_id,
            owner=lease_owner,
            expires_at=task.lease_expires_at,
            task_attempt=task.attempts,
        )

    def renew_lease(self, task_id: str, lease_id: str, *, lease_seconds: float = 60.0, now: float | None = None) -> bool:
        task = self.plan.tasks.get(task_id)
        now = float(now if now is not None else time.time())
        if not task or task.state not in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING) or task.lease_id != lease_id:
            return False
        task.lease_expires_at = now + max(1.0, float(lease_seconds))
        return True

    def renew_active_leases(self, *, lease_seconds: float = 60.0, now: float | None = None) -> int:
        now = float(now if now is not None else time.time())
        renewed = 0
        for task in self.plan.tasks.values():
            if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING) and task.lease_id:
                if self.renew_lease(task.task_id, task.lease_id, lease_seconds=lease_seconds, now=now):
                    renewed += 1
        return renewed

    def mark_completed(
        self,
        task_id: str,
        result_summary: str,
        evidence: Sequence[Mapping[str, object]] | None = None,
        output_artifacts: Sequence[str] | None = None,
        *,
        lease_id: str | None = None,
        result_payload: Mapping[str, object] | None = None,
    ) -> SwarmTaskNode | None:
        """Mark a task complete, fencing stale or late worker results."""

        task = self.plan.tasks.get(task_id)
        if not task:
            return None
        # Once a terminal task has released its lease, any result carrying an
        # explicit lease is stale. Return ``None`` rather than the old task so
        # callers cannot mistake a late result for an accepted completion.
        if lease_id is not None and task.state in _TERMINAL_STATES and task.lease_id != lease_id:
            return None
        if task.state in _TERMINAL_STATES:
            return task
        if lease_id is not None and task.lease_id != lease_id:
            return None
        now = time.time()
        task.state = TaskNodeState.COMPLETED
        task.completed_at = now
        task.duration_seconds = max(0.0, now - task.started_at) if task.started_at else 0.0
        summary = str(result_summary)
        if len(summary) > _MAX_RESULT_SUMMARY_CHARS:
            marker = "\n[truncated by swarm result bound]"
            summary = (summary[: max(0, _MAX_RESULT_SUMMARY_CHARS - len(marker))] + marker)[:_MAX_RESULT_SUMMARY_CHARS]
        task.result_summary = summary
        if evidence:
            task.evidence.extend(dict(item) for item in evidence if isinstance(item, Mapping))
            del task.evidence[:-_MAX_EVIDENCE_ITEMS]
        if output_artifacts:
            task.output_artifacts.extend(str(item) for item in output_artifacts if item)
            del task.output_artifacts[:-_MAX_OUTPUT_ARTIFACTS]
        if result_payload:
            task.result_payload.update(dict(result_payload))
        task.lease_id = None
        task.lease_owner = None
        task.lease_expires_at = None
        task.next_attempt_at = None
        return task

    def mark_failed(
        self,
        task_id: str,
        error_message: str,
        *,
        lease_id: str | None = None,
        retry_backoff_seconds: float = 0.0,
    ) -> SwarmTaskNode | None:
        """Fail or requeue a task, preserving first-terminal-result-wins semantics."""

        task = self.plan.tasks.get(task_id)
        if not task:
            return None
        if lease_id is not None and task.state in _TERMINAL_STATES and task.lease_id != lease_id:
            return None
        if task.state in _TERMINAL_STATES:
            return task
        if lease_id is not None and task.lease_id != lease_id:
            return None
        task.error_message = str(error_message)
        task.lease_id = None
        task.lease_owner = None
        task.lease_expires_at = None
        now = time.time()
        if task.attempts < task.max_attempts:
            task.state = TaskNodeState.PENDING
            task.next_attempt_at = now + max(0.0, float(retry_backoff_seconds))
        else:
            task.state = TaskNodeState.FAILED
            task.completed_at = now
            task.next_attempt_at = None
        return task

    def fail_unrunnable_tasks(self) -> list[SwarmTaskNode]:
        """Fail tasks that cannot become ready after a dependency failure/cycle."""

        failed: list[SwarmTaskNode] = []
        now = time.time()
        for task in self.plan.tasks.values():
            if task.state not in (TaskNodeState.PENDING, TaskNodeState.QUEUED):
                continue
            blocked = any(dependency not in self.plan.tasks or self.plan.tasks[dependency].state in (TaskNodeState.FAILED, TaskNodeState.CANCELLED) for dependency in task.dependencies)
            if blocked:
                task.state = TaskNodeState.FAILED
                task.error_message = "unrunnable: dependency failed, was cancelled, or the dependency graph has a cycle"
                task.completed_at = now
                task.lease_id = None
                task.lease_owner = None
                task.lease_expires_at = None
                failed.append(task)
        return failed

    def is_swarm_finished(self) -> bool:
        """True when every task has a terminal state."""

        if not self.plan.tasks:
            return True
        return all(task.state in _TERMINAL_STATES for task in self.plan.tasks.values())

    def has_active_tasks(self) -> bool:
        """True while any task is not terminal."""

        return any(task.state not in _TERMINAL_STATES for task in self.plan.tasks.values())

    def progress(self) -> dict[str, int]:
        return {
            "total": len(self.plan.tasks),
            "pending": sum(1 for task in self.plan.tasks.values() if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED)),
            "running": sum(1 for task in self.plan.tasks.values() if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING)),
            "completed": sum(1 for task in self.plan.tasks.values() if task.state == TaskNodeState.COMPLETED),
            "failed": sum(1 for task in self.plan.tasks.values() if task.state == TaskNodeState.FAILED),
            "cancelled": sum(1 for task in self.plan.tasks.values() if task.state == TaskNodeState.CANCELLED),
        }
