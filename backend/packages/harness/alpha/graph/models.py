"""The canonical data model for Alpha's graph plane.

What this module is
-------------------
One vocabulary. ``Goal``, ``TaskNode``, ``GraphEdge``, ``TaskAttempt``,
``Artifact``, ``EvidenceClaim``, ``RunEvent``, ``ApprovalRequest`` and
``CapabilityRequirement`` are defined **here** and nowhere else, so every
subsystem that touches a graph means the same thing by the same field.

Why that matters when the repository already has task models
------------------------------------------------------------
``alpha.workflow.models.WorkflowNode`` exists, ``alpha.kanban`` has work items,
``alpha.mission`` has goals and work queues, and ``alpha.swarm`` has plans. All
four are real, load-bearing, and stay exactly where they are. This model is
deliberately **not** a fifth copy of them:

* it carries the fields
  ``docs/ALPHA_GRAPH_ENGINEERING_IMPLEMENTATION_PLAN.md`` §5 requires and those
  models do not have -- explicit ``acceptance_checks`` with per-check verdicts,
  ``required_capabilities``, ``risk_level``, a bounded ``budget``, and
  attempt-level ``idempotency_key``;
* it owns the **validation** those models leave to their callers;
* and it is a *vocabulary*, not a store. :mod:`alpha.graph.ledger` persists it;
  ``alpha.workflow`` projects onto it; nothing here writes a row.

The load-bearing refusals
--------------------------
Each property below exists because the softer version of it is a known failure
in this codebase.

**A task cannot succeed unverified.** :meth:`TaskNode.mark_succeeded` requires
that every declared acceptance check carries a verdict, and ``UNVERIFIED`` is
not a pass -- this is ``alpha.mission.acceptance``'s rule applied at the model
boundary so a caller cannot bypass it by writing the status field directly.

**Bounds are refused, not clamped.** :class:`GraphBudget` rejects
``max_concurrency=0`` and ``max_attempts=-1`` rather than substituting a
default. A limit that was silently adjusted is a limit nobody governs with.

**Cancellation is sticky and terminal.** :data:`TERMINAL_GOAL_STATUSES` is what
``cancel_requested`` is checked against, so the watchdog cannot restart a goal
the user stopped.

**Confidence is an estimate; verification is a fact.**
:class:`EvidenceClaim` keeps them in different fields, because a model assigning
itself ``confidence=0.97`` is not evidence -- only
:meth:`EvidenceClaim.mark_verified` with a real ``source_locator`` promotes a
claim out of ``CANDIDATE``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from alpha.graph.ids import (
    ApprovalId,
    ArtifactId,
    AttemptId,
    ClaimId,
    EventId,
    GoalId,
    GraphId,
    TaskId,
    new_approval_id,
    new_artifact_id,
    new_attempt_id,
    new_claim_id,
    new_event_id,
    new_goal_id,
    new_task_id,
)

__all__ = [
    "TERMINAL_GOAL_STATUSES",
    "TERMINAL_TASK_STATUSES",
    "AcceptanceCheck",
    "AcceptanceReport",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalState",
    "Artifact",
    "ArtifactSensitivity",
    "AttemptOutcome",
    "CapabilityRequirement",
    "ClaimStatus",
    "CriterionVerdict",
    "EdgeType",
    "EventSeverity",
    "EventType",
    "Goal",
    "GoalMode",
    "GoalStatus",
    "GraphBudget",
    "GraphEdge",
    "GraphSnapshot",
    "IllegalTransition",
    "ModelValidationError",
    "RiskLevel",
    "RunEvent",
    "TaskAttempt",
    "TaskNode",
    "TaskStatus",
    "TaskType",
    "now_iso",
]


class ModelValidationError(ValueError):
    """A model was built or mutated in a shape the graph plane refuses.

    A distinct type rather than a bare ``ValueError`` because
    :mod:`alpha.graph.failures` maps it onto a registry code, and because a
    caller catching ``ValueError`` broadly would otherwise swallow the refusal
    that was the whole point of the call.
    """


class IllegalTransition(ModelValidationError):
    """A status change the closed transition table does not permit."""


def now_iso() -> str:
    """UTC ISO-8601 with an explicit offset.

    ``datetime.now(UTC).isoformat()`` rather than a hand-written ``Z`` so a
    consumer using a naive-tolerant parser still reads an offset rather than
    guessing one.
    """
    return datetime.now(UTC).isoformat()


def _non_empty(value: str, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ModelValidationError(f"{field_name} must not be empty")
    return text


def _digest(payload: Any) -> str:
    """A stable content digest of ``payload``.

    Used to bind an approval to the exact operation it was granted for. The
    plan's §13.2 rule -- "if arguments change, request approval again" -- is
    only enforceable if the grant names the thing it was granted for, and that
    requires a digest that does not depend on dict ordering.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Goal
# ---------------------------------------------------------------------------


class GoalMode(StrEnum):
    """What the user asked for, which decides what "done" means.

    The split is the plan's §4.3 rule and it is load-bearing: an ``answer``
    request is complete when a grounded reply exists, while an ``execute``
    request is complete only when real operation records **and** verification
    evidence exist. Collapsing them is how a system answers "done" to a
    request that required a tool call.
    """

    ANSWER = "answer"
    PLAN = "plan"
    EXECUTE = "execute"


class GoalStatus(StrEnum):
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUSPENDED = "suspended"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_GOAL_STATUSES: Final[frozenset[GoalStatus]] = frozenset({GoalStatus.SUCCEEDED, GoalStatus.PARTIAL, GoalStatus.FAILED, GoalStatus.CANCELLED})


#: The closed goal transition table. A status change absent from this mapping is
#: refused by :meth:`Goal.transition_to` -- an unknown transition is a bug, and
#: defaulting to "allow" would make every future addition a silent regression.
_GOAL_TRANSITIONS: Final[dict[GoalStatus, frozenset[GoalStatus]]] = {
    GoalStatus.PLANNING: frozenset({GoalStatus.RUNNING, GoalStatus.BLOCKED, GoalStatus.FAILED, GoalStatus.CANCELLED}),
    GoalStatus.RUNNING: frozenset(
        {
            GoalStatus.WAITING_APPROVAL,
            GoalStatus.SUSPENDED,
            GoalStatus.SUCCEEDED,
            GoalStatus.PARTIAL,
            GoalStatus.BLOCKED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }
    ),
    GoalStatus.WAITING_APPROVAL: frozenset({GoalStatus.RUNNING, GoalStatus.BLOCKED, GoalStatus.FAILED, GoalStatus.CANCELLED}),
    GoalStatus.SUSPENDED: frozenset({GoalStatus.RUNNING, GoalStatus.BLOCKED, GoalStatus.FAILED, GoalStatus.CANCELLED}),
    GoalStatus.BLOCKED: frozenset({GoalStatus.RUNNING, GoalStatus.PLANNING, GoalStatus.FAILED, GoalStatus.CANCELLED}),
    GoalStatus.SUCCEEDED: frozenset(),
    GoalStatus.PARTIAL: frozenset(),
    GoalStatus.FAILED: frozenset(),
    GoalStatus.CANCELLED: frozenset(),
}


class RiskLevel(StrEnum):
    """The plan's §13.1 tiers, kept as data rather than prose.

    ``TaskNode.requires_approval`` is **derived** from this
    (:meth:`TaskNode.needs_approval`) rather than set independently, because the
    failure that prevents is a task declared ``HIGH`` that nothing ever gated.
    """

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    PROHIBITED = "prohibited"


class GraphBudget:
    """Ceilings for one goal. Every field is a ceiling, never a target.

    Bounds are **refused, not clamped**: ``max_concurrency=0`` raises rather
    than becoming 1. That is the rule ``ContextWindowPolicy`` and the
    ``deepagent`` channel ceiling already apply, and the reason is that a
    silently-adjusted limit is a limit the operator believes is in force and is
    not. ``None`` means *unbounded on this axis*, which callers must treat as a
    refusal to schedule rather than as permission to spend.
    """

    __slots__ = (
        "max_model_calls",
        "max_tool_calls",
        "max_subagents",
        "max_planning_iterations",
        "max_verify_cycles",
        "max_repair_attempts",
        "max_concurrent_tasks",
        "max_wall_seconds",
        "max_task_attempts",
        "max_identical_failure_signature",
    )

    def __init__(
        self,
        *,
        max_model_calls: int | None = None,
        max_tool_calls: int | None = None,
        max_subagents: int | None = None,
        max_planning_iterations: int | None = 3,
        max_verify_cycles: int | None = 2,
        max_repair_attempts: int | None = 2,
        max_concurrent_tasks: int | None = 2,
        max_wall_seconds: float | None = None,
        max_task_attempts: int | None = 2,
        max_identical_failure_signature: int | None = 2,
    ) -> None:
        self.max_model_calls = _positive_or_none(max_model_calls, "max_model_calls")
        self.max_tool_calls = _positive_or_none(max_tool_calls, "max_tool_calls")
        self.max_subagents = _positive_or_none(max_subagents, "max_subagents")
        self.max_planning_iterations = _positive_or_none(max_planning_iterations, "max_planning_iterations")
        self.max_verify_cycles = _positive_or_none(max_verify_cycles, "max_verify_cycles")
        self.max_repair_attempts = _positive_or_none(max_repair_attempts, "max_repair_attempts")
        self.max_concurrent_tasks = _positive_or_none(max_concurrent_tasks, "max_concurrent_tasks")
        self.max_wall_seconds = _positive_or_none(max_wall_seconds, "max_wall_seconds")
        self.max_task_attempts = _positive_or_none(max_task_attempts, "max_task_attempts")
        self.max_identical_failure_signature = _positive_or_none(max_identical_failure_signature, "max_identical_failure_signature")

    def to_dict(self) -> dict[str, int | float | None]:
        return {name: getattr(self, name) for name in self.__slots__}

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> GraphBudget:
        """Build from untrusted input, ignoring unknown keys rather than raising.

        Unknown keys are ignored on purpose: this data arrives from a request
        body, and refusing an unrecognised field would let a caller probe which
        budget names exist. What is *not* ignored is a present-but-invalid
        value -- that raises, because a silently dropped ``max_model_calls: 0``
        is an unbounded budget nobody bounded.
        """
        if not data:
            return cls()
        known = {name: data[name] for name in cls.__slots__ if name in data}
        return cls(**known)  # type: ignore[arg-type]

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"GraphBudget({self.to_dict()})"


def _positive_or_none(value: Any, name: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelValidationError(f"{name} must be a number or None, got {value!r}")
    if value <= 0:
        raise ModelValidationError(f"{name} must be strictly positive or None (unbounded); {value!r} is refused rather than clamped")
    return value


@dataclass(slots=True)
class Goal:
    """What the user asked for, in the system's own words.

    ``acceptance_criteria`` is on the **goal** as well as on each task because
    goal-level success is a separate measurement from task-level success: every
    task can pass while the goal it was serving is unmet, and reporting that as
    success is the false-success rate the plan's §16.5 calls the metric that
    must be zero in automated tests.
    """

    goal_id: GoalId
    run_id: str
    user_request: str
    normalized_objective: str
    mode: GoalMode
    status: GoalStatus = GoalStatus.PLANNING
    acceptance_criteria: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    budget: GraphBudget = field(default_factory=GraphBudget)
    policy_profile: str = "default"
    plan_version: int = 1
    task_ids: list[TaskId] = field(default_factory=list)
    artifact_ids: list[ArtifactId] = field(default_factory=list)
    claim_ids: list[ClaimId] = field(default_factory=list)
    failure_summary: str | None = None
    final_result: str | None = None
    cancel_requested: bool = False
    deadline_at: str | None = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        self.user_request = _non_empty(self.user_request, "user_request")
        self.normalized_objective = _non_empty(self.normalized_objective, "normalized_objective")
        if not self.run_id:
            raise ModelValidationError("run_id must not be empty: a goal with no owning run cannot be attributed")

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        user_request: str,
        normalized_objective: str,
        mode: GoalMode,
        **kwargs: Any,
    ) -> Goal:
        return cls(
            goal_id=new_goal_id(),
            run_id=run_id,
            user_request=user_request,
            normalized_objective=normalized_objective,
            mode=mode,
            **kwargs,
        )

    # -- transitions ------------------------------------------------------
    def transition_to(self, status: GoalStatus, *, reason: str = "") -> Goal:
        """Move to ``status`` or raise.

        Three refusals live here, and the **order is the contract**:

        1. Cancellation is checked *first*, before the terminal table. Once
           ``cancel_requested`` is set, any move that would put the goal back
           into a live state is refused, and the message says so -- a goal that
           is already ``cancelled`` is also a goal nobody may resurrect, so
           checking the table first would report "cannot move cancelled ->
           running" and hide the reason the caller actually needs.
        2. The closed table: an unlisted move is a bug, and a default-allow
           table makes every future addition a silent regression.
        3. The self-transition short-circuit is last: an idempotent status
           write is a normal outcome of two writers racing, and refusing it
           would turn a benign race into a crash.
        """
        if self.cancel_requested and status in (GoalStatus.RUNNING, GoalStatus.PLANNING):
            raise IllegalTransition(
                f"goal {self.goal_id} has a pending cancellation; refusing to resume it into {status.value}. A watchdog must not resurrect a goal the user stopped -- clear cancel_requested explicitly if this is a deliberate restart."
            )
        if status == self.status:
            return self
        allowed = _GOAL_TRANSITIONS[self.status]
        if status not in allowed:
            raise IllegalTransition(f"goal {self.goal_id} cannot move {self.status.value} -> {status.value}; allowed: {sorted(s.value for s in allowed) or 'none (terminal)'}")
        self.status = status
        self.updated_at = now_iso()
        if reason and status in TERMINAL_GOAL_STATUSES:
            self.failure_summary = self.failure_summary or reason
        return self

    def request_cancel(self, reason: str = "") -> Goal:
        """Set the sticky cancel flag and move to ``cancelled``.

        Idempotent by design: a second cancel is not an error, because a user
        pressing stop twice is not a protocol violation.
        """
        self.cancel_requested = True
        if self.status in TERMINAL_GOAL_STATUSES:
            return self
        return self.transition_to(GoalStatus.CANCELLED, reason=reason)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_GOAL_STATUSES

    @property
    def requires_evidence(self) -> bool:
        """Whether "succeeded" is a claim that needs proof.

        An ``answer`` goal is complete when a grounded reply exists, so it does
        not require acceptance evidence. ``plan`` and ``execute`` do: a plan
        nobody checked is an opinion, and an execute goal with no operation
        record is a claim that work happened.
        """
        return self.mode is not GoalMode.ANSWER

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "run_id": self.run_id,
            "user_request": self.user_request,
            "normalized_objective": self.normalized_objective,
            "mode": self.mode.value,
            "status": self.status.value,
            "acceptance_criteria": list(self.acceptance_criteria),
            "risk_level": self.risk_level.value,
            "budget": self.budget.to_dict(),
            "policy_profile": self.policy_profile,
            "plan_version": self.plan_version,
            "task_ids": list(self.task_ids),
            "artifact_ids": list(self.artifact_ids),
            "claim_ids": list(self.claim_ids),
            "failure_summary": self.failure_summary,
            "final_result": self.final_result,
            "cancel_requested": self.cancel_requested,
            "deadline_at": self.deadline_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "requires_evidence": self.requires_evidence,
        }


# ---------------------------------------------------------------------------
# Task graph
# ---------------------------------------------------------------------------


class TaskType(StrEnum):
    TEST = "test"
    RESEARCH = "research"
    CODE = "code"
    REVIEW = "review"
    BROWSER = "browser"
    TOOL = "tool"
    SYNTHESIS = "synthesis"
    APPROVAL = "approval"


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


TERMINAL_TASK_STATUSES: Final[frozenset[TaskStatus]] = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED})

#: Statuses that count as "this prerequisite is satisfied" for readiness.
#: ``SKIPPED`` is deliberately **excluded**: a skipped task produced nothing, so
#: treating it as satisfied would let a downstream task run on a hole. A plan
#: that genuinely wants to proceed past it must mark the prerequisite
#: ``SUCCEEDED`` or record an explicit waiver (plan §7.2 rule 4).
SATISFIED_TASK_STATUSES: Final[frozenset[TaskStatus]] = frozenset({TaskStatus.SUCCEEDED})

_TASK_TRANSITIONS: Final[dict[TaskStatus, frozenset[TaskStatus]]] = {
    # PENDING -> RUNNING is permitted directly: a task dispatched without an
    # intervening READY/QUEUED hand-off still ran, and refusing the transition
    # would force the caller to fake a READY step that never happened.
    # PENDING -> FAILED likewise covers "dispatch refused", where the task never
    # ran but the caller has reason to record.
    # PENDING -> SUCCEEDED covers a task verified complete without an attempt --
    # "the file already exists" satisfied by a probe, where attempt_count stays
    # 0 and the ledger honestly shows no attempt was made.
    # PENDING -> WAITING_APPROVAL exists because approval is a **dispatch**
    # decision, not a readiness one: a HIGH-risk task is parked before it is
    # ever dispatched, so there is no attempt to pause.
    TaskStatus.PENDING: frozenset({TaskStatus.READY, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.WAITING_APPROVAL, TaskStatus.BLOCKED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}),
    TaskStatus.READY: frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.WAITING_APPROVAL, TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED}),
    TaskStatus.WAITING_APPROVAL: frozenset({TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.SKIPPED, TaskStatus.FAILED}),
    TaskStatus.BLOCKED: frozenset({TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.CANCELLED, TaskStatus.SKIPPED, TaskStatus.FAILED}),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.READY}),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.SKIPPED: frozenset(),
}


class EdgeType(StrEnum):
    """Typed dependency edges.

    Kept separate from knowledge-graph relationship types on purpose (plan
    §5.3): a task dependency answers "may this run yet?", while a knowledge
    relationship answers "why is this connected?". Merging them would let a
    ``DERIVED_FROM`` citation satisfy a scheduler precondition.
    """

    DEPENDS_ON = "depends_on"
    ASSIGNED_TO = "assigned_to"
    PRODUCES = "produces"
    USES_TOOL = "uses_tool"
    REQUIRES_CAPABILITY = "requires_capability"
    BLOCKED_BY = "blocked_by"
    VERIFIED_BY = "verified_by"
    REPAIRS = "repairs"
    PRODUCED = "produced"


@dataclass(slots=True, frozen=True)
class GraphEdge:
    edge_id: str
    graph_id: GraphId
    source_id: str
    target_id: str
    edge_type: EdgeType = EdgeType.DEPENDS_ON
    reason: str = ""
    source_artifact_id: ArtifactId | None = None
    created_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        source = _non_empty(self.source_id, "edge.source_id")
        target = _non_empty(self.target_id, "edge.target_id")
        if source == target:
            # Refused, not de-duplicated: a self-dependency is either a planner
            # bug or a malformed model output, and both deserve to be loud.
            raise ModelValidationError(f"edge {self.edge_id} is a self-dependency on {source!r}")
        object.__setattr__(self, "source_id", source)
        object.__setattr__(self, "target_id", target)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "graph_id": self.graph_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
            "reason": self.reason,
            "source_artifact_id": self.source_artifact_id,
            "created_at": self.created_at,
        }


class CriterionVerdict(StrEnum):
    """One acceptance check's outcome.

    ``UNVERIFIED`` is a first-class value, not an error and not a failure: it is
    what a check looks like when nobody has measured it. Collapsing it into
    ``passed``/``failed`` is what makes "all tests pass" reachable without any
    test having run.
    """

    UNVERIFIED = "unverified"
    MET = "met"
    NOT_MET = "not_met"


@dataclass(slots=True)
class AcceptanceCheck:
    """One executable or inspectable condition on a task.

    A check with no ``verdict`` is *not* a soft pass; see
    :meth:`TaskNode.mark_succeeded`, which refuses to succeed a task whose
    declared checks were never evaluated.
    """

    check: str
    verdict: CriterionVerdict = CriterionVerdict.UNVERIFIED
    evidence: str = ""
    evaluated_by: str = ""
    evaluated_at: str | None = None

    @property
    def evaluated(self) -> bool:
        return self.verdict is not CriterionVerdict.UNVERIFIED

    @property
    def holds(self) -> bool:
        return self.verdict is CriterionVerdict.MET

    def evaluate(self, verdict: bool, *, evidence: str, evaluated_by: str) -> AcceptanceCheck:
        """Record a measured verdict.

        ``verdict`` is a real ``bool``. A truthy string is refused rather than
        coerced, because ``"false"`` is truthy in Python and a caller passing
        the string would otherwise record a passing check.
        """
        if not isinstance(verdict, bool):
            raise ModelValidationError(f"acceptance verdict for {self.check!r} must be a real bool, got {verdict!r}")
        evidence_text = _non_empty(evidence, "acceptance evidence")
        self.verdict = CriterionVerdict.MET if verdict else CriterionVerdict.NOT_MET
        self.evidence = evidence_text
        self.evaluated_by = _non_empty(evaluated_by, "evaluated_by")
        self.evaluated_at = now_iso()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "verdict": self.verdict.value,
            "evidence": self.evidence,
            "evaluated_by": self.evaluated_by,
            "evaluated_at": self.evaluated_at,
            "evaluated": self.evaluated,
            "holds": self.holds,
        }


@dataclass(slots=True)
class AcceptanceReport:
    """The two independent facts a pass depends on.

    ``all_evaluated`` asks "was anything actually checked?"; ``all_hold`` asks
    "did the checks pass?". Only both together is a pass. An **empty** report is
    not all-evaluated -- a task with no declared checks has proved nothing, and
    treating vacuous truth as success is the bug class this whole module
    exists to close.
    """

    criteria: list[AcceptanceCheck] = field(default_factory=list)
    evaluator: str = ""
    generated_at: str = field(default_factory=now_iso)

    @classmethod
    def from_checks(cls, checks: Iterable[str]) -> AcceptanceReport:
        return cls(criteria=[AcceptanceCheck(check=_non_empty(text, "check")) for text in checks])

    @property
    def declared(self) -> bool:
        return bool(self.criteria)

    @property
    def all_evaluated(self) -> bool:
        return self.declared and all(check.evaluated for check in self.criteria)

    @property
    def all_hold(self) -> bool:
        return self.declared and all(check.holds for check in self.criteria)

    @property
    def passed(self) -> bool:
        return self.all_evaluated and self.all_hold

    @property
    def unevaluated(self) -> list[str]:
        return [check.check for check in self.criteria if not check.evaluated]

    @property
    def failed(self) -> list[str]:
        return [check.check for check in self.criteria if check.verdict is CriterionVerdict.NOT_MET]

    def refusal_reason(self) -> str | None:
        """Why this report is not a pass, or ``None`` when it is.

        Derived from the refusal rather than recomputed by a second predicate,
        so "passed" and "refusal_reason() is None" cannot drift apart.
        """
        if not self.declared:
            return "no acceptance checks were declared, so nothing was verified"
        missing = self.unevaluated
        if missing:
            return f"{len(missing)} acceptance check(s) were never evaluated: {missing[:5]}"
        failed = self.failed
        if failed:
            return f"{len(failed)} acceptance check(s) were evaluated and did not hold: {failed[:5]}"
        return None

    def assert_passed(self) -> AcceptanceReport:
        reason = self.refusal_reason()
        if reason is not None:
            raise ModelValidationError(f"acceptance did not pass: {reason}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator": self.evaluator,
            "generated_at": self.generated_at,
            "criteria": [check.to_dict() for check in self.criteria],
            "declared": self.declared,
            "all_evaluated": self.all_evaluated,
            "all_hold": self.all_hold,
            "passed": self.passed,
            "unevaluated": self.unevaluated,
            "failed": self.failed,
            "refusal_reason": self.refusal_reason(),
        }


@dataclass(slots=True)
class CapabilityRequirement:
    """A capability a task needs, resolved before dispatch.

    ``satisfied_by`` is filled by the capability resolver. A requirement with an
    empty ``satisfied_by`` and ``resolved=False`` is the difference between "the
    task needs shell access" and "the task has shell access", and dispatching on
    the former is how work silently degrades into prose.
    """

    capability: str
    required: bool = True
    satisfied_by: str = ""
    resolved: bool = False
    reason: str = ""

    def mark_satisfied(self, provider: str) -> CapabilityRequirement:
        self.satisfied_by = _non_empty(provider, "capability provider")
        self.resolved = True
        return self

    def mark_unavailable(self, reason: str) -> CapabilityRequirement:
        self.resolved = True
        self.satisfied_by = ""
        self.reason = _non_empty(reason, "unavailable reason")
        return self

    @property
    def available(self) -> bool:
        return self.resolved and bool(self.satisfied_by)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "required": self.required,
            "satisfied_by": self.satisfied_by,
            "resolved": self.resolved,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(slots=True)
class TaskNode:
    """One bounded unit of work with one authoritative status.

    ``status`` is only ever written through :meth:`transition_to` (or the
    convenience wrappers that go through it), so the transition table cannot be
    bypassed by assigning the field. The ledger re-checks it on write, because
    two processes both believing they own this row is the case the table is
    there for.
    """

    task_id: TaskId
    goal_id: GoalId
    title: str
    description: str = ""
    task_type: TaskType = TaskType.TOOL
    status: TaskStatus = TaskStatus.PENDING
    depends_on: list[TaskId] = field(default_factory=list)
    assigned_worker: str = ""
    required_capabilities: list[CapabilityRequirement] = field(default_factory=list)
    acceptance: AcceptanceReport = field(default_factory=AcceptanceReport)
    output_artifact_ids: list[ArtifactId] = field(default_factory=list)
    evidence_ids: list[ClaimId] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    attempt_count: int = 0
    max_attempts: int = 2
    priority: int = 50
    error_code: str | None = None
    failure_summary: str | None = None
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None

    def __post_init__(self) -> None:
        self.title = _non_empty(self.title, "task.title")
        if self.max_attempts < 1:
            raise ModelValidationError(f"task {self.task_id}: max_attempts must be >= 1, got {self.max_attempts}")
        if self.depends_on:
            deduped: list[TaskId] = []
            seen: set[str] = set()
            for dependency in self.depends_on:
                if dependency in seen:
                    continue
                if dependency == self.task_id:
                    raise ModelValidationError(f"task {self.task_id} declares a self-dependency")
                seen.add(dependency)
                deduped.append(dependency)
            self.depends_on = deduped

    @classmethod
    def create(cls, *, goal_id: GoalId, title: str, **kwargs: Any) -> TaskNode:
        """Create a task, honouring a caller-supplied ``task_id``.

        The caller's id is used when given so a plan can be built with stable,
        human-authored identifiers (``task_reproduce``) and re-validated
        against the same ids after a round-trip through persistence. When
        absent, a fresh one is minted.
        """
        task_id = kwargs.pop("task_id", None)
        if task_id is not None:
            kwargs["task_id"] = TaskId(task_id)
        else:
            kwargs["task_id"] = new_task_id()
        return cls(goal_id=goal_id, title=title, **kwargs)

    # -- derived ----------------------------------------------------------
    @property
    def needs_approval(self) -> bool:
        """Whether this task must park for approval before it dispatches.

        Derived, never declared. ``PROHIBITED`` is the sharp end: the plan's
        §13.1 tier whose treatment is *deny and record a reason*, so it is
        surfaced as a refusal rather than as a task that waits for a human who
        could approve it.
        """
        if self.risk_level is RiskLevel.PROHIBITED:
            return False
        return self.risk_level is RiskLevel.HIGH

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    @property
    def can_retry(self) -> bool:
        """Whether another attempt is allowed.

        Both the count and the table gate this: an attempt ceiling that is
        ignored when the table happens to permit ``FAILED -> READY`` would make
        the ceiling decorative.
        """
        return self.attempt_count < self.max_attempts and TaskStatus.READY in _TASK_TRANSITIONS[self.status]

    # -- transitions ------------------------------------------------------
    def transition_to(self, status: TaskStatus, *, reason: str = "", error_code: str | None = None) -> TaskNode:
        """Move to ``status`` or raise :class:`IllegalTransition`.

        The self-transition short-circuit is deliberate: an idempotent status
        write is a normal outcome of two writers racing, and refusing it would
        turn a benign race into a crash.
        """
        if status == self.status:
            return self
        allowed = _TASK_TRANSITIONS[self.status]
        if status not in allowed:
            raise IllegalTransition(f"task {self.task_id} cannot move {self.status.value} -> {status.value}; allowed: {sorted(s.value for s in allowed) or 'none (terminal)'}")
        self.status = status
        if error_code is not None:
            self.error_code = error_code
        if status is TaskStatus.RUNNING and self.started_at is None:
            self.started_at = now_iso()
        if status in TERMINAL_TASK_STATUSES:
            self.finished_at = now_iso()
        if reason and status in (TaskStatus.FAILED, TaskStatus.BLOCKED):
            self.failure_summary = reason
        return self

    def begin_attempt(self, attempt_id: AttemptId | None = None) -> TaskAttempt:
        """Record an attempt and move to ``RUNNING``.

        Refuses past ``max_attempts``. A retry loop bounded only by "the
        previous one failed" is the unbounded self-reflection loop the plan's
        §20 anti-pattern 5 names.
        """
        if self.attempt_count >= self.max_attempts:
            raise IllegalTransition(f"task {self.task_id} has used all {self.max_attempts} attempt(s); refusing another. The attempt ceiling is a hard bound -- report the failure with evidence rather than retrying identical work.")
        self.transition_to(TaskStatus.RUNNING)
        self.attempt_count += 1
        return TaskAttempt.create(task_id=self.task_id, goal_id=self.goal_id, attempt_number=self.attempt_count, attempt_id=attempt_id)

    def mark_succeeded(self, *, artifact_ids: Sequence[ArtifactId] | None = None) -> TaskNode:
        """Succeed the task, refusing when its acceptance is unmet.

        Three refusals, each for a distinct false-success shape:

        1. declared checks that were never evaluated -> the task did not verify;
        2. a declared check that was evaluated and failed -> it failed;
        3. a task with **no** declared checks at all -> nothing was declared to
           verify, which is a weaker case of the same problem and is reported
           rather than waved through, because the caller can always declare
           "the command exited 0" first.
        """
        if artifact_ids:
            for artifact_id in artifact_ids:
                if artifact_id not in self.output_artifact_ids:
                    self.output_artifact_ids.append(artifact_id)
        if not self.acceptance.declared:
            raise ModelValidationError(f"task {self.task_id} declares no acceptance checks, so '{self.title}' cannot be recorded as succeeded. Declare what would prove the work before claiming it is done.")
        self.acceptance.assert_passed()
        return self.transition_to(TaskStatus.SUCCEEDED)

    def mark_failed(self, *, reason: str, error_code: str | None = None) -> TaskNode:
        self.failure_summary = _non_empty(reason, "failure reason")
        if error_code:
            self.error_code = error_code
        return self.transition_to(TaskStatus.FAILED, reason=self.failure_summary)

    def mark_blocked(self, *, reason: str) -> TaskNode:
        """Park the task with a visible reason.

        A blocked task is a *decision*, not an absence. Leaving downstream
        tasks pending forever because one prerequisite died is the plan's §7.2
        rule 7 failure.
        """
        self.failure_summary = _non_empty(reason, "blocked reason")
        return self.transition_to(TaskStatus.BLOCKED, reason=self.failure_summary)

    def unresolved_capabilities(self) -> list[CapabilityRequirement]:
        return [requirement for requirement in self.required_capabilities if requirement.required and not requirement.available]

    def ready_to_dispatch(self, *, dependency_status: Mapping[str, TaskStatus] | None = None, allow_blocked: bool = False) -> tuple[bool, str]:
        """Whether this task may start now, and why not when it may not.

        Returns a ``(ready, reason)`` pair rather than raising, because the
        scheduler asks this about *every* pending task on every pass and an
        exception is the wrong shape for "not yet".

        **Approval and capability availability are not readiness questions.**
        An earlier version checked ``needs_approval`` here, which made a
        HIGH-risk task permanently un-ready: it could only become ready by
        first being ``WAITING_APPROVAL``, and it could only reach
        ``WAITING_APPROVAL`` by being dispatched, and it could only be
        dispatched by being ready. The loop had no exit. Approval is what
        :meth:`~alpha.graph.nodes.dispatch_worker` does to a ready task, so
        readiness must not second-guess it.

        ``allow_blocked`` exists because a blocked task needs an explicit
        re-entry signal (the caller decided its blocker was resolved). Note
        that a blocked task with **no** recorded reason is refused either way:
        an unexplained block is indistinguishable from a task nobody scheduled.
        """
        if self.status not in (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.BLOCKED):
            return False, f"task status is {self.status.value}, which is not dispatchable"
        if self.status is TaskStatus.BLOCKED:
            if not self.failure_summary:
                return False, "task is blocked without a recorded reason"
            if not allow_blocked:
                return False, f"task is blocked and requires explicit re-entry: {self.failure_summary}"
        if self.attempt_count >= self.max_attempts:
            return False, f"attempt ceiling reached ({self.attempt_count}/{self.max_attempts})"
        missing = self.unresolved_capabilities()
        if missing:
            return False, "unresolved required capabilities: " + ", ".join(item.capability for item in missing)
        statuses = dependency_status or {}
        unmet: list[str] = []
        for dependency in self.depends_on:
            dependency_state = statuses.get(dependency)
            if dependency_state is None:
                unmet.append(f"{dependency} (unknown)")
            elif dependency_state not in SATISFIED_TASK_STATUSES:
                unmet.append(f"{dependency} ({dependency_state.value})")
        if unmet:
            return False, "unsatisfied dependencies: " + ", ".join(unmet)
        return True, ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "task_type": self.task_type.value,
            "status": self.status.value,
            "depends_on": list(self.depends_on),
            "assigned_worker": self.assigned_worker,
            "required_capabilities": [item.to_dict() for item in self.required_capabilities],
            "acceptance": self.acceptance.to_dict(),
            "output_artifact_ids": list(self.output_artifact_ids),
            "evidence_ids": list(self.evidence_ids),
            "risk_level": self.risk_level.value,
            "needs_approval": self.needs_approval,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "priority": self.priority,
            "can_retry": self.can_retry,
            "error_code": self.error_code,
            "failure_summary": self.failure_summary,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    FAILED = "failed"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class TaskAttempt:
    """One dispatch of one task, with its own idempotency key.

    ``idempotency_key`` is what makes a retry safe to *attempt*: the broker can
    refuse to re-run an operation whose key already completed. The plan's §12.4
    is explicit that this is at-least-once with idempotent operations, never a
    general promise of exactly-once side effects, and this field is the honest
    expression of that.
    """

    attempt_id: AttemptId
    task_id: TaskId
    goal_id: GoalId
    attempt_number: int
    idempotency_key: str = ""
    outcome: AttemptOutcome | None = None
    worker: str = ""
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    artifact_ids: list[ArtifactId] = field(default_factory=list)
    failure_summary: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ModelValidationError(f"attempt {self.attempt_id}: attempt_number must be >= 1, got {self.attempt_number}")
        if not self.idempotency_key:
            # Derived, not random: the same task at the same attempt number must
            # produce the same key across a process restart, or the whole point
            # (recognising a repeat) is lost.
            self.idempotency_key = f"{self.task_id}:a{self.attempt_number}"

    @classmethod
    def create(cls, *, task_id: TaskId, goal_id: GoalId, attempt_number: int, attempt_id: AttemptId | None = None) -> TaskAttempt:
        return cls(
            attempt_id=attempt_id or new_attempt_id(),
            task_id=task_id,
            goal_id=goal_id,
            attempt_number=attempt_number,
        )

    @property
    def is_open(self) -> bool:
        return self.outcome is None

    def complete(self, outcome: AttemptOutcome, *, artifact_ids: Sequence[ArtifactId] | None = None, reason: str = "", error_code: str | None = None) -> TaskAttempt:
        if self.outcome is not None:
            raise IllegalTransition(f"attempt {self.attempt_id} already closed with outcome {self.outcome.value}; refusing to overwrite it with {outcome.value}")
        self.outcome = outcome
        self.finished_at = now_iso()
        for artifact_id in artifact_ids or ():
            if artifact_id not in self.artifact_ids:
                self.artifact_ids.append(artifact_id)
        if reason:
            self.failure_summary = reason
        if error_code:
            self.error_code = error_code
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "attempt_number": self.attempt_number,
            "idempotency_key": self.idempotency_key,
            "outcome": self.outcome.value if self.outcome else None,
            "worker": self.worker,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "artifact_ids": list(self.artifact_ids),
            "failure_summary": self.failure_summary,
            "error_code": self.error_code,
        }


@dataclass(slots=True, frozen=True)
class GraphSnapshot:
    """A renderer-neutral view of one goal's task graph.

    Frozen: a snapshot handed to the UI is a value, so a later transition cannot
    mutate what a caller is already rendering. This is the plan's §17
    "renderer-neutral JSON schema", and it deliberately carries counts beside
    the rows -- ``direct_count`` beside ``effective_count`` in the group tree is
    the same rule, because a header claiming "3 members" over six visible tasks
    is a fabricated count.
    """

    goal_id: GoalId
    graph_id: GraphId
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    blocked_task_ids: tuple[TaskId, ...]
    ready_task_ids: tuple[TaskId, ...]
    wave_count: int
    critical_path: tuple[TaskId, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "graph_id": self.graph_id,
            "nodes": list(self.nodes),
            "edges": list(self.edges),
            "counts": {
                "total": len(self.nodes),
                "blocked": len(self.blocked_task_ids),
                "ready": len(self.ready_task_ids),
                "waves": self.wave_count,
            },
            "blocked_task_ids": list(self.blocked_task_ids),
            "ready_task_ids": list(self.ready_task_ids),
            "critical_path": list(self.critical_path),
        }


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class ArtifactSensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    SECRET = "secret"


@dataclass(slots=True)
class Artifact:
    """A durable result of work, referenced by id from graph state.

    The payload lives on disk; this is the pointer plus its digest. That split
    is what keeps a checkpoint small (plan §6.3) *and* what lets an artifact be
    hashed and re-checked later -- a claim about a file that the file can
    contradict is not evidence.

    ``content_hash`` is required, not optional: an artifact nobody can verify is
    an attachment, and the plan's §5.4 lists it as a field.
    """

    artifact_id: ArtifactId
    run_id: str
    artifact_type: str
    uri: str
    content_hash: str
    mime_type: str = "application/octet-stream"
    size_bytes: int | None = None
    goal_id: GoalId | None = None
    task_id: TaskId | None = None
    created_by_node: str = ""
    sensitivity: ArtifactSensitivity = ArtifactSensitivity.INTERNAL
    created_at: str = field(default_factory=now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.uri = _non_empty(self.uri, "artifact.uri")
        self.content_hash = _non_empty(self.content_hash, "artifact.content_hash")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ModelValidationError(f"artifact {self.artifact_id}: size_bytes must not be negative")

    @classmethod
    def create(cls, *, run_id: str, artifact_type: str, uri: str, content_hash: str, **kwargs: Any) -> Artifact:
        return cls(
            artifact_id=new_artifact_id(),
            run_id=run_id,
            artifact_type=artifact_type,
            uri=uri,
            content_hash=content_hash,
            **kwargs,
        )

    @staticmethod
    def hash_bytes(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "artifact_type": self.artifact_type,
            "uri": self.uri,
            "content_hash": self.content_hash,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "created_by_node": self.created_by_node,
            "sensitivity": self.sensitivity.value,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# Evidence claims (provenance-first knowledge)
# ---------------------------------------------------------------------------


class ClaimStatus(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


#: The only legal claim moves. ``VERIFIED -> SUPERSEDED`` is how a later fact
#: retires an earlier one **without erasing why the system previously believed
#: it** -- the plan's §9.2 rule that Alpha "must not silently overwrite a claim".
#: ``CANDIDATE -> SUPERSEDED`` is allowed because a candidate can be rejected
#: before it was ever accepted: the model extracted something a newer source
#: contradicts, and refusing the move would strand the candidate as current.
_CLAIM_TRANSITIONS: Final[dict[ClaimStatus, frozenset[ClaimStatus]]] = {
    ClaimStatus.CANDIDATE: frozenset({ClaimStatus.VERIFIED, ClaimStatus.DISPUTED, ClaimStatus.SUPERSEDED, ClaimStatus.RETRACTED}),
    ClaimStatus.VERIFIED: frozenset({ClaimStatus.DISPUTED, ClaimStatus.SUPERSEDED, ClaimStatus.RETRACTED}),
    ClaimStatus.DISPUTED: frozenset({ClaimStatus.VERIFIED, ClaimStatus.SUPERSEDED, ClaimStatus.RETRACTED}),
    ClaimStatus.SUPERSEDED: frozenset({ClaimStatus.VERIFIED}),
    ClaimStatus.RETRACTED: frozenset(),
}


@dataclass(slots=True)
class EvidenceClaim:
    """A statement with a source and a validity window.

    ``confidence`` is an **estimate** and ``status`` is a **fact**; keeping them
    apart is the point. A model that extracted a claim cannot promote itself to
    ``VERIFIED`` by writing a high number -- only :meth:`mark_verified` with a
    real ``source_locator`` does that, and ``created_by`` records who claimed
    the estimate.

    Four time fields, and the difference matters (plan §9.3): ``observed_at`` is
    when Alpha met the evidence, ``recorded_at`` when it wrote the row,
    ``valid_from``/``valid_to`` when the fact was true **in the world**. All four
    may be unknown; fabricating a validity date is worse than leaving it unset,
    which is why they are optional rather than defaulted to now.
    """

    claim_id: ClaimId
    subject_entity_id: str
    predicate: str
    claim_text: str
    status: ClaimStatus = ClaimStatus.CANDIDATE
    object_entity_id: str | None = None
    source_artifact_id: ArtifactId | None = None
    source_locator: str = ""
    confidence: float | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    observed_at: str | None = None
    recorded_at: str = field(default_factory=now_iso)
    created_by: str = ""
    verified_by: str = ""
    failure_summary: str | None = None
    contradicts: list[ClaimId] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.subject_entity_id = _non_empty(self.subject_entity_id, "claim.subject_entity_id")
        self.predicate = _non_empty(self.predicate, "claim.predicate")
        self.claim_text = _non_empty(self.claim_text, "claim.claim_text")
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
                raise ModelValidationError(f"claim {self.claim_id}: confidence must be a number or None, got {self.confidence!r}")
            if not 0.0 <= float(self.confidence) <= 1.0:
                raise ModelValidationError(f"claim {self.claim_id}: confidence {self.confidence} is outside [0, 1]")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ModelValidationError(f"claim {self.claim_id}: valid_to ({self.valid_to}) precedes valid_from ({self.valid_from})")

    @classmethod
    def create(cls, *, subject_entity_id: str, predicate: str, claim_text: str, **kwargs: Any) -> EvidenceClaim:
        return cls(
            claim_id=new_claim_id(),
            subject_entity_id=subject_entity_id,
            predicate=predicate,
            claim_text=claim_text,
            **kwargs,
        )

    @property
    def is_current(self) -> bool:
        """Whether this claim is one Alpha should answer from right now.

        A ``SUPERSEDED`` claim is retained for audit and must not be presented
        as current -- that is the plan's golden scenario 10, and a fact whose
        validity ended is not a fact.
        """
        return self.status in (ClaimStatus.CANDIDATE, ClaimStatus.VERIFIED)

    def transition_to(self, status: ClaimStatus) -> EvidenceClaim:
        if status == self.status:
            return self
        allowed = _CLAIM_TRANSITIONS[self.status]
        if status not in allowed:
            raise IllegalTransition(f"claim {self.claim_id} cannot move {self.status.value} -> {status.value}; allowed: {sorted(s.value for s in allowed) or 'none (terminal)'}")
        self.status = status
        return self

    def mark_verified(self, *, source_locator: str, verified_by: str) -> EvidenceClaim:
        """Promote to ``VERIFIED``.

        ``verified_by`` is non-empty, and the claim must already carry **some**
        source -- a source artifact, an observation time, or the locator being
        supplied here. A locator is a source (``path/to/file.py#L10-L42``
        names the bytes that support the claim), so supplying one is enough;
        refusing it would make every claim extracted with a locator and no
        artifact unverifiable, which would be a rule that blocks the honest
        case rather than the dishonest one.
        """
        self.source_locator = _non_empty(source_locator, "source_locator")
        self.verified_by = _non_empty(verified_by, "verified_by")
        if not (self.source_artifact_id or self.observed_at or self.source_locator):
            raise ModelValidationError(f"claim {self.claim_id} cannot be verified with no source at all: name a source_artifact_id, an observed_at, or a source_locator")
        return self.transition_to(ClaimStatus.VERIFIED)

    def mark_disputed(self, *, conflicting_claim_id: ClaimId) -> EvidenceClaim:
        if conflicting_claim_id not in self.contradicts:
            self.contradicts.append(conflicting_claim_id)
        return self.transition_to(ClaimStatus.DISPUTED)

    def mark_superseded(self, *, by_claim_id: ClaimId | None = None) -> EvidenceClaim:
        if by_claim_id is not None and by_claim_id not in self.contradicts:
            self.contradicts.append(by_claim_id)
        return self.transition_to(ClaimStatus.SUPERSEDED)

    def mark_retracted(self, *, reason: str = "") -> EvidenceClaim:
        self.failure_summary = reason or None
        return self.transition_to(ClaimStatus.RETRACTED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "subject_entity_id": self.subject_entity_id,
            "predicate": self.predicate,
            "object_entity_id": self.object_entity_id,
            "claim_text": self.claim_text,
            "status": self.status.value,
            "is_current": self.is_current,
            "source_artifact_id": self.source_artifact_id,
            "source_locator": self.source_locator,
            "confidence": self.confidence,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "observed_at": self.observed_at,
            "recorded_at": self.recorded_at,
            "created_by": self.created_by,
            "verified_by": self.verified_by,
            "failure_summary": self.failure_summary,
            "contradicts": list(self.contradicts),
        }


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class EventType(StrEnum):
    NODE_STARTED = "node.started"
    NODE_COMPLETED = "node.completed"
    NODE_FAILED = "node.failed"
    TASK_CREATED = "task.created"
    TASK_READY = "task.ready"
    TASK_ASSIGNED = "task.assigned"
    TASK_BLOCKED = "task.blocked"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    MODEL_REQUESTED = "model.requested"
    MODEL_COMPLETED = "model.completed"
    MODEL_FAILED = "model.failed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_APPROVAL_REQUIRED = "tool.approval_required"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_VALIDATION_FAILED = "artifact.validation_failed"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_PASSED = "verification.passed"
    VERIFICATION_FAILED = "verification.failed"
    RETRY_SCHEDULED = "retry.scheduled"
    RETRY_EXHAUSTED = "retry.exhausted"
    PLAN_VALIDATED = "plan.validated"
    PLAN_REJECTED = "plan.rejected"
    GOAL_COMPLETED = "goal.completed"
    GOAL_PARTIAL = "goal.partial"
    GOAL_FAILED = "goal.failed"
    RUN_CANCELLED = "run.cancelled"


#: Events that prove an operation actually *happened*. A caller that wants to
#: know "was this really run?" filters on these and never on the matching
#: ``*.requested`` pair -- the plan's §19 row "Tool call is only proposed but
#: not executed" and its UI rule "planned/blocked, not completed".
COMPLETION_EVENT_TYPES: Final[frozenset[EventType]] = frozenset({EventType.NODE_COMPLETED, EventType.TOOL_COMPLETED, EventType.MODEL_COMPLETED, EventType.VERIFICATION_PASSED, EventType.TASK_COMPLETED, EventType.GOAL_COMPLETED})


class EventSeverity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(slots=True)
class RunEvent:
    """One structured, redacted fact about a run.

    Causal links are **ids**, never log prose: ``parent_event_id``, ``task_id``,
    ``attempt_id``, ``trace_id``/``span_id``. The plan's §11.2 rule is that
    causal order is never reconstructed from message text when structured
    identifiers exist, because two events whose messages interleave in a log
    give you no order at all.

    ``message_safe`` is named for what it is: the caller passes it through
    :mod:`alpha.graph.redact` before construction, so a secret cannot reach an
    event by being interpolated into a human-readable field.
    """

    event_id: EventId
    run_id: str
    event_type: EventType
    severity: EventSeverity = EventSeverity.INFO
    status: str = ""
    goal_id: GoalId | None = None
    task_id: TaskId | None = None
    attempt_id: AttemptId | None = None
    parent_event_id: EventId | None = None
    trace_id: str = ""
    span_id: str = ""
    component: str = "alpha.graph"
    operation: str = ""
    attempt: int | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    message_safe: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    artifact_ids: list[ArtifactId] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        self.run_id = _non_empty(self.run_id, "event.run_id")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ModelValidationError(f"event {self.event_id}: duration_ms must not be negative")

    @classmethod
    def create(cls, *, run_id: str, event_type: EventType, **kwargs: Any) -> RunEvent:
        return cls(event_id=new_event_id(), run_id=run_id, event_type=event_type, **kwargs)

    @property
    def is_completion(self) -> bool:
        return self.event_type in COMPLETION_EVENT_TYPES

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "parent_event_id": self.parent_event_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "status": self.status,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "component": self.component,
            "operation": self.operation,
            "attempt": self.attempt,
            "error_code": self.error_code,
            "message_safe": self.message_safe,
            "attributes": dict(self.attributes),
            "artifact_ids": list(self.artifact_ids),
            "is_completion": self.is_completion,
        }


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(slots=True)
class ApprovalRequest:
    """A human decision bound to one exact operation payload.

    ``payload_digest`` is the mechanism behind the plan's §13.2 rule -- "if
    arguments change, request approval again". :meth:`decide` recomputes the
    digest from the operation it was handed and **refuses** a decision when it
    no longer matches, so a mutation between the request and the click cannot
    ride an old grant. That is not a nicety: it is the difference between an
    approval system and a rubber stamp.
    """

    approval_id: ApprovalId
    run_id: str
    operation: str
    target: str
    task_id: TaskId | None = None
    command: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    expected_side_effects: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    state: ApprovalState = ApprovalState.PENDING
    payload_digest: str = ""
    requested_at: str = field(default_factory=now_iso)
    decided_at: str | None = None
    decided_by: str = ""
    decision_note: str = ""

    def __post_init__(self) -> None:
        self.operation = _non_empty(self.operation, "approval.operation")
        self.target = _non_empty(self.target, "approval.target")
        if not self.payload_digest:
            self.payload_digest = self.compute_digest()

    @classmethod
    def create(cls, *, run_id: str, operation: str, target: str, **kwargs: Any) -> ApprovalRequest:
        return cls(approval_id=new_approval_id(), run_id=run_id, operation=operation, target=target, **kwargs)

    def compute_digest(self) -> str:
        """Digest of everything the operator was actually shown."""
        return _digest(
            {
                "operation": self.operation,
                "target": self.target,
                "command": self.command,
                "arguments": self.arguments,
                "side_effects": sorted(self.expected_side_effects),
            }
        )

    def preview(self) -> dict[str, Any]:
        """Exactly what the user is shown before deciding."""
        return {
            "approval_id": self.approval_id,
            "operation": self.operation,
            "target": self.target,
            "command": self.command,
            "arguments": dict(self.arguments),
            "expected_side_effects": list(self.expected_side_effects),
            "alternatives": list(self.alternatives),
            "requested_at": self.requested_at,
            "payload_digest": self.payload_digest,
        }

    def decide(self, decision: ApprovalDecision, *, decided_by: str, operation_payload: dict[str, Any] | None = None, note: str = "") -> ApprovalRequest:
        """Record a decision, refusing one that no longer matches the operation.

        ``operation_payload`` is the *current* shape of the operation. When it is
        supplied and its digest differs from ``payload_digest``, the operation
        changed after the operator saw it, so this raises rather than approving
        something nobody looked at.
        """
        if self.state is not ApprovalState.PENDING:
            raise IllegalTransition(f"approval {self.approval_id} is already {self.state.value}; refusing a second decision")
        actor = _non_empty(decided_by, "decided_by")
        if operation_payload is not None:
            current = _digest(
                {
                    "operation": operation_payload.get("operation", self.operation),
                    "target": operation_payload.get("target", self.target),
                    "command": operation_payload.get("command", self.command),
                    "arguments": operation_payload.get("arguments", self.arguments),
                    "side_effects": sorted(operation_payload.get("expected_side_effects", self.expected_side_effects)),
                }
            )
            if current != self.payload_digest:
                raise IllegalTransition(
                    f"approval {self.approval_id} was granted for payload {self.payload_digest[:12]} but the operation is now {current[:12]}. The arguments changed after it was shown; request approval again instead of reusing this grant."
                )
        self.state = ApprovalState.APPROVED if decision is ApprovalDecision.APPROVE else ApprovalState.REJECTED
        self.decided_at = now_iso()
        self.decided_by = actor
        self.decision_note = note
        return self

    @property
    def is_approved(self) -> bool:
        return self.state is ApprovalState.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "state": self.state.value,
            "requested_at": self.requested_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "decision_note": self.decision_note,
            "payload_digest": self.payload_digest,
            **self.preview(),
        }
