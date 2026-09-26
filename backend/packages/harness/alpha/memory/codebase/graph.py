"""Deterministic dependency-graph algorithms for structure memory.

Edges point from the importing/calling module to its target.  All ordering is
lexicographic and all traversal is iterative, so a cyclic or very large graph
cannot make refresh recurse forever.  The graph is a pure data structure; it
has no filesystem, parser, or retrieval dependencies.
"""

from __future__ import annotations

import heapq
from collections import deque
from collections.abc import Iterable, Mapping

from .models import CodebaseSnapshot, DependencyEdge


class CycleError(ValueError):
    """Raised by strict topological sorting when a cycle is present."""

    def __init__(self, cycles: list[list[str]]):
        self.cycles = cycles
        self.cycle = cycles[0] if cycles else []
        rendered = "; ".join(" -> ".join(cycle) for cycle in cycles)
        super().__init__(f"dependency graph contains cycle(s): {rendered}")


class DependencyGraph:
    """A small mutable directed multigraph keyed by edge kind."""

    def __init__(self, edges: Iterable[DependencyEdge] = ()) -> None:
        self._edges: dict[tuple[str, str, str], DependencyEdge] = {}
        for edge in edges:
            self.add_edge(edge)

    @classmethod
    def from_snapshot(cls, snapshot: CodebaseSnapshot) -> DependencyGraph:
        return cls(snapshot.edges)

    @property
    def nodes(self) -> list[str]:
        """All endpoint names in deterministic order."""

        return sorted(self._nodes())

    @property
    def edges(self) -> list[DependencyEdge]:
        return [self._edges[key] for key in sorted(self._edges)]

    def _nodes(self) -> set[str]:
        nodes: set[str] = set()
        for from_path, to_path, _ in self._edges:
            nodes.add(from_path)
            nodes.add(to_path)
        return nodes

    def add_edge(self, edge: DependencyEdge | str, to_path: str | None = None, *, kind: str = "reference", weight: float = 1.0, evidence: str = "") -> DependencyEdge:
        """Add an edge, combining repeated evidence for the same relation.

        ``add_edge(edge)`` is the normal form.  The string form is a convenient
        ``add_edge(from_path, to_path, kind=...)`` seam for small callers.
        Repeated observations retain deterministic evidence and the strongest
        observed weight rather than depending on insertion order.
        """

        if isinstance(edge, DependencyEdge):
            normalized = edge
        else:
            if to_path is None:
                raise ValueError("to_path is required when adding an edge from a string")
            normalized = DependencyEdge(from_path=str(edge), to_path=str(to_path), kind=kind, weight=weight, evidence=evidence)
        key = (normalized.from_path, normalized.to_path, normalized.kind)
        current = self._edges.get(key)
        if current is None:
            self._edges[key] = normalized
            return normalized
        evidence_values = [item for item in (current.evidence, normalized.evidence) if item]
        merged_evidence = "; ".join(dict.fromkeys(evidence_values))
        merged = current.model_copy(update={"weight": max(current.weight, normalized.weight), "evidence": merged_evidence})
        self._edges[key] = merged
        return merged

    def add_edges(self, edges: Iterable[DependencyEdge]) -> None:
        for edge in edges:
            self.add_edge(edge)

    def remove_edge(self, edge_or_from: DependencyEdge | str, to_path: str | None = None, *, kind: str | None = None) -> bool:
        """Remove one edge (or all matching kinds when ``kind`` is omitted)."""

        if isinstance(edge_or_from, DependencyEdge):
            from_path = edge_or_from.from_path
            target = edge_or_from.to_path
            kinds = (edge_or_from.kind,)
        else:
            from_path = str(edge_or_from)
            if to_path is None:
                raise ValueError("to_path is required when removing an edge")
            target = str(to_path)
            kinds = (kind,) if kind is not None else tuple(item[2] for item in self._edges if item[0] == from_path and item[1] == target)
        removed = False
        for item_kind in kinds:
            removed = self._edges.pop((from_path, target, item_kind), None) is not None or removed
        return removed

    def outgoing(self, path: str, *, kind: str | None = None) -> list[DependencyEdge]:
        return [edge for edge in self.edges if edge.from_path == path and (kind is None or edge.kind == kind)]

    def incoming(self, path: str, *, kind: str | None = None) -> list[DependencyEdge]:
        return [edge for edge in self.edges if edge.to_path == path and (kind is None or edge.kind == kind)]

    def _adjacency(self, *, reverse: bool = False) -> dict[str, set[str]]:
        adjacency = {node: set() for node in self._nodes()}
        for from_path, to_path, _ in self._edges:
            if reverse:
                adjacency.setdefault(to_path, set()).add(from_path)
                adjacency.setdefault(from_path, set())
            else:
                adjacency.setdefault(from_path, set()).add(to_path)
                adjacency.setdefault(to_path, set())
        return adjacency

    def topological_order(self, nodes: Iterable[str] | None = None) -> list[str]:
        """Return a deterministic Kahn order, appending cycle nodes safely.

        A cycle does not recurse or raise here: the nodes that cannot be
        removed are appended in lexical order.  Use :meth:`topological_sort`
        when a caller requires an acyclic graph and wants a loud error.
        """

        selected = sorted(set(self._nodes() if nodes is None else nodes))
        selected_set = set(selected)
        indegree = {node: 0 for node in selected}
        adjacency = self._adjacency()
        for from_path, to_path, _ in self._edges:
            if from_path in selected_set and to_path in selected_set:
                indegree[to_path] += 1
        ready = [node for node, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        order: list[str] = []
        while ready:
            node = heapq.heappop(ready)
            order.append(node)
            for target in sorted(adjacency.get(node, ())):
                if target not in selected_set:
                    continue
                indegree[target] -= 1
                if indegree[target] == 0:
                    heapq.heappush(ready, target)
        if len(order) < len(selected):
            order.extend(sorted(set(selected) - set(order)))
        return order

    def topological_sort(self, nodes: Iterable[str] | None = None) -> list[str]:
        """Strict topological sort that reports cycles via :class:`CycleError`."""

        cycles = self.detect_cycles(nodes)
        if cycles:
            raise CycleError(cycles)
        return self.topological_order(nodes)

    def detect_cycles(self, nodes: Iterable[str] | None = None) -> list[list[str]]:
        """Return deterministic directed cycles using iterative SCC analysis."""

        selected = set(self._nodes() if nodes is None else nodes)
        if not selected:
            return []
        forward = self._adjacency()
        reverse = self._adjacency(reverse=True)
        visited: set[str] = set()
        finish: list[str] = []
        for start in sorted(selected):
            if start in visited:
                continue
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                node, expanded = stack.pop()
                if expanded:
                    finish.append(node)
                    continue
                if node in visited:
                    continue
                visited.add(node)
                stack.append((node, True))
                for target in sorted(forward.get(node, ()), reverse=True):
                    if target in selected and target not in visited:
                        stack.append((target, False))
        components: list[list[str]] = []
        assigned: set[str] = set()
        for start in reversed(finish):
            if start not in selected or start in assigned:
                continue
            component: list[str] = []
            stack = [start]
            assigned.add(start)
            while stack:
                node = stack.pop()
                component.append(node)
                for target in sorted(reverse.get(node, ()), reverse=True):
                    if target in selected and target not in assigned:
                        assigned.add(target)
                        stack.append(target)
            if len(component) > 1:
                components.append(sorted(component))
            elif start in forward.get(start, ()):
                components.append([start])
        cycles = [_cycle_for_component(component, forward) for component in components]
        return sorted(cycles)

    def find_cycles(self, nodes: Iterable[str] | None = None) -> list[list[str]]:
        return self.detect_cycles(nodes)

    def detect_cycle(self, nodes: Iterable[str] | None = None) -> list[str] | None:
        cycles = self.detect_cycles(nodes)
        return cycles[0] if cycles else None

    def cycle_paths(self, nodes: Iterable[str] | None = None) -> list[list[str]]:
        return self.detect_cycles(nodes)

    def reachable(self, start: str, max_depth: int = 3, *, direction: str = "out", include_start: bool = False, depth_cap: int | None = None) -> dict[str, int]:
        """Return minimum distances from ``start`` up to a hard depth cap.

        ``direction='out'`` follows dependencies; ``'in'``/``'dependents'``
        follows reverse edges.  The breadth-first traversal is bounded and
        deterministic, so cycles cannot cause an infinite walk.
        """

        if depth_cap is not None:
            max_depth = depth_cap
        if max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if direction not in {"out", "in", "dependents"}:
            raise ValueError("direction must be 'out', 'in', or 'dependents'")
        adjacency = self._adjacency(reverse=direction != "out")
        distances: dict[str, int] = {}
        if start in adjacency or include_start:
            if include_start:
                distances[start] = 0
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for target in sorted(adjacency.get(node, ())):
                if target == start and not include_start:
                    continue
                candidate_depth = depth + 1
                if target not in distances or candidate_depth < distances[target]:
                    distances[target] = candidate_depth
                    queue.append((target, candidate_depth))
        return dict(sorted(distances.items(), key=lambda pair: (pair[1], pair[0])))

    def transitive_reachability(self, start: str, max_depth: int = 3, *, direction: str = "out", depth_cap: int | None = None) -> dict[str, int]:
        return self.reachable(start, max_depth, direction=direction, depth_cap=depth_cap)

    def fan_in(self) -> dict[str, int]:
        values = {node: 0 for node in self._nodes()}
        for edge in self._edges.values():
            values[edge.to_path] = values.get(edge.to_path, 0) + 1
        return {key: values[key] for key in sorted(values)}

    def fan_out(self) -> dict[str, int]:
        values = {node: 0 for node in self._nodes()}
        for edge in self._edges.values():
            values[edge.from_path] = values.get(edge.from_path, 0) + 1
        return {key: values[key] for key in sorted(values)}

    def fan_in_ranking(self, limit: int | None = None) -> list[tuple[str, int]]:
        """Rank by descending count, breaking ties by ascending path."""

        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        ranked = sorted(self.fan_in().items(), key=lambda pair: (-pair[1], pair[0]))
        return ranked if limit is None else ranked[:limit]

    def fan_out_ranking(self, limit: int | None = None) -> list[tuple[str, int]]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        ranked = sorted(self.fan_out().items(), key=lambda pair: (-pair[1], pair[0]))
        return ranked if limit is None else ranked[:limit]

    def rank_fan_in(self, limit: int | None = None) -> list[tuple[str, int]]:
        return self.fan_in_ranking(limit)

    def rank_fan_out(self, limit: int | None = None) -> list[tuple[str, int]]:
        return self.fan_out_ranking(limit)

    def fan_in_scores(self) -> dict[str, int]:
        return self.fan_in()

    def fan_out_scores(self) -> dict[str, int]:
        return self.fan_out()


def _cycle_for_component(component: list[str], adjacency: Mapping[str, set[str]]) -> list[str]:
    """Return one canonical cycle for a strongly connected component."""

    members = set(component)
    start = min(members)
    path = [start]
    position = {start: 0}
    stack: list[tuple[str, int]] = [(start, 0)]
    while stack:
        node, index = stack[-1]
        targets = sorted(target for target in adjacency.get(node, ()) if target in members)
        if index >= len(targets):
            stack.pop()
            position.pop(node, None)
            if path and path[-1] == node:
                path.pop()
            continue
        stack[-1] = (node, index + 1)
        target = targets[index]
        if target == start:
            return path + [start]
        if target in position:
            cycle_start = position[target]
            cycle = path[cycle_start:] + [target]
            return _canonical_cycle(cycle)
        position[target] = len(path)
        path.append(target)
        stack.append((target, 0))
    return _canonical_cycle([start, start])


def _canonical_cycle(cycle: list[str]) -> list[str]:
    if not cycle:
        return []
    nodes = cycle[:-1] if len(cycle) > 1 and cycle[0] == cycle[-1] else cycle[:]
    if not nodes:
        return cycle
    rotations = [tuple(nodes[index:] + nodes[:index]) for index in range(len(nodes))]
    chosen = min(rotations)
    result = list(chosen)
    if len(chosen) == 1 or chosen[0] != chosen[-1]:
        result.append(chosen[0])
    return result


CodebaseGraph = DependencyGraph

__all__ = ["CodebaseGraph", "CycleError", "DependencyGraph"]
