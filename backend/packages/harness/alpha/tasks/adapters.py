"""Per-engine projections onto :class:`alpha.tasks.contracts.TaskContract`.

One adapter per orchestration engine, each doing exactly two things:

* translate an engine's own task model **into** a ``TaskContract``;
* translate a ``TaskContract``'s status **back** into that engine's vocabulary.

Three rules make these adapters rather than a migration
-------------------------------------------------------

1. **They hold no state and own no lifecycle.** An adapter is a pure function
   pair. It never schedules, never persists and never decides a status — the
   engine that produced the task still owns it, and ``RunManager`` remains the
   sole run-lifecycle owner.
2. **An unmapped status refuses.** Each engine declares a complete status map
   here. A value with no entry raises :class:`~alpha.tasks.contracts.UnmappableStatus`
   naming the engine and the value, rather than defaulting to ``pending`` —
   which would turn an unfamiliar state into a task that looks ready to run.
   The maps are therefore **exhaustive by construction**: the tests enumerate
   each engine's live enum members and assert every one is covered.
3. **The reverse map is total for the statuses an engine can hold.** A
   ``TaskContract`` may carry a status the target engine has no word for (the
   vocabularies are not equal), so the reverse map declares where each
   contract status lands in a given engine rather than leaving a caller to
   discover it at write time.

Why the vocabularies are not equal
---------------------------------
``alpha.graph`` says ``succeeded``/``completed``; ``alpha.workflow`` has no
``queued`` but has ``retrying``; ``alpha.swarm`` says ``completed`` and has
``budget_exhausted`` and ``stalled``. A *union* enum would be a seventh
vocabulary that belongs to nobody; a *narrow common core* is what makes a
projection lossless, and :attr:`TaskContract.native_status` preserves whatever
the engine actually said.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from alpha.tasks.contracts import TaskContract, TaskContractBudget, TaskContractStatus, TaskContractType, UnmappableStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from alpha.graph.models import TaskNode as GraphTaskNode
    from alpha.swarm.models import SwarmTaskNode
    from alpha.workflow.models import WorkflowNode

__all__ = [
    "ENGINE_DWE",
    "ENGINE_GRAPH",
    "ENGINE_SWARM",
    "KNOWN_ENGINES",
    "status_map_for",
    "native_status_for",
    "graph_task_to_contract",
    "workflow_node_to_contract",
    "swarm_task_to_contract",
    "unmapped_statuses_for",
]


ENGINE_GRAPH: Final[str] = "graph"
ENGINE_DWE: Final[str] = "workflow"
ENGINE_SWARM: Final[str] = "swarm"
KNOWN_ENGINES: Final[frozenset[str]] = frozenset({ENGINE_GRAPH, ENGINE_DWE, ENGINE_SWARM})


#: ``engine -> {native status -> canonical status}``.
#:
#: Every map must cover **every** member of its engine's live enum. The
#: adapter tests enumerate the enums and fail on a gap, because a missing entry
#: turns an ordinary state change into a runtime
#: :class:`UnmappableStatus` in the middle of a run.
_NATIVE_TO_CANONICAL: Final[Mapping[str, Mapping[str, TaskContractStatus]]] = {
    # alpha.graph.models.TaskStatus (10 members)
    ENGINE_GRAPH: {
        "pending": TaskContractStatus.PENDING,
        "ready": TaskContractStatus.READY,
        "queued": TaskContractStatus.QUEUED,
        "running": TaskContractStatus.RUNNING,
        "waiting_approval": TaskContractStatus.WAITING_APPROVAL,
        "blocked": TaskContractStatus.BLOCKED,
        "succeeded": TaskContractStatus.SUCCEEDED,
        "failed": TaskContractStatus.FAILED,
        "cancelled": TaskContractStatus.CANCELLED,
        "skipped": TaskContractStatus.SKIPPED,
    },
    # alpha.workflow.models.NodeStatus. The extra members are real states the
    # engine holds, not synonyms: WAITING covers the approval wait *and* the
    # event wait, RETRYING is a backoff between attempts, SUSPENDED is a parked
    # run, COMPENSATING and ABORTED are saga outcomes. They are mapped to the
    # nearest canonical status rather than dropped, and ``native_status`` keeps
    # the exact value so a projection is lossless.
    ENGINE_DWE: {
        "pending": TaskContractStatus.PENDING,
        "ready": TaskContractStatus.READY,
        "running": TaskContractStatus.RUNNING,
        "waiting": TaskContractStatus.WAITING_APPROVAL,
        "succeeded": TaskContractStatus.SUCCEEDED,
        "failed": TaskContractStatus.FAILED,
        "retrying": TaskContractStatus.READY,
        "skipped": TaskContractStatus.SKIPPED,
        "cancelled": TaskContractStatus.CANCELLED,
        "suspended": TaskContractStatus.BLOCKED,
        "compensating": TaskContractStatus.RUNNING,
        "aborted": TaskContractStatus.CANCELLED,
    },
    # alpha.swarm carries **two** disjoint vocabularies across its two
    # models, so this map covers both and must stay that way:
    #
    # * ``SwarmPlan.status`` is a bare string whose live value set (11
    #   values, ``partial_success`` included) is documented in a comment on
    #   the field at swarm/models.py and enumerated by
    #   TERMINAL_SWARM_STATUSES / NON_TERMINAL_SWARM_STATUSES;
    # * ``SwarmTaskNode.state`` is the typed ``TaskNodeState`` enum the
    #   scheduler actually transitions.
    #
    # ``verifying`` maps to RUNNING rather than READY because work is still in
    # flight; ``stalled`` and ``paused`` map to BLOCKED because a parked task
    # needs an explicit re-entry, not a silent redispatch; ``straggling`` is
    # RUNNING because calling it terminal would free a concurrency slot early.
    ENGINE_SWARM: {
        # documented `status` strings
        "planning": TaskContractStatus.PENDING,
        "running": TaskContractStatus.RUNNING,
        "paused": TaskContractStatus.BLOCKED,
        "aggregating": TaskContractStatus.RUNNING,
        "verifying": TaskContractStatus.RUNNING,
        "completed": TaskContractStatus.SUCCEEDED,
        # ``partial_success`` is in NON_TERMINAL_SWARM_STATUSES, so the task is
        # still live. Mapping it to FAILED would fabricate a terminal verdict the
        # engine never reached, and mapping it to SUCCEEDED would fabricate
        # success; RUNNING is the only honest landing, and ``native_status``
        # preserves the exact word so the round-trip stays lossless.
        "partial_success": TaskContractStatus.RUNNING,
        "failed": TaskContractStatus.FAILED,
        "cancelled": TaskContractStatus.CANCELLED,
        "budget_exhausted": TaskContractStatus.FAILED,
        "stalled": TaskContractStatus.BLOCKED,
        # `state` enum members
        "pending": TaskContractStatus.PENDING,
        "queued": TaskContractStatus.QUEUED,
        "straggling": TaskContractStatus.RUNNING,
    },
}


#: ``engine -> {canonical status -> the engine's word for it}``.
#:
#: Declared rather than inverted from the forward map so the choice is
#: explicit: ``SUCCEEDED`` becomes ``completed`` in a swarm, ``QUEUED`` has no
#: DWE word and falls back to ``pending``, and an approval wait in a swarm is
#: the pause it actually is.
_CANONICAL_TO_NATIVE: Final[Mapping[str, Mapping[TaskContractStatus, str]]] = {
    ENGINE_GRAPH: {status: status.value for status in TaskContractStatus},
    ENGINE_DWE: {
        TaskContractStatus.PENDING: "pending",
        TaskContractStatus.READY: "ready",
        TaskContractStatus.QUEUED: "pending",
        TaskContractStatus.RUNNING: "running",
        TaskContractStatus.WAITING_APPROVAL: "waiting",
        TaskContractStatus.BLOCKED: "suspended",
        TaskContractStatus.SUCCEEDED: "succeeded",
        TaskContractStatus.FAILED: "failed",
        TaskContractStatus.SKIPPED: "skipped",
        TaskContractStatus.CANCELLED: "cancelled",
    },
    ENGINE_SWARM: {
        TaskContractStatus.PENDING: "planning",
        TaskContractStatus.READY: "planning",
        TaskContractStatus.QUEUED: "running",
        TaskContractStatus.RUNNING: "running",
        TaskContractStatus.WAITING_APPROVAL: "paused",
        TaskContractStatus.BLOCKED: "paused",
        TaskContractStatus.SUCCEEDED: "completed",
        TaskContractStatus.FAILED: "failed",
        TaskContractStatus.SKIPPED: "skipped",
        TaskContractStatus.CANCELLED: "cancelled",
    },
}


def status_map_for(engine: str) -> Mapping[str, TaskContractStatus]:
    """The engine's native-status -> canonical-status map."""
    try:
        return _NATIVE_TO_CANONICAL[engine]
    except KeyError:
        raise UnmappableStatus(engine, "<not a known engine>") from None


def native_status_for(engine: str, status: TaskContractStatus) -> str:
    """The engine's own word for a canonical ``status``.

    Round-trips :func:`graph_task_to_contract` and friends: a projected task
    with ``native_status`` set returns that exact string, because the caller
    asked to preserve it. Without it, the reverse map decides — which is what
    makes a ``TaskContract`` written by one engine storable in another.
    """
    try:
        return _CANONICAL_TO_NATIVE[engine][status]
    except KeyError:
        raise UnmappableStatus(engine, status.value) from None


def _canonical_status(engine: str, native: object) -> TaskContractStatus:
    """Translate a native status, refusing anything unmapped."""
    if isinstance(native, TaskContractStatus):
        return native
    raw = native.value if hasattr(native, "value") else native
    table = status_map_for(engine)
    if isinstance(raw, str) and raw in table:
        return table[raw]
    raise UnmappableStatus(engine, native)


def unmapped_statuses_for(engine: str, natives: object) -> tuple[str, ...]:
    """Which of ``natives`` this adapter cannot translate.

    Exposed so the test suite can enumerate an engine's live enum and fail on a
    gap without needing to catch an exception per member.
    """
    table = status_map_for(engine)
    return tuple(sorted(str(value) for value in natives if getattr(value, "value", value) not in table))


def _objective_from_text(text: str, *, limit: int = 2000) -> str:
    """Trim an engine's free-text field to a bounded objective."""
    return text.strip()[:limit]


def _criteria_from_dicts(raw: object) -> list[str]:
    """Normalise an engine's acceptance criteria to plain strings.

    ``SwarmTaskNode.acceptance_criteria`` is ``list[dict]`` while the contract
    wants strings, so a mapping is rendered as ``"<key>: <value>"``. A
    non-string, non-mapping entry is kept as its own repr rather than dropped:
    a criterion nobody can read is still a criterion somebody declared.
    """
    if not isinstance(raw, list):
        return []
    rendered: list[str] = []
    for item in raw:
        if isinstance(item, str):
            rendered.append(item)
        elif isinstance(item, Mapping):
            rendered.extend(f"{key}: {value}" for key, value in item.items())
        else:
            rendered.append(str(item))
    return rendered


def graph_task_to_contract(
    task: GraphTaskNode,
    *,
    run_id: str,
    parent_task_id: str | None = None,
    acceptance_criteria: list[str] | None = None,
) -> TaskContract:
    """Project an ``alpha.graph`` ``TaskNode`` onto the canonical contract.

    ``TaskNode`` is the closest of the three models to the contract, so this
    mapping is close to one-to-one. Two things are read rather than invented:
    the criteria come from the caller's goal when the node itself declares
    none, and ``risk_level`` is carried as a string because the graph plane's
    ``RiskLevel`` is a five-member enum no other engine shares.
    """
    return TaskContract(
        task_id=str(task.task_id),
        run_id=run_id,
        parent_task_id=parent_task_id,
        title=task.title,
        objective=_objective_from_text(task.description),
        task_type=TaskContractType(task.task_type.value),
        native_type=task.task_type.value,
        status=_canonical_status(ENGINE_GRAPH, task.status),
        engine=ENGINE_GRAPH,
        native_status=task.status.value,
        depends_on=[str(dependency) for dependency in task.depends_on],
        acceptance_criteria=list(acceptance_criteria or []),
        expected_artifacts=[str(artifact) for artifact in task.output_artifact_ids],
        eligible_capabilities=[requirement.capability for requirement in task.required_capabilities if requirement.capability],
        budget=TaskContractBudget(max_attempts=task.max_attempts),
        priority=task.priority,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        risk_level=task.risk_level.value,
        approval_policy="required" if task.needs_approval else "not_required",
        idempotency_key=None,
        created_at=task.created_at,
        updated_at=task.finished_at,
    )


def workflow_node_to_contract(
    node: WorkflowNode,
    *,
    run_id: str,
    parent_task_id: str | None = None,
    title: str | None = None,
) -> TaskContract:
    """Project an ``alpha.workflow`` ``WorkflowNode`` onto the contract.

    ``WorkflowNode`` has no ``title``, so the node's prompt or its id supplies
    the human-readable title. Its ``budget`` is a **token count**, which is why
    it lands in ``max_tokens`` and never in ``max_cost_usd``: the engine has no
    price for a token and this adapter must not invent one. ``verification``
    criteria are read from the node's declared criteria when present.
    """
    label = title or node.prompt or node.id
    criteria: list[str] = []
    declared = node.config.get("verification_criteria") if isinstance(node.config, dict) else None
    if isinstance(declared, list):
        criteria = _criteria_from_dicts(declared)
    return TaskContract(
        task_id=node.id,
        run_id=run_id,
        parent_task_id=parent_task_id,
        title=_objective_from_text(str(label), limit=300),
        objective=_objective_from_text(node.prompt or ""),
        task_type=TaskContractType.TOOL,
        native_type=node.type.value,
        status=_canonical_status(ENGINE_DWE, node.status),
        engine=ENGINE_DWE,
        native_status=node.status.value,
        depends_on=list(node.depends_on),
        acceptance_criteria=criteria,
        expected_artifacts=[str(item) for item in node.evidence],
        scope_paths=list(node.write_scope),
        budget=TaskContractBudget(max_tokens=node.budget, max_wall_seconds=node.timeout_seconds),
        attempt_count=0,
        max_attempts=max(1, node.retry_policy.max_attempts if node.retry_policy else 2),
        approval_policy="required" if node.requires_approval else "not_required",
        idempotency_key=node.idempotency_key,
        created_at=None,
        updated_at=None,
    )


def swarm_task_to_contract(
    task: SwarmTaskNode,
    *,
    run_id: str,
    parent_task_id: str | None = None,
    title: str | None = None,
) -> TaskContract:
    """Project an ``alpha.swarm`` ``SwarmTaskNode`` onto the contract.

    ``SwarmTaskNode`` is the richest of the three (artifacts, capability tags,
    attempt ceilings, verification, deadlines) and its ``state``/``status``
    fields are two different vocabularies inside one object. The projection
    reads ``state`` — the typed enum the scheduler actually transitions —
    rather than the bare status string, because the string is the API
    projection of ``state`` and can lag it.
    """
    return TaskContract(
        task_id=task.task_id,
        run_id=run_id,
        parent_task_id=task.parent_task_id or parent_task_id,
        title=_objective_from_text(title or task.objective, limit=300),
        objective=_objective_from_text(task.objective),
        task_type=TaskContractType.TOOL,
        native_type=task.worker_type,
        status=_canonical_status(ENGINE_SWARM, task.state),
        engine=ENGINE_SWARM,
        native_status=task.state.value,
        depends_on=list(task.dependencies),
        acceptance_criteria=_criteria_from_dicts(task.acceptance_criteria),
        expected_artifacts=list(task.output_artifacts),
        scope_paths=[task.worktree_path] if task.worktree_path else [],
        eligible_capabilities=list(task.capability_tags),
        budget=TaskContractBudget(max_tool_calls=task.tool_calls or None),
        priority=task.priority,
        attempt_count=task.attempts,
        max_attempts=task.max_attempts,
        approval_policy="required" if task.acceptance_status == "required" else "not_required",
        created_at=None,
        updated_at=None,
    )
