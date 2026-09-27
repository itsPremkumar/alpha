"""Structural topology routing for swarm plans.

The keyword/semantic signals in :mod:`alpha.swarm.estimator` can only read the
*goal text*.  Once a plan exists, its dependency graph is a much better signal:
the same sentence ("build the platform") decomposes into a wide fan-out, a deep
chain, or several disconnected components, and each shape wants a different
coordination topology.

This module measures that shape and derives a mode from it in ``O(V + E)``.
It is deliberately *only* structural — it never reads the goal text, so a route
rationale always cites a number a reviewer can re-derive from the plan.  Semantic
overrides (debate, ensemble, worktree isolation) belong to
:mod:`alpha.swarm.strategy`, which layers structure under intent.

Nothing here mutates a plan or decides whether work is worth doing: a route with
``should_swarm=False`` still decomposes, because dropping nodes would silently
convert "this shape does not need coordination" into "these tasks do not exist".
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alpha.swarm.models import SwarmMode, SwarmTaskNode

__all__ = [
    "DagFeatures",
    "TopologyRoute",
    "compute_dag_features",
    "route_topology",
]

# A plan this small cannot pay back coordination overhead no matter its shape,
# so structural routing reports ``should_swarm=False`` instead of inventing a
# benefit.  Three nodes is the smallest fan-out/fan-in that overlaps spawn cost.
MIN_SWARM_NODES = 3

# Edge density over all ordered pairs, where ``coupling >= 0.30`` means "within
# reach of the densest acyclic shape possible".  A DAG's ceiling is a balanced
# bipartite graph (K2,2 = 0.333, K3,3 = 0.30), so 0.30 is not an arbitrary
# number: it is the point where almost every task already depends on almost
# every other task and peer fan-out can no longer hide the coordination cost.
# Raising this to 0.50 would make the rule unreachable and silently dead.
COUPLING_THRESHOLD = 0.30


@dataclass(frozen=True)
class DagFeatures:
    """Measured shape of one dependency graph.

    Every field is derived, never estimated.  ``level`` assignments use Kahn's
    algorithm, so ``depth`` is the longest dependency chain (the critical path
    in *hops*, not seconds) and ``max_width`` is the widest topological level —
    an upper bound on how many tasks can actually run at once.
    """

    task_count: int
    edge_count: int
    depth: int
    max_width: int
    mean_width: float
    root_count: int
    leaf_count: int
    max_fan_out: int
    max_fan_in: int
    coupling: float
    width_depth_ratio: float
    component_count: int
    chain_ratio: float
    cyclic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_count": self.task_count,
            "edge_count": self.edge_count,
            "depth": self.depth,
            "max_width": self.max_width,
            "mean_width": self.mean_width,
            "root_count": self.root_count,
            "leaf_count": self.leaf_count,
            "max_fan_out": self.max_fan_out,
            "max_fan_in": self.max_fan_in,
            "coupling": self.coupling,
            "width_depth_ratio": self.width_depth_ratio,
            "component_count": self.component_count,
            "chain_ratio": self.chain_ratio,
            "cyclic": self.cyclic,
        }


@dataclass(frozen=True)
class TopologyRoute:
    """The mode a graph's shape calls for, with the numbers that justify it."""

    mode: SwarmMode
    should_swarm: bool
    confidence: float
    rationale: str
    features: DagFeatures

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "should_swarm": self.should_swarm,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "features": self.features.to_dict(),
        }


def _dependencies(tasks: Mapping[str, SwarmTaskNode] | Iterable[SwarmTaskNode]) -> dict[str, list[str]]:
    if isinstance(tasks, Mapping):
        nodes: list[SwarmTaskNode] = list(tasks.values())
    else:
        nodes = list(tasks)
    return {node.task_id: [dep for dep in node.dependencies] for node in nodes}


def compute_dag_features(tasks: Mapping[str, SwarmTaskNode] | Iterable[SwarmTaskNode]) -> DagFeatures:
    """Measure graph shape.  Single Kahn pass; no plan mutation, no I/O."""

    deps = _dependencies(tasks)
    known = set(deps)

    # Unknown dependencies are an invalid plan; the scheduler rejects those
    # before dispatch.  Here they are simply dropped so feature extraction can
    # still describe the reachable portion instead of raising mid-routing.
    children: dict[str, list[str]] = {task_id: [] for task_id in deps}
    edge_count = 0
    for task_id, task_deps in deps.items():
        for dep in task_deps:
            if dep in known and dep != task_id:
                children[dep].append(task_id)
                edge_count += 1

    in_degree = {task_id: sum(1 for dep in deps[task_id] if dep in known and dep != task_id) for task_id in deps}
    level: dict[str, int] = {}
    queue: deque[str] = deque(sorted(task_id for task_id, degree in in_degree.items() if degree == 0))
    for task_id in queue:
        level[task_id] = 0
    processed = 0
    while queue:
        task_id = queue.popleft()
        processed += 1
        for child in sorted(children[task_id]):
            level[child] = max(level.get(child, 0), level[task_id] + 1)
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    cyclic = processed != len(deps) and bool(deps)
    # Unreached nodes (cycle members) are reported at level 0 so the counts stay
    # honest about *measured* reachability; ``cyclic`` is what routing reads.
    for task_id in deps:
        level.setdefault(task_id, 0)

    per_level: dict[int, int] = {}
    for task_id in deps:
        per_level[level[task_id]] = per_level.get(level[task_id], 0) + 1

    task_count = len(deps)
    depth = (max(per_level) + 1) if per_level else 0
    max_width = max(per_level.values()) if per_level else 0
    mean_width = (task_count / depth) if depth else 0.0
    root_count = sum(1 for task_id in deps if not [d for d in deps[task_id] if d in known and d != task_id])
    leaf_count = sum(1 for task_id in deps if not children[task_id])
    max_fan_out = max((len(children[task_id]) for task_id in deps), default=0)
    max_fan_in = max((len([d for d in deps[task_id] if d in known and d != task_id]) for task_id in deps), default=0)
    possible = task_count * (task_count - 1)
    coupling = (edge_count / possible) if possible > 0 else 0.0
    width_depth_ratio = (max_width / depth) if depth else 0.0

    chain_nodes = 0
    for task_id in deps:
        fan_in = len([d for d in deps[task_id] if d in known and d != task_id])
        if fan_in <= 1 and len(children[task_id]) <= 1:
            chain_nodes += 1
    chain_ratio = (chain_nodes / task_count) if task_count else 0.0

    return DagFeatures(
        task_count=task_count,
        edge_count=edge_count,
        depth=depth,
        max_width=max_width,
        mean_width=round(mean_width, 3),
        root_count=root_count,
        leaf_count=leaf_count,
        max_fan_out=max_fan_out,
        max_fan_in=max_fan_in,
        coupling=round(coupling, 4),
        width_depth_ratio=round(width_depth_ratio, 4),
        component_count=_weakly_connected_components(deps, children),
        chain_ratio=round(chain_ratio, 4),
        cyclic=cyclic,
    )


def _weakly_connected_components(deps: Mapping[str, list[str]], children: Mapping[str, list[str]]) -> int:
    """Weakly connected components — disjoint sub-swarms need no shared bus."""

    undirected: dict[str, set[str]] = {task_id: set() for task_id in deps}
    for task_id, task_deps in deps.items():
        for dep in task_deps:
            if dep in undirected:
                undirected[task_id].add(dep)
                undirected[dep].add(task_id)
    for task_id, child_list in children.items():
        for child in child_list:
            if child in undirected:
                undirected[task_id].add(child)
                undirected[child].add(task_id)

    seen: set[str] = set()
    components = 0
    for start in sorted(undirected):
        if start in seen:
            continue
        components += 1
        stack = [start]
        seen.add(start)
        while stack:
            current = stack.pop()
            for neighbour in sorted(undirected[current]):
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
    return components


def route_topology(
    features: DagFeatures,
    *,
    items: Sequence[str] | None = None,
    max_concurrency: int = 8,
) -> TopologyRoute:
    """Derive a coordination topology from measured graph shape.

    Deterministic and ordered: the first matching rule wins, so a given shape
    always routes the same way and the rationale can name the deciding numbers.
    A cyclic or sub-minimal graph never claims a benefit it cannot justify.
    """

    f = features
    concurrency = max(1, int(max_concurrency))

    if f.cyclic:
        return TopologyRoute(
            mode=SwarmMode.PARALLEL,
            should_swarm=False,
            confidence=0.0,
            rationale="dependency graph is cyclic; structural routing is not measurable and falls back to parallel dispatch",
            features=f,
        )

    if f.task_count < MIN_SWARM_NODES:
        return TopologyRoute(
            mode=SwarmMode.PARALLEL,
            should_swarm=False,
            confidence=0.9,
            rationale=f"graph has {f.task_count} node(s); coordination overhead exceeds any structural parallel gain",
            features=f,
        )

    if f.max_width == 1 and f.depth == f.task_count:
        return TopologyRoute(
            mode=SwarmMode.HIERARCHICAL,
            should_swarm=False,
            confidence=0.95,
            rationale=f"pure chain: {f.task_count} nodes at width 1 (depth {f.depth}); no stage can overlap, so a single leader-sequenced pipeline is the only faithful topology",
            features=f,
        )

    if f.component_count >= 2 and f.max_width >= 2:
        # Disjoint components share nothing, so one bus and one leader add cost
        # without adding information: fan the sub-swarms out independently.
        # Shape alone never yields ``ensemble`` — redundancy is a claim about
        # *intent* (are these the same question asked twice?), which the graph
        # cannot answer.  Ensemble stays a semantic or explicit choice.
        return TopologyRoute(
            mode=SwarmMode.SCATTER_GATHER,
            should_swarm=True,
            confidence=0.8,
            rationale=f"{f.component_count} disjoint components with max width {f.max_width}; each is an independent sub-swarm so results scatter and gather without cross-component messaging",
            features=f,
        )

    if f.coupling >= COUPLING_THRESHOLD:
        return TopologyRoute(
            mode=SwarmMode.HIERARCHICAL,
            should_swarm=True,
            confidence=0.7,
            rationale=f"edge coupling {f.coupling:.2f} over {f.task_count} nodes (acyclic ceiling ~0.33); densely coupled tasks need a single coordinating leader rather than peer fan-out",
            features=f,
        )

    if f.max_width >= 4 and f.depth <= 3:
        mode = SwarmMode.MAP_REDUCE if items else SwarmMode.SCATTER_GATHER
        detail = f"item list present ({len(items)} entries)" if items else "no explicit item list"
        return TopologyRoute(
            mode=mode,
            should_swarm=True,
            confidence=0.85,
            rationale=f"fan-out/fan-in: width {f.max_width} at depth {f.depth} ({detail}); independent leaves map concurrently into one reduce",
            features=f,
        )

    if f.max_width >= 3 and f.depth >= 4:
        return TopologyRoute(
            mode=SwarmMode.HIERARCHICAL,
            should_swarm=True,
            confidence=0.7,
            rationale=f"deep wide graph: depth {f.depth} with width {f.max_width}; long dependency chains plus real fan-out favour a leader over a flat mesh",
            features=f,
        )

    if f.root_count >= 3 or f.max_fan_out >= 3:
        return TopologyRoute(
            mode=SwarmMode.PARALLEL,
            should_swarm=True,
            confidence=0.7,
            rationale=f"{f.root_count} roots and max fan-out {f.max_fan_out} with no deeper structure; broadcast dispatch is the cheapest topology that covers every branch",
            features=f,
        )

    if f.max_width >= 3:
        return TopologyRoute(
            mode=SwarmMode.PARALLEL,
            should_swarm=True,
            confidence=0.65,
            rationale=f"width {f.max_width} at depth {f.depth} ({f.width_depth_ratio:.2f} width/depth); concurrent siblings with shallow coupling",
            features=f,
        )

    return TopologyRoute(
        mode=SwarmMode.HIERARCHICAL,
        should_swarm=True,
        confidence=0.5,
        rationale=f"width {f.max_width} over depth {f.depth} with coupling {f.coupling:.2f}; narrow structure keeps a coordinating leader over a flat swarm",
        features=f,
    )
