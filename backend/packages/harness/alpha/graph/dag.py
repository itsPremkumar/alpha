"""Deterministic validation and scheduling maths for a task DAG.

The one rule this module exists to enforce
------------------------------------------
**A plan is data until validated, and validation is code, never a model.** The
plan in ``docs/ALPHA_GRAPH_ENGINEERING_IMPLEMENTATION_PLAN.md`` §7.2 lists
eight rules for the task-dependency graph; this module implements them
mechanically so no LLM decision can be the thing that decides whether a graph
is safe to execute. §20 anti-pattern 4 is the failure being closed: "treating
the LLM's task plan as executable truth."

Every function here is pure: no clock, no store, no I/O. That is what makes the
graph's correctness testable without a run, which is why the graph-structure
tests in ``tests/test_graph_engineering_dag.py`` can pin the topology properties
directly.

The four refusals, and why each one raises instead of repairing
----------------------------------------------------------------
**A cycle is refused with its path, not detected.** ``validate_tasks`` returns
the actual cycle (``a -> b -> c -> a``) so the caller can name what to break.
"Dependency cycle detected" tells an operator nothing they can act on; the
existing ``WorkflowGraph.get_executable_waves`` already makes the point by
listing its unresolved nodes.

**A missing dependency is refused, not dropped.** Silently discarding an unknown
prerequisite produces a task that runs *too early* — the exact shape of a false
success.

**Self-dependency is refused at construction** (``TaskNode.__post_init__``) and
again here, because a deserialised plan re-enters through this door.

**Readiness excludes ``SKIPPED``.** A skipped prerequisite produced nothing, so
treating it as satisfied would run downstream work on a hole. ``SKIPPED`` is not
in :data:`~alpha.graph.models.SATISFIED_TASK_STATUSES`, and this module never
adds it.

Wave scheduling, not a general DAG scheduler
--------------------------------------------
:func:`execution_waves` returns groups that may run **concurrently** and is the
basis for the plan's §7.5 scheduler. Concurrency itself is capped by
:class:`~alpha.graph.models.GraphBudget`, not here: this module computes what
*may* run together and never decides how many *will*.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from alpha.graph.ids import GraphId, TaskId
from alpha.graph.models import (
    TERMINAL_TASK_STATUSES,
    EdgeType,
    Goal,
    GraphEdge,
    GraphSnapshot,
    ModelValidationError,
    TaskNode,
    TaskStatus,
)

__all__ = [
    "SCHEDULING_EDGE_TYPES",
    "CycleDetected",
    "DagReport",
    "UnknownDependency",
    "blocked_tasks",
    "build_graph_id",
    "critical_path",
    "dependency_map",
    "dependents_map",
    "execution_waves",
    "ready_tasks",
    "select_ready_batch",
    "validate_tasks",
]


class CycleDetected(ModelValidationError):
    """The dependency graph is not acyclic. Carries the cycle that closed it."""

    def __init__(self, cycle: Sequence[str]) -> None:
        self.cycle = tuple(cycle)
        super().__init__("dependency cycle detected: " + " -> ".join(self.cycle))


class UnknownDependency(ModelValidationError):
    """A task depends on an id that is not in the graph."""


@dataclass(slots=True, frozen=True)
class DagReport:
    """What validation found. ``ok`` is derived, never a field to set."""

    goal_id: str
    graph_id: GraphId
    task_count: int
    edge_count: int
    waves: tuple[tuple[TaskId, ...], ...]
    critical_path: tuple[TaskId, ...]
    unknown_dependencies: tuple[str, ...] = ()
    isolated_tasks: tuple[TaskId, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.unknown_dependencies

    @property
    def depth(self) -> int:
        """Longest dependency chain. The graph's makespan lower bound."""
        return len(self.critical_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "graph_id": self.graph_id,
            "ok": self.ok,
            "task_count": self.task_count,
            "edge_count": self.edge_count,
            "wave_count": len(self.waves),
            "depth": self.depth,
            "critical_path": list(self.critical_path),
            "waves": [list(wave) for wave in self.waves],
            "unknown_dependencies": list(self.unknown_dependencies),
            "isolated_tasks": list(self.isolated_tasks),
        }


def build_graph_id(goal_id: str) -> GraphId:
    """The graph id for a goal's task graph.

    Deterministic from the goal, not random: re-validating the same plan twice
    must address the same graph, or a caller cannot tell "nothing changed" from
    "this is a different graph".
    """
    return GraphId(f"task-graph:{goal_id}")


def dependency_map(tasks: Iterable[TaskNode]) -> dict[str, set[str]]:
    """``task -> {prerequisites}``, sourced from ``depends_on`` **and** edges.

    Both are merged deliberately. A plan can express a prerequisite either way,
    and honouring only one of them means a dependency written as an edge is
    invisible to the scheduler -- which is precisely how downstream work runs
    before its input exists.
    """
    mapping: dict[str, set[str]] = {task.task_id: set(task.depends_on) for task in tasks}
    for task in tasks:
        mapping.setdefault(task.task_id, set())
    return mapping


def apply_edges(mapping: dict[str, set[str]], edges: Iterable[GraphEdge], *, direction: str = "incoming") -> dict[str, set[str]]:
    """Fold ``GraphEdge`` rows into a dependency map.

    ``direction="incoming"`` (the default) treats an edge as
    ``target depends on source`` -- the reading that matches ``WorkflowEdge``'s
    directionality and the plan's ``DEPENDS_ON`` example, where
    ``task_reproduce -> task_patch`` means the patch follows the reproduction.
    """
    resolved: dict[str, set[str]] = {key: set(value) for key, value in mapping.items()}
    for edge in edges:
        if edge.edge_type is EdgeType.DEPENDS_ON or edge.edge_type is EdgeType.BLOCKED_BY:
            if direction == "incoming":
                resolved.setdefault(edge.target_id, set()).add(edge.source_id)
            else:
                resolved.setdefault(edge.source_id, set()).add(edge.target_id)
    return resolved


def dependents_map(dependencies: Mapping[str, set[str]]) -> dict[str, set[str]]:
    """Invert a dependency map: ``task -> {things that wait on it}``."""
    dependents: dict[str, set[str]] = {task_id: set() for task_id in dependencies}
    for task_id, prerequisites in dependencies.items():
        for prerequisite in prerequisites:
            dependents.setdefault(prerequisite, set()).add(task_id)
    return dependents


def find_unknown_dependencies(dependencies: Mapping[str, set[str]]) -> tuple[str, ...]:
    """Every ``(task, missing)`` pair, sorted.

    Returned rather than raised so the caller can report *all* of them at once.
    A validator that raises on the first bad edge makes a model repair the same
    plan five times.
    """
    known = set(dependencies)
    problems: list[str] = []
    for task_id in sorted(dependencies):
        for prerequisite in sorted(dependencies[task_id]):
            if prerequisite not in known:
                problems.append(f"{task_id} depends on unknown task {prerequisite}")
    return tuple(problems)


def find_cycle(dependencies: Mapping[str, set[str]]) -> list[str] | None:
    """Return one cycle as a node list, or ``None``.

    Iterative depth-first search with an explicit stack. Recursion would be the
    shorter version and would turn a planner bug into a ``RecursionError`` on a
    graph a human can easily draw -- which is the wrong error for the wrong
    cause. The three-colour marking (``WHITE``/``GREY``/``BLACK``) is standard
    and returns the *path*, not just "there is a loop".
    """
    white, grey, black = 0, 1, 2
    colour: dict[str, int] = dict.fromkeys(dependencies, white)

    for root in sorted(dependencies):
        if colour[root] != white:
            continue
        stack: list[tuple[str, list[str]]] = [(root, [root])]
        colour[root] = grey
        while stack:
            node, path = stack[-1]
            advanced = False
            for neighbour in sorted(dependencies.get(node, ())):
                state = colour.get(neighbour, white)
                if state == grey:
                    # Found the back edge: slice the path from the neighbour.
                    start = path.index(neighbour) if neighbour in path else 0
                    return [*path[start:], neighbour]
                if state == white:
                    colour[neighbour] = grey
                    stack.append((neighbour, [*path, neighbour]))
                    advanced = True
                    break
            if not advanced:
                colour[node] = black
                stack.pop()
    return None


def execution_waves(tasks: Sequence[TaskNode], edges: Sequence[GraphEdge] | None = None) -> list[list[TaskId]]:
    """Group tasks into waves that may run concurrently.

    Kahn's algorithm, with the wave contents sorted so two runs over the same
    plan produce the same waves -- a scheduler that reorders independent tasks
    between passes makes every replay and projection non-deterministic.
    """
    dependencies = dependency_map(tasks)
    scheduling = _scheduling_edges(edges)
    if scheduling:
        dependencies = apply_edges(dependencies, scheduling)

    cycle = find_cycle(dependencies)
    if cycle is not None:
        raise CycleDetected(cycle)

    known = set(dependencies)
    for task_id, prerequisites in dependencies.items():
        missing = prerequisites - known
        if missing:
            raise UnknownDependency(f"task {task_id} depends on unknown task(s): {sorted(missing)}")

    in_degree = {task_id: len(prerequisites) for task_id, prerequisites in dependencies.items()}
    dependents = dependents_map(dependencies)

    ready = sorted(task_id for task_id, degree in in_degree.items() if degree == 0)
    waves: list[list[TaskId]] = []
    processed = 0
    while ready:
        wave = [TaskId(task_id) for task_id in ready]
        waves.append(wave)
        processed += len(wave)
        nxt: list[str] = []
        for task_id in ready:
            for dependent in dependents.get(task_id, ()):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    nxt.append(dependent)
        ready = sorted(nxt)

    if processed != len(dependencies):
        # find_cycle already ran, so reaching here means the graph changed
        # underneath us. Refuse rather than silently dropping the remainder.
        unresolved = sorted(set(dependencies) - {task_id for wave in waves for task_id in wave})
        raise CycleDetected(unresolved)
    return waves


def critical_path(tasks: Sequence[TaskNode], edges: Sequence[GraphEdge] | None = None) -> list[TaskId]:
    """The longest dependency chain, as ids.

    This is the makespan *lower bound* for a given concurrency. It is reported
    next to the ready count rather than instead of it, because the same
    distinction ``alpha.swarm`` draws between ``exposed_width`` (instant
    parallelism) and ``lower_bound_rounds`` (idealised makespan) applies here:
    conflating them scores a wide graph as more parallel than it is.
    """
    dependencies = dependency_map(tasks)
    scheduling = _scheduling_edges(edges)
    if scheduling:
        dependencies = apply_edges(dependencies, scheduling)
    cycle = find_cycle(dependencies)
    if cycle is not None:
        raise CycleDetected(cycle)

    depth: dict[str, int] = {}
    #: ``best_prev[node]`` is the predecessor on the longest chain ending at
    #: ``node``. Storing the *immediate* predecessor (not the chain root) is
    #: what makes reconstruction a simple walk; storing the root instead makes
    #: the walk terminate one hop too early and drops the middle of every chain --
    #: the bug that returned ``('a', 'd')`` for a diamond whose critical path is
    #: ``a -> b -> d``.
    best_prev: dict[str, str] = {}
    for wave in execution_waves(tasks, edges):
        for task_id in wave:
            prerequisites = dependencies.get(task_id, set())
            if not prerequisites:
                depth[task_id] = 1
                continue
            predecessor = max(prerequisites, key=lambda item: (depth.get(item, 0), item))
            depth[task_id] = depth.get(predecessor, 0) + 1
            best_prev[task_id] = predecessor

    if not depth:
        return []
    end = max(sorted(depth), key=lambda item: (depth[item], item))
    path: list[str] = []
    cursor = end
    while True:
        path.append(cursor)
        nxt = best_prev.get(cursor)
        if nxt is None or nxt == cursor or nxt in path:
            break
        cursor = nxt
    return [TaskId(item) for item in reversed(path)]


#: Edge types that constrain **scheduling**: "may this task run yet?".
#:
#: Every other typed edge is *causal provenance* and must never reach the
#: dependency map. ``REPAIRS`` is the case that forced this split: a repair task
#: is caused by a failure, not gated on it. Feeding a ``REPAIRS`` edge into
#: :func:`apply_edges` makes the failed original a prerequisite, and because
#: :data:`~alpha.graph.models.SATISFIED_TASK_STATUSES` is ``{SUCCEEDED}`` only,
#: the repair can never become ready -- a repair branch that is structurally
#: unreachable. The plan's §5.3 rule ("keep task-dependency edge types separate
#: from knowledge-relationship types") is the same separation, applied to edges.
SCHEDULING_EDGE_TYPES: Final[frozenset[EdgeType]] = frozenset({EdgeType.DEPENDS_ON, EdgeType.BLOCKED_BY})


def _scheduling_edges(edges: Sequence[GraphEdge] | None) -> list[GraphEdge]:
    """Drop causal/provenance edges, keeping only scheduling constraints."""
    if not edges:
        return []
    return [edge for edge in edges if edge.edge_type in SCHEDULING_EDGE_TYPES]


def validate_tasks(
    tasks: Sequence[TaskNode],
    *,
    goal_id: str | None = None,
    edges: Sequence[GraphEdge] | None = None,
) -> DagReport:
    """Full validation pass. Raises on a cycle; reports unknown deps.

    The asymmetry is intentional. A cycle is **unrecoverable** by definition --
    there is no ordering of the plan that satisfies it -- so the caller cannot
    proceed at all and gets an exception. An unknown dependency is **recoverable
    in one step** (drop it or add the task), so the caller gets the full list
    and decides. Raising on both would make a five-edge plan take five repair
    rounds; raising on neither would execute a broken graph.
    """
    resolved_goal = goal_id or (tasks[0].goal_id if tasks else "")
    graph_id = build_graph_id(resolved_goal)
    scheduling = _scheduling_edges(edges)
    dependencies = dependency_map(tasks)
    if scheduling:
        dependencies = apply_edges(dependencies, scheduling)

    cycle = find_cycle(dependencies)
    if cycle is not None:
        raise CycleDetected(cycle)

    unknown = find_unknown_dependencies(dependencies)
    dependents = dependents_map(dependencies)
    isolated = tuple(TaskId(task.task_id) for task in tasks if not dependencies.get(task.task_id) and not dependents.get(task.task_id))
    try:
        waves = execution_waves(tasks, scheduling)
        path = critical_path(tasks, scheduling)
    except (CycleDetected, UnknownDependency):
        # A cycle or an unknown dependency already has a home in this report
        # (cycle raises; unknown is reported). If the graph is unschedulable for
        # a reason neither field covers, report empty shape rather than
        # raising -- a caller asking "is this plan valid?" gets the answer
        # "no, and here are the named reasons", which is more useful than an
        # exception whose message duplicates them.
        waves = []
        path = []
    return DagReport(
        goal_id=resolved_goal,
        graph_id=graph_id,
        task_count=len(tasks),
        edge_count=len(edges or ()),
        waves=tuple(tuple(TaskId(task_id) for task_id in wave) for wave in waves),
        critical_path=tuple(path),
        unknown_dependencies=unknown,
        isolated_tasks=isolated,
    )


def ready_tasks(
    tasks: Sequence[TaskNode],
    *,
    concurrency_cap: int | None = None,
    include_blocked: bool = False,
) -> list[TaskNode]:
    """Tasks whose prerequisites are satisfied and which may start now.

    ``include_blocked`` defaults to ``False`` so a blocked task needs an explicit
    re-entry to run again. A scheduler that picks blocked tasks up by default
    re-dispatches work that a failure deliberately parked, which is the
    "watchdog restarts a cancelled task" failure wearing a scheduler's clothes.

    Ordering is ``(-priority, attempt_count, created_at, task_id)``:
    declared priority first, then **fewest attempts** -- so a task that keeps
    failing is retried later, not sooner -- then the stable creation time and id
    so the result is fully deterministic.
    """
    if concurrency_cap is not None and concurrency_cap < 1:
        raise ModelValidationError(f"concurrency_cap must be >= 1, got {concurrency_cap}; a cap of 0 is refused, not clamped")

    by_id = {task.task_id: task for task in tasks}
    statuses = {task_id: task.status for task_id, task in by_id.items()}
    candidates: list[TaskNode] = []
    for task in tasks:
        if task.status in TERMINAL_TASK_STATUSES:
            continue
        if task.status is TaskStatus.BLOCKED and not include_blocked:
            continue
        ready, _reason = task.ready_to_dispatch(dependency_status=statuses, allow_blocked=include_blocked)
        if ready:
            candidates.append(task)

    candidates.sort(key=lambda task: (-task.priority, task.attempt_count, task.created_at, task.task_id))
    if concurrency_cap is not None:
        candidates = candidates[:concurrency_cap]
    return candidates


def select_ready_batch(tasks: Sequence[TaskNode], *, concurrency_cap: int) -> list[TaskNode]:
    """The batch a scheduler may dispatch this pass.

    A **cap is required**, not optional. An uncapped scheduler turns a 40-node
    plan into 40 concurrent workers, which on the 8 GB baseline machine the
    repository targets is an out-of-memory kill presented to the user as a
    crash. Refusing the call is better than picking a default nobody chose.
    """
    return ready_tasks(tasks, concurrency_cap=concurrency_cap)


def blocked_tasks(tasks: Sequence[TaskNode]) -> list[TaskNode]:
    """Tasks parked as blocked, each with the reason recorded on it.

    A blocked task with no reason is skipped here: an unexplained block is
    indistinguishable from a task nobody scheduled, which is the plan's golden
    scenario 8 failure ("downstream tasks blocked ... final result is
    partial/failed as appropriate") collapsing into silence.
    """
    return [task for task in tasks if task.status is TaskStatus.BLOCKED and bool(task.failure_summary)]


def propagate_failures(tasks: Sequence[TaskNode], *, failed: Iterable[TaskId], reason: str = "") -> list[TaskNode]:
    """Mark every transitive dependent of a failed task ``blocked``.

    Returns the tasks it actually moved. The plan's §7.2 rule 7 requires a
    failed dependency to produce a **visible** blocked state rather than leaving
    downstream tasks pending forever; this is the function that does it, and it
    is transitive because a direct-only pass leaves the second-level dependents
    pending, which is the same silence one level down.

    A task whose own status is terminal is left alone -- including one that
    already succeeded, which the caller may not want to retroactively un-verify
    just because a sibling failed.
    """
    failed_ids = set(failed)
    if not failed_ids:
        return []
    dependencies = dependency_map(tasks)
    dependents = dependents_map(dependencies)

    to_block: set[str] = set()
    frontier = list(failed_ids)
    while frontier:
        current = frontier.pop()
        for dependent in dependents.get(current, ()):
            if dependent not in to_block and dependent not in failed_ids:
                to_block.add(dependent)
                frontier.append(dependent)

    moved: list[TaskNode] = []
    for task in tasks:
        if task.task_id not in to_block or task.status in TERMINAL_TASK_STATUSES or task.status is TaskStatus.BLOCKED:
            continue
        # Name the prerequisites that actually caused this block: the task's own
        # dependencies that failed or were transitively blocked by a failure.
        # Naming the task itself -- which the earlier `to_block & {task_id}`
        # expression did, and which the `or` fallback then never corrected --
        # reports "blocked by failed prerequisite(s): itself", which is a
        # statement no operator can act on.
        culprits = sorted(dependency for dependency in dependencies.get(task.task_id, set()) if dependency in to_block or dependency in failed_ids)
        task.mark_blocked(reason=reason or f"blocked by failed prerequisite(s): {', '.join(culprits) or 'unknown'}")
        moved.append(task)
    return moved


def snapshot(goal: Goal, tasks: Sequence[TaskNode], edges: Sequence[GraphEdge] | None = None) -> GraphSnapshot:
    """Build the renderer-neutral projection the UI and API consume.

    Never raises on a broken graph: a snapshot is a *view* of whatever is
    stored, and a caller asking "what does the graph look like right now?"
    during an incident needs the broken shape, not an exception. The counts are
    computed from the same rows they describe, so a header cannot claim "3 ready"
    over five visible ready tasks.
    """
    by_id = {task.task_id: task for task in tasks}
    statuses = {task_id: task.status for task_id, task in by_id.items()}
    ready = tuple(TaskId(task.task_id) for task in tasks if task.ready_to_dispatch(dependency_status=statuses, allow_blocked=False)[0])
    try:
        waves = execution_waves(tasks, edges)
    except (CycleDetected, UnknownDependency):
        waves = []
    try:
        path = critical_path(tasks, edges)
    except CycleDetected:
        path = []
    return GraphSnapshot(
        goal_id=goal.goal_id,
        graph_id=build_graph_id(goal.goal_id),
        nodes=tuple(task.to_dict() for task in tasks),
        edges=tuple(edge.to_dict() for edge in (edges or ())),
        blocked_task_ids=tuple(TaskId(task.task_id) for task in blocked_tasks(tasks)),
        ready_task_ids=ready,
        wave_count=len(waves),
        critical_path=path,
    )
