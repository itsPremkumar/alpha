"""Measured execution observability for the Dynamic Workflow Engine.

The engine already journals rich per-node facts (evidence, outputs, iteration
counts, token charges) but never *measured time or shape*, so an operator could
not answer the two questions that matter when a run is slow or stuck:

- **Where did the time go?**  Every node execution is now timed with a monotonic
  clock, so the timeline is a measurement rather than an inference from event
  ordering (two events can share a timestamp; a monotonic interval cannot lie).
- **What is the critical path?**  A run is bounded by its slowest dependency
  chain, not by its slowest single node.  :func:`critical_path` computes that
  chain from measured durations over the graph's dependency edges, which turns
  "this run took 40s" into "these four nodes, in this order, are the 38s".

Everything here is derived from real recorded state.  A node that never executed
has no timing and is reported as such; a node that timed out reports the measured
overrun.  Nothing is interpolated, and a partial timeline is labelled partial
rather than presented as a complete one.

The payload is a pure projection of the run plus the engine's timing ledger, so it
can be recomputed at any time and cannot drift from the run it describes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from alpha.workflow.models import NodeStatus, WorkflowGraph, WorkflowRun, WorkflowRunStatus

# Key under which the engine persists its timing ledger on the run's metrics.
TIMING_LEDGER_KEY = "node_timings"

# Key under which the engine persists measured wave shape on the run's metrics.
WAVE_METRICS_KEY = "wave_metrics"


@dataclass
class NodeTiming:
    """One measured node execution window.

    ``started_at``/``ended_at`` are wall-clock ISO instants for human reading;
    ``duration_seconds`` is the authoritative monotonic measurement and is what
    every derived number (totals, critical path) is computed from.
    """

    node_id: str
    started_at: str
    ended_at: str | None = None
    duration_seconds: float = 0.0
    status: str = NodeStatus.RUNNING.value
    attempts: int = 0
    tokens_consumed: int = 0
    timed_out: bool = False
    thread_name: str | None = None

    @property
    def complete(self) -> bool:
        return self.ended_at is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunTimeline:
    """The engine's timing ledger for one run."""

    entries: list[NodeTiming] = field(default_factory=list)

    def record(self, timing: NodeTiming) -> NodeTiming:
        self.entries.append(timing)
        return timing

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self.entries]}

    @classmethod
    def from_dict(cls, payload: Any) -> RunTimeline:
        timeline = cls()
        if not isinstance(payload, dict):
            return timeline
        raw = payload.get("entries")
        if not isinstance(raw, list):
            return timeline
        for item in raw:
            if not isinstance(item, dict) or not item.get("node_id"):
                continue
            try:
                timeline.entries.append(NodeTiming(**{k: v for k, v in item.items() if k in NodeTiming.__dataclass_fields__}))
            except (TypeError, ValueError):
                # A malformed ledger row is dropped rather than failing the whole
                # projection: timing is observability, not a completion gate.
                continue
        return timeline


def load_timeline(run: WorkflowRun) -> RunTimeline:
    """Read the run's persisted timing ledger (empty when never recorded)."""
    return RunTimeline.from_dict(run.metrics.get(TIMING_LEDGER_KEY))


def persist_timeline(run: WorkflowRun, timeline: RunTimeline) -> None:
    """Write the timing ledger back onto the run's metrics."""
    run.metrics[TIMING_LEDGER_KEY] = timeline.to_dict()


def _dependencies(graph: WorkflowGraph, node_id: str) -> set[str]:
    """Real predecessors of ``node_id``: incoming edges plus ``depends_on``."""
    deps = {edge.source for edge in graph.incoming_edges(node_id)}
    node = graph.nodes.get(node_id)
    if node is not None:
        deps.update(node.depends_on)
    return {dep for dep in deps if dep != node_id and dep in graph.nodes}


def topological_order(graph: WorkflowGraph) -> tuple[list[str], list[str]]:
    """Kahn's algorithm over the real dependency edges.

    Returns ``(ordered, unresolved)``.  ``unresolved`` holds the nodes left in a
    cycle; they are reported rather than silently dropped so a caller can say
    the projection is partial instead of implying full coverage.  A
    bounded-loop graph legitimately contains cycles, and refusing to project such
    a run at all would hide exactly the runs an operator most wants to inspect.
    """
    successors: dict[str, set[str]] = {nid: set() for nid in graph.nodes}
    indegree: dict[str, int] = {nid: 0 for nid in graph.nodes}
    for nid in graph.nodes:
        for dep in _dependencies(graph, nid):
            if dep in successors and nid not in successors[dep]:
                successors[dep].add(nid)
                indegree[nid] += 1

    ready = sorted(nid for nid, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for successor in sorted(successors[current]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    unresolved = sorted(nid for nid, degree in indegree.items() if degree > 0)
    return ordered, unresolved


def critical_path(graph: WorkflowGraph, timeline: RunTimeline) -> dict[str, Any]:
    """Longest measured dependency chain, by summed node duration.

    Only nodes with a *completed* measurement contribute, so the path reflects
    work that actually happened.  Nodes still running are listed separately
    rather than being scored at zero, because a zero-duration in-flight node
    would silently produce a "critical path" that ends before the run does.
    """
    durations: dict[str, float] = {}
    for entry in timeline.entries:
        if entry.complete:
            durations[entry.node_id] = durations.get(entry.node_id, 0.0) + entry.duration_seconds

    ordered, unresolved = topological_order(graph)
    best_length: dict[str, float] = {}
    best_prev: dict[str, str | None] = {}
    for nid in ordered:
        prior = [(dep, best_length.get(dep, 0.0)) for dep in _dependencies(graph, nid) if dep in best_length]
        if prior:
            chosen, length = max(prior, key=lambda item: (item[1], item[0]))
            best_prev[nid] = chosen
            best_length[nid] = length
        else:
            best_prev[nid] = None
            best_length[nid] = 0.0
        best_length[nid] += durations.get(nid, 0.0)

    # The critical path ends at the measured node with the greatest accumulated
    # dependency time.  Ties break on id so the result is deterministic.
    candidates = [nid for nid in ordered if nid in durations]
    if not candidates:
        return {
            "path": [],
            "total_seconds": 0.0,
            "measured_nodes": 0,
            "unresolved_cycle_nodes": unresolved,
            "complete": False,
            "reason": "no node has a completed timing measurement yet",
        }

    end_node = max(candidates, key=lambda nid: (best_length[nid], nid))
    path: list[str] = []
    cursor: str | None = end_node
    seen: set[str] = set()
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        path.append(cursor)
        cursor = best_prev.get(cursor)
    path.reverse()

    return {
        "path": path,
        "total_seconds": round(best_length[end_node], 6),
        "measured_nodes": len(durations),
        "unresolved_cycle_nodes": unresolved,
        "complete": not unresolved,
        "reason": "" if not unresolved else f"{len(unresolved)} node(s) participate in a cycle and were excluded from the ordering",
    }


def build_run_observability(run: WorkflowRun, graph: WorkflowGraph | None = None) -> dict[str, Any]:
    """Project the run's measured execution shape.

    Returns a payload that distinguishes **execution** from **acceptance**: it
    reports what ran, how long it took, and how the run ended, and it never
    implies the work was verified.  Acceptance remains the executor's evidence
    contract, owned by the runtime.
    """
    timeline = load_timeline(run)
    entries = timeline.entries
    completed = [entry for entry in entries if entry.complete]
    in_flight = [entry for entry in entries if not entry.complete]

    by_status: dict[str, int] = {}
    for entry in entries:
        by_status[entry.status] = by_status.get(entry.status, 0) + 1

    timed_out = [entry.node_id for entry in entries if entry.timed_out]
    slowest = sorted(completed, key=lambda e: (-e.duration_seconds, e.node_id))[:10]

    observed_graph = graph
    if observed_graph is None:
        observed_graph = WorkflowGraph()

    return {
        "run_id": run.run_id,
        "workflow_id": run.workflow_id,
        "status": run.status.value,
        "terminal": run.status
        in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.BUDGET_EXHAUSTED,
            WorkflowRunStatus.ABORTED,
        },
        "graph_version": run.graph_version,
        "tokens_consumed": run.tokens_consumed,
        "budget_limit": run.budget_limit,
        "nodes_total": len(run.node_states),
        "nodes_completed": len(run.completed_nodes),
        "nodes_failed": len(set(run.failed_nodes)),
        "nodes_waiting": len(run.waiting_nodes),
        "timed_executions": len(entries),
        "measured_executions": len(completed),
        "in_flight_executions": len(in_flight),
        "timeline_complete": not in_flight,
        "total_measured_seconds": round(sum(entry.duration_seconds for entry in completed), 6),
        "slowest_nodes": [
            {
                "node_id": entry.node_id,
                "duration_seconds": round(entry.duration_seconds, 6),
                "status": entry.status,
                "tokens_consumed": entry.tokens_consumed,
            }
            for entry in slowest
        ],
        "timed_out_nodes": timed_out,
        "executions_by_status": by_status,
        "critical_path": critical_path(observed_graph, timeline),
        "timeline": [entry.to_dict() for entry in entries],
    }


def now_iso() -> str:
    """Current UTC instant, isolated so tests can reason about one clock."""
    return datetime.now(UTC).isoformat()


__all__ = [
    "TIMING_LEDGER_KEY",
    "WAVE_METRICS_KEY",
    "NodeTiming",
    "RunTimeline",
    "build_run_observability",
    "critical_path",
    "load_timeline",
    "now_iso",
    "persist_timeline",
    "topological_order",
]
