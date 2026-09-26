"""The supervisor: it decides the next step, by capability match, with a reason.

Reuses, and does not reimplement:

* :mod:`alpha.bots.capability_dispatch` - capability match and the recorded reason
* :mod:`alpha.bots.delegation` - ``DelegationContext``/``DelegationLimits`` for
  ENFORCED depth, fan-out, hop and budget ceilings, and ``child_report`` /
  ``DelegationTree`` for the two hard invariants
* :mod:`alpha.bots.reassignment` - retry-in-place versus route-elsewhere
* :mod:`alpha.bots.failure_reasons` - the taxonomy, imported and NEVER edited
* :mod:`alpha.orchestration.intent` - prompt intent, to derive required
  capability tags when the caller does not supply them
* :mod:`alpha.models.workforce_router` - which MODEL tier to spend on a step

Invariants, both of which this codebase has shipped violations of:

* **(a) a child failure is never visible to the parent as success.** Handled by
  :func:`alpha.bots.delegation.child_report`, which only returns ``COMPLETED``
  when there was no exception, no error text, no failed descendant and a
  non-``None`` result. :meth:`Supervisor.settle` re-checks it independently, so
  a bug in the helper still cannot turn a failure into a success.
* **(b) a task is never complete while any descendant is running or
  unresolved.** Handled by :meth:`DelegationTree.aggregate_status`, which
  returns ``PENDING`` while anything is non-terminal and ``UNRESOLVED`` when any
  child outcome is unknown. :meth:`Supervisor.settle` refuses to report
  completion in either case.

Loop detection, and which way it fails
--------------------------------------
:class:`LoopDetector` watches a PROGRESS SIGNATURE, not elapsed time. A step
advances the work if its signature changes; a genuinely stuck agent repeats the
same signature and is caught after ``max_repeats``. The failure mode is
therefore explicit: **it fails toward letting hard work continue.** Long
productive work that keeps changing state is never killed, and an agent that
loops while varying its output a little can run longer than it should. The
alternative - a wall-clock kill - is the failure mode that kills the hardest
tasks, and that is worse.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.bots.delegation import (
    DEFAULT_LIMITS,
    ChildReport,
    ChildStatus,
    DelegationContext,
    DelegationLimits,
    DelegationTree,
    TreeStatus,
    child_report,
    root_context,
)
from alpha.bots.failure_reasons import (
    ACTION_ESCALATE,
    ACTION_REASSIGN,
    ACTION_RESUME,
    ACTION_RETRY,
    ALL_REASONS,
    classify_work_failure,
    decide_failure,
    failure_class,
)

logger = logging.getLogger(__name__)

#: Next-step outcomes. Deliberately not the failure taxonomy: these answer
#: "what did the supervisor decide", which the taxonomy does not model. The
#: taxonomy is used for the FAILURE side and is imported unmodified.
STEP_DISPATCH = "dispatch"
STEP_RETRY_IN_PLACE = "retry_in_place"
STEP_RESCOPE = "rescope"
STEP_ESCALATE = "escalate"
STEP_STOP = "stop"
STEP_COMPLETE = "complete"
STEP_UNROUTABLE = "unroutable"


class SupervisorRefused(RuntimeError):
    """The supervisor could not take a decision. The step does not proceed."""


@dataclass(slots=True)
class SupervisorDecision:
    """One next-step decision, with the reason that produced it."""

    task_id: str
    step: str
    reason: str
    target: str | None = None
    reason_class: str = ""
    #: The taxonomy code the decision was made on, so the record shows WHICH
    #: classification drove the routing rather than only the prose.
    failure_reason: str = ""
    attempt: int = 1
    max_attempts: int = 3
    required_capability_tags: tuple[str, ...] = ()
    matched_capability_tags: tuple[str, ...] = ()
    guard: dict[str, Any] = field(default_factory=dict)
    depth: int = 0
    hops: int = 0
    token_budget: int = 0
    dispatched: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "step": self.step,
            "reason": self.reason,
            "target": self.target,
            "reason_class": self.reason_class,
            "failure_reason": self.failure_reason,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "required_capability_tags": list(self.required_capability_tags),
            "matched_capability_tags": list(self.matched_capability_tags),
            "guard": dict(self.guard),
            "depth": self.depth,
            "hops": self.hops,
            "token_budget": self.token_budget,
            "dispatched": self.dispatched,
        }

    def human_line(self) -> str:
        who = f" -> @{self.target}" if self.target else ""
        return f"[{self.task_id}] {self.step}{who} (attempt {self.attempt}/{self.max_attempts}): {self.reason}"


@dataclass(slots=True)
class SettleVerdict:
    """Whether a task may be reported complete. The two invariants, checked."""

    complete: bool
    status: str
    reason: str
    open_descendants: int = 0
    unresolved_descendants: int = 0
    failed_children: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "status": self.status,
            "reason": self.reason,
            "open_descendants": self.open_descendants,
            "unresolved_descendants": self.unresolved_descendants,
            "failed_children": list(self.failed_children),
        }


class LoopDetector:
    """Detects a stuck agent by a repeating PROGRESS SIGNATURE.

    Fails toward allowing long productive work: only a repeated signature trips
    it, so an agent that keeps making progress is never stopped no matter how
    long it takes.
    """

    def __init__(self, *, max_repeats: int = 3) -> None:
        if max_repeats < 2:
            raise ValueError("max_repeats must be >= 2, otherwise every step looks stuck")
        self.max_repeats = max_repeats
        self._seen: dict[str, list[str]] = {}

    @staticmethod
    def signature(task_id: str, state: Any) -> str:
        """A stable fingerprint of "where the work is", not of how long it took."""
        try:
            blob = json.dumps(state, sort_keys=True, default=str)
        except (TypeError, ValueError):
            blob = str(state)
        return hashlib.sha256(f"{task_id}|{blob}".encode()).hexdigest()[:16]

    def observe(self, task_id: str, state: Any) -> tuple[bool, str]:
        """Record one observation. Returns ``(is_looping, explanation)``."""
        sig = self.signature(task_id, state)
        history = self._seen.setdefault(task_id, [])
        history.append(sig)
        if len(history) > self.max_repeats:
            history.pop(0)
        repeats = history.count(sig)
        if repeats >= self.max_repeats:
            return True, (
                f"progress signature {sig} repeated {repeats} times across {len(history)} "
                f"observations; treating @{task_id} as stuck"
            )
        return False, f"progress signature {sig}, observation {len(history)}, not looping"

    def forget(self, task_id: str) -> None:
        self._seen.pop(task_id, None)


class Supervisor:
    """Chooses the next step for a task and refuses to lie about completion."""

    def __init__(
        self,
        *,
        root: str = "alpha",
        limits: DelegationLimits = DEFAULT_LIMITS,
        registry: Any | None = None,
        dispatcher: Any | None = None,
        loop_detector: LoopDetector | None = None,
    ) -> None:
        self.root = root
        self.limits = limits
        self.registry = registry
        self.dispatcher = dispatcher
        self.loop_detector = loop_detector or LoopDetector()
        self._trees: dict[str, DelegationTree] = {}
        self._contexts: dict[str, DelegationContext] = {}

    # -- intent -----------------------------------------------------------
    def required_tags(self, objective: str, explicit: Sequence[str] | None = None) -> tuple[str, ...]:
        """Capability tags for a step.

        An explicit requirement always wins. Otherwise the prompt domain is used
        to derive tags. Routing is NEVER by department or by name: both are how
        mismatched agents get work they cannot do.
        """
        if explicit:
            return tuple(sorted({str(t).strip().lower() for t in explicit if str(t).strip()}))
        try:
            from alpha.orchestration.intent import classify_domain

            domain = classify_domain(objective)
            derived = {f"domain:{domain.domain}", f"complexity:{domain.confidence:.2f}"}
            return tuple(sorted(derived))
        except Exception:  # noqa: BLE001 - intent is an aid, never a gate
            return ()

    def model_tier(self, objective: str, *, role: str | None = None, complexity: str = "medium") -> dict[str, Any]:
        """Which model tier to spend on this step, with the router's own reason."""
        try:
            from alpha.models.workforce_router import get_workforce_model_router

            decision = get_workforce_model_router().route_model(
                task_category="", bot_role=role, complexity=complexity
            )
            return decision.to_dict()
        except Exception as exc:  # noqa: BLE001
            return {"tier": "unknown", "reasoning": f"model router unavailable: {type(exc).__name__}: {exc}"}

    # -- ceilings, ENFORCED not merely configured -------------------------
    def context_for(self, task_id: str) -> DelegationContext:
        ctx = self._contexts.get(task_id)
        if ctx is None:
            ctx = root_context(self.root, task_id, limits=self.limits)
            self._contexts[task_id] = ctx
        return ctx

    def tree_for(self, task_id: str) -> DelegationTree:
        tree = self._trees.get(task_id)
        if tree is None:
            tree = DelegationTree(self.context_for(task_id))
            self._trees[task_id] = tree
        return tree

    def child_context(self, task_id: str, child: str) -> DelegationContext:
        """Budgets are INHERITED, so depth cannot multiply spend."""
        parent = self.context_for(task_id)
        child_ctx = parent.for_child(child)
        self._contexts[f"{task_id}::{child}"] = child_ctx
        return child_ctx

    # -- the next step ---------------------------------------------------
    def next_step(
        self,
        task_id: str,
        objective: str,
        *,
        required_capability_tags: Sequence[str] | None = None,
        attempt: int = 1,
        max_attempts: int = 3,
        exclude: Sequence[str] | None = None,
    ) -> SupervisorDecision:
        """Decide the next step. The guard is checked BEFORE any target is named."""
        required = self.required_tags(objective, required_capability_tags)
        ctx = self.context_for(task_id)

        # 1. Ceilings first. A runaway tree is stopped, not slowed.
        guard = ctx.check()
        if not guard.allowed:
            return SupervisorDecision(
                task_id=task_id,
                step=STEP_ESCALATE if guard.code.endswith("budget") else STEP_STOP,
                reason=f"delegation bound refused the step: {guard.detail}",
                guard=guard.to_dict(),
                depth=ctx.depth,
                hops=ctx.hops,
                token_budget=ctx.token_budget,
                attempt=attempt,
                max_attempts=max_attempts,
                required_capability_tags=required,
            )

        # 2. Capability match with a recorded reason, via the existing dispatcher.
        if self.dispatcher is not None:
            decision = self.dispatcher.dispatch(
                task_id,
                objective,
                required_capability_tags=list(required) or None,
                attempt=attempt,
                max_attempts=max_attempts,
                exclude=list(exclude or []),
            )
            if decision.ok:
                return SupervisorDecision(
                    task_id=task_id,
                    step=STEP_DISPATCH,
                    reason=decision.reason,
                    target=decision.target,
                    required_capability_tags=required,
                    matched_capability_tags=decision.matched_capability_tags,
                    guard=decision.guard,
                    depth=decision.depth,
                    hops=decision.hops,
                    token_budget=ctx.token_budget,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    dispatched=True,
                )
            return SupervisorDecision(
                task_id=task_id,
                step=STEP_UNROUTABLE,
                reason=f"no capable agent: {decision.reason}",
                guard=decision.guard,
                depth=decision.depth,
                hops=decision.hops,
                attempt=attempt,
                max_attempts=max_attempts,
                required_capability_tags=required,
            )

        # 3. No dispatcher wired: still refuse rather than guess by name.
        return SupervisorDecision(
            task_id=task_id,
            step=STEP_UNROUTABLE,
            reason=(
                "no capability dispatcher is wired into this supervisor; refusing to select a "
                "target by name, because selection by department or by name is how mismatched "
                "agents get tasks they cannot do"
            ),
            required_capability_tags=required,
            depth=ctx.depth,
            hops=ctx.hops,
            attempt=attempt,
            max_attempts=max_attempts,
        )

    def on_failure(
        self,
        task_id: str,
        failed_agent: str,
        error: str,
        *,
        objective: str = "",
        attempt: int = 1,
        max_attempts: int = 3,
        reason: str | None = None,
    ) -> SupervisorDecision:
        """Decide what a failure means, using the EXISTING taxonomy unmodified.

        The distinction that matters: "this agent cannot do this" routes
        elsewhere, "this agent timed out" retries in place. Both come from
        :func:`alpha.bots.failure_reasons.decide_failure`.

        ``reason`` accepts an EXPLICIT taxonomy code. That matters because
        :func:`classify_work_failure` is a text classifier: a caller that knows
        the precise code (the transport assigned ``delivery_timeout``, say) can
        pass it and avoid a guess. Without it, the text is classified, and an
        unrecognised string falls through to ``unknown`` -> ``permanent``. It
        never guesses transient, because guessing transient turns an unknown
        fault into an automatic retry.
        """
        resolved = reason if reason in ALL_REASONS else classify_work_failure(error)
        klass = failure_class(resolved)
        verdict = decide_failure(resolved, attempt=attempt, max_attempts=max_attempts)
        detail = (
            f"{verdict.detail} (reason={resolved}, class={klass}"
            + ("" if reason in ALL_REASONS else f", classified from {error!r}")
            + ")"
        )

        if verdict.action == ACTION_RETRY:
            step = STEP_RETRY_IN_PLACE
        elif verdict.action == ACTION_RESUME:
            step = STEP_RETRY_IN_PLACE
        elif verdict.action == ACTION_REASSIGN:
            step = STEP_RESCOPE
        elif verdict.action == ACTION_ESCALATE:
            step = STEP_ESCALATE
        else:
            step = STEP_STOP
        return SupervisorDecision(
            task_id=task_id,
            step=step,
            reason=detail,
            target=failed_agent if step == STEP_RETRY_IN_PLACE else None,
            reason_class=klass,
            failure_reason=resolved,
            attempt=attempt,
            max_attempts=max_attempts,
        )

    # -- the two invariants ----------------------------------------------
    def record_child(
        self,
        task_id: str,
        child: str,
        outcome: Any,
        *,
        error: str | None = None,
        attempt: int = 1,
        tokens: int = 0,
        cost_usd: float = 0.0,
        descendants: DelegationTree | None = None,
    ) -> ChildReport:
        """Admit a child result, charging the inherited budget.

        The report comes from the existing ``child_report`` helper, which is the
        single place a failure is prevented from reading as success.

        ``descendants`` is the child's OWN sub-tree, not this task's tree.
        Passing the parent tree here would be a category error: the helper
        reads it to ask "did any of this child's children fail?", and handing it
        the parent tree makes every child look unsettled.
        """
        tree = self.tree_for(task_id)
        ctx = tree.child_context(child)
        report = child_report(
            child_id=f"{task_id}::{child}",
            agent=child,
            outcome=outcome,
            error=error,
            tokens=tokens,
            cost_usd=cost_usd,
            attempt=attempt,
            descendants=descendants,
        )
        return tree.admit(ctx, report)

    def settle(self, task_id: str) -> SettleVerdict:
        """May this task be reported complete? Checked independently of the helper."""
        tree = self.tree_for(task_id)
        aggregate = tree.aggregate_status()
        unresolved = tree.unresolved()
        open_children = [c for c in tree.children if not c.status.is_terminal]

        if aggregate is TreeStatus.FAILED:
            return SettleVerdict(
                complete=False,
                status=str(aggregate),
                reason=f"a descendant failed: {tree.failure_detail}",
                open_descendants=len(open_children),
                unresolved_descendants=len(unresolved),
                failed_children=tuple(
                    c.agent for c in tree.children if c.status is ChildStatus.FAILED
                ),
            )
        if aggregate is TreeStatus.UNRESOLVED:
            # An unknown outcome is NOT a success and NOT a clean failure.
            return SettleVerdict(
                complete=False,
                status=str(aggregate),
                reason=f"{len(unresolved)} descendant(s) unresolved; an unknown outcome cannot be reported as success",
                open_descendants=len(open_children),
                unresolved_descendants=len(unresolved),
            )
        if aggregate is TreeStatus.PENDING or open_children:
            return SettleVerdict(
                complete=False,
                status=str(aggregate),
                reason=f"{len(open_children)} descendant(s) still running; a task is not complete while work is in flight",
                open_descendants=len(open_children),
                unresolved_descendants=len(unresolved),
            )
        # Belt and braces: re-verify each report rather than trusting the enum.
        for child in tree.children:
            # ``ChildReport`` carries ``detail``, not ``error``. The check is
            # written against the field that actually exists, so the guard
            # cannot itself raise AttributeError and be mistaken for a verdict.
            detail = (child.detail or "").strip()
            if child.status is ChildStatus.COMPLETED and (child.result is None or detail):
                return SettleVerdict(
                    complete=False,
                    status="inconsistent",
                    reason=(
                        f"@{child.agent} is marked completed but carries an error or an empty "
                        f"result; refusing to report success over a contradictory receipt"
                    ),
                    failed_children=(child.agent,),
                )
        return SettleVerdict(
            complete=True,
            status=str(aggregate),
            reason=f"all {len(tree.children)} descendant(s) settled successfully",
        )

    def forget(self, task_id: str) -> None:
        self._trees.pop(task_id, None)
        self._contexts.pop(task_id, None)
        for key in [k for k in self._contexts if k.startswith(f"{task_id}::")]:
            self._contexts.pop(key, None)
        self.loop_detector.forget(task_id)
