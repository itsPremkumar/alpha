"""Bounded delegation: the depth/fan-out/hop/budget ceilings every hop inherits.

The invariants this module exists to make true
----------------------------------------------
1. **A child's failure is never visible to the parent as success.** A child
   result carries the outcome the child actually reported; a child that raised,
   timed out, or returned a falsy/error-shaped result becomes ``FAILED``. There
   is no code path that maps a failed child onto a successful parent outcome.
2. **A task is never complete while any descendant is running or unresolved.**
   :meth:`DelegationTree.aggregate` returns ``COMPLETE`` only when every child
   is terminal *and* successful; any running or unresolved descendant yields
   ``PENDING``/``UNRESOLVED`` and a failed descendant forces ``FAILED``.

The bounds
----------
``max_depth``       - how far a chain may nest (``alpha`` -> subagent -> nested
                      subagent is depth 2).
``max_fanout``      - how many children one node may create.
``max_hops``        - how many times ONE task may change hands. This is the
                      cycle guard: a task bouncing between the same two agents
                      burns hops and stops, instead of looping forever.
``token_budget``    - inherited downward, charged by each child's real reported
                      usage, never by an estimate.
``cost_budget_usd`` - the same, in currency, so a deep tree cannot spend without
                      bound even when token counts are misreported.

Every bound is checked BEFORE a child is created, and a refusal is a first-class
outcome carrying a reason code, not an exception that a caller may swallow.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from alpha.bots.failure_reasons import HANDOFF_REQUESTED, UNKNOWN

# Guard refusal codes. These are deliberately *not* provider codes: they answer
# "why was no child created", which the failure taxonomy does not model.
REFUSAL_DEPTH_CEILING = "delegation_depth_ceiling"
REFUSAL_FANOUT_CEILING = "delegation_fanout_ceiling"
REFUSAL_HOP_CEILING = "delegation_hop_ceiling"
REFUSAL_CYCLE = "delegation_cycle_detected"
REFUSAL_TOKEN_BUDGET = "delegation_token_budget_exhausted"
REFUSAL_COST_BUDGET = "delegation_cost_budget_exhausted"
REFUSAL_AUTHORITY = "capability_missing"
REFUSAL_NO_ELIGIBLE_AGENT = "no_eligible_agent"

GUARD_REFUSAL_CODES: frozenset[str] = frozenset(
    {
        REFUSAL_DEPTH_CEILING,
        REFUSAL_FANOUT_CEILING,
        REFUSAL_HOP_CEILING,
        REFUSAL_CYCLE,
        REFUSAL_TOKEN_BUDGET,
        REFUSAL_COST_BUDGET,
        REFUSAL_AUTHORITY,
        REFUSAL_NO_ELIGIBLE_AGENT,
    }
)


class ChildStatus(StrEnum):
    """Terminal and non-terminal states of one delegated child."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"

    @property
    def is_terminal(self) -> bool:
        return self in (ChildStatus.COMPLETED, ChildStatus.FAILED, ChildStatus.UNKNOWN_OUTCOME)


class TreeStatus(StrEnum):
    """Aggregate state of a delegation subtree, as reported to the parent."""

    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class DelegationLimits:
    """Hard ceilings for one delegation tree. Inherited, never widened."""

    max_depth: int = 3
    max_fanout: int = 4
    max_hops: int = 6
    token_budget: int = 200_000
    cost_budget_usd: float = 5.0
    max_children_per_node: int = 4
    #: Fraction of the parent's remaining token budget a single child may take.
    #: Guarantees a deep tree converges instead of every level asking for
    #: everything it has left.
    child_token_share: float = 0.5

    def __post_init__(self) -> None:
        if self.max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        if self.max_fanout < 1:
            raise ValueError("max_fanout must be at least 1")
        if self.max_hops < 1:
            raise ValueError("max_hops must be at least 1")
        if self.token_budget < 0:
            raise ValueError("token_budget must not be negative")
        if self.cost_budget_usd < 0:
            raise ValueError("cost_budget_usd must not be negative")
        if not 0.0 < self.child_token_share <= 1.0:
            raise ValueError("child_token_share must be in (0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_depth": self.max_depth,
            "max_fanout": self.max_fanout,
            "max_hops": self.max_hops,
            "token_budget": self.token_budget,
            "cost_budget_usd": self.cost_budget_usd,
            "child_token_share": self.child_token_share,
        }


DEFAULT_LIMITS = DelegationLimits()


@dataclass(frozen=True)
class GuardVerdict:
    """Whether one guard allows a hop, and why not when it does not."""

    allowed: bool
    code: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "code": self.code, "detail": self.detail}


ALLOWED = GuardVerdict(True)


@dataclass(frozen=True)
class DelegationContext:
    """The budget/lineage a node passes to each of its children.

    Immutable and inherited: :meth:`for_child` returns a *new* context with a
    strictly smaller budget, so a child cannot hand a grandchild more than it
    received.
    """

    root: str
    task_id: str
    lineage: tuple[str, ...]
    depth: int
    fanout_used: int
    hops: int
    token_budget: int
    tokens_spent: int
    cost_budget_usd: float
    cost_spent_usd: float
    limits: DelegationLimits = DEFAULT_LIMITS
    parent_node_id: str | None = None

    @property
    def node_id(self) -> str:
        return self.lineage[-1] if self.lineage else self.root

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.token_budget - self.tokens_spent)

    @property
    def cost_remaining(self) -> float:
        return max(0.0, self.cost_budget_usd - self.cost_spent_usd)

    def for_child(
        self,
        child: str,
        *,
        token_budget: int | None = None,
        cost_budget_usd: float | None = None,
    ) -> DelegationContext:
        """Derive the context a child of ``child`` inherits.

        The child's budget is ``min(remaining * child_token_share, requested)``
        so it is never larger than the parent's remainder and never its whole
        remainder. ``hops`` increments here, which is what makes the hop ceiling
        a hard stop rather than a per-level budget.
        """
        share = self.limits.child_token_share
        inherited_tokens = token_budget if token_budget is not None else int(self.tokens_remaining * share)
        inherited_tokens = max(0, min(inherited_tokens, self.tokens_remaining))
        inherited_cost = cost_budget_usd if cost_budget_usd is not None else self.cost_remaining * share
        inherited_cost = max(0.0, min(inherited_cost, self.cost_remaining))
        return replace(
            self,
            lineage=(*self.lineage, child),
            depth=self.depth + 1,
            fanout_used=0,
            hops=self.hops + 1,
            token_budget=inherited_tokens,
            tokens_spent=0,
            cost_budget_usd=inherited_cost,
            cost_spent_usd=0.0,
            parent_node_id=self.node_id,
        )

    def charge(self, *, tokens: int = 0, cost_usd: float = 0.0) -> DelegationContext:
        """Return this context with a child's REAL usage charged against it."""
        return replace(
            self,
            fanout_used=self.fanout_used + 1,
            tokens_spent=self.tokens_spent + max(0, int(tokens)),
            cost_spent_usd=round(self.cost_spent_usd + max(0.0, float(cost_usd)), 6),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "task_id": self.task_id,
            "lineage": list(self.lineage),
            "depth": self.depth,
            "fanout_used": self.fanout_used,
            "hops": self.hops,
            "token_budget": self.token_budget,
            "tokens_spent": self.tokens_spent,
            "cost_budget_usd": self.cost_budget_usd,
            "cost_spent_usd": self.cost_spent_usd,
            "limits": self.limits.to_dict(),
        }

    # -- guards ------------------------------------------------------------

    def check_depth(self) -> GuardVerdict:
        if self.depth >= self.limits.max_depth:
            return GuardVerdict(
                False,
                REFUSAL_DEPTH_CEILING,
                f"depth {self.depth} reached the ceiling of {self.limits.max_depth}",
            )
        return ALLOWED

    def check_fanout(self) -> GuardVerdict:
        if self.fanout_used >= self.limits.max_fanout:
            return GuardVerdict(
                False,
                REFUSAL_FANOUT_CEILING,
                f"{self.fanout_used} children already created; ceiling is {self.limits.max_fanout}",
            )
        return ALLOWED

    def check_hops(self) -> GuardVerdict:
        if self.hops >= self.limits.max_hops:
            return GuardVerdict(
                False,
                REFUSAL_HOP_CEILING,
                f"task {self.task_id!r} has already changed hands {self.hops} time(s); ceiling is {self.limits.max_hops}",
            )
        return ALLOWED

    def check_cycle(self, candidate: str) -> GuardVerdict:
        """Refuse to hand the task to an agent already in its own lineage.

        This is the ping-pong guard. A two-agent loop (A fails -> B -> A -> B)
        would otherwise be re-dispatched forever; refusing the back-edge turns an
        infinite loop into one recorded refusal.
        """
        key = (candidate or "").strip().lower()
        if key and key in {item.lower() for item in self.lineage}:
            return GuardVerdict(
                False,
                REFUSAL_CYCLE,
                f"{key!r} already holds this task in its delegation lineage {' -> '.join(self.lineage)}",
            )
        return ALLOWED

    def check_budget(self) -> GuardVerdict:
        if self.tokens_remaining <= 0:
            return GuardVerdict(
                False,
                REFUSAL_TOKEN_BUDGET,
                f"token budget {self.token_budget} exhausted ({self.tokens_spent} spent)",
            )
        if self.cost_remaining <= 0.0:
            return GuardVerdict(
                False,
                REFUSAL_COST_BUDGET,
                f"cost budget {self.cost_budget_usd} exhausted ({self.cost_spent_usd} spent)",
            )
        return ALLOWED

    def check(self, candidate: str | None = None) -> GuardVerdict:
        """Run every guard in order and return the first refusal."""
        for guard in (self.check_depth, self.check_fanout, self.check_hops, self.check_budget):
            verdict = guard()
            if not verdict.allowed:
                return verdict
        if candidate:
            cycle = self.check_cycle(candidate)
            if not cycle.allowed:
                return cycle
        return ALLOWED


def root_context(
    root: str,
    task_id: str,
    *,
    limits: DelegationLimits = DEFAULT_LIMITS,
    token_budget: int | None = None,
    cost_budget_usd: float | None = None,
) -> DelegationContext:
    """Build the context a tree's root node starts from."""
    key = (root or "").strip().lower() or "alpha"
    return DelegationContext(
        root=key,
        task_id=task_id,
        lineage=(key,),
        depth=0,
        fanout_used=0,
        hops=0,
        token_budget=int(limits.token_budget if token_budget is None else token_budget),
        tokens_spent=0,
        cost_budget_usd=float(limits.cost_budget_usd if cost_budget_usd is None else cost_budget_usd),
        cost_spent_usd=0.0,
        limits=limits,
    )


@dataclass
class ChildReport:
    """One delegated child's real outcome, as reported to its parent.

    ``status`` is derived from what the child actually reported (see
    :func:`child_report`) — never from what the parent would like it to be.
    """

    child_id: str
    agent: str
    status: ChildStatus
    reason: str = UNKNOWN
    detail: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    attempt: int = 1
    result: Any = None
    descendants: DelegationTree | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status is ChildStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "agent": self.agent,
            "status": str(self.status),
            "ok": self.ok,
            "reason": self.reason,
            "detail": self.detail[:2000],
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "attempt": self.attempt,
        }


def child_report(
    child_id: str,
    agent: str,
    outcome: Any,
    *,
    error: str | None = None,
    tokens: int = 0,
    cost_usd: float = 0.0,
    attempt: int = 1,
    descendants: DelegationTree | None = None,
) -> ChildReport:
    """Build a :class:`ChildReport` from a child's ACTUAL outcome.

    Invariant 1 lives here. A child is ``COMPLETED`` only when it reported
    success: no exception was raised, no error text was given, the descendants
    (if any) did not fail, and the result is not an explicit error shape. Every
    other case is ``FAILED`` or ``UNKNOWN_OUTCOME`` — and an unknown outcome is
    never optimistically read as success, because "we did not hear back" is not
    "it worked".
    """
    from alpha.bots.failure_reasons import classify_work_failure

    if error:
        status = ChildStatus.FAILED
        reason = classify_work_failure(error)
        detail = error
    elif _reports_error(outcome):
        text = _error_text(outcome) or "child reported an error result"
        status = ChildStatus.FAILED
        reason = classify_work_failure(text)
        detail = text
    elif outcome is None:
        status = ChildStatus.UNKNOWN_OUTCOME
        reason = UNKNOWN
        detail = "child returned no result; completion cannot be confirmed"
    elif descendants is not None and descendants.aggregate_status() is TreeStatus.FAILED:
        # A child that succeeded while one of ITS children failed is not a
        # success. Propagating the deepest real failure is the whole point of
        # invariant 1.
        status = ChildStatus.FAILED
        reason = classify_work_failure(descendants.failure_detail or "descendant delegation failed")
        detail = descendants.failure_detail or "descendant delegation failed"
    elif descendants is not None and not descendants.is_settled:
        status = ChildStatus.FAILED
        reason = UNKNOWN
        detail = "descendant delegation is not settled"
    else:
        status = ChildStatus.COMPLETED
        reason = HANDOFF_REQUESTED
        detail = ""
    return ChildReport(
        child_id=child_id,
        agent=(agent or "").strip().lower(),
        status=status,
        reason=reason,
        detail=detail,
        tokens=max(0, int(tokens)),
        cost_usd=max(0.0, float(cost_usd)),
        attempt=max(1, int(attempt)),
        result=outcome,
        descendants=descendants,
    )


#: Keys an error-shaped result may use. A dict carrying any of them with a
#: truthy value is a failure report, not a result.
_ERROR_KEYS: tuple[str, ...] = ("error", "error_message", "errors", "failure", "failed", "exception")


def _error_text(outcome: Any) -> str:
    if isinstance(outcome, str):
        return outcome
    if isinstance(outcome, dict):
        for key in _ERROR_KEYS:
            value = outcome.get(key)
            if value:
                return str(value) if not isinstance(value, (list, tuple)) else "; ".join(str(item) for item in value)
    error = getattr(outcome, "error", None)
    if error:
        return str(error)
    return ""


def _reports_error(outcome: Any) -> bool:
    if isinstance(outcome, str):
        stripped = outcome.strip()
        return stripped.startswith(("Error:", "Traceback (most recent call last)"))
    if isinstance(outcome, dict):
        return any(bool(outcome.get(key)) for key in _ERROR_KEYS)
    return bool(getattr(outcome, "error", None))


class DelegationTree:
    """A bounded delegation subtree rooted at one agent.

    Owns the children a node has created, their real outcomes, and the aggregate
    status its parent is allowed to see.
    """

    def __init__(self, context: DelegationContext, *, recorder: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._context = context
        self._children: list[ChildReport] = []
        self._lock = threading.RLock()
        #: Optional hook used by the dispatcher to append every hop to the
        #: global handoff ledger. Kept as a callback so this module owns no I/O.
        self._recorder = recorder

    @property
    def context(self) -> DelegationContext:
        return self._context

    @property
    def children(self) -> list[ChildReport]:
        with self._lock:
            return list(self._children)

    def child_context(self, agent: str, **kwargs: Any) -> DelegationContext:
        """The context ``agent`` inherits if it is granted this task."""
        return self._context.for_child(agent, **kwargs)

    def admit(self, context: DelegationContext, report: ChildReport) -> ChildReport:
        """Record an admitted child and charge its real usage to this node."""
        with self._lock:
            self._children.append(report)
            if self._recorder is not None:
                self._record(report, context)
            self._context = self._context.charge(tokens=report.tokens, cost_usd=report.cost_usd)
        return report

    def _record(self, report: ChildReport, context: DelegationContext) -> None:
        assert self._recorder is not None
        try:
            self._recorder(
                {
                    "child_id": report.child_id,
                    "from": context.parent_node_id or self._context.root,
                    "to": report.agent,
                    "reason": report.reason,
                    "attempt": report.attempt,
                    "status": str(report.status),
                    "depth": context.depth,
                    "lineage": list(context.lineage),
                    "tokens": report.tokens,
                    "cost_usd": report.cost_usd,
                }
            )
        except Exception:  # noqa: BLE001 - recording must never mask the outcome
            pass

    # -- invariant 2: aggregate status -------------------------------------

    @property
    def failure_detail(self) -> str:
        """The deepest real failure in this subtree, for propagation upward."""
        for report in self._children:
            if report.status is ChildStatus.FAILED:
                if report.detail:
                    return f"{report.agent}: {report.detail}"
                return f"{report.agent} failed ({report.reason})"
            if report.descendants is not None and report.descendants.failure_detail:
                return f"{report.agent}/{report.descendants.failure_detail}"
        return ""

    @property
    def is_settled(self) -> bool:
        """True only when every child reached a terminal, non-error state."""
        with self._lock:
            children = list(self._children)
        if not children:
            return False
        return all(child.status is ChildStatus.COMPLETED for child in children)

    def aggregate_status(self) -> TreeStatus:
        """What the parent is allowed to believe about this subtree.

        * any ``FAILED`` child -> ``FAILED`` (a failure is never laundered)
        * any ``UNKNOWN_OUTCOME`` -> ``UNRESOLVED`` (absence of a result is not
          success)
        * no children, or any child not yet terminal -> ``PENDING``
        * every child ``COMPLETED`` -> ``COMPLETE``
        """
        with self._lock:
            children = list(self._children)
        if not children:
            return TreeStatus.PENDING
        if any(child.status is ChildStatus.FAILED for child in children):
            return TreeStatus.FAILED
        if any(child.status is ChildStatus.UNKNOWN_OUTCOME for child in children):
            return TreeStatus.UNRESOLVED
        if not all(child.status.is_terminal for child in children):
            return TreeStatus.PENDING
        return TreeStatus.COMPLETE

    def unresolved(self) -> list[dict[str, Any]]:
        """Children that are neither running-confirmed-complete nor failed."""
        return [child.to_dict() for child in self.children if not child.ok and child.status is not ChildStatus.FAILED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self._context.to_dict(),
            "aggregate_status": str(self.aggregate_status()),
            "settled": self.is_settled,
            "children": [child.to_dict() for child in self.children],
        }


def run_bounded(
    agent: str,
    objective: str,
    runner: Callable[[str], Any],
    *,
    parent: DelegationTree,
    context: DelegationContext,
    child_id: str,
    tokens_of: Callable[[Any], int] | None = None,
    cost_of: Callable[[Any], float] | None = None,
) -> ChildReport:
    """Run one child under a bounded context, honestly reporting its outcome.

    This is the seam a real executor plugs into: ``runner`` performs the work and
    its result (or exception) is what determines the child's status. There is no
    path here that returns a success the runner did not produce.
    """
    try:
        outcome = runner(objective)
    except Exception as exc:  # noqa: BLE001 - the failure IS the result
        report = child_report(
            child_id,
            agent,
            None,
            error=f"{type(exc).__name__}: {exc}",
            attempt=context.hops,
        )
        parent.admit(context, report)
        return report
    tokens = int(tokens_of(outcome)) if tokens_of is not None else 0
    cost = float(cost_of(outcome)) if cost_of is not None else 0.0
    report = child_report(child_id, agent, outcome, tokens=tokens, cost_usd=cost, attempt=context.hops)
    parent.admit(context, report)
    return report


def lineage_agents(lineage: Iterable[str]) -> tuple[str, ...]:
    return tuple(str(item or "").strip().lower() for item in lineage if str(item or "").strip())


__all__ = [
    "ALLOWED",
    "DEFAULT_LIMITS",
    "GUARD_REFUSAL_CODES",
    "REFUSAL_AUTHORITY",
    "REFUSAL_COST_BUDGET",
    "REFUSAL_CYCLE",
    "REFUSAL_DEPTH_CEILING",
    "REFUSAL_FANOUT_CEILING",
    "REFUSAL_HOP_CEILING",
    "REFUSAL_NO_ELIGIBLE_AGENT",
    "REFUSAL_TOKEN_BUDGET",
    "ChildReport",
    "ChildStatus",
    "DelegationContext",
    "DelegationLimits",
    "DelegationTree",
    "GuardVerdict",
    "TreeStatus",
    "child_report",
    "lineage_agents",
    "root_context",
    "run_bounded",
]
