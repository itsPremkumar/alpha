"""Conservative reverse-dependency impact analysis."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .graph import DependencyGraph
from .models import ChangeImpact, CodebaseSnapshot, DependencyEdge

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs)(/|$)|(^|/)(test_[^/]+|[^/]+_test|[^/]+\.test|[^/]+\.spec)\.[^/]+$",
    re.IGNORECASE,
)


def is_test_path(path: str) -> bool:
    """Return whether a path follows common Python/JS test conventions."""

    normalized = str(path).replace("\\", "/")
    return bool(_TEST_PATH_RE.search(normalized))


def _coerce_graph(source: DependencyGraph | CodebaseSnapshot | Iterable[DependencyEdge]) -> tuple[DependencyGraph, bool]:
    if isinstance(source, DependencyGraph):
        return source, False
    if isinstance(source, CodebaseSnapshot):
        return DependencyGraph.from_snapshot(source), source.partial_index
    return DependencyGraph(source), False


def compute_change_impact(
    source: DependencyGraph | CodebaseSnapshot | Iterable[DependencyEdge],
    path: str,
    *,
    depth_cap: int = 4,
    partial_index: bool = False,
    test_paths: Iterable[str] | None = None,
    max_depth: int | None = None,
) -> ChangeImpact:
    """Compute direct and bounded transitive dependents for ``path``.

    The graph direction is source dependency -> target.  Therefore a change to
    a target is propagated along incoming edges.  BFS records minimum depth;
    when a path is first seen at a depth beyond the cap it is not traversed.
    ``partial_index`` is sticky and is always copied into the result and its
    disclosure text.
    """

    if not path:
        raise ValueError("path must be non-empty")
    if max_depth is not None:
        depth_cap = max_depth
    if depth_cap < 1:
        raise ValueError("depth_cap must be >= 1")
    graph, snapshot_partial = _coerce_graph(source)
    incomplete = bool(partial_index or snapshot_partial)
    direct = sorted({edge.from_path for edge in graph.incoming(path) if edge.from_path != path})
    distances: dict[str, int] = {item: 1 for item in direct}
    frontier = list(direct)
    reached_cap = False
    for depth in range(1, depth_cap + 1):
        next_frontier: list[str] = []
        for current in frontier:
            for edge in graph.incoming(current):
                dependent = edge.from_path
                if dependent == path or dependent in distances:
                    continue
                next_depth = depth + 1
                if next_depth > depth_cap:
                    reached_cap = True
                    continue
                distances[dependent] = next_depth
                next_frontier.append(dependent)
        frontier = sorted(set(next_frontier))
        if not frontier:
            break
    transitive = {item: depth for item, depth in distances.items() if depth > 1}
    all_affected = set(direct) | set(transitive)
    explicit_tests = {str(item) for item in (test_paths or ()) if item}
    tests = sorted(item for item in all_affected if is_test_path(item) or item in explicit_tests)
    disclosures: list[str] = []
    if incomplete:
        disclosures.append("partial_index: graph may omit skipped or unparsed files; impact is conservative lower bound")
    if reached_cap:
        disclosures.append(f"depth_cap: transitive traversal stopped at depth {depth_cap}")
    if not direct and not transitive:
        disclosures.append("no indexed reverse dependencies found")
    if incomplete:
        confidence = "low"
    elif reached_cap:
        confidence = "medium"
    elif not graph.edges:
        confidence = "low"
    else:
        confidence = "high"
    computation = f"reverse_dependency_bfs(depth_cap={depth_cap}, test_path_detection=convention+injected)"
    return ChangeImpact(
        path=path,
        directly_affected=direct,
        transitively_affected=transitive,
        test_files=tests,
        confidence=confidence,
        computation=computation,
        partial_index=incomplete,
        disclosures=disclosures,
        depth_cap=depth_cap,
    )


class ImpactAnalyzer:
    """Injectable facade for :func:`compute_change_impact`."""

    def __init__(self, *, depth_cap: int = 4) -> None:
        if depth_cap < 1:
            raise ValueError("depth_cap must be >= 1")
        self.depth_cap = depth_cap

    def compute(
        self,
        source: DependencyGraph | CodebaseSnapshot | Iterable[DependencyEdge],
        path: str,
        *,
        partial_index: bool = False,
        test_paths: Iterable[str] | None = None,
        max_depth: int | None = None,
    ) -> ChangeImpact:
        return compute_change_impact(source, path, depth_cap=self.depth_cap, partial_index=partial_index, test_paths=test_paths, max_depth=max_depth)


def compute_impact(
    source: DependencyGraph | CodebaseSnapshot | Iterable[DependencyEdge],
    path: str,
    *,
    depth_cap: int = 4,
    partial_index: bool = False,
    test_paths: Iterable[str] | None = None,
    max_depth: int | None = None,
) -> ChangeImpact:
    """Alias retained for hosts that use the shorter impact verb."""

    return compute_change_impact(source, path, depth_cap=depth_cap, partial_index=partial_index, test_paths=test_paths, max_depth=max_depth)


def change_impact(
    source: DependencyGraph | CodebaseSnapshot | Iterable[DependencyEdge],
    path: str,
    *,
    depth_cap: int = 4,
    partial_index: bool = False,
    test_paths: Iterable[str] | None = None,
) -> ChangeImpact:
    """Short compatibility alias for callers that prefer a noun-style API."""

    return compute_change_impact(source, path, depth_cap=depth_cap, partial_index=partial_index, test_paths=test_paths)


__all__ = ["ImpactAnalyzer", "change_impact", "compute_change_impact", "compute_impact", "is_test_path"]
