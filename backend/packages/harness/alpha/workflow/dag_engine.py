"""DAG Task Workflow Engine (mass-ulw / omo-dag).

Implements dependency-ordered multi-agent execution:
1. Declarative task graphs with nodes, categories, and dependencies.
2. Topological wave execution (nodes execute in parallel waves as dependencies clear).
3. Disjoint write scopes to prevent parallel git and filesystem race conditions.
4. "TREAT AS FALSE UNTIL YOU PROVE IT" verification protocol: tasks cannot be
   marked complete without registered verification evidence (test outputs, compiler logs).
5. Scoped budgets: per-workflow and per-node token budgets. Spend is charged
   against the workflow/node scope (the budget-scope contextvar is bound around
   every node execution so middleware/cost accounting lands on the right scope);
   on exhaustion the node/run ends with an explicit ``BUDGET_EXHAUSTED`` status
   carrying consumed/limit numbers, dependent nodes do NOT run, and no
   fabricated "completed" evidence is ever emitted.
6. Human-in-the-loop gates: a node declared with ``requires_approval`` pauses
   the run in ``WAITING_APPROVAL`` through the existing
   ``alpha.projects.approval_queue`` mechanism, resumes on approval, and fails
   honestly on rejection (``REJECTED``) or timeout (``APPROVAL_TIMED_OUT``).
   The paused run's state lives on the workflow objects (``snapshot`` /
   ``restore``) plus the file-backed approval queue, so a paused run can resume
   after a restart without inventing a new database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from alpha.config.token_budget_config import budget_scope

# Explicit status vocabulary. Existing lowercase statuses are kept verbatim for
# backward compatibility; the new terminal/pause statuses are uppercase so a
# budget or gate outcome can never be mistaken for "completed".
STATUS_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"
STATUS_REJECTED = "REJECTED"
STATUS_APPROVAL_TIMED_OUT = "APPROVAL_TIMED_OUT"
STATUS_SKIPPED = "SKIPPED"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
# Run-level status for a failed execution (node-level keeps legacy "failed").
STATUS_RUN_FAILED = "FAILED"
STATUS_RUNNING = "running"
STATUS_PENDING = "pending"


class WriteScopeCollisionError(ValueError):
    """Raised when two nodes in the same parallel execution wave share overlapping write paths."""
    pass


class UnverifiedNodeCompletionError(RuntimeError):
    """Raised when an attempt is made to mark a node complete without concrete evidence."""
    pass


@dataclass
class DAGNode:
    id: str
    prompt: str
    category: str = "quick"
    depends_on: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)  # Files/directories this node may touch
    status: str = STATUS_PENDING  # "pending", "running", "completed", "failed" + explicit gate/budget statuses
    evidence: list[str] = field(default_factory=list)
    output: str | None = None
    # Scoped budget: max tokens this single node may consume (None = unbounded).
    budget: int | None = None
    tokens_consumed: int = 0
    # Human-in-the-loop gate: pause the run until a human approves this node.
    requires_approval: bool = False
    approval_request_id: str | None = None
    gate_timeout_seconds: float | None = None


@dataclass
class NodeRunResult:
    """What a node runner reports back for one executed node.

    ``status`` must be ``"completed"`` or ``"failed"``. A completed node MUST
    carry ``evidence``: without it the engine refuses to mark it completed
    (the OmO "treat as false until you prove it" doctrine) and fails the run
    honestly instead. ``tokens_used`` is charged to the node and workflow
    budgets and to the bound budget scopes.
    """

    status: str = STATUS_COMPLETED
    output: str | None = None
    evidence: str | None = None
    tokens_used: int = 0


@dataclass
class WorkflowRunResult:
    """Honest outcome of one ``DAGEngine.execute`` call.

    ``status`` is ``"completed"`` only when every node really completed with
    evidence; otherwise it is an explicit status (``BUDGET_EXHAUSTED``,
    ``WAITING_APPROVAL``, ``REJECTED``, ``APPROVAL_TIMED_OUT``, ``FAILED``).
    For budget stops, ``consumed_tokens``/``budget_limit``/``scope_id`` carry
    the exhausted scope's numbers; ``consumed_tokens`` always reflects the
    workflow's total spend otherwise.
    """

    workflow_key: str
    status: str
    detail: str = ""
    node_status: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    consumed_tokens: int = 0
    budget_limit: int | None = None
    scope_id: str | None = None
    waiting_node: str | None = None
    approval_request_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DAGWorkflow:
    key: str
    name: str
    nodes: dict[str, DAGNode] = field(default_factory=dict)
    # Scoped budget: max tokens the whole workflow may consume (None = unbounded).
    budget: int | None = None
    tokens_consumed: int = 0

    def add_node(
        self,
        node_id: str,
        prompt: str,
        category: str = "quick",
        depends_on: list[str] | None = None,
        write_scope: list[str] | None = None,
        budget: int | None = None,
        requires_approval: bool = False,
        gate_timeout_seconds: float | None = None,
    ) -> DAGNode:
        if node_id in self.nodes:
            raise ValueError(f"Node '{node_id}' already exists in workflow '{self.key}'")
        node = DAGNode(
            id=node_id,
            prompt=prompt,
            category=category,
            depends_on=depends_on or [],
            write_scope=write_scope or [],
            budget=budget,
            requires_approval=requires_approval,
            gate_timeout_seconds=gate_timeout_seconds,
        )
        self.nodes[node_id] = node
        return node

    def get_executable_waves(self) -> list[list[str]]:
        """Compute topological execution waves (nodes that can run concurrently).
        
        Raises ValueError if a cycle is detected.
        """
        # Calculate in-degrees
        in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}
        graph: dict[str, list[str]] = {nid: [] for nid in self.nodes}

        for nid, node in self.nodes.items():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"Node '{nid}' depends on unknown node '{dep}'")
                graph[dep].append(nid)
                in_degree[nid] += 1

        # Kahn's algorithm wave by wave
        waves: list[list[str]] = []
        current_wave = [nid for nid, deg in in_degree.items() if deg == 0]
        processed_count = 0

        while current_wave:
            waves.append(sorted(current_wave))
            processed_count += len(current_wave)
            next_wave = []
            for nid in current_wave:
                for dependent in graph[nid]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_wave.append(dependent)
            current_wave = next_wave

        if processed_count != len(self.nodes):
            raise ValueError("Dependency cycle detected in DAG workflow!")

        return waves

    def validate_wave_write_scopes(self) -> None:
        """Verify that nodes in each parallel wave have disjoint write scopes."""
        waves = self.get_executable_waves()
        for wave_idx, wave in enumerate(waves):
            seen_scopes: dict[str, str] = {}
            for nid in wave:
                for scope in self.nodes[nid].write_scope:
                    norm = scope.replace("\\", "/").rstrip("/").lower()
                    if norm in seen_scopes:
                        other_nid = seen_scopes[norm]
                        raise WriteScopeCollisionError(
                            f"Parallel wave {wave_idx} write collision: node '{nid}' and node '{other_nid}' "
                            f"both declare write scope '{scope}'."
                        )
                    seen_scopes[norm] = nid

    def record_evidence(self, node_id: str, evidence: str) -> None:
        """Register verifiable evidence for a node's execution."""
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found")
        self.nodes[node_id].evidence.append(evidence)

    def mark_completed(self, node_id: str, output: str | None = None) -> None:
        """Mark node completed, strictly requiring evidence per the OmO doctrine."""
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found")
        node = self.nodes[node_id]
        if not node.evidence:
            raise UnverifiedNodeCompletionError(
                f"Node '{node_id}' cannot be marked completed without verification evidence! "
                f"(TREAT AS FALSE UNTIL YOU PROVE IT)"
            )
        node.status = "completed"
        if output:
            node.output = output

    def is_all_completed(self) -> bool:
        return all(node.status == "completed" for node in self.nodes.values())

    def to_dict(self) -> dict:
        """JSON-able snapshot of the workflow and every node (pause/resume state)."""
        return {
            "key": self.key,
            "name": self.name,
            "budget": self.budget,
            "tokens_consumed": self.tokens_consumed,
            "nodes": {nid: asdict(node) for nid, node in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> DAGWorkflow:
        """Rebuild a workflow from :meth:`to_dict` output (resume after restart)."""
        wf = cls(
            key=data["key"],
            name=data.get("name", data["key"]),
            budget=data.get("budget"),
            tokens_consumed=int(data.get("tokens_consumed", 0)),
        )
        for nid, node_data in (data.get("nodes") or {}).items():
            wf.nodes[nid] = DAGNode(**node_data)
        return wf


class DAGEngine:
    """Registry and manager for active DAG workflows."""

    def __init__(self):
        self._workflows: dict[str, DAGWorkflow] = {}

    def create_workflow(self, key: str, name: str, budget: int | None = None) -> DAGWorkflow:
        if key in self._workflows:
            return self._workflows[key]
        wf = DAGWorkflow(key=key, name=name, budget=budget)
        self._workflows[key] = wf
        return wf

    def get_workflow(self, key: str) -> DAGWorkflow | None:
        return self._workflows.get(key)

    def list_workflows(self) -> list[str]:
        return list(self._workflows.keys())

    def snapshot(self, key: str) -> dict:
        """JSON-able state of a workflow (statuses, spend, gate state) for persistence."""
        wf = self._workflows.get(key)
        if wf is None:
            raise KeyError(f"Workflow '{key}' not found")
        return wf.to_dict()

    def restore(self, data: dict) -> DAGWorkflow:
        """Rebuild a workflow from :meth:`snapshot` output into this engine.

        Restoring over an existing key replaces it, so a paused run can be
        resumed by a fresh process/engine instance.
        """
        wf = DAGWorkflow.from_dict(data)
        self._workflows[wf.key] = wf
        return wf

    def execute(
        self,
        key: str,
        node_runner: Callable[[DAGNode], NodeRunResult],
        *,
        approval_queue=None,
        project_id: str | None = None,
        gate_timeout_seconds: float | None = None,
    ) -> WorkflowRunResult:
        """Run a workflow's ready nodes in dependency order until done or stopped.

        ``node_runner`` executes one node and reports :class:`NodeRunResult`.
        The budget-scope contextvar is bound to ``workflow:<key>`` and then
        ``node:<key>:<node_id>`` around every runner call so scoped token
        accounting (middleware / cost governor) charges the right scope.

        Stops honestly and returns - dependent nodes are then marked
        ``SKIPPED`` and never executed:
        - ``BUDGET_EXHAUSTED``: node or workflow budget exceeded (numbers in
          ``consumed_tokens``/``budget_limit``/``scope_id``), no evidence fabricated.
        - ``WAITING_APPROVAL``: a ``requires_approval`` node paused the run;
          call ``execute`` again after the approval queue records a decision.
        - ``REJECTED`` / ``APPROVAL_TIMED_OUT``: the human gate failed.
        - ``FAILED``: runner failure or a completion attempt without evidence.

        ``approval_queue`` must satisfy ``alpha.projects.approval_queue.ApprovalQueue``;
        when omitted (and a gated node is reached) the shared queue for
        ``project_id`` (or project ``"dag-workflows"``) is used.
        """
        wf = self._workflows.get(key)
        if wf is None:
            raise KeyError(f"Workflow '{key}' not found")

        skipped: list[str] = []
        stop_status: str | None = None
        stop_detail = ""
        stop_scope: str | None = None
        stop_limit: int | None = None
        stop_consumed: int | None = None

        for wave in wf.get_executable_waves():
            for nid in wave:
                node = wf.nodes[nid]

                if stop_status is not None:
                    # Run already stopped: dependents (and everything else
                    # left) must NOT run - mark them skipped, honestly.
                    if node.status not in ("completed", STATUS_SKIPPED):
                        node.status = STATUS_SKIPPED
                        skipped.append(nid)
                    continue

                if node.status == "completed":
                    continue

                unmet = [dep for dep in node.depends_on if wf.nodes[dep].status != "completed"]
                if unmet:
                    node.status = STATUS_SKIPPED
                    skipped.append(nid)
                    continue

                if node.requires_approval:
                    queue = approval_queue if approval_queue is not None else _resolve_approval_queue(project_id)
                    decision = _gate_decision(node, queue, gate_timeout_seconds)
                    if decision == STATUS_WAITING_APPROVAL:
                        node.status = STATUS_WAITING_APPROVAL
                        return self._result(
                            wf,
                            status=STATUS_WAITING_APPROVAL,
                            detail=f"Node '{nid}' is waiting for human approval.",
                            skipped=skipped,
                            waiting_node=nid,
                        )
                    if decision in (STATUS_REJECTED, STATUS_APPROVAL_TIMED_OUT):
                        node.status = decision
                        stop_status = decision
                        stop_detail = f"Node '{nid}' gate: {decision}."
                        continue
                    # decision == approved: fall through and run the node.

                workflow_scope = f"workflow:{wf.key}"
                node_scope = f"node:{wf.key}:{nid}"

                if wf.budget is not None and wf.tokens_consumed >= wf.budget:
                    if node.status != STATUS_BUDGET_EXHAUSTED:
                        node.status = STATUS_SKIPPED
                        skipped.append(nid)
                    stop_status = STATUS_BUDGET_EXHAUSTED
                    stop_detail = f"Workflow budget exhausted before node '{nid}'."
                    stop_scope = workflow_scope
                    stop_limit = wf.budget
                    stop_consumed = wf.tokens_consumed
                    continue
                if node.budget is not None and node.tokens_consumed >= node.budget:
                    stop_status = STATUS_BUDGET_EXHAUSTED
                    stop_detail = f"Node '{nid}' budget already exhausted."
                    stop_scope = node_scope
                    stop_limit = node.budget
                    stop_consumed = node.tokens_consumed
                    node.status = STATUS_BUDGET_EXHAUSTED
                    continue

                node.status = STATUS_RUNNING
                try:
                    with budget_scope(workflow_scope), budget_scope(node_scope):
                        outcome = node_runner(node)
                except Exception as exc:  # noqa: BLE001 - runner failure must fail the run honestly, not corrupt state
                    node.status = STATUS_FAILED
                    node.output = f"runner raised {type(exc).__name__}: {exc}"
                    stop_status = STATUS_RUN_FAILED
                    stop_detail = f"Node '{nid}' failed: {node.output}"
                    continue

                charge = max(0, int(getattr(outcome, "tokens_used", 0) or 0))
                node.tokens_consumed += charge
                wf.tokens_consumed += charge

                if outcome.status != STATUS_COMPLETED:
                    node.status = STATUS_FAILED
                    node.output = outcome.output or f"Node '{nid}' reported failure."
                    stop_status = STATUS_RUN_FAILED
                    stop_detail = f"Node '{nid}' failed."
                    continue

                if node.budget is not None and node.tokens_consumed > node.budget:
                    # Spend is real, completion is not proven: explicit budget
                    # status, no evidence recorded, never "completed".
                    node.status = STATUS_BUDGET_EXHAUSTED
                    node.output = f"BUDGET_EXHAUSTED: consumed {node.tokens_consumed} of {node.budget} tokens."
                    stop_status = STATUS_BUDGET_EXHAUSTED
                    stop_detail = f"Node '{nid}' exceeded its token budget."
                    stop_scope = node_scope
                    stop_limit = node.budget
                    stop_consumed = node.tokens_consumed
                    continue

                if wf.budget is not None and wf.tokens_consumed > wf.budget:
                    node.status = STATUS_BUDGET_EXHAUSTED
                    node.output = f"BUDGET_EXHAUSTED: workflow consumed {wf.tokens_consumed} of {wf.budget} tokens."
                    stop_status = STATUS_BUDGET_EXHAUSTED
                    stop_detail = f"Workflow budget exceeded at node '{nid}'."
                    stop_scope = workflow_scope
                    stop_limit = wf.budget
                    stop_consumed = wf.tokens_consumed
                    continue

                if not outcome.evidence:
                    node.status = STATUS_FAILED
                    node.output = "refused completion: no verification evidence (TREAT AS FALSE UNTIL YOU PROVE IT)"
                    stop_status = STATUS_RUN_FAILED
                    stop_detail = f"Node '{nid}' returned no evidence; completion refused."
                    continue

                wf.record_evidence(nid, outcome.evidence)
                wf.mark_completed(nid, output=outcome.output)

        if stop_status is not None:
            return self._result(
                wf,
                status=stop_status,
                detail=stop_detail,
                skipped=skipped,
                scope_id=stop_scope,
                budget_limit=stop_limit,
                consumed_tokens=stop_consumed,
            )

        if wf.is_all_completed():
            return self._result(wf, status=STATUS_COMPLETED, detail="All nodes completed with evidence.", skipped=skipped)

        return self._result(
            wf,
            status=STATUS_RUN_FAILED,
            detail="Workflow finished with nodes that never completed.",
            skipped=skipped,
        )

    @staticmethod
    def _result(
        wf: DAGWorkflow,
        status: str,
        detail: str = "",
        skipped: list[str] | None = None,
        scope_id: str | None = None,
        budget_limit: int | None = None,
        consumed_tokens: int | None = None,
        waiting_node: str | None = None,
    ) -> WorkflowRunResult:
        waiting_node_id = waiting_node
        approval_request_id = wf.nodes[waiting_node_id].approval_request_id if waiting_node_id else None
        return WorkflowRunResult(
            workflow_key=wf.key,
            status=status,
            detail=detail,
            node_status={nid: node.status for nid, node in wf.nodes.items()},
            skipped=list(skipped or []),
            consumed_tokens=wf.tokens_consumed if consumed_tokens is None else consumed_tokens,
            budget_limit=budget_limit if budget_limit is not None else wf.budget,
            scope_id=scope_id,
            waiting_node=waiting_node_id,
            approval_request_id=approval_request_id,
        )


def _resolve_approval_queue(project_id: str | None):
    """Shared file-backed approval queue for HITL-gated DAG nodes."""
    from alpha.projects.approval_queue import get_approval_queue

    return get_approval_queue(project_id or "dag-workflows")


def _gate_decision(node: DAGNode, queue, default_timeout: float | None) -> str:
    """Resolve a node's human gate against the approval queue.

    Returns ``STATUS_WAITING_APPROVAL`` (request pending, run must pause),
    ``STATUS_REJECTED``, ``STATUS_APPROVAL_TIMED_OUT``, or ``"approved"``
    (safe to execute the node). A fresh request is created on first contact.
    """
    request = None
    if node.approval_request_id:
        request = queue.get_request(node.approval_request_id)
    if request is None:
        request = queue.request_approval(
            bot_name=f"dag:{node.id}",
            action_type="workflow_node_approval",
            risk_level="medium",
            details={
                "node_id": node.id,
                "prompt": node.prompt,
                "category": node.category,
            },
        )
        node.approval_request_id = request.request_id
        return STATUS_WAITING_APPROVAL

    if request.status == "approved":
        return "approved"
    if request.status == "rejected":
        return STATUS_REJECTED
    if request.status == "timed_out":
        return STATUS_APPROVAL_TIMED_OUT

    timeout = node.gate_timeout_seconds if node.gate_timeout_seconds is not None else default_timeout
    if timeout is not None:
        try:
            created = datetime.fromisoformat(request.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            elapsed = (datetime.now(UTC) - created).total_seconds()
        except ValueError:
            elapsed = 0.0
        if elapsed >= timeout:
            return STATUS_APPROVAL_TIMED_OUT
    return STATUS_WAITING_APPROVAL
