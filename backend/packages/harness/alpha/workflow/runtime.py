"""Deterministic runtime engine for Alpha Dynamic Workflows.

Coordinates scheduling, dynamic routing, graph patching, fan-out (Map/Reduce),
races, quorums, bounded loops, human approvals, and saga compensations.

DY-R1 honesty contract: every node action that performs real work runs through
the ``node_runner`` seam - the per-call argument of ``execute_step`` or the
module-level default bound with ``set_node_runner``. When no runner is bound,
the node fails with the real reason; outputs and evidence are never fabricated.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.recovery.policies import decide_from_reason
from alpha.workflow.events import get_event_dispatcher
from alpha.workflow.execution import (
    ConcurrencyGovernor,
    clamp_concurrency,
    execute_wave,
    first_error,
    mark_timeout_occurred,
    reset_timeout_flag,
    run_with_deadline,
    timeout_occurred,
)
from alpha.workflow.expressions import evaluate_condition, evaluate_condition_strict
from alpha.workflow.failures import (
    DEFAULT_STAGNATION_LIMIT,
    StagnationDetector,
    classify_node_failure,
)
from alpha.workflow.leases import LeaseManager, ResultVerdict
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.observability import (
    NODE_TIMED_EVENT,
    WAVE_DISPATCHED_EVENT,
    NodeTiming,
    now_iso,
)
from alpha.workflow.patch import WorkflowPatchEngine
from alpha.workflow.replanner import RuntimeReplanner
from alpha.workflow.router import DynamicRouter
from alpha.workflow.scheduler import WorkflowScheduler

# Module-level node-runner seam: the single default executor binding shared by
# every DynamicWorkflowEngine. ``None`` means NO executor is bound, and nodes
# that require real execution must fail with the honest reason.
_NODE_RUNNER: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None

# Key under which a run records which state entries were written from real
# executor results (used to refuse reductions over non-executor values).
EXECUTOR_STATE_KEYS = "executor_state_keys"

# Key under which a run counts the stagnation-recovery patches the deadlock path
# has committed for it.
STAGNATION_RECOVERY_KEY = "stagnation_recovery_attempts"

# Hard ceiling on stagnation-recovery patches per run.  Each committed patch
# adds a ``gather_evidence_<node>_<version>`` node and reopens the same
# unschedulable target, so without a ceiling a run whose graph cannot progress
# re-enters its own step forever: an unbounded livelock that grows both the
# in-memory graph and the durable event log on every single call.  A run that
# exhausts this budget ends FAILED with the real attempt count instead.
STAGNATION_RECOVERY_LIMIT = 3

# Metric key under which a run records its recorded (completed) idempotency
# attempt keys, so a retried step is not executed twice.
COMPLETED_IDEMPOTENCY_KEYS = "completed_idempotency_keys"

# Metric key holding the verified output of each already-completed idempotent
# attempt, so a suppressed re-attempt reports the ORIGINAL result.  Deliberately
# in ``metrics``: the attempt key is a hash of ``run.state``.
IDEMPOTENT_OUTPUTS_KEY = "idempotent_outputs"

# Key holding the run's external-wait registrations (``event_wait``/``wait``
# nodes parked until a signal or a deadline).  The engine owns this map so a
# suspended run can be resumed by signal, by deadline sweep, or by an operator.
EXTERNAL_WAIT_REGISTRY_KEY = "external_waits"

# Key holding the measured wall-clock instant a run was first created, so a
# suspended run can report its true elapsed time instead of "since last step".
RUN_STARTED_AT_KEY = "run_started_at"

# Policy keys a definition/graph may declare to widen the default wave
# concurrency.  Unparsable or absent values fall back to the module default via
# ``clamp_concurrency`` rather than being trusted or raising.
CONCURRENCY_POLICY_KEY = "max_concurrency"
NODE_CONCURRENCY_CONFIG_KEY = "max_concurrency"

# A run executes SEQUENTIALLY unless its definition/graph declares a wider
# ``max_concurrency``.  Concurrency reorders the event log relative to node
# order, so it is opt-in: an existing definition's observable behaviour must not
# change because the engine gained a thread pool.
SEQUENTIAL_CONCURRENCY = 1

# Key holding the run's checkpoint ledger.  Each entry is a content-addressed
# snapshot record written by a ``CHECKPOINT`` node.
CHECKPOINT_LEDGER_KEY = "checkpoints"

# Hard ceiling on a ``WAIT`` node's delay.  A declared delay is clamped rather
# than obeyed, because an unbounded timer silently parks a run for as long as a
# typo says and nothing would report it.
MAX_WAIT_SECONDS = 300.0

# Hard ceiling on the waves a ``SUBWORKFLOW`` node may drive its child through.
SUBWORKFLOW_WAVE_CEILING = 200

# Statuses after which a run is never re-dispatched.  Shared by ``execute_step``,
# ``cancel_run``, ``suspend_run`` and the subworkflow driver so "terminal" is
# defined once instead of drifting between four literal sets.
TERMINAL_RUN_STATUSES = frozenset(
    {
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.CANCELLED,
        WorkflowRunStatus.BUDGET_EXHAUSTED,
        WorkflowRunStatus.ABORTED,
    }
)

# Statuses that are non-terminal but not dispatchable: the run is parked and a
# step must return it unchanged rather than resuming work the operator is holding.
PARKED_RUN_STATUSES = frozenset(
    {
        WorkflowRunStatus.WAITING_APPROVAL,
        WorkflowRunStatus.WAITING_EVENT,
        WorkflowRunStatus.SUSPENDED,
    }
)

# Process-wide reentrant lock guarding every read-modify-write on shared run and
# graph bookkeeping.  It is process-wide (not per engine) because the mutation
# helpers below are module-level functions called from deep inside node handlers;
# plumbing a per-engine lock into each of them would invite a missed call site,
# and a missed call site is a silent lost update.  Contention is irrelevant
# here: every critical section is a dict or list operation measured in
# microseconds, and the slow part of a node — the executor call — deliberately
# runs OUTSIDE the lock, which is what actually delivers wave parallelism.
_STATE_LOCK = threading.RLock()


def get_node_runner() -> Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None:
    """Return the bound module-level node-runner seam, or None when unbound."""
    return _NODE_RUNNER


def set_node_runner(runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None) -> None:
    """Bind (or unbind by passing None) the module-level node-runner seam."""
    global _NODE_RUNNER
    _NODE_RUNNER = runner


def _no_runner_reason(node: WorkflowNode) -> str:
    """The honest failure reason when no executor is bound for this node."""
    return f"no node_runner bound to execute node '{node.id}' (kind={node.type.value})"


def _record_executor_state(run: WorkflowRun, key: str) -> None:
    """Record that ``run.state[key]`` was written from real executor results.

    Runs under the engine's state lock: the produced-keys list is a
    read-append-write, so two concurrent wave nodes writing different keys would
    otherwise lose one entry and a later REDUCE would refuse a valid input.
    """
    produced = run.metrics.get(EXECUTOR_STATE_KEYS)
    if not isinstance(produced, list):
        produced = []
        run.metrics[EXECUTOR_STATE_KEYS] = produced
    if key not in produced:
        produced.append(key)


def _is_executor_state(run: WorkflowRun, key: str) -> bool:
    """True when ``run.state[key]`` was written from real executor results."""
    produced = run.metrics.get(EXECUTOR_STATE_KEYS)
    return isinstance(produced, list) and key in produced


def _set_run_status(run: WorkflowRun, new_status: WorkflowRunStatus, reason: str | None = None) -> None:
    """Transition the run to ``new_status``, journaling it in ``run.history``.

    Every run-status change (completion, fail-close, budget exhaustion, approval
    pause/resolution, deadlock) lands as one honest ``{from, to, timestamp}``
    entry with the real ``reason`` when one exists. No-op transitions append
    nothing, and ``start_run`` records nothing — history stays empty until
    something actually happened.

    The compare-then-append-then-assign sequence holds ``_STATE_LOCK`` because
    two nodes of a parallel wave can reach a terminal decision at the same
    instant, and an unguarded read-modify-write would drop one transition from
    ``run.history`` entirely.
    """
    with _STATE_LOCK:
        if run.status == new_status:
            return
        now = datetime.now(UTC).isoformat()
        entry: dict[str, Any] = {"timestamp": now, "from": run.status.value, "to": new_status.value}
        if reason:
            entry["reason"] = reason
        run.history.append(entry)
        run.status = new_status
        run.updated_at = now


def _sync_waiting_nodes(run: WorkflowRun) -> None:
    """Recompute ``run.waiting_nodes`` from the per-node statuses.

    The list mirrors whichever nodes are ``WAITING`` right now (currently only
    human-approval gates set that status), so it is derived state — never a
    hand-maintained second source of truth.  It is rebuilt from a snapshot of
    the per-node status map so a node finishing concurrently cannot be missed or
    duplicated by the rebuild.
    """
    with _STATE_LOCK:
        statuses = list(run.node_states.items())
    run.waiting_nodes = [nid for nid, status in statuses if status == NodeStatus.WAITING]


class DynamicWorkflowError(RuntimeError):
    """Base error for dynamic workflow execution."""

    pass


class UnverifiedNodeCompletionError(DynamicWorkflowError):
    """Raised when an attempt is made to mark a node complete without concrete evidence."""

    pass


class WorkflowDefinitionError(DynamicWorkflowError):
    """Raised when a workflow definition is structurally unrunnable.

    Validation happens BEFORE a run exists and therefore before any node can
    perform a side effect.  Previously a malformed definition (an edge or a
    ``depends_on`` naming a node that does not exist, a dependency cycle, an
    unbounded self-loop) was accepted, and the defect only surfaced once
    execution was already underway: the dangling edge was silently dropped and
    the run reported ``completed``, or the unschedulable node drove the
    deadlock-recovery path forever.  A definition that cannot run is now
    refused at its first executable boundary, with the real reason.
    """


def validate_workflow_graph(graph: WorkflowGraph, *, workflow_id: str | None = None) -> None:
    """Raise :class:`WorkflowDefinitionError` if ``graph`` cannot be executed.

    Checked, in order, because the earlier failures are the more specific ones:

    1. the graph has at least one node (an empty graph can never complete);
    2. every ``nodes`` key matches its node's own ``id``;
    3. every edge endpoint names a node that exists (a dangling edge used to be
       dropped silently, so a run reported ``completed`` while the declared
       downstream work never happened);
    4. every ``depends_on`` entry names a node that exists;
    5. no dependency cycle over ``depends_on`` + edges (a cycle is what drove
       the unbounded deadlock-recovery livelock);
    6. no self-edge (in this engine a self-edge does not re-enter a node, it
       makes the node permanently unschedulable; bounded re-entry is a
       ``loop_policy``).
    """
    label = f"workflow definition '{workflow_id}'" if workflow_id else "workflow definition"
    if not graph.nodes:
        raise WorkflowDefinitionError(f"{label} graph must contain at least one node")

    for key, node in graph.nodes.items():
        if node.id != key:
            raise WorkflowDefinitionError(f"{label} graph node key '{key}' does not match node id '{node.id}'")

    for edge in graph.edges:
        if edge.source not in graph.nodes:
            raise WorkflowDefinitionError(f"{label} graph edge {edge.source!r}->{edge.target!r} names an unknown source node '{edge.source}'")
        if edge.target not in graph.nodes:
            raise WorkflowDefinitionError(f"{label} graph edge {edge.source!r}->{edge.target!r} names an unknown target node '{edge.target}'")

    for key, node in graph.nodes.items():
        for dependency in node.depends_on:
            if dependency not in graph.nodes:
                raise WorkflowDefinitionError(f"{label} graph node '{key}' depends on unknown node '{dependency}'")

    for edge in graph.edges:
        if edge.source == edge.target:
            # A self-edge is not "a loop" in this engine: the scheduler admits a
            # node only while its incoming source is still unexecuted, so a
            # self-edge makes the node permanently unschedulable rather than
            # re-entering it.  Bounded re-entry is expressed by
            # ``loop_policy`` (the node returns to READY via ``node_iteration``
            # and is re-admitted because it has no incoming edge).  Refusing the
            # self-edge names the real fix instead of livelocking.
            raise WorkflowDefinitionError(f"{label} graph node '{edge.source}' is its own successor; a self-edge makes the node permanently unschedulable (express bounded re-entry with loop_policy)")

    # Kahn's algorithm over depends_on + edges.  A cycle is reported with the
    # real unresolved node ids rather than a generic message.  Self-edges are
    # excluded: the scheduler admits a node only while its source is still
    # unexecuted, so a self-edge is a no-op edge, and bounded re-entry is
    # expressed by ``loop_policy`` + ``node_iteration`` (rule 6 above), not by
    # the edge.
    successors: dict[str, set[str]] = {nid: set() for nid in graph.nodes}
    indegree: dict[str, int] = {nid: 0 for nid in graph.nodes}
    for key, node in graph.nodes.items():
        incoming = {e.source for e in graph.incoming_edges(key) if e.source != key}
        for dependency in {*node.depends_on, *incoming}:
            if dependency not in graph.nodes or dependency == key:
                continue
            if key not in successors[dependency]:
                successors[dependency].add(key)
                indegree[key] += 1
    queue = [nid for nid, degree in indegree.items() if degree == 0]
    processed = 0
    while queue:
        current = queue.pop()
        processed += 1
        for dependent in successors[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if processed != len(graph.nodes):
        unresolved = sorted(nid for nid, degree in indegree.items() if degree > 0)
        raise WorkflowDefinitionError(f"{label} graph has a dependency cycle through nodes {unresolved}; a cyclic graph cannot be scheduled to completion")


def _has_unfinished_work(run: WorkflowRun) -> bool:
    """Whether ``run`` still has a node that is neither done nor terminally stuck.

    Used as the no-progress guard when driving a child workflow: a child that
    stopped without a terminal status and without pending work would otherwise
    spin the dispatch loop to its wave ceiling for no reason.
    """
    return any(status.value not in ("succeeded", "skipped", "failed", "cancelled", "aborted") for status in run.node_states.values())


def _is_bounded_loop(node: WorkflowNode) -> bool:
    """Whether a node's own loop policy is a real, positive iteration bound."""
    return node.loop_policy is not None and node.loop_policy.max_iterations > 0


def _recovery_attempts(run: WorkflowRun) -> int:
    """Stagnation-recovery patches already committed for ``run``."""
    raw = run.metrics.get(STAGNATION_RECOVERY_KEY, 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _system1_loop_termination(run: WorkflowRun, node: WorkflowNode, iteration: int) -> dict[str, Any]:
    """Consult the System-1 reflex layer (Master Mission B) about ending a loop.

    Advisory only, and deterministic runtime decides. ``terminate`` is the
    reflex layer's PROPOSAL; ``evidence_agreement`` is the deterministic check
    that lets the runtime act on it — the loop node must already carry real
    evidence from earlier iterations and nothing in the run may have failed.
    A reflex refusal never blocks a loop; a reflex "stop" without agreeing
    evidence is journaled and ignored. Any layer error is returned as a
    disclosed non-termination, never raised into the run.
    """
    try:
        from alpha.system1.pruning import evaluate_loop_termination

        objective = f"{node.prompt}\nstate: {str(run.state)[:2000]}"
        decision = evaluate_loop_termination(objective)
    except Exception as exc:  # noqa: BLE001 - advisory layer; disclosed, never fatal
        return {
            "terminate": False,
            "probability": 0.0,
            "reason": f"system1_unavailable: {type(exc).__name__}: {str(exc)[:200]}",
            "evidence_agreement": False,
        }
    return {
        "terminate": decision.terminate,
        "probability": decision.probability,
        "reason": decision.reason,
        "evidence_agreement": bool(node.evidence) and not run.failed_nodes,
    }


class WaveOverlap:
    """Measure how many wave members actually overlapped in time.

    A wave is dispatched through ``execute_wave(..., max_concurrency=...)`` on a
    bounded pool, but the engine never *acquired* the ``ConcurrencyGovernor`` that
    models that cap - the governor's ``limit`` is read and its slots are not. So
    ``peak_in_flight`` had no source at all and every ``wave_dispatched`` payload
    carried the ``0`` default, including for a genuinely 4-wide wave. A report
    field named ``peak_in_flight`` that always reads 0 is a false measurement,
    not an absent one.

    This counts real overlap around each per-node call instead, so the journal
    records what actually happened. It is **measurement only**: it never blocks
    and never admits, because the documented contract of the governor here is
    that it bounds CONCURRENCY and not wave membership - gating admission on an
    in-flight count would admit exactly ``limit`` nodes and silently serialise a
    three-node wave into three steps.
    """

    __slots__ = ("_current", "_lock", "peak")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current = 0
        self.peak = 0

    @contextmanager
    def track(self):
        with self._lock:
            self._current += 1
            if self._current > self.peak:
                self.peak = self._current
        try:
            yield
        finally:
            with self._lock:
                self._current -= 1

    def observe(self, invoke: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """Wrap a per-item callable so each of its invocations is counted."""

        def _tracked(item: Any) -> Any:
            with self.track():
                return invoke(item)

        return _tracked


class DynamicWorkflowEngine:
    """Production-grade Dynamic Workflow Engine."""

    def __init__(self) -> None:
        self.definitions: dict[str, WorkflowDefinition] = {}
        self.graphs: dict[str, WorkflowGraph] = {}  # key: f"{workflow_id}:v{version}"
        self.runs: dict[str, WorkflowRun] = {}
        # Per-run graph state is authoritative for execution.  The public
        # definition/graphs mapping remains a compatibility projection for
        # read-only callers, but never becomes another run's mutable template.
        self._run_graphs: dict[str, WorkflowGraph] = {}
        self.scheduler = WorkflowScheduler()
        self.patch_engine = WorkflowPatchEngine()
        self.router = DynamicRouter()
        self.replanner = RuntimeReplanner()
        self.events = get_event_dispatcher()
        self._workflow_locks: dict[str, threading.RLock] = {}
        self._workflow_locks_guard = threading.Lock()
        # Serializes every read-modify-write on shared run/graph bookkeeping.
        # Wave nodes now execute CONCURRENTLY (their ``write_scope`` entries are
        # disjoint by construction), so the run's own mutable state is no longer
        # single-threaded by construction.  A lock around each *short* mutation
        # is what keeps that safe; the expensive executor call deliberately runs
        # OUTSIDE it, which is the whole point of the parallel wave.  The lock
        # itself is the process-wide ``_STATE_LOCK`` (see :meth:`state`).
        # Per-run admission control, so a run's declared concurrency cap is
        # enforced in exactly one place regardless of which caller dispatches.
        self._governors: dict[str, ConcurrencyGovernor] = {}
        self._governors_guard = threading.Lock()
        # Per-run wave counter. Kept beside the governor and released with it.
        self._wave_counts: dict[str, int] = {}
        # Durable attempt leases, created on first use so a throwaway engine
        # (``simulate_run``'s dry run) can be handed a process-local manager
        # instead of writing into the real store.  Creation is guarded: a wave
        # dispatches nodes on a bounded pool, and if two of them raced here
        # they would each build a manager over an empty store, so one thread
        # would acquire on a manager the others never see and every later
        # verdict would read UNKNOWN_LEASE and discard good results.
        self._leases: LeaseManager | None = None
        self._lease_init_lock = threading.Lock()
        # Nodes THIS process is executing, keyed ``run:node``.  The lease TTL
        # is a wall-clock observation and lapses on a long-running node, so
        # orphan reconciliation needs process-local ground truth too: a node
        # in this set is alive by construction and is never reconciled away.
        self._in_flight: set[str] = set()
        # One honest worker identity per process.  The lease store is
        # single-Gateway (see ``alpha.workflow.leases``), so a pid is exactly
        # as specific as the guarantee this engine can actually make.
        self.worker_id = f"gateway-pid-{os.getpid()}"

    # ----------------------------------------------------------------- leases

    @property
    def leases(self) -> LeaseManager:
        """Durable, fenced leases for node attempts, created on first use.

        Exactly one manager ever exists per engine.  This matters because a
        manager loads the store into memory at construction and writes it back
        wholesale: two managers built by racing wave threads would each hold a
        private view, an acquire recorded by one would be invisible to the
        other, and every ``check_result`` on that node would come back
        ``UNKNOWN_LEASE`` — discarding work that actually succeeded.  The lock
        closes that window; the fast path stays lock-free once built.
        """
        manager = self._leases
        if manager is None:
            with self._lease_init_lock:
                manager = self._leases
                if manager is None:
                    manager = LeaseManager(runtime_home() / "workflow_store")
                    self._leases = manager
        return manager

    @leases.setter
    def leases(self, value: LeaseManager | None) -> None:
        """Install a manager (a process-local one for throwaway engines)."""
        self._leases = value

    @staticmethod
    def _lease_key(run_id: str, node_id: str) -> str:
        return f"{run_id}:{node_id}"

    def reconcile_orphaned_nodes(self, run: WorkflowRun) -> list[dict[str, Any]]:
        """Fail nodes a dead worker left ``RUNNING``, and say why.

        The event log folds ``node_started`` into ``RUNNING`` and folds
        nothing back when the worker dies mid-attempt, and the scheduler
        admits only ``PENDING``/``READY``.  Left alone, such a node is never
        dispatched again and the run hangs forever on work nobody owns —
        precisely the "a backend restart must not become a task failure"
        case.

        A node is orphaned when no *live* lease holds it: either this process
        never dispatched it (no record — state restored by replay or
        hydration) or its lease has expired (worker presumed gone).  A node
        executing in this process is never touched regardless of lease state,
        because a wall-clock TTL lapsing on a long node is not evidence that
        the node stopped.

        The node is **failed**, never silently reset: we cannot know whether
        its side effects landed, and re-running work whose effects are
        unknown is exactly what the failure taxonomy exists to prevent.  The
        ordinary retry policy then decides what happens next, from
        ``worker_lost``.
        """
        report: list[dict[str, Any]] = []
        graph = self._run_graphs.get(run.run_id)
        graph_version = int(graph.version) if graph is not None else 0
        for node_id, status in list(run.node_states.items()):
            if status is not NodeStatus.RUNNING:
                continue
            key = self._lease_key(run.run_id, node_id)
            if key in self._in_flight:
                continue
            lease = self.leases.get_lease(run.run_id, node_id)
            if lease is not None and not lease.is_expired:
                report.append(
                    {
                        "node_id": node_id,
                        "action": "held",
                        "worker_id": lease.worker_id,
                        "attempt_id": lease.attempt_id,
                        "expires_in_seconds": round(lease.remaining_seconds(), 3),
                    }
                )
                continue
            superseded = self.leases.reclaim(run.run_id, node_id)
            node = graph.nodes.get(node_id) if graph is not None else None
            if node is None:
                # Nothing to fail: the graph no longer carries this node, so
                # clearing the state entry is the whole recovery.
                with self.state():
                    run.node_states.pop(node_id, None)
                report.append({"node_id": node_id, "action": "dropped", "reason": "node no longer present in the run graph"})
                continue
            detail = (
                f"node '{node_id}' was left RUNNING by a worker that is no longer present" if superseded is None else f"node '{node_id}' was left RUNNING by worker {superseded.worker_id} whose lease expired after {superseded.ttl_seconds}s"
            )
            self._fail_node(run, node, detail, failure_class="worker_lost", reconcile_action="failed")
            report.append(
                {
                    "node_id": node_id,
                    "action": "failed",
                    "failure_class": "worker_lost",
                    "reason": detail,
                    "superseded_attempt_id": None if superseded is None else superseded.attempt_id,
                    "superseded_fence_token": None if superseded is None else superseded.fence_token,
                    "graph_version": graph_version,
                }
            )
        if report:
            self.events.emit(
                "orphaned_nodes_reconciled",
                run.run_id,
                reconciled=[entry for entry in report if entry["action"] == "failed"],
                held=[entry for entry in report if entry["action"] == "held"],
                dropped=[entry for entry in report if entry["action"] == "dropped"],
                run_status=run.status.value,
            )
        return report

    def adopt_replayed_run(self, source: DynamicWorkflowEngine, run_id: str) -> tuple[WorkflowRun, list[dict[str, Any]]]:
        """Install a run rebuilt from its event log, then reconcile its orphans.

        ``source`` is the throwaway engine ``replay_run`` produced. This exists
        because the honest fail-closed path alone has a hole: a crash mid-node
        leaves the projection behind its append-only log, ``hydrate()`` refuses
        to install it (correctly — a stale projection is never presented as
        current), and the run then simply has no way back into a live engine.
        Replay rebuilds it faithfully; this installs the result and resolves
        whatever replay had to fold into ``RUNNING`` without an owning worker.

        Nothing here claims the recovered run is correct — only that it now
        matches its journal and that no node is left in a state the scheduler
        will never admit again.
        """
        run = source.runs.get(run_id)
        if run is None:
            raise KeyError(f"Run '{run_id}' is not present on the replayed engine.")
        definition = source.definitions.get(run.workflow_id)
        if definition is None:
            raise KeyError(f"Workflow '{run.workflow_id}' is not present on the replayed engine.")
        graph = source._run_graphs.get(run_id) or source.graphs.get(f"{run.workflow_id}:v{run.graph_version}")
        if graph is None:
            raise KeyError(f"No graph revision {run.graph_version} for workflow '{run.workflow_id}' on the replayed engine.")
        with self._workflow_lock(run.workflow_id):
            self.register_definition(definition, allow_replace=True)
            self.runs[run_id] = run
            self._run_graphs[run_id] = graph
            self.graphs[f"{run.workflow_id}:v{graph.version}"] = graph
            for key, value in source.graphs.items():
                self.graphs.setdefault(key, value)
            report = self.reconcile_orphaned_nodes(run)
        return run, report

    # -------------------------------------------------------------- run state

    @contextmanager
    def state(self) -> Any:
        """Hold the engine's run-state lock.

        Every mutation of shared ``run``/``graph`` bookkeeping that is a
        read-modify-write (token charges, list appends guarded by a membership
        test, status transitions, timing-ledger updates) must happen inside this
        lock, because a parallel wave can have several nodes in flight at once.
        Plain single-key dict assignment is atomic on its own and needs no lock.
        """
        with _STATE_LOCK:
            yield

    def governor_for(self, run: WorkflowRun) -> ConcurrencyGovernor:
        """Return (creating on first use) the admission governor for ``run``.

        The limit comes from the definition's ``policies`` and then the run
        graph's ``metadata``, so widening concurrency is a declared, auditable
        property of a workflow rather than a global tuning knob.
        """
        with self._governors_guard:
            existing = self._governors.get(run.run_id)
            if existing is not None:
                return existing
            declared: Any = None
            definition = self.definitions.get(run.workflow_id)
            if definition is not None:
                declared = definition.policies.get(CONCURRENCY_POLICY_KEY)
            if declared is None:
                graph = self._run_graphs.get(run.run_id)
                if graph is not None:
                    declared = graph.metadata.get(CONCURRENCY_POLICY_KEY)
            # SEQUENTIAL unless the workflow explicitly declares a wider limit.
            # Parallel waves reorder the event log relative to node order, so
            # making concurrency the default would silently change the observable
            # behaviour of every existing definition — and of every deterministic
            # replay, projection and hydration built on that log — with nothing
            # in the workflow having asked for it. Widening is a declared,
            # auditable property of a workflow instead.
            governor = ConcurrencyGovernor(limit=clamp_concurrency(declared, default=SEQUENTIAL_CONCURRENCY))
            self._governors[run.run_id] = governor
            return governor

    def release_run_resources(self, run_id: str) -> None:
        """Drop per-run admission state for a finished run.

        Called when a run reaches a terminal status so a long-lived process does
        not accumulate a governor and a counter per historical run forever.
        """
        with self._governors_guard:
            self._governors.pop(run_id, None)
            self._wave_counts.pop(run_id, None)

    # ------------------------------------------------------------- timing

    def _begin_timing(self, run: WorkflowRun, node: WorkflowNode) -> NodeTiming:
        """Open a measured execution window for ``node``.

        The window is returned rather than stored: the measurement is JOURNALLED
        as a ``node_timed`` event when the window closes, not written into
        ``run.metrics``.  That is deliberate — the DWE guarantees everything in
        ``run.metrics`` is reconstructible from the journal, and a duration
        cannot be re-derived from the events that recorded the work.  Journalling
        also means the timeline survives a restart and a durable hydration.
        """
        with self.state():
            return NodeTiming(
                node_id=node.id,
                started_at=now_iso(),
                status=NodeStatus.RUNNING.value,
                attempts=run.iteration_counts.get(node.id, 0),
                tokens_consumed=node.tokens_consumed,
                thread_name=threading.current_thread().name,
            )

    def _end_timing(
        self,
        run: WorkflowRun,
        node: WorkflowNode,
        timing: NodeTiming,
        started: float,
        *,
        timed_out: bool = False,
    ) -> float:
        """Close the node's measured window, journal it, and return its duration.

        The duration is measured on a monotonic clock rather than derived from
        the two ISO timestamps, so a wall-clock adjustment during a long run
        cannot produce a negative or inflated interval.
        """
        elapsed = max(0.0, time.monotonic() - started)
        with self.state():
            timing.ended_at = now_iso()
            timing.duration_seconds = elapsed
            timing.status = node.status.value
            timing.tokens_consumed = node.tokens_consumed
            timing.timed_out = timed_out
        self.events.emit(NODE_TIMED_EVENT, run.run_id, **timing.to_dict())
        return elapsed

    def _record_wave(
        self,
        run: WorkflowRun,
        *,
        wave_index: int,
        nodes: list[str],
        concurrency: int,
        elapsed: float,
        peak_in_flight: int = 0,
    ) -> None:
        """Journal one measured wave-shape record.

        Journalled rather than accumulated in ``run.metrics`` for the same reason
        as the timing ledger: a wave's elapsed time is not re-derivable from the
        log, and the replay-equality contract requires that ``run.metrics`` match
        between a live run and its replay.
        """
        with self.state():
            self._wave_counts[run.run_id] = max(int(self._wave_counts.get(run.run_id, 0)), wave_index)
        self.events.emit(
            WAVE_DISPATCHED_EVENT,
            run.run_id,
            wave_index=wave_index,
            nodes=list(nodes),
            concurrency=concurrency,
            elapsed_seconds=round(elapsed, 6),
            peak_in_flight=peak_in_flight,
        )

    def _next_wave_index(self, run: WorkflowRun) -> int:
        """The 1-based index of the wave about to be journalled.

        A counter, not a rescan of the run's events. Counting the journalled
        ``wave_dispatched`` records would make every step of a long run re-parse
        its entire history, turning a wave loop into O(events^2) work for no
        additional information — the count is already known.
        """
        with self.state():
            return int(self._wave_counts.get(run.run_id, 0)) + 1

    def register_definition(self, definition: WorkflowDefinition, *, allow_replace: bool = False) -> None:
        """Register an immutable definition without cross-owner replacement.

        Library/host callers get duplicate-id protection by default. Trusted
        internal bridges may opt into replacement explicitly while no run is
        executing; the Gateway CRUD boundary never does so implicitly.
        """
        existing = self.definitions.get(definition.id)
        if existing is not None and not allow_replace:
            if existing.owner_id != definition.owner_id or existing.graph != definition.graph:
                raise ValueError(f"workflow definition '{definition.id}' already exists")
            return
        # Keep an immutable authored base separately from the compatibility
        # graph projection.  Runtime publication may advance definition.graph
        # to a patched revision; replay must start from the authored base to
        # re-apply the append-only patch events honestly.
        definition._base_graph = deepcopy(definition.graph)
        self.definitions[definition.id] = definition
        self.graphs[f"{definition.id}:v{definition.graph.version}"] = definition.graph

    def get_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        return self.definitions.get(workflow_id)

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self.runs.get(run_id)

    def _workflow_lock(self, workflow_id: str) -> threading.RLock:
        with self._workflow_locks_guard:
            lock = self._workflow_locks.get(workflow_id)
            if lock is None:
                lock = threading.RLock()
                self._workflow_locks[workflow_id] = lock
            return lock

    def _publish_run_graph(self, run_id: str) -> None:
        """Refresh the legacy public graph projection for compatibility.

        Execution always reads ``_run_graphs``; this projection is only a
        read-only view for older callers that inspect ``engine.graphs`` or a
        definition after a single run.  Mutating it cannot affect another run.
        """
        run = self.runs.get(run_id)
        private = self._run_graphs.get(run_id)
        if run is None or private is None:
            return
        key = f"{run.workflow_id}:v{run.graph_version}"
        public = self.graphs.get(key)
        if public is None:
            public = deepcopy(private)
            self.graphs[key] = public
            return
        snapshot = deepcopy(private)
        public.version = snapshot.version
        public.metadata = deepcopy(snapshot.metadata)
        public.edges[:] = deepcopy(snapshot.edges)
        public.nodes.clear()
        public.nodes.update(deepcopy(snapshot.nodes))
        definition = self.definitions.get(run.workflow_id)
        if definition is not None:
            definition.graph = public

    def _run_graph_for(self, run: WorkflowRun) -> WorkflowGraph:
        """Return the exact per-run graph, never a mutable definition fallback."""
        private = self._run_graphs.get(run.run_id)
        if private is not None:
            return private
        key = f"{run.workflow_id}:v{run.graph_version}"
        public = self.graphs.get(key)
        if public is None:
            raise DynamicWorkflowError(f"missing graph revision '{key}' for run '{run.run_id}'; continuation refused")
        private = deepcopy(public)
        self._run_graphs[run.run_id] = private
        return private

    def start_run(
        self,
        workflow_id: str,
        initial_state: dict[str, Any] | None = None,
        run_id: str | None = None,
        owner_id: str | None = None,
    ) -> WorkflowRun:
        definition = self.get_definition(workflow_id)
        if not definition:
            raise KeyError(f"Workflow definition '{workflow_id}' not found.")

        # Validate BEFORE a run exists.  A definition that cannot be scheduled
        # is refused here, so no node can have performed a side effect and no
        # ``workflow_started`` event is journaled for a run that was never
        # going to be runnable.
        validate_workflow_graph(definition.graph, workflow_id=workflow_id)

        rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
        if rid in self.runs:
            raise ValueError(f"Run '{rid}' already exists.")
        with self._workflow_lock(workflow_id):
            sibling_graphs = [(other_id, graph) for other_id, graph in self._run_graphs.items() if other_id != rid and self.runs.get(other_id) is not None and self.runs[other_id].workflow_id == workflow_id]
            if sibling_graphs:
                # Detach any first-run graph that still aliases the public
                # template before a concurrent sibling is admitted.
                for other_id, graph in sibling_graphs:
                    if graph is definition.graph:
                        self._run_graphs[other_id] = deepcopy(graph)
                run_graph = deepcopy(definition.graph)
            else:
                # Preserve the historical single-run graph handle for library
                # callers that inspect their submitted graph after execution.
                # Once a sibling exists, the branch above gives every run an
                # isolated graph.
                run_graph = definition.graph
            for node in run_graph.nodes.values():
                node.status = NodeStatus.PENDING
                node.evidence = []
                node.output = None
                node.tokens_consumed = 0
                node.approval_request_id = None
                node.approval_requested_at = None
            run = WorkflowRun(
                run_id=rid,
                workflow_id=workflow_id,
                owner_id=owner_id or definition.owner_id,
                graph_version=run_graph.version,
                status=WorkflowRunStatus.RUNNING,
                state=dict(initial_state) if initial_state is not None else dict(definition.variables),
                node_states={nid: NodeStatus.PENDING for nid in run_graph.nodes},
                budget_limit=definition.budget,
            )
            self.runs[rid] = run
            self._run_graphs[rid] = run_graph

        # Gap 5: the logged state is a SNAPSHOT at start — later mutations of
        # the live ``run.state`` can never rewrite what was journaled.
        self.events.emit(
            "workflow_started",
            rid,
            workflow_id=workflow_id,
            owner_id=run.owner_id,
            state=deepcopy(run.state),
        )
        return run

    def execute_step(
        self,
        run_id: str,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None,
        compensation_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None,
    ) -> WorkflowRun:
        """Serialize graph access for runs sharing one workflow template."""
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")
        with self._workflow_lock(run.workflow_id):
            try:
                return self._execute_step_locked(run_id, node_runner, compensation_runner)
            finally:
                self._publish_run_graph(run_id)

    def _execute_step_locked(
        self,
        run_id: str,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None,
        compensation_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None,
    ) -> WorkflowRun:
        """Execute one scheduling wave of ready nodes.

        ``node_runner`` executes one node and returns a result dict with real
        ``status``/``output``/``evidence``/``tokens_used`` entries. When the
        per-call argument is None the module-level seam bound via
        ``set_node_runner`` is used; when neither is bound, nodes that require
        real execution fail with the honest reason instead of fabricating
        output. ``compensation_runner`` is a separate seam for rollback nodes;
        it is never implicitly replaced by a normal work executor.
        """
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")

        if run.status in TERMINAL_RUN_STATUSES or run.status in PARKED_RUN_STATUSES:
            # Gap 3: BUDGET_EXHAUSTED is terminal for scheduling purposes too —
            # a budget-spent run is never re-entered (its node already failed).
            # PARKED statuses (approval / external event / operator suspend) are
            # non-terminal but equally not dispatchable: stepping a parked run
            # must return its real status rather than resume held work.
            return run

        # Recover any node a dead worker left RUNNING before computing what is
        # ready.  The scheduler admits only PENDING/READY, so an unreconciled
        # orphan would be skipped forever and the run would hang on work nobody
        # owns — this is the seam that makes "a restart must not become a task
        # failure" true for a run whose state was rebuilt from its event log.
        # Cheap by construction: with no RUNNING node it touches neither the
        # lease store nor the event log.
        self.reconcile_orphaned_nodes(run)

        runner = node_runner if node_runner is not None else get_node_runner()

        graph = self._run_graph_for(run)

        if run.budget_limit is not None and run.tokens_consumed >= run.budget_limit:
            reason = f"Workflow budget exhausted: {run.tokens_consumed}/{run.budget_limit} tokens consumed."
            self._exhaust_budget(run, reason)
            return run

        ready = self.scheduler.compute_ready_nodes(graph, run)

        # A conditional router can leave losing branches pending forever.  A
        # completed source plus an all-false alternative group is a proven
        # skip; journal it before the completion/deadlock checks.
        if not ready:
            skipped = self.scheduler.unselected_branch_nodes(graph, run)
            if skipped:
                for nid in skipped:
                    node = graph.nodes[nid]
                    node.status = NodeStatus.SKIPPED
                    run.node_states[nid] = NodeStatus.SKIPPED
                    node.evidence.append("conditional branch was not selected by the completed router")
                    self.events.emit(
                        "node_skipped",
                        run.run_id,
                        node_id=nid,
                        reason="conditional branch not selected",
                        evidence=list(node.evidence),
                    )
                return self.execute_step(run_id, node_runner=runner, compensation_runner=compensation_runner)

        # Check completion
        if not ready:
            all_done = all(run.node_states.get(nid) in (NodeStatus.SUCCEEDED, NodeStatus.SKIPPED) for nid in graph.nodes)
            if all_done:
                _set_run_status(run, WorkflowRunStatus.COMPLETED)
                self.events.emit("workflow_completed", run.run_id, state=deepcopy(run.state))
                return run

            # If no ready nodes and not all done, check if waiting or deadlocked
            if run.waiting_nodes:
                return run

            # Gap 4: stagnation recovery must target a REAL node. The replanner
            # builds insert_before/insert_after ops, and the patch validator
            # rejects ops whose target is not in the graph — so the historical
            # phantom target ("stagnation_recovery") made every proposal fail
            # validation and the run silently dead-ended with a generic reason.
            target_id = next((nid for nid in run.failed_nodes if nid in graph.nodes), None)
            if target_id is None:
                target_id = next(
                    (nid for nid, status in run.node_states.items() if nid in graph.nodes and status in (NodeStatus.PENDING, NodeStatus.READY)),
                    None,
                )

            if target_id is None:
                # No node could anchor a remediation patch: an honest FAILED with
                # the disclosed no-op reason. Nothing is claimed to have been
                # recovered, and no patch is fabricated.
                reason = "Deadlock: no nodes ready to execute; no PENDING/READY node exists to anchor a stagnation-recovery patch (no recovery attempted)."
                _set_run_status(run, WorkflowRunStatus.FAILED, reason=reason)
                self.events.emit("workflow_failed", run.run_id, reason=reason)
                _sync_waiting_nodes(run)
                return run

            # Bounded recovery.  Each committed remediation patch inserts a new
            # ``gather_evidence_<node>_<version>`` node and reopens the SAME
            # unschedulable target, so an unbounded number of attempts is a
            # livelock: the run never reaches a terminal status and both the
            # in-memory graph and the durable event log grow on every call.
            # Past the ceiling the run ends FAILED carrying the real attempt
            # count and the node it kept trying to unblock.
            recovery_attempts = _recovery_attempts(run)
            if recovery_attempts >= STAGNATION_RECOVERY_LIMIT:
                reason = (
                    f"Deadlock: no nodes ready to execute; stagnation recovery exhausted after {recovery_attempts} remediation patch(es) targeting node '{target_id}' (limit {STAGNATION_RECOVERY_LIMIT}); no further progress is achievable."
                )
                _set_run_status(run, WorkflowRunStatus.FAILED, reason=reason)
                self.events.emit(
                    "stagnation_recovery_exhausted",
                    run.run_id,
                    node_id=target_id,
                    attempts=recovery_attempts,
                    limit=STAGNATION_RECOVERY_LIMIT,
                    reason=reason,
                )
                self.events.emit("workflow_failed", run.run_id, reason=reason)
                _sync_waiting_nodes(run)
                return run

            # Attempt replan against the real target if deadlocked
            patch = self.replanner.propose_evidence_remediation_patch(
                target_id,
                "No progress achievable with current graph dependencies.",
                graph,
                run,
            )
            new_graph, validation = self.patch_engine.apply(run, graph, patch)
            if validation.allowed:
                # The deadlock recovery path bypasses ``apply_patch`` so it
                # must install the private per-run graph explicitly.  Updating
                # only the public compatibility map would let the final
                # publication overwrite v2 with the stale v1 nodes.
                self._run_graphs[run.run_id] = new_graph
                self.graphs[f"{run.workflow_id}:v{new_graph.version}"] = new_graph
                # Same PENDING seeding apply_patch does, so the recovery node is schedulable.
                for nid, node in new_graph.nodes.items():
                    if nid not in run.node_states:
                        run.node_states[nid] = node.status
                run.metrics[STAGNATION_RECOVERY_KEY] = recovery_attempts + 1
                self.events.emit(
                    "stagnation_recovery_attempted",
                    run.run_id,
                    node_id=target_id,
                    attempt=recovery_attempts + 1,
                    limit=STAGNATION_RECOVERY_LIMIT,
                    graph_version=new_graph.version,
                )
                return run

            # The recovery proposal itself was rejected: FAILED carrying the
            # patch layer's REAL validation reason, not a generic message.
            reason = f"Deadlock: no nodes ready to execute; remediation patch rejected: {validation.reason}"
            _set_run_status(run, WorkflowRunStatus.FAILED, reason=reason)
            self.events.emit("workflow_failed", run.run_id, reason=reason)
            _sync_waiting_nodes(run)
            return run

        waves = self.scheduler.partition_into_waves(graph, ready)
        wave_nodes = waves[0] if waves else []

        if wave_nodes:
            # A node deferred out of this wave because its write scope overlapped
            # an admitted node's is SAFE (it simply runs in the next wave) but it
            # used to be invisible: nothing in the event log said why "parallel"
            # work was serialized. Journal the pairs so the graph's author can
            # see it and fix the scopes rather than guess.
            if len(waves) > 1:
                collisions = self.scheduler.find_write_scope_collisions(graph, ready)
                if collisions:
                    self.events.emit(
                        "wave_write_scope_serialized",
                        run.run_id,
                        admitted=list(wave_nodes),
                        deferred=[nid for wave in waves[1:] for nid in wave],
                        collisions=collisions,
                        reason=(f"{len(collisions)} node pair(s) declare overlapping write scopes; they were deferred to later waves instead of running concurrently"),
                    )

            governor = self.governor_for(run)
            wave_started = time.monotonic()
            with self.state():
                for nid in wave_nodes:
                    if nid not in run.active_nodes:
                        run.active_nodes.append(nid)

            def _invoke(nid: str) -> None:
                # The whole wave is submitted before any result is inspected, so
                # a node that fails cannot abandon its siblings mid-wave and
                # strand them in RUNNING forever. A gate or failure becomes a
                # run-level stop only AFTER the wave drains, which is the honest
                # semantics: work already in flight really happened and is not
                # pretended away.
                self._execute_single_node(nid, graph, run, runner, compensation_runner)

            # Counted, not limited: see `WaveOverlap`. The governor's cap is
            # applied by the pool below; this only records what overlapped so
            # `peak_in_flight` is a measurement instead of the old hardcoded 0.
            overlap = WaveOverlap()

            # ``governor.limit`` is the run's declared cap and is what
            # ``execute_wave`` bounds the wave by.  A wave is dispatched
            # synchronously inside this call, so gating admission on an
            # in-flight count would admit exactly ``limit`` nodes per step and
            # silently serialise a three-node wave into three steps.  The
            # governor therefore bounds CONCURRENCY, not wave membership: the
            # whole wave always runs, at most ``limit`` at a time.
            outcomes = execute_wave(wave_nodes, overlap.observe(_invoke), max_concurrency=governor.limit)

            self._record_wave(
                run,
                wave_index=self._next_wave_index(run),
                nodes=list(wave_nodes),
                concurrency=governor.limit,
                elapsed=time.monotonic() - wave_started,
                peak_in_flight=overlap.peak,
            )

            escaped = first_error(outcomes)
            if escaped is not None:
                # ``execute_wave`` captures per-node errors so one node cannot
                # abandon the wave. An exception that still escaped the node body
                # is an unexpected fault and must fail the run honestly instead of
                # being swallowed.
                reason = f"wave dispatch fault: {type(escaped).__name__}: {escaped}"
                _set_run_status(run, WorkflowRunStatus.FAILED, reason=reason)
                self.events.emit("workflow_failed", run.run_id, reason=reason)

        # Gap 1: fail-closed after EVERY wave — a run left with failed nodes is
        # driven to FAILED and journals exactly ONE ``workflow_failed`` event,
        # carrying the same reason format the orchestrator kernel's fail-closed
        # policy used (see ExecutionKernel._fail_closed). DEFERRED while any
        # node is still COMPENSATING so the saga compensation wave gets its
        # chance to run first; the kernel's own guard then sees status FAILED
        # and emits nothing — single emission on both layers.
        if run.failed_nodes and run.status in (WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING) and not any(n.status == NodeStatus.COMPENSATING for n in graph.nodes.values()):
            failed = sorted(set(run.failed_nodes))
            reason = f"fail-closed: {len(failed)} node(s) failed: {failed}; see the node_failed events for the real per-node reasons"
            _set_run_status(run, WorkflowRunStatus.FAILED, reason=reason)
            self.events.emit("workflow_failed", run.run_id, reason=reason)

        # Check if all non-compensation nodes are completed
        all_done = all(run.node_states.get(k) in (NodeStatus.SUCCEEDED, NodeStatus.SKIPPED) for k, n in graph.nodes.items() if n.type != NodeType.COMPENSATION)
        if all_done and run.status == WorkflowRunStatus.RUNNING:
            _set_run_status(run, WorkflowRunStatus.COMPLETED)
            self.events.emit("workflow_completed", run.run_id, state=deepcopy(run.state))

        # Gap 9: waiting_nodes is derived from node statuses on every step.
        _sync_waiting_nodes(run)
        run.updated_at = datetime.now(UTC).isoformat()
        if run.status in TERMINAL_RUN_STATUSES:
            # A finished run no longer needs an admission governor; drop it so a
            # long-lived process does not accumulate one per historical run.
            self.release_run_resources(run.run_id)
        return run

    def _fail_node(self, run: WorkflowRun, node: WorkflowNode, reason: str, **extra: Any) -> None:
        """Mark a node honestly failed: real reason in output and event log."""
        with self.state():
            node.status = NodeStatus.FAILED
            run.node_states[node.id] = NodeStatus.FAILED
            # Membership-then-append: two concurrent wave nodes can fail at once,
            # so the guard and the append must be one atomic step or the same id
            # lands in ``failed_nodes`` twice.
            if node.id not in run.failed_nodes:
                run.failed_nodes.append(node.id)
            # Gap 5: deepcopy the extras so neither the node output nor the log can
            # be rewritten through a caller-owned mutable object.
            node.output = {"status": "failed", "reason": reason, **deepcopy(extra)}
        # Gap 10: evidence + iteration counts travel WITH the failure so a replay
        # can reconstruct them without the caller's definition snapshot.
        failure_payload = {
            "reason": reason,
            "evidence": list(node.evidence),
            "iteration_counts": dict(run.iteration_counts),
        }
        attempt_key = self._node_attempt_key(node, run)
        event_key = f"{attempt_key}:failed" if attempt_key else None
        self.events.emit(
            "node_failed",
            run.run_id,
            node_id=node.id,
            idempotency_key=event_key,
            **failure_payload,
        )

    def _exhaust_budget(self, run: WorkflowRun, reason: str) -> None:
        """Terminal budget stop, journaled as its own replayable event.

        Every other terminal run status has a terminal event the log fold
        recognises.  Budget exhaustion used to journal only a ``node_failed``
        (or, on the pre-wave check, a ``workflow_failed``), so a replay of the
        same log produced a DIFFERENT outcome than the live run: the node-level
        case replayed as still ``running`` — a run that was actually
        terminated read as in-flight and could be dispatched again — and the
        pre-wave case replayed as ``failed`` instead of ``budget_exhausted``.
        ``workflow_budget_exhausted`` is the missing terminal seam.
        """
        if run.status == WorkflowRunStatus.BUDGET_EXHAUSTED:
            return
        _set_run_status(run, WorkflowRunStatus.BUDGET_EXHAUSTED, reason=reason)
        self.events.emit(
            "workflow_budget_exhausted",
            run.run_id,
            reason=reason,
            tokens_consumed=run.tokens_consumed,
            budget_limit=run.budget_limit,
            failed_nodes=sorted(set(run.failed_nodes)),
        )

    def _succeed_node(self, run: WorkflowRun, node: WorkflowNode, output: Any, **event_payload: Any) -> None:
        """Mark a node succeeded; the caller must have attached real evidence first."""
        with self.state():
            node.status = NodeStatus.SUCCEEDED
            run.node_states[node.id] = NodeStatus.SUCCEEDED
            if node.id not in run.completed_nodes:
                run.completed_nodes.append(node.id)
            node.output = output
        if not event_payload:
            event_payload = {"output": output}
        # Gap 10: additive evidence/iteration_counts; gap 5: the logged payload
        # is a snapshot (deepcopy), decoupled from live run/graph objects.
        event_payload.setdefault("evidence", list(node.evidence))
        event_payload.setdefault("iteration_counts", dict(run.iteration_counts))
        attempt_key = self._node_attempt_key(node, run)
        event_key = f"{attempt_key}:complete" if attempt_key else None
        if attempt_key:
            self._record_idempotency_key(run, attempt_key)
            # Remember the produced output so a deduplicated re-attempt reports
            # the ORIGINAL result.  It goes in ``metrics``, not ``state``:
            # ``_node_attempt_key`` hashes ``run.state``, so writing the result
            # there would change the key on the next attempt and the dedupe
            # would never match.
            outputs = run.metrics.setdefault(IDEMPOTENT_OUTPUTS_KEY, {})
            if isinstance(outputs, dict):
                outputs[node.id] = deepcopy(output)
        self.events.emit(
            "node_completed",
            run.run_id,
            node_id=node.id,
            idempotency_key=event_key,
            **deepcopy(event_payload),
        )

    def _charge_node_tokens(self, run: WorkflowRun, node: WorkflowNode, tokens: Any) -> bool:
        """Charge real runner-reported tokens against the node budget.

        Returns True when the budget is spent and the node has been failed
        accordingly (never reported as succeeded).

        The accumulate-then-test sequence holds ``_STATE_LOCK``: with parallel
        waves, two nodes can charge concurrently and an unguarded
        ``run.tokens_consumed += charge`` would lose one charge, letting a run
        overspend its real budget before the guard noticed.
        """
        charge = max(0, int(tokens or 0))
        with self.state():
            node.tokens_consumed += charge
            run.tokens_consumed += charge
            if run.budget_limit is not None and run.tokens_consumed > run.budget_limit:
                node.status = NodeStatus.FAILED
                run.node_states[node.id] = NodeStatus.FAILED
                if node.id not in run.failed_nodes:
                    run.failed_nodes.append(node.id)
                run_exhausted = True
                node_exhausted = False
            elif node.budget is not None and node.tokens_consumed > node.budget:
                node.status = NodeStatus.FAILED
                run.node_states[node.id] = NodeStatus.FAILED
                # Gap-8-consistent bookkeeping: a budget-killed node is a failed
                # node, so keep run.failed_nodes (fail-close/handoff/replay folds)
                # in sync with run.node_states. BUDGET_EXHAUSTED is terminal, so
                # this never triggers a fail-closed transition on its own.
                if node.id not in run.failed_nodes:
                    run.failed_nodes.append(node.id)
                run_exhausted = True
                node_exhausted = True
            else:
                run_exhausted = False
                node_exhausted = False

        if run_exhausted and not node_exhausted:
            # Gap 9: budget exhaustion is a real run-status transition.
            self._exhaust_budget(run, "Workflow token budget exhausted.")
            self.events.emit(
                "node_failed",
                run.run_id,
                node_id=node.id,
                reason="Workflow token budget exhausted.",
                evidence=list(node.evidence),
                iteration_counts=dict(run.iteration_counts),
            )
            return True
        if run_exhausted and node_exhausted:
            self._exhaust_budget(run, "Node budget exhausted.")
            self.events.emit(
                "node_failed",
                run.run_id,
                node_id=node.id,
                reason="Node budget exhausted.",
                evidence=list(node.evidence),
                iteration_counts=dict(run.iteration_counts),
            )
            return True
        return False

    @staticmethod
    def _node_attempt_key(node: WorkflowNode, run: WorkflowRun) -> str | None:
        """Return an explicit, deterministic attempt key when one is declared."""
        if not node.idempotency_key:
            return None
        material = json.dumps(
            {"node": node.id, "key": node.idempotency_key, "state": run.state},
            sort_keys=True,
            default=str,
            ensure_ascii=False,
        ).encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()
        return f"{run.run_id}:{node.id}:{node.idempotency_key}:{digest}"

    def _completed_idempotency_keys(self, run: WorkflowRun) -> list[str]:
        recorded = run.metrics.get(COMPLETED_IDEMPOTENCY_KEYS)
        return list(recorded) if isinstance(recorded, list) else []

    def _record_idempotency_key(self, run: WorkflowRun, attempt_key: str) -> None:
        """Remember that this exact attempt already produced a verified success."""
        with self.state():
            completed = self._completed_idempotency_keys(run)
            if attempt_key not in completed:
                completed.append(attempt_key)
            run.metrics[COMPLETED_IDEMPOTENCY_KEYS] = completed

    def _deduplicated_attempt(self, node: WorkflowNode, run: WorkflowRun) -> str | None:
        """The recorded attempt key for this node's effect, if it already ran.

        A node that declares an ``idempotency_key`` names ONE logical side
        effect.  The key already travelled into the append-only log, but nothing
        ever CONSUMED it, so every retry re-ran the effect: a node whose runner
        sends mail, charges a card, or POSTs a record performed the write once
        per attempt.  Returning the key here lets the caller skip the runner and
        report the recorded evidence instead, which is what makes a declared
        idempotent step actually idempotent.

        The check and the later record in :meth:`_invoke_runner` are not one
        atomic step, so the whole decision is made under the state lock: a node
        must never pass the dedupe check and then find its key already recorded
        by a concurrent attempt.
        """
        attempt_key = self._node_attempt_key(node, run)
        if attempt_key is None:
            return None
        with self.state():
            return attempt_key if attempt_key in self._completed_idempotency_keys(run) else None

    def _call_runner_bounded(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str]:
        """Invoke ``runner`` under the node's declared deadline.

        Returns ``(result, timeout_reason)``.  Exactly one is meaningful:
        ``result`` is ``None`` **only** when the deadline was missed, in which
        case ``timeout_reason`` carries the measured, honest description.

        A node with no declared timeout is called inline on the calling thread,
        so unbounded nodes keep their exact previous behaviour.

        A Map/Reduce/Race/Quorum child inherits its parent's
        ``timeout_seconds`` through :meth:`_fanout_child`, so the bound is
        **per child execution** rather than for the whole fan-out. That is the
        useful semantic (one pathological item cannot consume the entire budget)
        and it is deliberately not a whole-node budget: a node that wants a
        total bound should set ``node.budget`` for tokens and a wall-clock bound
        on the parent, not rely on this per-item limit.
        """
        if node.timeout_seconds is None or node.timeout_seconds <= 0:
            return runner(node, run), ""

        result = run_with_deadline(
            lambda: runner(node, run),
            timeout_seconds=float(node.timeout_seconds),
            label=node.id,
        )
        if result.timed_out:
            reason = result.describe_timeout(f"node '{node.id}'")
            # Recorded on this thread so the node's timing entry can be marked
            # ``timed_out`` without threading a flag back out of every branch.
            mark_timeout_occurred()
            self.events.emit(
                "node_timeout",
                run.run_id,
                node_id=node.id,
                timeout_seconds=float(node.timeout_seconds),
                elapsed_seconds=round(result.elapsed_seconds, 6),
                overrun_seconds=round(result.overrun_seconds, 6),
                late_work_fenced=result.late_work_fenced,
                reason=reason,
            )
            return None, reason
        if result.error is not None:
            raise result.error
        return result.value, ""

    def _invoke_runner(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Invoke a real runner with the node's bounded retry policy.

        Retries are opt-in through ``RetryPolicy``.  A failure is retried only
        when its measured text matches an allowed marker (or ``*``), never on a
        blanket assumption that every error is transient.  Each attempt is bounded
        by the node's ``timeout_seconds`` deadline; a missed deadline is a retryable
        failure carrying the real measured overrun, and the worker's late result is
        discarded rather than adopted.  The final typed result is returned
        unchanged so the normal evidence/failure gates remain the single completion
        authority; a direct non-retryable runner exception is re-raised to the outer
        engine boundary so its real reason is not wrapped a second time.
        """
        if runner is None:
            return {"status": "failed", "output": _no_runner_reason(node), "evidence": "", "tokens_used": 0}

        # A declared idempotent step whose effect this run already performed is
        # NOT executed a second time.  The recorded attempt key is the whole
        # point of the field: without this short circuit every retry of a
        # "charge the card" / "send the mail" node duplicated its side effect.
        duplicate = self._deduplicated_attempt(node, run)
        if duplicate is not None:
            self.events.emit(
                "node_deduplicated",
                run.run_id,
                node_id=node.id,
                idempotency_key=duplicate,
                declared_key=node.idempotency_key,
                reason="idempotency key already completed in this run; the side effect was not repeated",
            )
            outputs = run.metrics.get(IDEMPOTENT_OUTPUTS_KEY)
            recorded_output = outputs.get(node.id) if isinstance(outputs, dict) else None
            return {
                "status": "completed",
                "output": recorded_output,
                "evidence": f"idempotency key '{duplicate}' already completed in this run; effect not repeated",
                "tokens_used": 0,
                "deduplicated": True,
            }

        policy = node.retry_policy
        attempts = max(1, min(int(policy.max_attempts), 20))
        # Stagnation is measured per node invocation: a streak of identical
        # error signatures means the unchanged strategy is not distinguishing a
        # transient fault from a deterministic one, so continuing to repeat it
        # is a stall rather than a retry.  The limit is the spec's 3, bounded
        # by the node's own attempt ceiling so a 1-attempt node cannot report a
        # streak it never had.
        stagnation = StagnationDetector(limit=max(1, min(DEFAULT_STAGNATION_LIMIT, attempts)))
        result: dict[str, Any] = {}
        for attempt in range(1, attempts + 1):
            raised_exception: Exception | None = None
            self.events.emit(
                "node_attempt_started",
                run.run_id,
                node_id=node.id,
                attempt=attempt,
                of_attempts=attempts,
                idempotency_key=self._node_attempt_key(node, run),
            )
            try:
                bounded, timeout_reason = self._call_runner_bounded(node, run, runner)
                if bounded is None:
                    # Deadline missed: a real, measured failure. The in-flight
                    # call is fenced (not killed) and its late result discarded,
                    # so it flows through the same retry-marker gate as any other
                    # failure instead of being reported as a success.
                    raw = {"status": "failed", "output": timeout_reason, "evidence": "", "tokens_used": 0}
                else:
                    raw = bounded
            except Exception as exc:  # noqa: BLE001 - preserve the real failure boundary
                raised_exception = exc
                if attempt >= attempts:
                    raise
                raw = {
                    "status": "failed",
                    "output": f"{type(exc).__name__}: {exc}",
                    "evidence": "",
                    "tokens_used": 0,
                }
            if not isinstance(raw, dict):
                raw = {
                    "status": "failed",
                    "output": f"runner returned invalid result {raw!r}; expected dict",
                    "evidence": "",
                    "tokens_used": 0,
                }
            result = raw
            if result.get("status") == "completed":
                return result

            # Classify every failure so the journal records WHAT failed, not
            # just that it did.  The verdict carries the rule that matched, the
            # normalized signature, and both vocabulary bridges, so an operator
            # can audit the classification instead of trusting it.
            failure_text = str(result.get("output", ""))
            classified = classify_node_failure(
                failure_text,
                exception_type=type(raised_exception).__name__ if raised_exception is not None else None,
            )
            self.events.emit(
                "failure_classified",
                run.run_id,
                node_id=node.id,
                attempt=attempt,
                failure_class=classified.failure_class,
                retryable=classified.retryable,
                matched_rule=classified.matched_rule,
                signature=classified.signature,
                recovery_class=classified.recovery_class,
                reason_code=classified.reason_code,
            )

            verdict = stagnation.observe(classified.signature)
            if verdict.stagnated:
                # Stop repeating the identical attempt and say exactly why.
                # The node keeps its real failed result: stagnation is a reason
                # to stop retrying, never a claim about the outcome.
                self.events.emit(
                    "node_stagnated",
                    run.run_id,
                    node_id=node.id,
                    attempt=attempt,
                    failure_class=classified.failure_class,
                    identical_streak=verdict.identical_streak,
                    limit=verdict.limit,
                    signature=classified.signature,
                    reason=verdict.reason,
                    required_action="change_strategy",
                )
                return {
                    **result,
                    "stagnation": {
                        "identical_streak": verdict.identical_streak,
                        "limit": verdict.limit,
                        "failure_class": classified.failure_class,
                        "required_action": "change_strategy",
                    },
                }

            if attempt >= attempts:
                # A spent ceiling must ESCALATE, never become a silently
                # repeated attempt.  The verdict is taken from the repository's
                # existing recovery authority rather than from a table local to
                # this engine, so a workflow node and every other bounded work
                # unit end on the same decision.  `reason_code` is the bridge
                # onto the shared work-unit vocabulary; where the class has no
                # counterpart the authority classifies the measured text
                # itself, and that fallback is recorded too.
                authority = decide_from_reason(
                    failure_text,
                    attempt=attempt,
                    max_attempts=attempts,
                    failure_reason=classified.reason_code,
                )
                self.events.emit(
                    "recovery_exhausted",
                    run.run_id,
                    node_id=node.id,
                    attempt=attempt,
                    of_attempts=attempts,
                    failure_class=classified.failure_class,
                    recovery_class=classified.recovery_class,
                    reason=str(result.get("output", "")),
                    terminal_strategy=authority.action,
                    strategy_source="alpha.recovery.policies",
                    strategy_reason=authority.reason,
                    strategy_bridged=classified.reason_code is not None,
                )
                return result

            text = str(result.get("output", "")).lower()
            markers = [str(marker).lower() for marker in policy.retry_on_errors]
            marker_ok = "*" in markers or "all" in markers or any(marker and marker in text for marker in markers)
            # Two independent gates: the node's marker says this class of text
            # is worth another try, AND the failure class says a retry is not
            # provably futile.  A wildcard marker must not be able to re-enable
            # an auth/permission/security retry that cannot succeed.
            if not (marker_ok and classified.retryable):
                # A direct runner exception is already the authoritative
                # failure.  Let the outer engine boundary journal its exact
                # ``str(exc)`` rather than wrapping it as a second generic
                # "runner reported failure" error.  Typed failed results (for
                # example ExecutorRegistry results carrying a traceback) still
                # flow through the normal wrapper below.
                self.events.emit(
                    "node_retry_refused",
                    run.run_id,
                    node_id=node.id,
                    attempt=attempt,
                    failure_class=classified.failure_class,
                    marker_matched=marker_ok,
                    class_retryable=classified.retryable,
                    matched_rule=classified.matched_rule,
                    reason=(f"retry refused: marker={'matched' if marker_ok else 'not matched'}, class={classified.failure_class} retryable={classified.retryable}"),
                )
                if raised_exception is not None:
                    raise raised_exception
                return result

            delay = 0.0
            if policy.backoff == "fixed":
                delay = policy.initial_delay_seconds
            elif policy.backoff == "exponential":
                delay = policy.initial_delay_seconds * (2 ** (attempt - 1))
            elif policy.backoff == "jitter":
                # Deterministic bounded jitter keeps replay/test behavior stable.
                delay = policy.initial_delay_seconds * (1 + ((attempt - 1) % 3) / 3)
            self.events.emit(
                "node_retry_scheduled",
                run.run_id,
                node_id=node.id,
                attempt=attempt + 1,
                delay_seconds=round(min(delay, policy.max_delay_seconds), 6),
                reason=result.get("output"),
            )
            if delay > 0:
                time.sleep(min(delay, policy.max_delay_seconds))

        return result

    @staticmethod
    def _fanout_child(node: WorkflowNode, child_id: str, overrides: dict[str, Any]) -> WorkflowNode:
        """Build the transient per-item node handed to the runner seam."""
        child = node.model_copy(deep=True)
        child.id = child_id
        child.config = {**node.config, **overrides}
        child.status = NodeStatus.PENDING
        child.evidence = []
        child.output = None
        return child

    def _execute_single_node(
        self,
        nid: str,
        graph: WorkflowGraph,
        run: WorkflowRun,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
        compensation_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None,
    ) -> None:
        """Execute one node inside a measured window.

        Every node has many terminal exits (approval gate, loop stop, each node
        type's success/failure branch, the exception boundary), so the timing
        window is opened and closed HERE rather than inside each branch. A branch
        that forgot to close its window would leave the node permanently
        "in flight" and the run's timeline permanently incomplete.
        """
        node = graph.nodes.get(nid)
        if node is None:
            # A node removed by a concurrent patch between wave computation and
            # dispatch. Skip it honestly rather than inventing a result.
            self.events.emit(
                "node_dispatch_skipped",
                run.run_id,
                node_id=nid,
                reason="node is no longer present in the run graph at dispatch time",
            )
            return

        timing = self._begin_timing(run, node)
        started = time.monotonic()
        # A node runs on exactly one thread, so a thread-local flag is the honest
        # way to carry "this execution missed a deadline" back to the timing
        # layer without threading it through every node-type branch. Reset it
        # first so a previous node on a reused pool thread cannot leak into this
        # node's measurement.
        reset_timeout_flag()
        try:
            self._dispatch_node(nid, graph, run, node_runner, compensation_runner)
        finally:
            self._end_timing(run, node, timing, started, timed_out=timeout_occurred())
            with self.state():
                run.active_nodes = [active for active in run.active_nodes if active != nid]

    def _dispatch_node(
        self,
        nid: str,
        graph: WorkflowGraph,
        run: WorkflowRun,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
        compensation_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None,
        deadline_missed: threading.Event | None = None,
    ) -> None:
        """The real node body: gates, loop policy, node-type handlers, default run."""
        node = graph.nodes[nid]

        # 1. Human-in-the-loop gate
        if (
            node.requires_approval
            and not node.approval_request_id
            and node.status
            not in {
                NodeStatus.READY,
                NodeStatus.SUCCEEDED,
                NodeStatus.SKIPPED,
            }
        ):
            appr_id = f"appr_{uuid.uuid4().hex[:8]}"
            waiting_reason = f"Node '{nid}' requires human approval before execution."
            node.approval_request_id = appr_id
            node.approval_requested_at = datetime.now(UTC).isoformat()
            node.status = NodeStatus.WAITING
            run.node_states[nid] = NodeStatus.WAITING
            # Gap 9: the pause is a real run-status transition, journaled.
            _set_run_status(run, WorkflowRunStatus.WAITING_APPROVAL, reason=waiting_reason)
            run.approval_request_id = appr_id
            run.waiting_reason = waiting_reason
            # Gap 7: node_status rides along so a replay folds the gated node
            # to WAITING instead of leaving it PENDING.
            self.events.emit(
                "approval_requested",
                run.run_id,
                node_id=nid,
                approval_id=appr_id,
                prompt=node.prompt,
                node_status=NodeStatus.WAITING.value,
                requested_at=node.approval_requested_at,
            )
            return

        if node.budget is not None and node.tokens_consumed >= node.budget:
            reason = f"Node budget exhausted before execution: {node.tokens_consumed}/{node.budget} tokens."
            self._fail_node(run, node, reason, budget=node.budget, consumed=node.tokens_consumed)
            self._exhaust_budget(run, reason)
            return

        # 2. Loop Policy & Stagnation Check
        if node.loop_policy:
            count = run.iteration_counts.get(nid, 0)
            if node.loop_policy.stop_condition:
                context = {"state": run.state, "metrics": run.metrics}
                if evaluate_condition(node.loop_policy.stop_condition, context):
                    # Stop condition satisfied: a TRUE completion, journaled so a
                    # replay folds it as succeeded (gap 11) instead of silently
                    # dropping it the way the old no-emit path did.
                    node.evidence.append("loop stop condition evaluated true")
                    node.status = NodeStatus.SUCCEEDED
                    run.node_states[nid] = NodeStatus.SUCCEEDED
                    if nid not in run.completed_nodes:
                        run.completed_nodes.append(nid)
                    self.events.emit(
                        "node_completed",
                        run.run_id,
                        node_id=nid,
                        output=deepcopy(node.output),
                        stopped_by="loop_stop_condition",
                        evidence=list(node.evidence),
                        iteration_counts=dict(run.iteration_counts),
                    )
                    return

            if count >= node.loop_policy.max_iterations:
                if node.loop_policy.max_iterations <= 0 and not node.evidence:
                    self._fail_node(
                        run,
                        node,
                        "loop policy permits zero iterations; no stop-condition evidence was produced",
                    )
                    return
                # Max loop iterations reached after real executor evidence:
                # record the bound as the completion proof.
                if not node.evidence:
                    self._fail_node(run, node, "loop reached its iteration bound without evidence")
                    return
                node.status = NodeStatus.SUCCEEDED
                run.node_states[nid] = NodeStatus.SUCCEEDED
                if nid not in run.completed_nodes:
                    run.completed_nodes.append(nid)
                self.events.emit(
                    "node_completed",
                    run.run_id,
                    node_id=nid,
                    output=deepcopy(node.output),
                    stopped_by="loop_max_iterations",
                    evidence=list(node.evidence),
                    iteration_counts=dict(run.iteration_counts),
                )
                return
            # System-1 reflex hook (Master Mission B): the fast, zero-token
            # reflex layer may PROPOSE an early loop stop. The deterministic
            # runtime decides — the stop is taken only when the proposal comes
            # WITH agreeing evidence (real evidence from prior iterations and
            # nothing failed). Every consideration is journaled, including the
            # ones that do not stop, so a replay can see what was considered.
            reflex = _system1_loop_termination(run, node, count)
            self.events.emit(
                "loop_termination_considered",
                run.run_id,
                node_id=nid,
                iteration=count,
                terminate=reflex["terminate"],
                probability=reflex["probability"],
                reason=reflex["reason"],
                evidence_agreement=reflex["evidence_agreement"],
            )
            if reflex["terminate"] and reflex["evidence_agreement"]:
                node.status = NodeStatus.SUCCEEDED
                run.node_states[nid] = NodeStatus.SUCCEEDED
                if nid not in run.completed_nodes:
                    run.completed_nodes.append(nid)
                self.events.emit(
                    "node_completed",
                    run.run_id,
                    node_id=nid,
                    output=deepcopy(node.output),
                    stopped_by="system1_reflex_agreement",
                    evidence=list(node.evidence),
                    iteration_counts=dict(run.iteration_counts),
                )
                return

            run.iteration_counts[nid] = count + 1

        # A declared ``timeout_seconds`` is now ENFORCED, not refused: every
        # runner invocation for this node runs under the real deadline enforced
        # by ``_call_runner_bounded``.  The old code failed the node outright
        # with "the bound synchronous executor has no cancellation seam", which
        # meant a declared timeout guaranteed failure instead of bounding
        # anything.

        # Claim the attempt DURABLY before the node reads as RUNNING.  The
        # record is what lets a later process tell an in-flight attempt from
        # one whose worker is already dead, and the fence is what lets a
        # result from a superseded attempt be recognised instead of adopted.
        lease = self.leases.acquire_lease(
            run.run_id,
            nid,
            self.worker_id,
            attempt_id=self._node_attempt_key(node, run) or None,
            graph_version=int(graph.version),
        )
        if lease is None:
            held = self.leases.get_lease(run.run_id, nid)
            holder = f"worker {held.worker_id}" if held is not None else "an unknown holder"
            # One-shot and honest: a lease refusal is not a transient fault of
            # THIS attempt — somebody else owns the node — so there is nothing
            # to retry and the run must not be put into a retry loop.
            self._fail_node(run, node, f"node '{nid}' could not be claimed: its lease is held by {holder}")
            return

        # Mark Running
        with self.state():
            node.status = NodeStatus.RUNNING
            run.node_states[nid] = NodeStatus.RUNNING
        self.events.emit(
            "node_started",
            run.run_id,
            node_id=nid,
            type=node.type,
            active_nodes=list(run.active_nodes),
        )
        attempt_key = self._node_attempt_key(node, run)
        if attempt_key:
            attempt_no = run.iteration_counts.get(nid, 0) + 1
            self.events.emit(
                "node_attempt_started",
                run.run_id,
                node_id=nid,
                attempt=attempt_no,
                idempotency_key=attempt_key,
            )

        lease_key = self._lease_key(run.run_id, nid)
        try:
            self._in_flight.add(lease_key)
            # 3. Structural node types (checkpoint / goal_gate / handoff /
            #    wait / event_wait / parallel / subworkflow).  These need no
            #    executor: their completion evidence is a measurement the engine
            #    can take itself.  Handled first so such a node cannot fall
            #    through to the default runner path and demand an executor for
            #    work the runtime already did.
            if self._handle_structural_node(nid, graph, run, node_runner, compensation_runner):
                return

            # 4. Dynamic Node Type Handlers
            if node.type == NodeType.CONDITION:
                context = {"state": run.state, "metrics": run.metrics}
                try:
                    cond_result = evaluate_condition_strict(node.condition, context)
                except Exception as exc:
                    self._fail_node(
                        run,
                        node,
                        f"condition evaluation failed for {node.condition!r}: {type(exc).__name__}: {exc}",
                    )
                    return
                run.state[f"{nid}_result"] = cond_result
                node.output = cond_result
                node.evidence.append(f"Condition '{node.condition}' evaluated to {cond_result}")
                node.status = NodeStatus.SUCCEEDED
                run.node_states[nid] = NodeStatus.SUCCEEDED
                if nid not in run.completed_nodes:
                    run.completed_nodes.append(nid)
                self.events.emit(
                    "node_completed",
                    run.run_id,
                    node_id=nid,
                    output=cond_result,
                    evidence=list(node.evidence),
                    iteration_counts=dict(run.iteration_counts),
                )
                return

            elif node.type == NodeType.ROUTER:
                decisions = self.router.resolve_next_nodes(nid, graph, run)
                node.output = [d.model_dump() for d in decisions]
                node.evidence.append(f"Router selected: {[d.target for d in decisions]}")
                node.status = NodeStatus.SUCCEEDED
                run.node_states[nid] = NodeStatus.SUCCEEDED
                run.completed_nodes.append(nid)
                self.events.emit(
                    "node_completed",
                    run.run_id,
                    node_id=nid,
                    decisions=node.output,
                    evidence=list(node.evidence),
                    iteration_counts=dict(run.iteration_counts),
                )
                return

            elif node.type == NodeType.MAP:
                # Dynamic fan-out: every item is executed through the real runner seam.
                if node_runner is None:
                    self._fail_node(run, node, _no_runner_reason(node))
                    return
                items_key = node.config.get("items_key", "items")
                items = run.state.get(items_key)
                if not isinstance(items, list):
                    self._fail_node(run, node, f"map input '{items_key}' not present as a list in run state")
                    return
                if not items:
                    self._fail_node(run, node, f"map input '{items_key}' contains no items; nothing to execute")
                    return
                results: list[Any] = []
                for idx, item in enumerate(items):
                    child = self._fanout_child(node, f"{nid}[{idx}]", {"item": item, "item_index": idx})
                    res = self._invoke_runner(child, run, node_runner)
                    if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                        return
                    if res.get("status", "completed") != "completed":
                        self._fail_node(run, node, f"map item {idx}: node_runner reported failure: {res.get('output')!r}")
                        return
                    item_evidence = res.get("evidence")
                    if not item_evidence:
                        self._fail_node(run, node, f"map item {idx}: node_runner returned no evidence; completion refused")
                        return
                    results.append(res.get("output"))
                    node.evidence.append(f"map[{idx}] item={item!r}: {item_evidence}")
                run.state[f"{nid}_mapped"] = results
                _record_executor_state(run, f"{nid}_mapped")
                self._succeed_node(run, node, results)
                return

            elif node.type == NodeType.REDUCE:
                # Dynamic fan-in: fold only over real executor-produced child results.
                if node_runner is None:
                    self._fail_node(run, node, _no_runner_reason(node))
                    return
                input_key = node.config.get("input_key")
                if not isinstance(input_key, str) or not input_key:
                    self._fail_node(run, node, f"reduce node '{nid}' has no input_key configured")
                    return
                children = run.state.get(input_key)
                if not isinstance(children, list):
                    self._fail_node(run, node, f"reduce input '{input_key}' not present as a list in run state")
                    return
                if not _is_executor_state(run, input_key):
                    self._fail_node(run, node, f"reduce input '{input_key}' is not recorded as executor-produced child results")
                    return
                if not children:
                    self._fail_node(run, node, f"reduce input '{input_key}' has no executor-produced child results to reduce")
                    return
                accumulator: Any = node.config.get("initial_value")
                for idx, child_value in enumerate(children):
                    child_overrides: dict[str, Any] = {"item": child_value, "item_index": idx, "accumulator": accumulator}
                    child = self._fanout_child(node, f"{nid}[{idx}]", child_overrides)
                    res = self._invoke_runner(child, run, node_runner)
                    if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                        return
                    if res.get("status", "completed") != "completed":
                        self._fail_node(run, node, f"reduce step {idx}: node_runner reported failure: {res.get('output')!r}")
                        return
                    child_evidence = res.get("evidence")
                    if not child_evidence:
                        self._fail_node(run, node, f"reduce step {idx}: node_runner returned no evidence; completion refused")
                        return
                    accumulator = res.get("output")
                    node.evidence.append(f"reduce[{idx}]: {child_evidence}")
                run.state[f"{nid}_reduced"] = accumulator
                _record_executor_state(run, f"{nid}_reduced")
                self._succeed_node(run, node, accumulator)
                return

            elif node.type == NodeType.RACE:
                # Race: bounded first-real-success-wins, every candidate actually run.
                if node_runner is None:
                    self._fail_node(run, node, _no_runner_reason(node))
                    return
                candidates = node.config.get("candidates")
                if not isinstance(candidates, list) or not candidates:
                    self._fail_node(run, node, f"race node '{nid}' has no candidates configured")
                    return
                attempts: list[dict[str, Any]] = []
                for idx, candidate in enumerate(candidates):
                    child = self._fanout_child(node, f"{nid}[{idx}]", {"candidate": candidate, "candidate_index": idx})
                    res = self._invoke_runner(child, run, node_runner)
                    if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                        return
                    candidate_status = res.get("status", "completed")
                    candidate_output = res.get("output")
                    attempts.append({"candidate": candidate, "status": candidate_status, "output": candidate_output})
                    if candidate_status != "completed":
                        continue
                    candidate_evidence = res.get("evidence")
                    if not candidate_evidence:
                        raise UnverifiedNodeCompletionError(f"Race candidate '{candidate}' completed without evidence.")
                    node.evidence.append(f"race winner candidate={candidate!r}: {candidate_evidence}")
                    self._succeed_node(run, node, {"winner": candidate, "attempts": attempts})
                    return
                fail_summary = f"race node '{nid}': no candidate succeeded after {len(candidates)} candidate attempt(s)"
                self._fail_node(run, node, fail_summary, attempts=attempts)
                return

            elif node.type == NodeType.QUORUM:
                # Quorum: real votes come from executor results; config-supplied
                # votes are allowed only with an explicit vote_source disclosure.
                voters = node.config.get("voters")
                config_votes = node.config.get("votes")
                votes: list[str] | None = None
                vote_source: str | None = None
                if isinstance(voters, list) and voters and node_runner is not None:
                    vote_source = "executor"
                    votes = []
                    for idx, voter in enumerate(voters):
                        child = self._fanout_child(node, f"{nid}[{idx}]", {"voter": voter, "voter_index": idx})
                        res = self._invoke_runner(child, run, node_runner)
                        if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                            return
                        if res.get("status", "completed") != "completed":
                            self._fail_node(run, node, f"quorum voter '{voter}': node_runner reported failure: {res.get('output')!r}")
                            return
                        voter_evidence = res.get("evidence")
                        if not voter_evidence:
                            self._fail_node(run, node, f"quorum voter '{voter}': node_runner returned no evidence; completion refused")
                            return
                        raw_vote = res.get("output")
                        if isinstance(raw_vote, dict):
                            raw_vote = raw_vote.get("vote")
                        vote = str(raw_vote).strip().lower() if raw_vote is not None else ""
                        if vote not in ("agree", "disagree"):
                            invalid = f"quorum voter '{voter}' returned invalid vote {raw_vote!r} (expected 'agree' or 'disagree')"
                            self._fail_node(run, node, invalid)
                            return
                        votes.append(vote)
                        node.evidence.append(f"quorum voter {voter!r}: vote={vote} evidence={voter_evidence}")
                elif isinstance(config_votes, list) and config_votes:
                    # Config-supplied votes are disclosed as config, never implied to be agent votes.
                    vote_source = "config"
                    votes = [str(v).strip().lower() for v in config_votes]
                    node.evidence.append(f"provenance: votes supplied via node config (vote_source=config), not agent votes: {votes}")
                else:
                    if node_runner is None:
                        self._fail_node(run, node, _no_runner_reason(node))
                    else:
                        self._fail_node(run, node, f"quorum node '{nid}' has no voters configured and no config-supplied votes")
                    return
                required_votes = int(node.config.get("required_votes", 2))
                agrees = sum(1 for v in votes if v == "agree")
                passed = agrees >= required_votes
                payload = {
                    "agreed": passed,
                    "votes": votes,
                    "vote_source": vote_source,
                    "agrees": agrees,
                    "required_votes": required_votes,
                }
                if passed:
                    node.evidence.append(f"Quorum reached: {agrees}/{required_votes} votes agreed (vote_source={vote_source}).")
                    self._succeed_node(run, node, payload)
                    return
                unmet = f"quorum not reached: {agrees}/{required_votes} votes agreed (vote_source={vote_source})"
                self._fail_node(run, node, unmet, **payload)
                return

            elif node.type == NodeType.COMPENSATION:
                # Saga compensation is a separate side-effect seam.  A legacy
                # caller may still pass its normal node runner explicitly, but
                # nodes marked ``requires_dedicated_compensation_executor``
                # never fall back to a digest/work executor.
                target = node.config.get("target_rollback_node")
                callback = compensation_runner
                if callback is None and not node.config.get("requires_dedicated_compensation_executor", False):
                    callback = node_runner
                if callback is None:
                    self._fail_node(
                        run,
                        node,
                        f"{_no_runner_reason(node)}: no dedicated compensation callback executed",
                        compensated=False,
                        target=target,
                    )
                    return
                res = self._invoke_runner(node, run, callback)
                if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                    return
                if res.get("status", "completed") != "completed":
                    comp_failed = f"compensation callback failed for target '{target}': {res.get('output')!r}"
                    self._fail_node(run, node, comp_failed, compensated=False, target=target)
                    return
                comp_evidence = res.get("evidence")
                if not comp_evidence:
                    no_ev = f"compensation callback for target '{target}' returned no evidence; completion refused"
                    self._fail_node(run, node, no_ev, compensated=False, target=target)
                    return
                node.evidence.append(f"compensation target={target!r}: {comp_evidence}")
                self._succeed_node(run, node, {"compensated": True, "target": target, "result": res.get("output")})
                return

            elif node.type == NodeType.BOT:
                # Bot nodes execute through the real seam. The cloning registry is
                # only consulted when an executor exists to run the bot's task; a
                # clone without a model/tool call is never reported as execution.
                if node_runner is None:
                    self._fail_node(run, node, f"{_no_runner_reason(node)}: no bot executor performed a model/tool call")
                    return
                if node.config.get("clone_bot", False):
                    from alpha.bots.cloning import get_bot_clone_engine

                    specialist_directive = node.config.get("specialist_directive", node.prompt)
                    bot_profile = get_bot_clone_engine().clone_bot(
                        source_name=node.config.get("bot_name", "developer"),
                        specialist_directive=specialist_directive,
                        skills_to_add=node.config.get("skills"),
                        tools_to_add=node.config.get("tools"),
                        ttl_seconds=node.config.get("ttl_seconds", 3600),
                    )
                    # Real registry side effect, logged with the true clone name.
                    self.events.emit("bot_cloned", run.run_id, node_id=nid, bot_name=bot_profile.name)
                # Fall through: the bot's task itself runs through node_runner below.

            # Default / Agent / Tool execution (and prepared BOT tasks above)
            if node_runner is not None:
                res = self._invoke_runner(node, run, node_runner)
                status = res.get("status", "completed")
                output = res.get("output")
                evidence = res.get("evidence")
                tokens = res.get("tokens_used", 0)

                if self._charge_node_tokens(run, node, tokens):
                    return

                if status == "completed":
                    # Fence before adopting.  A worker that outlived its lease,
                    # or whose node was replanned out from under it, produces a
                    # result that LOOKS complete; the lease is the only thing
                    # that can tell the difference, so it is asked before any
                    # evidence, output or status is folded into the run.
                    verdict = self.leases.check_result(
                        run.run_id,
                        nid,
                        worker_id=self.worker_id,
                        fence_token=lease.fence_token,
                        graph_version=int(graph.version),
                    )
                    if verdict is not ResultVerdict.ACCEPTED:
                        self._fail_node(
                            run,
                            node,
                            f"node '{nid}' result discarded: lease verdict is '{verdict.value}' (attempt fence {lease.fence_token} no longer owns this node)",
                            lease_verdict=verdict.value,
                        )
                        return
                    # Evidence must belong to THIS executor result.  Reusing
                    # evidence from an earlier loop iteration would let a
                    # later, unverified attempt inherit a success marker.
                    if not evidence:
                        raise UnverifiedNodeCompletionError(f"Node '{nid}' completed without evidence.")
                    node.evidence.append(evidence)
                    node.output = output
                    if node.loop_policy:
                        stop_met = False
                        if node.loop_policy.stop_condition:
                            context = {"state": run.state, "metrics": run.metrics}
                            stop_met = evaluate_condition(node.loop_policy.stop_condition, context)
                        count = run.iteration_counts.get(nid, 0)
                        if stop_met or count >= node.loop_policy.max_iterations:
                            # True completion: the loop's stop condition or its
                            # bounded iteration limit was reached (gap 11).
                            node.status = NodeStatus.SUCCEEDED
                            run.node_states[nid] = NodeStatus.SUCCEEDED
                            if nid not in run.completed_nodes:
                                run.completed_nodes.append(nid)
                            self.events.emit(
                                "node_completed",
                                run.run_id,
                                node_id=nid,
                                output=deepcopy(output),
                                stopped_by=("loop_stop_condition" if stop_met else "loop_max_iterations"),
                                evidence=list(node.evidence),
                                iteration_counts=dict(run.iteration_counts),
                            )
                        else:
                            # Interim iteration: the node stays READY and OUT of
                            # completed_nodes, and is journaled as node_iteration
                            # (gap 11) so a mid-run replay never over-states it
                            # as succeeded the way the old node_completed emit did.
                            node.status = NodeStatus.READY
                            run.node_states[nid] = NodeStatus.READY
                            self.events.emit(
                                "node_iteration",
                                run.run_id,
                                node_id=nid,
                                output=deepcopy(output),
                                evidence=list(node.evidence),
                                iteration_counts=dict(run.iteration_counts),
                            )
                    else:
                        # Route the plain (non-loop) success through the shared
                        # helper so a declared ``idempotency_key`` is actually
                        # RECORDED here too.  This branch used to inline the
                        # state change, which is why the key was journalled but
                        # never consumed: a retried "send the mail" node
                        # repeated its effect on every attempt.
                        self._succeed_node(run, node, output)
                else:
                    raise RuntimeError(f"Runner reported failure for node '{nid}': {output}")
            else:
                # No executor is bound: fail honestly, never fabricate success.
                self._fail_node(run, node, _no_runner_reason(node))

        except Exception as exc:
            # Gap 2: the exception path unifies on ``_fail_node`` — the SAME
            # ``reason`` key (plus the node output dict) every other node_failed
            # carries, instead of a one-off ``error`` key. ``str(exc)`` still
            # carries the runner's captured traceback verbatim.
            self._fail_node(run, node, str(exc))

            # Trigger Saga Compensation if defined
            if node.compensation_node_id and node.compensation_node_id in graph.nodes:
                comp_node = graph.nodes[node.compensation_node_id]
                comp_node.status = NodeStatus.COMPENSATING
                run.node_states[comp_node.id] = NodeStatus.COMPENSATING
                self.events.emit("compensation_triggered", run.run_id, node_id=comp_node.id)

        finally:
            # The attempt is over whether it completed, parked at an approval
            # or wait, looped for another iteration, or failed — so the durable
            # claim is released and the node stops counting as executing here.
            # The fence travels with the release so a late release from a
            # superseded attempt cannot steal a newer claim.
            self._in_flight.discard(lease_key)
            self.leases.release_lease(run.run_id, nid, self.worker_id, fence_token=lease.fence_token)

    # ------------------------------------------------------- structural nodes

    def _handle_structural_node(
        self,
        nid: str,
        graph: WorkflowGraph,
        run: WorkflowRun,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
        compensation_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
    ) -> bool:
        """Handle node kinds whose evidence the engine can produce itself.

        Returns ``True`` when the node was handled (it is now terminal or
        parked) and ``False`` when the kind is not structural, in which case the
        caller continues into the executor-driven handlers.

        Every branch is fail-closed: a structural node that cannot take its
        measurement fails with the real reason rather than being skipped or
        treated as complete. The one thing these nodes never do is *invent*
        domain work — a ``CHECKPOINT`` proves a digest of the state it snapshotted,
        and a ``GOAL_GATE`` proves which declared criteria it could actually
        measure, not that the underlying goal was met.
        """
        node = graph.nodes[nid]
        kind = node.type

        if kind == NodeType.CHECKPOINT:
            return self._handle_checkpoint(nid, run, node)
        if kind == NodeType.GOAL_GATE:
            return self._handle_goal_gate(nid, run, node)
        if kind == NodeType.HANDOFF:
            return self._handle_handoff_node(nid, run, node)
        if kind == NodeType.WAIT:
            return self._handle_wait(nid, run, node)
        if kind == NodeType.EVENT_WAIT:
            return self._handle_event_wait(nid, run, node)
        if kind == NodeType.PARALLEL:
            return self._handle_parallel(nid, graph, run, node, node_runner, compensation_runner)
        if kind == NodeType.SUBWORKFLOW:
            return self._handle_subworkflow(nid, run, node, node_runner, compensation_runner)
        return False

    def _handle_checkpoint(self, nid: str, run: WorkflowRun, node: WorkflowNode) -> bool:
        """Record a verifiable, content-addressed snapshot of the run state.

        The evidence is a SHA-256 over the exact state captured, so any consumer
        can recompute it and detect whether the state really was the one
        checkpointed. This is a measurement, not a durability claim: the
        authoritative durable record is still the append-only event log
        (:mod:`alpha.workflow.event_log`), and the node's evidence says so.
        """
        material = json.dumps(run.state, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()
        label = str(node.config.get("label") or nid)
        record = {
            "label": label,
            "node_id": nid,
            "state_sha256": digest,
            "state_keys": sorted(str(key) for key in run.state),
            "tokens_consumed": run.tokens_consumed,
            "recorded_at": now_iso(),
        }
        with self.state():
            checkpoints = run.metrics.get(CHECKPOINT_LEDGER_KEY)
            if not isinstance(checkpoints, list):
                checkpoints = []
            checkpoints.append(record)
            run.metrics[CHECKPOINT_LEDGER_KEY] = checkpoints
        # ``record`` already carries ``node_id``; passing it again would collide
        # with the event's own payload binding.
        self.events.emit("workflow_checkpointed", run.run_id, **record)
        node.evidence.append(
            f"checkpoint '{label}' captured {len(record['state_keys'])} state key(s); state sha256={digest} (recomputable over the sorted-key JSON of run.state); the append-only event log remains the authoritative durable record"
        )
        self._succeed_node(run, node, record)
        return True

    def _handle_goal_gate(self, nid: str, run: WorkflowRun, node: WorkflowNode) -> bool:
        """Evaluate the run's declared acceptance criteria as a real gate.

        Criteria are ``{name, expression, required}`` records evaluated by the
        SAME safe AST evaluator used for routing, so a gate cannot smuggle in
        arbitrary code. A criterion that cannot be evaluated is reported as
        ``unevaluated`` and counts as NOT met when required — a gate must never
        pass on the strength of a check that did not run.

        The patch validator already refuses to remove or replace a ``goal_gate``,
        so it stays a real barrier between execution and a ``completed`` status.
        """
        raw_criteria = node.config.get("acceptance_criteria")
        if raw_criteria is None:
            state_criteria = run.state.get("acceptance_criteria")
            raw_criteria = state_criteria if isinstance(state_criteria, list) else []
        if not isinstance(raw_criteria, list) or not raw_criteria:
            self._fail_node(
                run,
                node,
                f"goal gate '{nid}' declares no acceptance criteria; a gate with nothing to verify cannot pass",
                criteria=[],
            )
            return True

        context = {"state": run.state, "metrics": run.metrics}
        results: list[dict[str, Any]] = []
        for index, criterion in enumerate(raw_criteria):
            if not isinstance(criterion, dict):
                results.append(
                    {
                        "index": index,
                        "name": f"criterion[{index}]",
                        "required": False,
                        "met": False,
                        "evaluated": False,
                        "detail": f"criterion is not a mapping: {criterion!r}",
                    }
                )
                continue
            name = str(criterion.get("name") or f"criterion[{index}]")
            expression = criterion.get("expression")
            required = bool(criterion.get("required", True))
            if not isinstance(expression, str) or not expression.strip():
                results.append({"index": index, "name": name, "required": required, "met": False, "evaluated": False, "detail": "criterion declares no expression"})
                continue
            try:
                met = evaluate_condition_strict(expression, context)
            except Exception as exc:  # noqa: BLE001 - an unevaluable gate is not a passing gate
                results.append({"index": index, "name": name, "required": required, "met": False, "evaluated": False, "detail": f"{type(exc).__name__}: {exc}"})
                continue
            results.append({"index": index, "name": name, "required": required, "met": met, "evaluated": True, "detail": expression})

        unmet_required = [item["name"] for item in results if item.get("required") and not item.get("met")]
        unevaluated = [item["name"] for item in results if not item.get("evaluated")]
        payload = {"passed": not unmet_required, "criteria": results, "unmet_required": unmet_required, "unevaluated": unevaluated}
        if unmet_required:
            detail = f"unmet required criteria: {unmet_required}"
            if unevaluated:
                detail += f"; {len(unevaluated)} criterion/criteria could not be evaluated and count as not met: {unevaluated}"
            self._fail_node(run, node, f"goal gate '{nid}' rejected: {detail}", **payload)
            return True
        node.evidence.append(f"goal gate '{nid}' passed {len(results)} declared criterion/criteria (required: {sorted(item['name'] for item in results if item.get('required'))})")
        self._succeed_node(run, node, payload)
        return True

    def _handle_handoff_node(self, nid: str, run: WorkflowRun, node: WorkflowNode) -> bool:
        """Publish a cross-mode handoff contract into the run's state.

        The contract is built from REAL run state only: completed nodes, failed
        nodes, and what remains. ``decisions`` stays empty because the engine
        journals no DecisionRecords, so this node cannot become a place where
        artefacts are conjured up to make a handoff look complete.
        """
        completed = list(run.completed_nodes)
        failed = sorted(set(run.failed_nodes))
        remaining = sorted(nid_ for nid_, status in run.node_states.items() if status.value not in ("succeeded", "skipped"))
        declared_files = node.config.get("files")
        files = [str(item) for item in declared_files] if isinstance(declared_files, list) else []
        contract = {
            "objective": str(run.state.get("objective") or node.prompt or nid),
            "from_node": nid,
            "to": node.config.get("handoff_to") or node.config.get("to"),
            "status": run.status.value,
            "completed": completed,
            "failed": failed,
            "remaining": remaining,
            "files": files,
            "decisions": [],
        }
        with self.state():
            run.state[f"{nid}_handoff"] = contract
        node.evidence.append(f"handoff contract recorded: {len(completed)} completed, {len(failed)} failed, {len(remaining)} remaining; decisions are empty because the run recorded none")
        self._succeed_node(run, node, contract)
        return True

    def _handle_wait(self, nid: str, run: WorkflowRun, node: WorkflowNode) -> bool:
        """A bounded in-process timer.

        Distinct from ``EVENT_WAIT``: this is a delay the run owes itself, not an
        external signal. The duration is clamped to a hard ceiling so a typo
        (``delay_seconds: 100000``) cannot park a run for days, and the MEASURED
        sleep is reported rather than the requested number.
        """
        raw_delay = node.config.get("delay_seconds", node.config.get("seconds"))
        try:
            requested = float(raw_delay)
        except (TypeError, ValueError):
            self._fail_node(run, node, f"wait node '{nid}' has a non-numeric delay_seconds {raw_delay!r}", requested=raw_delay)
            return True
        if requested < 0:
            self._fail_node(run, node, f"wait node '{nid}' declares a negative delay_seconds {requested}", requested=requested)
            return True
        effective = min(requested, MAX_WAIT_SECONDS)
        started = time.monotonic()
        if effective > 0:
            time.sleep(effective)
        measured = time.monotonic() - started
        payload = {"requested_seconds": requested, "slept_seconds": round(measured, 6), "clamped": effective != requested}
        if effective != requested:
            node.evidence.append(f"wait delay clamped from {requested}s to the {MAX_WAIT_SECONDS:g}s ceiling")
        self._succeed_node(run, node, payload)
        return True

    def _handle_event_wait(self, nid: str, run: WorkflowRun, node: WorkflowNode) -> bool:
        """Park the node until an external signal or its deadline.

        This is the node kind that makes ``WorkflowRunStatus.WAITING_EVENT``
        reachable — previously that status existed but nothing could ever set it,
        so a workflow had no way to wait for an outside world at all.

        Resolution is explicit: :meth:`signal_event` delivers a named signal, or
        :meth:`sweep_expired_waits` fails the wait honestly once its deadline
        passes. Nothing polls, guesses, or times the wait out silently.
        """
        event_name = node.config.get("event") or node.config.get("event_name")
        if not isinstance(event_name, str) or not event_name.strip():
            self._fail_node(run, node, f"event_wait node '{nid}' declares no event name; a wait with nothing to wait for cannot park")
            return True
        event_name = event_name.strip()

        with self.state():
            waits = run.metrics.get(EXTERNAL_WAIT_REGISTRY_KEY)
            if not isinstance(waits, dict):
                waits = {}
            registration = waits.get(nid)
            if isinstance(registration, dict) and registration.get("signalled"):
                payload = registration.get("payload")
                waits.pop(nid, None)
                run.metrics[EXTERNAL_WAIT_REGISTRY_KEY] = waits
                run.state[f"{nid}_event_payload"] = payload
                node.evidence.append(f"external event '{event_name}' delivered; payload keys: {sorted(payload) if isinstance(payload, dict) else 'non-mapping payload'}")
                self._succeed_node(run, node, {"event": event_name, "payload": payload})
                return True
            if not isinstance(registration, dict):
                registration = {
                    "event": event_name,
                    "registered_at": now_iso(),
                    "deadline_seconds": node.config.get("timeout_seconds", node.gate_timeout_seconds),
                }
                waits[nid] = registration
                run.metrics[EXTERNAL_WAIT_REGISTRY_KEY] = waits
                self.events.emit(
                    "external_wait_registered",
                    run.run_id,
                    node_id=nid,
                    event=event_name,
                    deadline_seconds=registration.get("deadline_seconds"),
                )

        # Park: the node goes WAITING and the run reports WAITING_EVENT — a real,
        # non-terminal state the engine refuses to re-dispatch until a signal or
        # a deadline sweep resolves it.
        with self.state():
            node.status = NodeStatus.WAITING
            run.node_states[nid] = NodeStatus.WAITING
        reason = f"Node '{nid}' is waiting for external event '{event_name}'."
        _set_run_status(run, WorkflowRunStatus.WAITING_EVENT, reason=reason)
        run.waiting_reason = reason
        self.events.emit(
            "external_wait_parked",
            run.run_id,
            node_id=nid,
            event=event_name,
            node_status=NodeStatus.WAITING.value,
            reason=reason,
        )
        _sync_waiting_nodes(run)
        return True

    def _handle_parallel(
        self,
        nid: str,
        graph: WorkflowGraph,
        run: WorkflowRun,
        node: WorkflowNode,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
        compensation_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
    ) -> bool:
        """Execute an explicitly named set of child nodes concurrently.

        ``NodeType.PARALLEL`` used to be a no-op that fell through to the default
        runner path, so declaring it silently demanded an executor instead of
        expressing intent. It now means what it says: the node names its members
        in ``config.nodes`` and the engine runs them as one bounded wave.

        Membership is validated against the real graph before anything executes —
        a name that is not a node of this run fails the gate rather than being
        skipped. The parent succeeds only when EVERY member succeeded, so a
        partially-completed group is never reported as a completed group.
        """
        members = node.config.get("nodes")
        if not isinstance(members, list) or not members:
            self._fail_node(run, node, f"parallel node '{nid}' declares no member nodes in config.nodes; an empty group cannot run")
            return True
        member_ids = [str(item) for item in members]
        missing = [member for member in member_ids if member not in graph.nodes]
        if missing:
            self._fail_node(run, node, f"parallel node '{nid}' names member(s) absent from the run graph: {missing}", members=member_ids)
            return True
        if nid in member_ids:
            self._fail_node(run, node, f"parallel node '{nid}' lists itself as a member, which would recurse forever")
            return True

        governor = self.governor_for(run)
        with self.state():
            for member in member_ids:
                if member not in run.active_nodes:
                    run.active_nodes.append(member)

        started = time.monotonic()
        # As with a scheduling wave, the whole group runs; ``max_concurrency``
        # bounds how many members overlap rather than truncating the group.
        group_overlap = WaveOverlap()
        outcomes = execute_wave(
            member_ids,
            group_overlap.observe(lambda member: self._execute_single_node(member, graph, run, node_runner, compensation_runner)),
            max_concurrency=governor.limit,
        )
        self._record_wave(
            run,
            wave_index=self._next_wave_index(run),
            nodes=list(member_ids),
            concurrency=governor.limit,
            elapsed=time.monotonic() - started,
            peak_in_flight=group_overlap.peak,
        )

        escaped = first_error(outcomes)
        if escaped is not None:
            self._fail_node(run, node, f"parallel group '{nid}' dispatch fault: {type(escaped).__name__}: {escaped}")
            return True

        statuses = {member: run.node_states.get(member) for member in member_ids}
        not_succeeded = sorted(member for member, status in statuses.items() if status != NodeStatus.SUCCEEDED)
        payload = {
            "group": nid,
            "members": member_ids,
            "executed": list(member_ids),
            "member_statuses": {member: (status.value if status is not None else "unknown") for member, status in statuses.items()},
            "duration_seconds": round(time.monotonic() - started, 6),
        }
        if not_succeeded:
            self._fail_node(
                run,
                node,
                f"parallel group '{nid}' did not complete: {len(not_succeeded)}/{len(member_ids)} member(s) are not succeeded: {not_succeeded}",
                **payload,
            )
            return True
        node.evidence.append(f"parallel group '{nid}' completed all {len(member_ids)} member(s): {member_ids}")
        self._succeed_node(run, node, payload)
        return True

    def _handle_subworkflow(
        self,
        nid: str,
        run: WorkflowRun,
        node: WorkflowNode,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
        compensation_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
    ) -> bool:
        """Run a registered child workflow to a terminal state, then adopt it.

        The child is a REAL run of a REAL registered definition, driven through
        this same engine — not a simulation and not a summarized claim. The
        parent adopts the child's MEASURED terminal status: a child that failed
        fails the parent, and only a genuinely ``completed`` child lets the
        parent succeed.

        Recursion is bounded: a child naming its own parent workflow is refused,
        because an unbounded subworkflow cycle would grow the run's state with no
        bound the operator ever declared.
        """
        child_id = node.config.get("workflow_id") or node.config.get("subworkflow")
        if not isinstance(child_id, str) or not child_id.strip():
            self._fail_node(run, node, f"subworkflow node '{nid}' declares no workflow_id in its config")
            return True
        child_id = child_id.strip()
        if child_id == run.workflow_id:
            self._fail_node(run, node, f"subworkflow node '{nid}' targets its own workflow '{child_id}', which would recurse forever")
            return True
        if not self.get_definition(child_id):
            self._fail_node(run, node, f"subworkflow node '{nid}' targets workflow '{child_id}', which is not registered on this engine")
            return True

        try:
            waves = int(node.config.get("max_waves", SUBWORKFLOW_WAVE_CEILING))
        except (TypeError, ValueError):
            waves = SUBWORKFLOW_WAVE_CEILING
        waves = max(1, min(waves, SUBWORKFLOW_WAVE_CEILING))

        started = time.monotonic()
        try:
            child = self.start_run(child_id, initial_state=dict(node.config.get("initial_state") or {}), owner_id=run.owner_id)
        except Exception as exc:  # noqa: BLE001 - surfaced with its real reason
            self._fail_node(run, node, f"subworkflow node '{nid}' could not start child '{child_id}': {type(exc).__name__}: {exc}")
            return True

        dispatched = 0
        try:
            while dispatched < waves:
                self.execute_step(child.run_id, node_runner=node_runner, compensation_runner=compensation_runner)
                dispatched += 1
                if child.status in TERMINAL_RUN_STATUSES:
                    break
                if not _has_unfinished_work(child):
                    break
        except Exception as exc:  # noqa: BLE001
            self._fail_node(
                run,
                node,
                f"subworkflow node '{nid}' child '{child_id}' faulted: {type(exc).__name__}: {exc}",
                child_run_id=child.run_id,
            )
            return True

        completed_evidence = [nid_ for nid_, status in child.node_states.items() if status == NodeStatus.SUCCEEDED]
        payload = {
            "child_workflow_id": child_id,
            "child_run_id": child.run_id,
            "child_status": child.status.value,
            "waves_dispatched": dispatched,
            "child_completed_nodes": completed_evidence,
            "child_tokens_consumed": child.tokens_consumed,
            "duration_seconds": round(time.monotonic() - started, 6),
        }
        if child.status is not WorkflowRunStatus.COMPLETED:
            self._fail_node(
                run,
                node,
                f"subworkflow node '{nid}' child run '{child.run_id}' ended '{child.status.value}', not completed; the parent does not adopt an unfinished child",
                **payload,
            )
            return True
        with self.state():
            run.state[f"{nid}_subworkflow"] = payload
        node.evidence.append(f"subworkflow '{child_id}' completed as run '{child.run_id}' after {dispatched} wave(s) with {len(completed_evidence)} verified node(s) and {child.tokens_consumed} token(s)")
        self._succeed_node(run, node, payload)
        return True

    # ------------------------------------------------- external signal surface

    def signal_event(self, run_id: str, event_name: str, payload: Any = None) -> WorkflowRun:
        """Deliver a named external signal and release its waiting nodes.

        The counterpart to :meth:`_handle_event_wait`. Only nodes registered for
        exactly this event are released; a signal for an event nothing waits on is
        journalled as unmatched and changes no node state, so a typo in a caller's
        event name cannot silently advance a run.
        """
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Run '{run_id}' not found.")
        if not isinstance(event_name, str) or not event_name.strip():
            raise ValueError("event_name must be a non-empty string")
        event_name = event_name.strip()
        graph = self._run_graph_for(run)

        with self.state():
            waits = run.metrics.get(EXTERNAL_WAIT_REGISTRY_KEY)
            waits = waits if isinstance(waits, dict) else {}
            matched = sorted(nid for nid, registration in waits.items() if isinstance(registration, dict) and registration.get("event") == event_name and not registration.get("signalled"))
            for nid in matched:
                waits[nid]["signalled"] = True
                waits[nid]["signalled_at"] = now_iso()
                waits[nid]["payload"] = payload
            if matched:
                run.metrics[EXTERNAL_WAIT_REGISTRY_KEY] = waits

        # Return the parked node to READY.  The scheduler only admits a node in
        # PENDING or READY, so a signalled node left in WAITING would stay
        # permanently unschedulable and the run could never leave WAITING_EVENT.
        # The next step re-enters ``_handle_event_wait``, which consumes the
        # recorded signal and completes the node with the delivered payload.
        released: list[str] = []
        for nid in matched:
            node = graph.nodes.get(nid)
            if node is None:
                continue
            with self.state():
                if run.node_states.get(nid) == NodeStatus.WAITING:
                    node.status = NodeStatus.READY
                    run.node_states[nid] = NodeStatus.READY
                    released.append(nid)

        self.events.emit(
            "external_event_signalled",
            run_id,
            event=event_name,
            matched_nodes=matched,
            released_nodes=released,
            unmatched=not matched,
            payload_keys=sorted(payload) if isinstance(payload, dict) else None,
        )
        if released and run.status is WorkflowRunStatus.WAITING_EVENT:
            _set_run_status(run, WorkflowRunStatus.RUNNING, reason=f"external event '{event_name}' delivered")
            run.waiting_reason = None
            _sync_waiting_nodes(run)
        return run

    def sweep_expired_waits(self, run_id: str) -> WorkflowRun:
        """Fail every external wait whose declared deadline has already passed.

        The honest counterpart to :meth:`signal_event`: a wait nobody ever
        satisfies must end, and it must end as a FAILURE carrying the measured
        deadline. Letting it park forever would make the run permanently
        non-terminal while reporting no error at all.
        """
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Run '{run_id}' not found.")
        graph = self._run_graph_for(run)
        now = datetime.now(UTC)

        expired: list[tuple[str, float]] = []
        with self.state():
            waits = run.metrics.get(EXTERNAL_WAIT_REGISTRY_KEY)
            waits = dict(waits) if isinstance(waits, dict) else {}
            for nid, registration in waits.items():
                if not isinstance(registration, dict) or registration.get("signalled"):
                    continue
                try:
                    deadline = float(registration.get("deadline_seconds"))
                except (TypeError, ValueError):
                    continue
                registered_at = registration.get("registered_at")
                age = 0.0
                if isinstance(registered_at, str):
                    try:
                        age = (now - datetime.fromisoformat(registered_at)).total_seconds()
                    except ValueError:
                        age = 0.0
                if age >= deadline:
                    expired.append((nid, age))
            for nid, _age in expired:
                waits.pop(nid, None)
            if expired:
                run.metrics[EXTERNAL_WAIT_REGISTRY_KEY] = waits

        for nid, age in expired:
            node = graph.nodes.get(nid)
            if node is None:
                continue
            self._fail_node(
                run,
                node,
                f"external wait on node '{nid}' expired after {age:.3f}s without a matching signal; the run cannot wait forever",
            )
        if expired:
            _sync_waiting_nodes(run)
            if run.status is WorkflowRunStatus.WAITING_EVENT and not run.waiting_nodes:
                _set_run_status(run, WorkflowRunStatus.RUNNING, reason="all external waits expired")
                run.waiting_reason = None
            # Apply the same fail-closed policy ``execute_step`` applies after a
            # wave.  Without this a swept wait left a FAILED node inside a RUNNING
            # run: the run looked alive while carrying a node that can never
            # succeed, and no terminal status was ever reached.
            if run.failed_nodes and run.status in (WorkflowRunStatus.PENDING, WorkflowRunStatus.RUNNING):
                failed = sorted(set(run.failed_nodes))
                reason = f"fail-closed: {len(failed)} node(s) failed: {failed}; see the node_failed events for the real per-node reasons"
                _set_run_status(run, WorkflowRunStatus.FAILED, reason=reason)
                self.events.emit("workflow_failed", run.run_id, reason=reason)
        return run

    def suspend_run(self, run_id: str, reason: str = "suspended by operator") -> WorkflowRun:
        """Park a live run in ``SUSPENDED`` without inventing a terminal outcome.

        A suspended run is non-terminal and non-dispatchable, so a step after a
        suspend is a no-op returning the real status rather than quietly
        continuing the work the operator asked to hold.
        """
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Run '{run_id}' not found.")
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        _set_run_status(run, WorkflowRunStatus.SUSPENDED, reason=reason)
        run.waiting_reason = reason
        self.events.emit("workflow_suspended", run_id, reason=reason)
        self._publish_run_graph(run_id)
        return run

    def resume_run(self, run_id: str, reason: str = "resumed by operator") -> WorkflowRun:
        """Return a ``SUSPENDED`` run to ``RUNNING`` so it can be stepped again."""
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Run '{run_id}' not found.")
        if run.status is not WorkflowRunStatus.SUSPENDED:
            raise ValueError(f"run '{run_id}' is '{run.status.value}', not suspended; nothing to resume")
        _set_run_status(run, WorkflowRunStatus.RUNNING, reason=reason)
        run.waiting_reason = None
        self.events.emit("workflow_resumed", run_id, reason=reason)
        self._publish_run_graph(run_id)
        return run

    def apply_patch(self, run_id: str, patch: WorkflowPatch) -> tuple[WorkflowGraph, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")

        graph = self._run_graph_for(run)
        new_graph, validation = self.patch_engine.apply(run, graph, patch)
        if validation.allowed:
            self._run_graphs[run.run_id] = new_graph
            self.graphs[f"{run.workflow_id}:v{new_graph.version}"] = new_graph
            for nid, node in new_graph.nodes.items():
                if nid not in run.node_states:
                    run.node_states[nid] = node.status
            self._publish_run_graph(run.run_id)
        return new_graph, validation

    def cancel_run(self, run_id: str, reason: str = "operator requested cancellation") -> WorkflowRun:
        """Cancel a live workflow without inventing terminal node success."""
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        _set_run_status(run, WorkflowRunStatus.CANCELLED, reason=reason)
        run.active_nodes.clear()
        self.events.emit("workflow_cancelled", run.run_id, reason=reason)
        self._publish_run_graph(run_id)
        return run

    def resolve_approval(
        self,
        run_id: str,
        node_id: str,
        approved: bool,
        feedback: str = "",
        approval_request_id: str | None = None,
    ) -> WorkflowRun:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")

        graph = self._run_graph_for(run)
        if node_id not in graph.nodes:
            raise KeyError(f"Node '{node_id}' not found in graph.")

        node = graph.nodes[node_id]
        current = run.node_states.get(node_id, node.status)
        if run.status in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.BUDGET_EXHAUSTED,
        }:
            raise ValueError(f"run '{run_id}' is terminal ({run.status.value}); approval cannot be resolved")
        if current != NodeStatus.WAITING:
            if approved and current == NodeStatus.READY and run.approval_request_id is None:
                return run
            raise ValueError(f"node '{node_id}' is not awaiting approval (status={current.value})")
        if not node.approval_request_id or node.approval_request_id != run.approval_request_id:
            raise ValueError(f"approval request for node '{node_id}' is not active")
        if approval_request_id is not None and approval_request_id != node.approval_request_id:
            raise ValueError("approval_request_id does not match the active request")
        active_approval_id = node.approval_request_id
        if node.gate_timeout_seconds is not None and node.approval_requested_at:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(node.approval_requested_at)).total_seconds()
            except ValueError:
                age = 0.0
            if age > node.gate_timeout_seconds:
                denial_reason = f"Approval gate for node '{node_id}' timed out after {age:.3f}s."
                node.status = NodeStatus.FAILED
                run.node_states[node_id] = NodeStatus.FAILED
                if node_id not in run.failed_nodes:
                    run.failed_nodes.append(node_id)
                run.approval_request_id = None
                run.waiting_reason = denial_reason
                _set_run_status(run, WorkflowRunStatus.FAILED, reason=denial_reason)
                self.events.emit(
                    "approval_timed_out",
                    run_id,
                    node_id=node_id,
                    reason=denial_reason,
                    node_status=NodeStatus.FAILED.value,
                )
                _sync_waiting_nodes(run)
                return run

        if approved:
            node.status = NodeStatus.READY
            run.node_states[node_id] = NodeStatus.READY
            # Gap 9: the grant is a real run-status transition, journaled.
            _set_run_status(run, WorkflowRunStatus.RUNNING)
            run.approval_request_id = None
            run.waiting_reason = None
            # Gap 7: node_status lets a replay fold the granted node to READY.
            self.events.emit(
                "approval_granted",
                run_id,
                node_id=node_id,
                approval_id=active_approval_id,
                feedback=feedback,
                node_status=NodeStatus.READY.value,
            )
        else:
            node.status = NodeStatus.FAILED
            run.node_states[node_id] = NodeStatus.FAILED
            # Gap 8: a denied gate node IS a failed node of the run — recorded
            # (deduplicated) instead of living only in the run-level status.
            if node_id not in run.failed_nodes:
                run.failed_nodes.append(node_id)
            denial_reason = f"Human rejected node '{node_id}': {feedback}"
            # Gap 9: the denial is a real run-status transition, journaled.
            _set_run_status(run, WorkflowRunStatus.FAILED, reason=denial_reason)
            run.approval_request_id = None
            run.waiting_reason = denial_reason
            # Gap 7: node_status lets a replay fold the denied node to FAILED.
            self.events.emit(
                "approval_denied",
                run_id,
                node_id=node_id,
                approval_id=active_approval_id,
                feedback=feedback,
                node_status=NodeStatus.FAILED.value,
            )

        # Gap 9: waiting_nodes is derived from node statuses after resolution.
        _sync_waiting_nodes(run)
        self._publish_run_graph(run_id)
        return run
