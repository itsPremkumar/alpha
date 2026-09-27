"""Measured execution observability for the Dynamic Workflow Engine.

The engine already journals rich per-node facts (evidence, outputs, iteration
counts, token charges) but never *measured time or shape*, so an operator could
not answer the two questions that matter when a run is slow or stuck:

- **Where did the time go?**  Every node execution is timed with a monotonic
  clock, so the timeline is a measurement rather than an inference from event
  ordering (two events can share a timestamp; a monotonic interval cannot lie).
- **What is the critical path?**  A run is bounded by its slowest dependency
  chain, not by its slowest single node.  :func:`critical_path` computes that
  chain from measured durations over the graph's dependency edges.

**The ledger is derived from the append-only event log, not from run state.**
That is a deliberate design choice with two consequences, both good:

- The DWE guarantees that everything in ``run.metrics`` is reconstructible from
  the journal, so a replayed run's metrics equal the live run's exactly. Storing
  timings in ``run.metrics`` would break that guarantee, because a duration
  cannot be re-derived from the events that recorded the *work*. So measurements
  are journalled as their own ``node_timed`` / ``wave_dispatched`` events and the
  ledger is projected from them.
- Timings therefore survive a process restart and a durable hydration, because
  the log does. A timeline that only lived in memory would be blank for exactly
  the runs an operator most wants to inspect after a crash.

Everything here is derived from real recorded state. A node that never executed
has no timing and is reported as such; a node that timed out reports the measured
overrun. Nothing is interpolated, and a partial timeline is labelled partial
rather than presented as a complete one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from alpha.workflow.models import NodeStatus, WorkflowGraph, WorkflowRun, WorkflowRunStatus

# Event types the observability projection reads. Named here so the emit sites
# and the projection cannot drift apart silently.
NODE_TIMED_EVENT = "node_timed"
WAVE_DISPATCHED_EVENT = "wave_dispatched"


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


def timeline_from_events(events: Any) -> RunTimeline:
    """Project the measured timeline from a run's journalled events.

    ``events`` may be ``WorkflowEvent`` objects or the ``PersistedEventRecord``
    dicts the durable log returns, so the same projection works for a live
    engine, a replayed engine, and a hydrated run read back from disk. Records
    that are not shaped like a timing measurement are skipped rather than
    failing the projection: observability must not be able to break a run.
    """
    timeline = RunTimeline()
    for event in events or []:
        if isinstance(event, dict):
            if event.get("event_type") != NODE_TIMED_EVENT:
                continue
            payload = event.get("payload")
        else:
            if getattr(event, "event_type", None) != NODE_TIMED_EVENT:
                continue
            payload = getattr(event, "payload", None)
        if not isinstance(payload, dict) or not payload.get("node_id"):
            continue
        fields = {key: value for key, value in payload.items() if key in NodeTiming.__dataclass_fields__}
        try:
            timeline.entries.append(NodeTiming(**fields))
        except (TypeError, ValueError):
            continue
    return timeline


def waves_from_events(events: Any) -> list[dict[str, Any]]:
    """Project the measured wave shape from a run's journalled events."""
    waves: list[dict[str, Any]] = []
    for event in events or []:
        if isinstance(event, dict):
            if event.get("event_type") != WAVE_DISPATCHED_EVENT:
                continue
            payload = event.get("payload")
        else:
            if getattr(event, "event_type", None) != WAVE_DISPATCHED_EVENT:
                continue
            payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            continue
        waves.append(
            {
                "wave_index": payload.get("wave_index"),
                "nodes": list(payload.get("nodes") or []),
                "node_count": len(payload.get("nodes") or []),
                "concurrency": payload.get("concurrency"),
                "elapsed_seconds": payload.get("elapsed_seconds"),
                "peak_in_flight": payload.get("peak_in_flight"),
            }
        )
    return waves


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


def build_run_observability(run: WorkflowRun, graph: WorkflowGraph | None = None, events: Any = None) -> dict[str, Any]:
    """Project the run's measured execution shape.

    Returns a payload that distinguishes **execution** from **acceptance**: it
    reports what ran, how long it took, and how the run ended, and it never
    implies the work was verified.  Acceptance remains the executor's evidence
    contract, owned by the runtime.

    ``events`` is the run's journalled event stream (live dispatcher events or
    durable records). Omit it and the timeline is reported empty rather than
    guessed — an observability payload that invented durations would be worse
    than one that admits it has none.
    """
    timeline = timeline_from_events(events)
    entries = timeline.entries
    completed = [entry for entry in entries if entry.complete]
    in_flight = [entry for entry in entries if not entry.complete]

    by_status: dict[str, int] = {}
    for entry in entries:
        by_status[entry.status] = by_status.get(entry.status, 0) + 1

    timed_out = [entry.node_id for entry in entries if entry.timed_out]
    slowest = sorted(completed, key=lambda e: (-e.duration_seconds, e.node_id))[:10]

    observed_graph = graph if graph is not None else WorkflowGraph()
    waves = waves_from_events(events)

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
        "waves_dispatched": len(waves),
        "wave_metrics": waves,
        "timed_executions": len(entries),
        "measured_executions": len(completed),
        "in_flight_executions": len(in_flight),
        "timeline_complete": not in_flight,
        "timeline_source": "event_log" if events else "unavailable",
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
    "NODE_TIMED_EVENT",
    "WAVE_DISPATCHED_EVENT",
    "NodeTiming",
    "RunTimeline",
    "build_run_observability",
    "critical_path",
    "now_iso",
    "timeline_from_events",
    "topological_order",
    "waves_from_events",
]
