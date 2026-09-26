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
import threading
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from alpha.workflow.events import get_event_dispatcher
from alpha.workflow.expressions import evaluate_condition, evaluate_condition_strict
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
    """Record that ``run.state[key]`` was written from real executor results."""
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
    """
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
    hand-maintained second source of truth.
    """
    run.waiting_nodes = [nid for nid, status in run.node_states.items() if status == NodeStatus.WAITING]


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
            raise WorkflowDefinitionError(
                f"{label} graph edge {edge.source!r}->{edge.target!r} names an unknown source node '{edge.source}'"
            )
        if edge.target not in graph.nodes:
            raise WorkflowDefinitionError(
                f"{label} graph edge {edge.source!r}->{edge.target!r} names an unknown target node '{edge.target}'"
            )

    for key, node in graph.nodes.items():
        for dependency in node.depends_on:
            if dependency not in graph.nodes:
                raise WorkflowDefinitionError(
                    f"{label} graph node '{key}' depends on unknown node '{dependency}'"
                )

    for edge in graph.edges:
        if edge.source == edge.target:
            # A self-edge is not "a loop" in this engine: the scheduler admits a
            # node only while its incoming source is still unexecuted, so a
            # self-edge makes the node permanently unschedulable rather than
            # re-entering it.  Bounded re-entry is expressed by
            # ``loop_policy`` (the node returns to READY via ``node_iteration``
            # and is re-admitted because it has no incoming edge).  Refusing the
            # self-edge names the real fix instead of livelocking.
            raise WorkflowDefinitionError(
                f"{label} graph node '{edge.source}' is its own successor; a self-edge makes the node "
                f"permanently unschedulable (express bounded re-entry with loop_policy)"
            )

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
        raise WorkflowDefinitionError(
            f"{label} graph has a dependency cycle through nodes {unresolved}; a cyclic graph cannot be "
            f"scheduled to completion"
        )


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

        if run.status in (
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.BUDGET_EXHAUSTED,
            WorkflowRunStatus.WAITING_APPROVAL,
            WorkflowRunStatus.WAITING_EVENT,
            WorkflowRunStatus.SUSPENDED,
            WorkflowRunStatus.ABORTED,
        ):
            # Gap 3: BUDGET_EXHAUSTED is terminal for scheduling purposes too —
            # a budget-spent run is never re-entered (its node already failed).
            return run

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
                    f"Deadlock: no nodes ready to execute; stagnation recovery exhausted after "
                    f"{recovery_attempts} remediation patch(es) targeting node '{target_id}' "
                    f"(limit {STAGNATION_RECOVERY_LIMIT}); no further progress is achievable."
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

        for nid in wave_nodes:
            if nid not in run.active_nodes:
                run.active_nodes.append(nid)
            try:
                self._execute_single_node(nid, graph, run, runner, compensation_runner)
            finally:
                run.active_nodes = [active for active in run.active_nodes if active != nid]
            # A gate or failure in one ready node is a run-level stop.  Do not
            # let later nodes in the same wave perform side effects after the
            # run has already paused or fail-closed.
            if run.status is not WorkflowRunStatus.RUNNING or run.failed_nodes:
                break

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
        return run

    def _fail_node(self, run: WorkflowRun, node: WorkflowNode, reason: str, **extra: Any) -> None:
        """Mark a node honestly failed: real reason in output and event log."""
        node.status = NodeStatus.FAILED
        run.node_states[node.id] = NodeStatus.FAILED
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
        """
        charge = max(0, int(tokens or 0))
        node.tokens_consumed += charge
        run.tokens_consumed += charge
        if run.budget_limit is not None and run.tokens_consumed > run.budget_limit:
            node.status = NodeStatus.FAILED
            run.node_states[node.id] = NodeStatus.FAILED
            if node.id not in run.failed_nodes:
                run.failed_nodes.append(node.id)
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
        if node.budget is not None and node.tokens_consumed > node.budget:
            node.status = NodeStatus.FAILED
            run.node_states[node.id] = NodeStatus.FAILED
            # Gap-8-consistent bookkeeping: a budget-killed node is a failed
            # node, so keep run.failed_nodes (fail-close/handoff/replay folds)
            # in sync with run.node_states. BUDGET_EXHAUSTED is terminal, so
            # this never triggers a fail-closed transition on its own.
            if node.id not in run.failed_nodes:
                run.failed_nodes.append(node.id)
            # Gap 9: budget exhaustion is a real run-status transition.
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
        """
        attempt_key = self._node_attempt_key(node, run)
        if attempt_key is None:
            return None
        return attempt_key if attempt_key in self._completed_idempotency_keys(run) else None

    def _invoke_runner(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Invoke a real runner with the node's bounded retry policy.

        Retries are opt-in through ``RetryPolicy``.  A failure is retried only
        when its measured text matches an allowed marker (or ``*``), never on a
        blanket assumption that every error is transient.  The final typed
        result is returned unchanged so the normal evidence/failure gates
        remain the single completion authority; a direct non-retryable runner
        exception is re-raised to the outer engine boundary so its real reason
        is not wrapped a second time.
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
                raw = runner(node, run)
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
            if result.get("status") == "completed" or attempt >= attempts:
                return result

            text = str(result.get("output", "")).lower()
            markers = [str(marker).lower() for marker in policy.retry_on_errors]
            retryable = "*" in markers or "all" in markers or any(marker and marker in text for marker in markers)
            if not retryable:
                # A direct runner exception is already the authoritative
                # failure.  Let the outer engine boundary journal its exact
                # ``str(exc)`` rather than wrapping it as a second generic
                # "runner reported failure" error.  Typed failed results (for
                # example ExecutorRegistry results carrying a traceback) still
                # flow through the normal wrapper below.
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

        if node.timeout_seconds is not None:
            self._fail_node(
                run,
                node,
                f"node timeout {node.timeout_seconds}s requested but the bound synchronous executor has no cancellation seam",
            )
            return

        # Mark Running
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

        try:
            # 3. Dynamic Node Type Handlers
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
        if run.status in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.BUDGET_EXHAUSTED,
            WorkflowRunStatus.ABORTED,
        }:
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
