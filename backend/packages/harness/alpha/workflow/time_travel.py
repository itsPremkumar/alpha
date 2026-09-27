"""Forking, time travel, and dry-run simulation for the Dynamic Workflow Engine.

The engine already replays its own append-only event log
(:mod:`alpha.orchestrator.replay`), which is what makes crash recovery possible.
This module turns that same primitive into three operator-facing capabilities
that the log was always able to support but nothing exposed:

- :func:`fork_run` — branch a NEW run from an arbitrary point in a finished or
  live run's history, so "what if I had done X here?" is answerable without
  destroying the original.
- :func:`run_history` — the ordered, replayable timeline of a run, with the
  sequence number and covered fields of each event.
- :func:`simulate_run` — execute a graph against a **recording** executor that
  performs no side effects, producing a projected outcome.

Two rules govern all three, and they are the reason this is a separate module
rather than three more engine methods:

1. **A simulation is never reported as execution.** :func:`simulate_run` returns
   ``simulated=True`` and ``execution_label="dry_run_simulation"`` on every node
   result, and it never touches the durable sink, never advances a real run, and
   never charges a budget. Presenting a dry run as work performed is precisely
   the dishonesty the DWE exists to prevent.
2. **A fork never mutates its source.** The fork is a distinct run with its own id
   and its own graph revision, carrying a real reference back to the run and
   event it branched from. The source's state, history and terminal status are
   read-only inputs.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.workflow.events import WorkflowEvent
from alpha.workflow.models import (
    NodeStatus,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.observability import build_run_observability
from alpha.workflow.runtime import DynamicWorkflowEngine

# Every event type the replay fold understands. Used to decide whether a prefix
# of the log is a *replayable* prefix or a truncated one, so a fork never claims
# to reproduce a state the fold could not actually reach.
_REPLAYABLE_PREFIX_EVENTS = frozenset({"workflow_started"})

# Ceiling on how many events a fork will replay. A fork is an operator action, not
# a bulk operation, and an unbounded fold over a very long log would be a
# memory and latency hazard with no operator-visible benefit.
MAX_FORK_EVENTS = 10_000

# Label carried by every simulated result. Never an acceptance claim.
SIMULATION_LABEL = "dry_run_simulation"


class ForkError(RuntimeError):
    """A fork could not be produced; the reason is real."""


@dataclass
class RunHistoryEntry:
    """One replayable event in a run's history."""

    index: int
    event_id: str
    event_type: str
    timestamp: str
    idempotency_key: str | None = None
    node_id: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "idempotency_key": self.idempotency_key,
            "node_id": self.node_id,
            "reason": self.reason,
        }


@dataclass
class ForkResult:
    """A new run branched from a point in another run's history."""

    run: WorkflowRun
    source_run_id: str
    forked_at_event_id: str
    forked_at_index: int
    definition: WorkflowDefinition
    inherited_completed_nodes: list[str] = field(default_factory=list)
    inherited_state_keys: list[str] = field(default_factory=list)
    replayed_events: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run.run_id,
            "workflow_id": self.run.workflow_id,
            "status": self.run.status.value,
            "source_run_id": self.source_run_id,
            "forked_at_event_id": self.forked_at_event_id,
            "forked_at_index": self.forked_at_index,
            "graph_version": self.run.graph_version,
            "inherited_completed_nodes": list(self.inherited_completed_nodes),
            "inherited_state_keys": list(self.inherited_state_keys),
            "replayed_events": self.replayed_events,
            "notes": list(self.notes),
        }


def run_history(engine: DynamicWorkflowEngine, run_id: str, *, limit: int | None = None) -> list[RunHistoryEntry]:
    """The ordered, replayable timeline of ``run_id``.

    Indexes are 1-based over the run's own event stream, so a caller can quote an
    index (or an ``event_id``) to :func:`fork_run` without ambiguity.
    """
    if engine.get_run(run_id) is None:
        raise ForkError(f"run '{run_id}' not found on this engine")
    events = engine.events.get_events(run_id)
    if limit is not None and limit > 0:
        events = events[-limit:]
    entries: list[RunHistoryEntry] = []
    for offset, event in enumerate(events):
        payload = event.payload or {}
        node_id = payload.get("node_id")
        entries.append(
            RunHistoryEntry(
                index=offset + 1,
                event_id=event.event_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                idempotency_key=event.idempotency_key,
                node_id=node_id if isinstance(node_id, str) else None,
                reason=payload.get("reason") if isinstance(payload.get("reason"), str) else None,
            )
        )
    return entries


def _prefix_for_fork(
    events: Sequence[WorkflowEvent],
    *,
    at_event_id: str | None,
    at_index: int | None,
) -> tuple[list[WorkflowEvent], int]:
    """Resolve and validate the replayable event prefix a fork starts from."""
    if not events:
        raise ForkError("the source run has no events; there is no history to fork from")
    if len(events) > MAX_FORK_EVENTS:
        raise ForkError(
            f"source run has {len(events)} events, above the {MAX_FORK_EVENTS} fork ceiling; "
            f"fork a shorter window or archive this run first"
        )
    if at_event_id is not None and at_index is not None:
        raise ForkError("pass either at_event_id or at_index, not both")
    if at_event_id is not None:
        for offset, event in enumerate(events):
            if event.event_id == at_event_id:
                return list(events[: offset + 1]), offset
        raise ForkError(f"event id {at_event_id!r} is not in the source run's history")
    if at_index is not None:
        if at_index < 1 or at_index > len(events):
            raise ForkError(f"fork index {at_index} is outside the source run's history of {len(events)} event(s)")
        return list(events[:at_index]), at_index - 1
    return list(events), len(events) - 1


def _node_completed_before(event: WorkflowEvent) -> str | None:
    """The node id when ``event`` proves a node reached a real completion."""
    if event.event_type != "node_completed":
        return None
    node_id = (event.payload or {}).get("node_id")
    return node_id if isinstance(node_id, str) else None


def fork_run(
    engine: DynamicWorkflowEngine,
    source_run_id: str,
    *,
    at_event_id: str | None = None,
    at_index: int | None = None,
    new_run_id: str | None = None,
    reset_completed_nodes: bool = False,
) -> ForkResult:
    """Branch a NEW run from a point in ``source_run_id``'s history.

    The fork is a real run on the same engine, carrying a real graph revision
    reconstructed from the patch events in the forked prefix. The source run is
    never touched: no status change, no history append, no node reset.

    Completed work up to the fork point is INHERITED rather than repeated, which
    is the point of forking — replaying a model call or a sandbox write that
    already happened would double the side effect. Set ``reset_completed_nodes``
    to re-run them deliberately, and understand that this is exactly the
    dangerous option: the engine's idempotency keys are per-run, so a re-run in
    the fork has no record of the source's effect and will perform it again.

    The fork is a distinct run id, so nothing about it can be mistaken for the
    source's outcome in the event log.
    """
    source = engine.get_run(source_run_id)
    if source is None:
        raise ForkError(f"run '{source_run_id}' not found on this engine")
    definition = engine.get_definition(source.workflow_id)
    if definition is None:
        raise ForkError(f"run '{source_run_id}' references workflow '{source.workflow_id}', which is not registered")

    events = engine.events.get_events(source_run_id)
    prefix, offset = _prefix_for_fork(events, at_event_id=at_event_id, at_index=at_index)
    if not prefix or prefix[0].event_type not in _REPLAYABLE_PREFIX_EVENTS:
        first_type = prefix[0].event_type if prefix else "none"
        raise ForkError(
            f"the forked prefix does not begin with a replayable '{sorted(_REPLAYABLE_PREFIX_EVENTS)}' event "
            f"(it begins with {first_type!r}); the reconstructed state would be incomplete"
        )

    # Reconstruct the exact state the source had at that point by folding the
    # prefix through the real replay implementation, rather than by copying
    # fields off the live run (which reflects the run's CURRENT state, not its
    # historical state at the fork point).
    from alpha.orchestrator.replay import replay_run

    replay_engine, replayed = replay_run(prefix, definition, engine=None)

    fork_id = new_run_id or f"fork_{uuid.uuid4().hex[:12]}"
    if engine.get_run(fork_id) is not None:
        raise ForkError(f"run id '{fork_id}' is already in use on this engine")

    # Register the reconstructed graph under the fork's own workflow id so two
    # forks of the same source never share mutable graph state.  The key comes
    # from the REPLAYED run, not the live source run, because replay may have
    # folded patch events forward to a newer graph revision than the source
    # currently advertises.
    fork_workflow_id = f"{source.workflow_id}@fork:{fork_id}"
    reconstructed = replay_engine.graphs.get(f"{replayed.workflow_id}:v{replayed.graph_version}")
    fork_graph: WorkflowGraph = copy.deepcopy(reconstructed if reconstructed is not None else definition.graph)
    fork_definition = WorkflowDefinition(
        id=fork_workflow_id,
        name=f"{definition.name} (fork of {source_run_id} at event {prefix[-1].event_id})",
        owner_id=source.owner_id,
        version=definition.version,
        description=definition.description,
        graph=fork_graph,
        variables=dict(definition.variables),
        policies=dict(definition.policies),
        budget=definition.budget,
    )
    engine.register_definition(fork_definition, allow_replace=True)

    completed = [] if reset_completed_nodes else list(replayed.completed_nodes)
    failed = [] if reset_completed_nodes else list(replayed.failed_nodes)
    inherited_state = copy.deepcopy(replayed.state)

    fork_run_object = WorkflowRun(
        run_id=fork_id,
        workflow_id=fork_workflow_id,
        owner_id=source.owner_id,
        graph_version=fork_graph.version,
        status=WorkflowRunStatus.RUNNING,
        state=inherited_state,
        node_states={nid: NodeStatus.PENDING for nid in fork_graph.nodes},
        budget_limit=source.budget_limit,
        tokens_consumed=replayed.tokens_consumed if not reset_completed_nodes else 0,
    )
    # Re-apply the reconstructed completion so the fork resumes AFTER the work
    # that had already happened.  ``fork_graph`` is a deep copy of the REPLAYED
    # graph, so each inherited node already carries the evidence the source
    # actually produced at that point; only the status needs restoring.
    for nid in completed:
        node = fork_graph.nodes.get(nid)
        if node is None:
            continue
        node.status = NodeStatus.SUCCEEDED
        fork_run_object.node_states[nid] = NodeStatus.SUCCEEDED
        fork_run_object.completed_nodes.append(nid)
    for nid in failed:
        node = fork_graph.nodes.get(nid)
        if node is None:
            continue
        node.status = NodeStatus.FAILED
        fork_run_object.node_states[nid] = NodeStatus.FAILED
        fork_run_object.failed_nodes.append(nid)

    engine.runs[fork_id] = fork_run_object
    engine._run_graphs[fork_id] = fork_graph

    notes: list[str] = []
    if reset_completed_nodes:
        notes.append(
            "completed nodes were RESET: this fork will repeat their side effects, and the engine's "
            "per-run idempotency keys cannot protect them because they were recorded in the source run"
        )
    if any(n.type.value == "compensation" for n in fork_graph.nodes.values()):
        notes.append("the forked graph contains compensation nodes; confirm the rollback path is still intended")

    engine.events.emit(
        "workflow_forked",
        fork_id,
        source_run_id=source_run_id,
        forked_at_event_id=prefix[-1].event_id,
        forked_at_index=offset + 1,
        replayed_events=len(prefix),
        inherited_completed_nodes=list(completed),
        workflow_id=fork_workflow_id,
    )

    return ForkResult(
        run=fork_run_object,
        source_run_id=source_run_id,
        forked_at_event_id=prefix[-1].event_id,
        forked_at_index=offset + 1,
        definition=fork_definition,
        inherited_completed_nodes=completed,
        inherited_state_keys=sorted(str(key) for key in inherited_state),
        replayed_events=len(prefix),
        notes=notes,
    )


# ------------------------------------------------------------------ dry run


@dataclass
class SimulationResult:
    """A dry-run projection. Explicitly NOT an execution result."""

    workflow_id: str
    run_id: str
    status: str
    waves: int
    nodes_visited: list[str] = field(default_factory=list)
    node_outcomes: dict[str, str] = field(default_factory=dict)
    state_keys: list[str] = field(default_factory=list)
    simulated: bool = True
    execution_label: str = SIMULATION_LABEL
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "status": self.status,
            "waves": self.waves,
            "nodes_visited": list(self.nodes_visited),
            "node_outcomes": dict(self.node_outcomes),
            "state_keys": list(self.state_keys),
            "simulated": self.simulated,
            "execution_label": self.execution_label,
            "notes": list(self.notes),
        }


def recording_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """A node runner that RECORDS intent and performs no work.

    It returns a successful result because a simulation must let the graph
    proceed, and the success is honest *as a simulation*: the evidence string
    names the simulated executor, ``tokens_used`` is ``0`` because no model was
    called, and the output records the node's declared intent rather than a
    result. Every node reached is journalled so the caller can see exactly what
    the graph would have done.
    """
    declared = {
        "node_id": node.id,
        "type": node.type.value,
        "executor": node.executor,
        "prompt_chars": len(node.prompt or ""),
        "config_keys": sorted(str(key) for key in node.config),
    }
    return {
        "status": "completed",
        "output": {"simulated": True, **declared},
        "evidence": f"{SIMULATION_LABEL}: node '{node.id}' would invoke executor '{node.executor}'; no work was performed",
        "tokens_used": 0,
    }


def simulate_run(
    engine: DynamicWorkflowEngine,
    workflow_id: str,
    *,
    initial_state: dict[str, Any] | None = None,
    max_waves: int = 25,
    register: bool = False,
) -> SimulationResult:
    """Dry-run ``workflow_id`` against a recording executor.

    Useful before committing a generated graph: it reports which nodes the
    scheduler would reach, in which waves, and what state keys would appear,
    without a single side effect.

    The simulation runs on a THROWAWAY engine and a throwaway run, so it cannot
    touch the caller's definitions, runs, durable log, or token budgets. That is
    not a limitation to apologise for — it is the guarantee that makes a dry run
    safe to run against a live graph.

    A graph that needs a real decision (an approval gate, an external wait) will
    park rather than resolve, and the returned status says so: a simulation that
    silently "passed" a gate would be worse than useless.
    """
    definition = engine.get_definition(workflow_id)
    if definition is None:
        raise ForkError(f"workflow '{workflow_id}' is not registered on this engine")
    if max_waves < 1 or max_waves > 200:
        raise ValueError("max_waves must be between 1 and 200")

    scratch = DynamicWorkflowEngine()
    scratch.register_definition(definition, allow_replace=True)

    # The scratch engine gets its own dispatcher so simulation events cannot
    # reach the caller's log or durable sink.
    from alpha.workflow.events import WorkflowEventDispatcher

    scratch.events = WorkflowEventDispatcher(durable_sink=None)

    run = scratch.start_run(workflow_id, initial_state=dict(initial_state or {}), owner_id=definition.owner_id)
    visited: list[str] = []
    waves = 0
    while waves < max_waves:
        before = (run.status, run.graph_version, len(run.completed_nodes), len(run.failed_nodes))
        run = scratch.execute_step(run.run_id, node_runner=recording_executor)
        waves += 1
        for nid, status in run.node_states.items():
            if status in (NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.SKIPPED) and nid not in visited:
                visited.append(nid)
        if run.status in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.BUDGET_EXHAUSTED,
            WorkflowRunStatus.ABORTED,
        }:
            break
        if before == (run.status, run.graph_version, len(run.completed_nodes), len(run.failed_nodes)):
            break

    notes: list[str] = []
    if run.status in (WorkflowRunStatus.WAITING_APPROVAL, WorkflowRunStatus.WAITING_EVENT, WorkflowRunStatus.SUSPENDED):
        notes.append(
            f"the graph parked in '{run.status.value}'; a real run would wait there for an approval, an external "
            f"signal, or an operator resume, so the simulation cannot project past that point"
        )
    if waves >= max_waves and run.status not in (
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.FAILED,
    ):
        notes.append(f"stopped at the {max_waves}-wave simulation ceiling without a terminal status")

    return SimulationResult(
        workflow_id=workflow_id,
        run_id=run.run_id,
        status=run.status.value,
        waves=waves,
        nodes_visited=sorted(visited),
        node_outcomes={nid: run.node_states[nid].value for nid in sorted(visited)},
        state_keys=sorted(str(key) for key in run.state),
        notes=notes,
    )


def run_report(engine: DynamicWorkflowEngine, run_id: str) -> dict[str, Any]:
    """Combined history + observability + provenance for one run.

    The single payload an operator (or the frontend) should read to answer "what
    happened, how long did it take, and what was claimed?".  Execution and
    acceptance stay separate fields: nothing here asserts the run's work was
    verified.
    """
    run = engine.get_run(run_id)
    if run is None:
        raise ForkError(f"run '{run_id}' not found on this engine")
    graph = engine._run_graph_for(run)
    events = engine.events.get_events(run_id)
    history = run_history(engine, run_id)
    return {
        "run_id": run.run_id,
        "workflow_id": run.workflow_id,
        "status": run.status.value,
        "history_depth": len(history),
        "first_event": history[0].to_dict() if history else None,
        "last_event": history[-1].to_dict() if history else None,
        "status_transitions": list(run.history),
        "applied_patches": len(run.patches_applied),
        "observability": build_run_observability(run, graph, events),
        "durability": engine.events.durable_status(),
        "provenance": {
            "owner_id": run.owner_id,
            "graph_version": run.graph_version,
            "budget_limit": run.budget_limit,
            "execution_mode": run.metrics.get("execution_mode"),
        },
    }


__all__ = [
    "MAX_FORK_EVENTS",
    "SIMULATION_LABEL",
    "ForkError",
    "ForkResult",
    "RunHistoryEntry",
    "SimulationResult",
    "fork_run",
    "recording_executor",
    "run_history",
    "run_report",
    "simulate_run",
]
