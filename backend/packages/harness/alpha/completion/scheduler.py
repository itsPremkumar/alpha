"""Readiness, dependency order and bounded rolling scheduling.

The scheduler is a *projection over state that already exists* — it never
mutates the store itself. It answers one question ("what may start now, and
what is stopping the rest") from the task DAG, the gate statuses and the
dispatch records, and the engine applies its answer through the state machine.

The ten scheduler rules the plan asks for reduce to four this module owns:

1. **Only nodes whose dependencies are verified become READY.** A dependency
   that merely *ran* is not a dependency that *held*.
2. **Rolling re-evaluation.** Readiness is recomputed from scratch on every
   call, so a leaf that verifies immediately unblocks its dependents with no
   cached plan to go stale.
3. **A failed prerequisite is never silently bypassed.** Descendants of a
   failed/abandoned/blocked dependency are *blocked with the reason*, while
   unrelated branches keep working — one leaf failing must not freeze the
   scope, and must not launder its dependents into running anyway.
4. **Cycles are refused.** A cyclic graph has no topological order, so the
   refusal happens where the graph is built rather than as a hang at
   dispatch time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ContractInvalid
from .models import CompletionScope, DispatchState, GateStatus, TaskNode, TaskState
from .state_machine import is_terminal_task

#: Task states that count as "this dependency held".
_SATISFIED = frozenset({TaskState.VERIFIED})

#: Task states that stop a scope from ever reaching READY through this node.
_DEAD = frozenset({TaskState.ABANDONED, TaskState.FAILED, TaskState.BLOCKED})


@dataclass(frozen=True)
class ReadinessVerdict:
    """Why one task is or is not dispatchable right now."""

    task_id: str
    ready: bool
    reason: str = ""
    blocking_task_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"task_id": self.task_id, "ready": self.ready, "reason": self.reason, "blocking": list(self.blocking_task_ids)}


@dataclass
class SchedulingView:
    """Everything a scheduling pass needs, computed in one read."""

    ready: list[TaskNode] = field(default_factory=list)
    waiting: list[TaskNode] = field(default_factory=list)
    in_flight: list[TaskNode] = field(default_factory=list)
    blocked: list[TaskNode] = field(default_factory=list)
    verdicts: dict[str, ReadinessVerdict] = field(default_factory=dict)

    @property
    def dispatchable(self) -> list[TaskNode]:
        """Ready tasks still under the concurrency ceiling."""
        return self.ready

    def summary(self) -> dict[str, object]:
        return {
            "ready": [task.task_id for task in self.ready],
            "waiting": [task.task_id for task in self.waiting],
            "in_flight": [task.task_id for task in self.in_flight],
            "blocked": [{"task_id": task.task_id, "reason": task.blocked_reason} for task in self.blocked],
        }


def detect_cycle(tasks: dict[str, TaskNode]) -> list[str]:
    """Return one dependency cycle as a task-id path, or ``[]``.

    Iterative DFS with an explicit colour marking, so a deep graph cannot
    blow the interpreter stack. The first cycle found is reported verbatim:
    a refusal that names the loop is actionable, "graph invalid" is not.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {task_id: WHITE for task_id in tasks}
    stack_path: list[str] = []

    for start in tasks:
        if colour[start] is not WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            task_id, index = stack[-1]
            if index == 0:
                colour[task_id] = GREY
                path.append(task_id)
            deps = tasks[task_id].depends_on
            if index < len(deps):
                stack[-1] = (task_id, index + 1)
                nxt = deps[index]
                if nxt not in tasks:
                    continue
                if colour[nxt] is GREY:
                    cycle = path[path.index(nxt) :] + [nxt]
                    stack_path = cycle
                    stack = []
                    break
                if colour[nxt] is WHITE:
                    stack.append((nxt, 0))
            else:
                colour[task_id] = BLACK
                path.pop()
                stack.pop()
        if stack_path:
            return stack_path
    return []


def assert_acyclic(tasks: dict[str, TaskNode]) -> None:
    """Refuse a plan whose dependencies cannot be ordered."""
    cycle = detect_cycle(tasks)
    if cycle:
        raise ContractInvalid(
            f"the task graph contains a dependency cycle: {' -> '.join(cycle)}; a cycle has no execution order",
            findings=[],
            subject=cycle[0],
        )


def verdict_for(scope: CompletionScope, task: TaskNode) -> ReadinessVerdict:
    """Why ``task`` may or may not be dispatched right now."""
    if is_terminal_task(task.state):
        return ReadinessVerdict(task.task_id, False, f"task is {task.state.value} (terminal)")
    if task.state in {TaskState.IN_FLIGHT, TaskState.RETURNED, TaskState.VERIFYING}:
        return ReadinessVerdict(task.task_id, False, f"task is already {task.state.value}")

    blocking: list[str] = []
    reasons: list[str] = []
    for dep_id in task.depends_on:
        dep = scope.tasks.get(dep_id)
        if dep is None:
            blocking.append(dep_id)
            reasons.append(f"dependency {dep_id!r} does not exist")
        elif dep.state in _SATISFIED:
            continue
        elif dep.state in _DEAD:
            blocking.append(dep_id)
            reasons.append(f"dependency {dep_id!r} is {dep.state.value}")
        else:
            blocking.append(dep_id)
            reasons.append(f"dependency {dep_id!r} is {dep.state.value}, not verified")

    # A parent integration task waits for its children: it exists to integrate
    # them, and verifying it first would prove nothing about the whole.
    if task.integration:
        for child in scope.children_of(task.task_id):
            if child.state is not TaskState.VERIFIED:
                blocking.append(child.task_id)
                reasons.append(f"child task {child.task_id!r} is {child.state.value}")

    gates = [scope.gates[gate_id] for gate_id in task.gate_ids if gate_id in scope.gates]
    waiting_gates = [gate.gate_id for gate in gates if gate.status in {GateStatus.PENDING, GateStatus.RUNNING} and gate.required]
    if waiting_gates and task.state in {TaskState.RETURNED, TaskState.VERIFYING}:
        reasons.append(f"gates {waiting_gates} have not run yet")

    if blocking:
        return ReadinessVerdict(task.task_id, False, "; ".join(reasons), tuple(blocking))

    # An unresolved dispatch wave for this task means its previous attempt is
    # still unknown — do not stack a second launch on an unaccounted one.
    unresolved = [dispatch.dispatch_id for dispatch in scope.dispatches.values() if dispatch.task_id == task.task_id and dispatch.state in {DispatchState.REQUESTED, DispatchState.STARTED, DispatchState.UNKNOWN}]
    if unresolved:
        return ReadinessVerdict(task.task_id, False, f"dispatch {unresolved} for this task is unresolved", tuple(unresolved))

    if task.state is TaskState.BLOCKED:
        return ReadinessVerdict(task.task_id, False, task.blocked_reason or "task is blocked")
    if task.attempts >= task.max_attempts:
        return ReadinessVerdict(task.task_id, False, f"retry budget exhausted ({task.attempts}/{task.max_attempts})")
    return ReadinessVerdict(task.task_id, True, "all dependencies are verified")


def scheduling_view(scope: CompletionScope, *, limit: int | None = None) -> SchedulingView:
    """One pass over the whole DAG: buckets plus a verdict per non-terminal task."""
    view = SchedulingView()
    for task in sorted(scope.tasks.values(), key=lambda item: (item.depth, item.task_id)):
        verdict = verdict_for(scope, task)
        view.verdicts[task.task_id] = verdict
        if task.state is TaskState.IN_FLIGHT:
            view.in_flight.append(task)
        elif task.state in {TaskState.BLOCKED, TaskState.FAILED, TaskState.ABANDONED}:
            view.blocked.append(task)
        elif verdict.ready and task.state in {TaskState.READY, TaskState.WAITING}:
            view.ready.append(task)
        elif task.state in {TaskState.READY, TaskState.WAITING}:
            view.waiting.append(task)
        else:
            # RETURNED / VERIFYING / VERIFIED: progressing or done.
            if task.state in {TaskState.RETURNED, TaskState.VERIFYING}:
                view.in_flight.append(task)
    if limit is not None:
        view.ready = view.ready[: max(0, limit)]
    return view


def promote_ready(scope: CompletionScope, *, now: float) -> list[str]:
    """Move every newly-satisfied ``WAITING`` task to ``READY``.

    Applied through the state machine, so the transition is validated even
    though the scheduler is the one that noticed it. Returns the ids moved.
    """
    from .state_machine import assert_task_transition

    moved: list[str] = []
    for task in scope.tasks.values():
        if task.state is not TaskState.WAITING:
            continue
        verdict = verdict_for(scope, task)
        if verdict.ready:
            assert_task_transition(task.state, TaskState.READY)
            task.state = TaskState.READY
            task.blocked_reason = ""
            task.updated_at = now
            moved.append(task.task_id)
    return moved


def propagate_failure(scope: CompletionScope, task_id: str, *, now: float, reason: str) -> list[str]:
    """Block every transitive dependent of a dead task, naming the cause.

    Unrelated branches are untouched (rule 3): the scope keeps making progress
    while the failed prerequisite's dependents are parked *with the reason*
    instead of being dispatched against work that never landed.
    """
    from .state_machine import assert_task_transition

    parked: list[str] = []
    frontier = [task_id]
    seen: set[str] = set()
    while frontier:
        current = frontier.pop()
        for task in scope.tasks.values():
            if current not in task.depends_on or task.task_id in seen:
                continue
            seen.add(task.task_id)
            if task.state in {TaskState.VERIFIED, TaskState.ABANDONED}:
                continue
            if task.state not in {TaskState.WAITING, TaskState.READY, TaskState.BLOCKED}:
                continue
            if task.state is not TaskState.BLOCKED:
                assert_task_transition(task.state, TaskState.BLOCKED)
            task.state = TaskState.BLOCKED
            task.blocked_reason = f"prerequisite {current!r} did not verify: {reason}"
            task.updated_at = now
            parked.append(task.task_id)
            frontier.append(task.task_id)
    return parked


def dispatch_capacity(scope: CompletionScope, settings_max_concurrency: int) -> int:
    """Rolling slots left, given how many launches are already unresolved."""
    in_flight = sum(1 for task in scope.tasks.values() if task.state in {TaskState.IN_FLIGHT, TaskState.RETURNED, TaskState.VERIFYING})
    return max(0, min(settings_max_concurrency, scope.budget.max_concurrency) - in_flight)


__all__ = [
    "ReadinessVerdict",
    "SchedulingView",
    "assert_acyclic",
    "detect_cycle",
    "dispatch_capacity",
    "propagate_failure",
    "promote_ready",
    "scheduling_view",
    "verdict_for",
]
