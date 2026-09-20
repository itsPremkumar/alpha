from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionWave:
    """A cohort of tasks that can be safely dispatched concurrently."""

    wave_index: int
    task_ids: list[str] = field(default_factory=list)
    parallel_allowed: bool = True
    barrier_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "wave_index": self.wave_index,
            "task_ids": self.task_ids,
            "parallel_allowed": self.parallel_allowed,
            "barrier_required": self.barrier_required,
        }


def validate_task_dag(task_dag: dict[str, list[str]]) -> tuple[bool, list[str]]:
    """Validate a task DAG without partitioning it.

    Returns (is_valid, errors). Checks: non-empty ids, list deps,
    no self-dependency, no unknown references, no cycles.
    """
    errors: list[str] = []
    if not isinstance(task_dag, dict) or not task_dag:
        return False, ["task_dag must be a non-empty mapping of task_id -> dependencies"]
    for tid, deps in task_dag.items():
        if not isinstance(tid, str) or not tid.strip():
            errors.append(f"Invalid task id: {tid!r}")
            continue
        if not isinstance(deps, list):
            errors.append(f"Task {tid!r} dependencies must be a list")
            continue
        for dep in deps:
            if dep == tid:
                errors.append(f"Task {tid!r} depends on itself")
            elif dep not in task_dag:
                errors.append(f"Task {tid!r} depends on unknown task {dep!r}")

    if not errors:
        cycle = find_cycle(task_dag)
        if cycle:
            errors.append(f"Dependency cycle detected: {' -> '.join(cycle)}")
    return (len(errors) == 0, errors)


def find_cycle(task_dag: dict[str, list[str]]) -> list[str]:
    """Return one cycle path if present, else []. Iterative DFS."""
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        visiting.add(node)
        stack.append(node)
        for dep in task_dag.get(node, []):
            if dep not in task_dag:
                continue
            if dep in visiting:
                idx = stack.index(dep)
                return stack[idx:] + [dep]
            if dep not in visited:
                found = visit(dep)
                if found:
                    return found
        visiting.discard(node)
        visited.add(node)
        stack.pop()
        return None

    for tid in task_dag:
        if tid not in visited:
            found = visit(tid)
            if found:
                return found
    return []


def partition_execution_waves(
    task_dag: dict[str, list[str]],
    *,
    strict: bool = False,
) -> list[ExecutionWave]:
    """
    P12: Partition a task dependency graph into sequential execution waves.
    task_dag: mapping of task_id -> list of dependencies (task_ids it depends on).

    strict=True raises ValueError on cycles/unknown refs instead of emitting
    an emergency wave. Default False preserves the historical lenient behavior.
    """
    if not task_dag:
        return []

    if strict:
        is_valid, errors = validate_task_dag(task_dag)
        if not is_valid:
            raise ValueError(f"Invalid task_dag: {'; '.join(errors)}")

    # Copy dependency structure
    remaining_deps: dict[str, set[str]] = {tid: set(deps) for tid, deps in task_dag.items()}
    # Unknown refs can never be satisfied — in lenient mode surface them in a
    # final fenced wave instead of looping forever.
    known = set(task_dag.keys())
    for tid in list(remaining_deps.keys()):
        remaining_deps[tid] = {d for d in remaining_deps[tid] if d in known}

    completed: set[str] = set()
    waves: list[ExecutionWave] = []
    wave_idx = 1

    while remaining_deps:
        # Find all tasks with all dependencies satisfied
        current_wave_tasks = [tid for tid, deps in remaining_deps.items() if deps.issubset(completed)]

        if not current_wave_tasks:
            # Dependency cycle detected — in lenient mode fence exactly one
            # task per emergency wave so progress is observable; strict mode
            # already raised above.
            current_wave_tasks = sorted(list(remaining_deps.keys()))[:1]

        waves.append(
            ExecutionWave(
                wave_index=wave_idx,
                task_ids=sorted(current_wave_tasks),
                parallel_allowed=len(current_wave_tasks) > 1,
                barrier_required=True,
            )
        )

        for tid in current_wave_tasks:
            completed.add(tid)
            remaining_deps.pop(tid, None)

        wave_idx += 1

    return waves
