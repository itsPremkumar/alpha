"""Tests for alpha.graph.dag — deterministic DAG validation and scheduling."""

from __future__ import annotations

import pytest

from alpha.graph.dag import (
    CycleDetected,
    critical_path,
    execution_waves,
    find_cycle,
    find_unknown_dependencies,
    propagate_failures,
    ready_tasks,
    validate_tasks,
)
from alpha.graph.models import TaskNode, TaskStatus


def _task(task_id: str, *, depends_on: list[str] | None = None, status: TaskStatus = TaskStatus.PENDING) -> TaskNode:
    return TaskNode(
        task_id=task_id,
        goal_id="goal-1",
        title=f"Task {task_id}",
        depends_on=depends_on or [],
        status=status,
    )


class TestCycleDetection:
    def test_no_cycle(self) -> None:
        assert find_cycle({"a": set(), "b": {"a"}, "c": {"b"}}) is None

    def test_simple_cycle(self) -> None:
        dependencies = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        cycle = find_cycle(dependencies)
        assert cycle is not None
        assert len(cycle) == 4  # a -> b -> c -> a

    def test_self_cycle(self) -> None:
        dependencies = {"a": {"a"}}
        cycle = find_cycle(dependencies)
        assert cycle is not None

    def test_validate_tasks_raises_on_cycle(self) -> None:
        tasks = [_task("a", depends_on=["b"]), _task("b", depends_on=["a"])]
        with pytest.raises(CycleDetected) as exc_info:
            validate_tasks(tasks, goal_id="goal-1")
        assert "cycle" in str(exc_info.value).lower()


class TestUnknownDependencies:
    def test_no_unknown_dependencies(self) -> None:
        tasks = [_task("a"), _task("b", depends_on=["a"])]
        report = validate_tasks(tasks, goal_id="goal-1")
        assert report.ok is True

    def test_unknown_dependency_is_reported(self) -> None:
        tasks = [_task("a", depends_on=["missing"])]
        report = validate_tasks(tasks, goal_id="goal-1")
        assert report.ok is False
        assert "missing" in report.unknown_dependencies[0]

    def test_find_unknown_dependencies_returns_all(self) -> None:
        dependencies = {"a": {"x", "y"}, "b": {"z"}}
        unknown = find_unknown_dependencies(dependencies)
        assert len(unknown) == 3


class TestExecutionWaves:
    def test_linear_chain(self) -> None:
        tasks = [_task("a"), _task("b", depends_on=["a"]), _task("c", depends_on=["b"])]
        waves = execution_waves(tasks)
        assert len(waves) == 3
        assert waves[0] == ["a"]
        assert waves[1] == ["b"]
        assert waves[2] == ["c"]

    def test_parallel_tasks(self) -> None:
        tasks = [_task("a"), _task("b"), _task("c")]
        waves = execution_waves(tasks)
        assert len(waves) == 1
        assert set(waves[0]) == {"a", "b", "c"}

    def test_diamond(self) -> None:
        tasks = [
            _task("a"),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["a"]),
            _task("d", depends_on=["b", "c"]),
        ]
        waves = execution_waves(tasks)
        assert len(waves) == 3
        assert waves[0] == ["a"]
        assert set(waves[1]) == {"b", "c"}
        assert waves[2] == ["d"]

    def test_cycle_raises(self) -> None:
        tasks = [_task("a", depends_on=["b"]), _task("b", depends_on=["a"])]
        with pytest.raises(CycleDetected):
            execution_waves(tasks)


class TestCriticalPath:
    def test_linear_chain(self) -> None:
        tasks = [_task("a"), _task("b", depends_on=["a"]), _task("c", depends_on=["b"])]
        path = critical_path(tasks)
        assert path == ["a", "b", "c"]

    def test_parallel_tasks(self) -> None:
        tasks = [_task("a"), _task("b"), _task("c")]
        path = critical_path(tasks)
        assert len(path) == 1

    def test_diamond(self) -> None:
        tasks = [
            _task("a"),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["a"]),
            _task("d", depends_on=["b", "c"]),
        ]
        path = critical_path(tasks)
        assert len(path) == 3


class TestReadyTasks:
    def test_no_ready_tasks_when_all_pending_with_deps(self) -> None:
        tasks = [_task("a", depends_on=["b"]), _task("b")]
        ready = ready_tasks(tasks)
        assert len(ready) == 1
        assert ready[0].task_id == "b"

    def test_ready_tasks_respects_concurrency_cap(self) -> None:
        tasks = [_task("a"), _task("b"), _task("c")]
        ready = ready_tasks(tasks, concurrency_cap=2)
        assert len(ready) == 2

    def test_ready_tasks_excludes_blocked_by_default(self) -> None:
        tasks = [_task("a", status=TaskStatus.BLOCKED), _task("b")]
        ready = ready_tasks(tasks)
        assert len(ready) == 1
        assert ready[0].task_id == "b"

    def test_ready_tasks_includes_blocked_when_requested(self) -> None:
        """A blocked task carries a recorded reason and needs explicit re-entry.

        ``allow_blocked`` is the signal that the caller decided the blocker was
        resolved. A blocked task with **no** reason stays excluded even then,
        because an unexplained block is indistinguishable from a task nobody
        scheduled.
        """
        blocked = _task("a", status=TaskStatus.BLOCKED)
        blocked.failure_summary = "browser driver unavailable"
        tasks = [blocked, _task("b")]
        ready = ready_tasks(tasks, include_blocked=True)
        assert len(ready) == 2

    def test_blocked_without_reason_is_excluded_even_when_allowed(self) -> None:
        tasks = [_task("a", status=TaskStatus.BLOCKED), _task("b")]
        ready = ready_tasks(tasks, include_blocked=True)
        assert len(ready) == 1
        assert ready[0].task_id == "b"

    def test_zero_concurrency_cap_is_refused(self) -> None:
        tasks = [_task("a")]
        with pytest.raises(ValueError, match="refused"):
            ready_tasks(tasks, concurrency_cap=0)


class TestPropagateFailures:
    def test_failure_blocks_downstream(self) -> None:
        tasks = [
            _task("a", status=TaskStatus.FAILED),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["b"]),
        ]
        moved = propagate_failures(tasks, failed=["a"])
        assert len(moved) == 2
        assert tasks[1].status is TaskStatus.BLOCKED
        assert tasks[2].status is TaskStatus.BLOCKED

    def test_failure_does_not_block_succeeded(self) -> None:
        tasks = [
            _task("a", status=TaskStatus.FAILED),
            _task("b", depends_on=["a"], status=TaskStatus.SUCCEEDED),
        ]
        moved = propagate_failures(tasks, failed=["a"])
        assert len(moved) == 0
        assert tasks[1].status is TaskStatus.SUCCEEDED

    def test_no_failure_no_block(self) -> None:
        tasks = [_task("a"), _task("b", depends_on=["a"])]
        moved = propagate_failures(tasks, failed=[])
        assert len(moved) == 0


class TestValidateTasks:
    def test_valid_plan(self) -> None:
        tasks = [_task("a"), _task("b", depends_on=["a"])]
        report = validate_tasks(tasks, goal_id="goal-1")
        assert report.ok is True
        assert report.task_count == 2
        assert report.depth == 2

    def test_report_includes_waves(self) -> None:
        tasks = [_task("a"), _task("b"), _task("c", depends_on=["a", "b"])]
        report = validate_tasks(tasks, goal_id="goal-1")
        assert len(report.waves) == 2

    def test_report_includes_critical_path(self) -> None:
        tasks = [_task("a"), _task("b", depends_on=["a"]), _task("c", depends_on=["b"])]
        report = validate_tasks(tasks, goal_id="goal-1")
        assert report.critical_path == ("a", "b", "c")
