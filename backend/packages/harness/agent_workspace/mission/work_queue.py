"""Durable Work Queue, Execution Budgeting & DAG Task Scheduler.

Inspired by Chapters 21, 23, and 39 of the Master Architecture Blueprint:
- First-class work queue with topological DAG dependency resolution
- Strict execution budget caps (max_runtime, max_subagents, max_tool_calls, max_retries, max_cost_usd)
- Priority scheduling (CRITICAL > HIGH > MEDIUM > LOW)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any

from agent_workspace.mission.state_machine import TaskState, TaskStateMachine


class TaskPriority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class CyclicDependencyError(ValueError):
    """Raised when circular dependencies are detected in the task DAG."""
    pass


class BudgetExceededError(RuntimeError):
    """Raised when an execution budget limit is exceeded."""
    pass


@dataclass
class ExecutionBudget:
    """Hard caps and live counters for execution safety (Chapter 39)."""
    max_runtime_sec: float = 3600.0
    max_subagents: int = 15
    max_tool_calls: int = 250
    max_retries: int = 3
    max_cost_usd: float = 20.0

    current_runtime_sec: float = 0.0
    current_subagents: int = 0
    current_tool_calls: int = 0
    current_retries: int = 0
    current_cost_usd: float = 0.0

    def check_and_record(
        self,
        tool_calls: int = 0,
        subagents: int = 0,
        cost_usd: float = 0.0,
        runtime_sec: float = 0.0,
        retries: int = 0,
    ) -> None:
        if self.current_tool_calls + tool_calls > self.max_tool_calls:
            raise BudgetExceededError(
                f"Budget exceeded: tool calls limit {self.max_tool_calls} "
                f"(current: {self.current_tool_calls}, requested: +{tool_calls})"
            )
        if self.current_subagents + subagents > self.max_subagents:
            raise BudgetExceededError(
                f"Budget exceeded: subagents limit {self.max_subagents} "
                f"(current: {self.current_subagents}, requested: +{subagents})"
            )
        if self.current_cost_usd + cost_usd > self.max_cost_usd:
            raise BudgetExceededError(
                f"Budget exceeded: cost limit ${self.max_cost_usd:.2f} "
                f"(current: ${self.current_cost_usd:.2f}, requested: +${cost_usd:.2f})"
            )
        if self.current_runtime_sec + runtime_sec > self.max_runtime_sec:
            raise BudgetExceededError(
                f"Budget exceeded: runtime limit {self.max_runtime_sec}s "
                f"(current: {self.current_runtime_sec:.1f}s, requested: +{runtime_sec:.1f}s)"
            )
        if self.current_retries + retries > self.max_retries:
            raise BudgetExceededError(
                f"Budget exceeded: retries limit {self.max_retries} "
                f"(current: {self.current_retries}, requested: +{retries})"
            )

        self.current_tool_calls += tool_calls
        self.current_subagents += subagents
        self.current_cost_usd += cost_usd
        self.current_runtime_sec += runtime_sec
        self.current_retries += retries

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QueuedTask:
    """A unit of work queued for execution within a mission."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    dependencies: list[str] = field(default_factory=list)  # task IDs that must be COMPLETED
    assignee: str = "orchestrator"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: Any | None = None
    error: str | None = None
    retry_count: int = 0
    state_machine: TaskStateMachine = field(default_factory=lambda: TaskStateMachine(task_id="init"))

    def __post_init__(self):
        if self.state_machine.task_id == "init":
            self.state_machine = TaskStateMachine(task_id=self.id)

    @property
    def state(self) -> TaskState:
        return self.state_machine.current_state

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority.name,
            "dependencies": self.dependencies,
            "assignee": self.assignee,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "state": self.state.value,
            "retry_count": self.retry_count,
            "result": self.result,
            "error": self.error,
        }


class DurableWorkQueue:
    """DAG Scheduler and Work Queue managing task execution order, dependencies, and budgets."""

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget: ExecutionBudget = budget or ExecutionBudget()
        self._tasks: dict[str, QueuedTask] = {}

    def add_task(
        self,
        title: str,
        description: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        dependencies: list[str] | None = None,
        assignee: str = "orchestrator",
        task_id: str | None = None,
    ) -> QueuedTask:
        deps = dependencies or []
        for dep in deps:
            if dep not in self._tasks:
                raise KeyError(f"Dependency task '{dep}' does not exist in work queue.")

        t_id = task_id or str(uuid.uuid4())
        task = QueuedTask(
            id=t_id,
            title=title,
            description=description,
            priority=priority,
            dependencies=deps,
            assignee=assignee,
        )
        self._tasks[task.id] = task

        # Check for cycles after insertion
        if self.detect_cycles():
            del self._tasks[task.id]
            raise CyclicDependencyError(f"Adding task '{title}' introduces a cyclic dependency.")

        # Advance state from CREATED -> READY if no dependencies, or BLOCKED if waiting
        if not deps:
            task.state_machine.transition_to(TaskState.ANALYZING, reason="Initial intake")
            task.state_machine.transition_to(TaskState.PLANNING, reason="Requirements defined")
            task.state_machine.transition_to(TaskState.READY, reason="Zero dependencies, ready for dispatch")
        else:
            task.state_machine.transition_to(TaskState.ANALYZING, reason="Initial intake")
            task.state_machine.transition_to(TaskState.PLANNING, reason="Requirements defined")
            task.state_machine.transition_to(TaskState.READY, reason="Plan created")
            task.state_machine.transition_to(TaskState.BLOCKED, reason=f"Awaiting dependencies: {deps}")

        return task

    def get_task(self, task_id: str) -> QueuedTask | None:
        return self._tasks.get(task_id)

    def detect_cycles(self) -> bool:
        """Kahn's algorithm to detect cycles in the registered tasks."""
        in_degree = {tid: 0 for tid in self._tasks}
        graph: dict[str, list[str]] = {tid: [] for tid in self._tasks}

        for tid, task in self._tasks.items():
            for dep in task.dependencies:
                if dep in graph:
                    graph[dep].append(tid)
                    in_degree[tid] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited_count = 0

        while queue:
            node = queue.pop(0)
            visited_count += 1
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return visited_count != len(self._tasks)

    def topological_sort(self) -> list[str]:
        """Compute legal topological execution sequence for all tasks."""
        if self.detect_cycles():
            raise CyclicDependencyError("Cannot sort tasks: Cycle detected in task DAG.")

        in_degree = {tid: 0 for tid in self._tasks}
        graph: dict[str, list[str]] = {tid: [] for tid in self._tasks}

        for tid, task in self._tasks.items():
            for dep in task.dependencies:
                if dep in graph:
                    graph[dep].append(tid)
                    in_degree[tid] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        sorted_ids: list[str] = []

        while queue:
            node = queue.pop(0)
            sorted_ids.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return sorted_ids

    def get_ready_tasks(self) -> list[QueuedTask]:
        """Return all tasks whose dependencies are all COMPLETED and are currently READY or unblocked."""
        ready: list[QueuedTask] = []
        for task in self._tasks.values():
            if task.state == TaskState.COMPLETED or task.state == TaskState.RUNNING:
                continue

            # Check if all dependencies are completed
            deps_met = all(
                self._tasks[dep].state == TaskState.COMPLETED
                for dep in task.dependencies
                if dep in self._tasks
            )

            if deps_met:
                if task.state == TaskState.BLOCKED:
                    task.state_machine.transition_to(TaskState.READY, reason="All upstream dependencies completed")
                if task.state == TaskState.READY:
                    ready.append(task)

        # Sort by Priority (descending) then created_at (ascending)
        ready.sort(key=lambda t: (-int(t.priority), t.created_at))
        return ready

    def dispatch_next(self) -> QueuedTask | None:
        """Dispatch the highest priority ready task, transitioning it to RUNNING."""
        ready = self.get_ready_tasks()
        if not ready:
            return None

        next_task = ready[0]
        next_task.state_machine.transition_to(TaskState.RUNNING, reason="Dispatched by DAG scheduler")
        next_task.started_at = time.time()
        return next_task

    def complete_task(self, task_id: str, result: Any | None = None) -> QueuedTask:
        """Mark a task as verified and completed."""
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")

        if task.state == TaskState.RUNNING:
            task.state_machine.transition_to(TaskState.VERIFYING, reason="Task execution done, verifying output")
        if task.state == TaskState.VERIFYING:
            task.state_machine.transition_to(TaskState.COMPLETED, reason="Verification passed")

        task.finished_at = time.time()
        task.result = result

        # Refresh blocked dependents
        self.get_ready_tasks()
        return task

    def fail_task(self, task_id: str, error: str) -> QueuedTask:
        """Record failure. If retries remain within budget, reset to READY for recovery."""
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(f"Task '{task_id}' not found.")

        task.error = error

        if task.retry_count < self.budget.max_retries and self.budget.current_retries < self.budget.max_retries:
            self.budget.check_and_record(retries=1)
            task.retry_count += 1
            if task.state == TaskState.RUNNING:
                task.state_machine.transition_to(TaskState.RECOVERING, reason=f"Failure encountered: {error}")
            task.state_machine.transition_to(TaskState.RUNNING, reason="Retrying execution")
        else:
            if task.state == TaskState.RUNNING or task.state == TaskState.RECOVERING:
                task.state_machine.transition_to(TaskState.FAILED, reason=f"Terminal failure: {error}")
            task.finished_at = time.time()

        return task

    def stats(self) -> dict[str, Any]:
        """Return execution queue metrics."""
        counts: dict[str, int] = {}
        for task in self._tasks.values():
            st = task.state.value
            counts[st] = counts.get(st, 0) + 1

        return {
            "total_tasks": len(self._tasks),
            "by_state": counts,
            "ready_count": len(self.get_ready_tasks()),
            "budget": self.budget.to_dict(),
        }
