"""Failure-aware reassignment: the existing taxonomy, actually used to move work.

The gap this closes
-------------------
:mod:`alpha.bots.failure_reasons` already answers the question that reassignment
needs: *transient* (retry), *crash* (resume), *capability* (hand to a capable
successor), *exhausted* (a human owns it), *permanent* (a human owns it), plus
``decide_failure``'s authoritative attempt ceiling. It was consulted by bot DMs
and recovery policies. Reassignment was not among them: the swarm classified a
failure with a substring check for ``"429"`` and batches stored free text, so a
reassignment loop could not tell "this agent cannot do this task" from "this
agent timed out" and retried both blindly.

This module imports that taxonomy and does **not** redefine it. Nothing here
edits :mod:`alpha.bots.failure_reasons`; if the taxonomy genuinely needs a new
code, that is a change to *that* file and it is reported, not smuggled in here.

The routing rule
----------------
=========================  ==========================================
failure class              what happens to the work
=========================  ==========================================
``transient``              retry IN PLACE - same agent, next attempt
``crash``                  resume IN PLACE - same agent, fresh worker
``capability``             REASSIGN - a different, capable agent
``permanent``              escalate - a retry cannot fix it
``exhausted``              escalate - the attempt ceiling is spent
``cancelled``              stop
``routed``                 nothing - a deliberate transfer, not a fault
=========================  ==========================================

A transient failure retrying in place and a capability gap routing elsewhere is
the whole point: retrying a capability gap in place burns the attempt ceiling
and then escalates, having produced nothing, while reassigning a timeout hands a
perfectly good task to a different agent for no reason.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.bots.capability_dispatch import (
    DISPATCH_DOMAIN,
    DISPATCH_METHOD,
    CapabilityDispatcher,
    DispatchDecision,
)
from alpha.bots.failure_reasons import (
    ACTION_ESCALATE,
    ACTION_NONE,
    ACTION_REASSIGN,
    ACTION_RESUME,
    ACTION_RETRY,
    ACTION_STOP,
    classify_work_failure,
    decide_failure,
    failure_class,
)
from alpha.bots.registry import BotRegistry

logger = logging.getLogger(__name__)

#: Which agents a transient/crash failure is retried on. ``"in_place"`` means
#: the SAME agent, on purpose: the work did not outgrow it.
RETRY_SCOPE_IN_PLACE = "in_place"
#: Capability gaps are never retried in place; they always move.
REASSIGN_SCOPE_ELSEWHERE = "elsewhere"


class ReassignmentAction(StrEnum):
    """The leader's decision about what happens to a failed task."""

    RETRY_IN_PLACE = "retry_in_place"
    RESUME_IN_PLACE = "resume_in_place"
    REASSIGN = "reassign"
    ESCALATE = "escalate"
    STOP = "stop"
    NOTHING = "nothing"
    UNROUTABLE = "unroutable"

    @property
    def moves_work(self) -> bool:
        """True when this action hands the task to a DIFFERENT agent."""
        return self is ReassignmentAction.REASSIGN


@dataclass
class ReassignmentOutcome:
    """What the leader decided, why, and where the work went."""

    task_id: str
    action: ReassignmentAction
    reason: str
    reason_class: str
    failed_agent: str
    target: str | None = None
    scope: str = ""
    attempt: int = 1
    max_attempts: int = 3
    detail: str = ""
    decision: DispatchDecision | None = None
    escalation: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def retried_in_place(self) -> bool:
        return self.action in (ReassignmentAction.RETRY_IN_PLACE, ReassignmentAction.RESUME_IN_PLACE)

    @property
    def reassigned(self) -> bool:
        return self.action is ReassignmentAction.REASSIGN and bool(self.target) and self.target != self.failed_agent

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "action": str(self.action),
            "reason": self.reason,
            "reason_class": self.reason_class,
            "failed_agent": self.failed_agent,
            "target": self.target,
            "scope": self.scope,
            "retried_in_place": self.retried_in_place,
            "reassigned": self.reassigned,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "detail": self.detail,
            "decision": self.decision.to_dict() if self.decision is not None else None,
            "escalation": self.escalation,
            "history": [dict(item) for item in self.history],
        }


def _record(
    *,
    task_id: str,
    from_ref: str,
    to_ref: str,
    reason: str,
    attempt: int,
    max_attempts: int,
    details: dict[str, Any] | None = None,
) -> int | None:
    from alpha.bots.capability_dispatch import _record_ledger

    return _record_ledger(
        task_id=task_id,
        from_ref=from_ref,
        to_ref=to_ref,
        reason=reason,
        attempt=attempt,
        max_attempts=max_attempts,
        details=details,
    )


def reassign_after_failure(
    task_id: str,
    failed_agent: str,
    error: str,
    *,
    objective: str = "",
    required_capability_tags: Sequence[str] | None = None,
    dispatcher: CapabilityDispatcher,
    attempt: int = 1,
    max_attempts: int = 3,
    registry: BotRegistry | None = None,
    exclude: Sequence[str] | None = None,
) -> ReassignmentOutcome:
    """Classify a failed attempt and route the work accordingly.

    ``error`` is free text from whatever ran; the closed vocabulary in
    :mod:`alpha.bots.failure_reasons` turns it into a code, and the code decides
    the route. Callers that already hold a code should pass it as ``error`` —
    :func:`classify_work_failure` passes a known code straight through.
    """
    dispatcher = dispatcher
    reg = registry or dispatcher.registry
    failed_key = (failed_agent or "").strip().lower()
    reason = classify_work_failure(error)
    klass = failure_class(reason)
    verdict = decide_failure(reason, attempt=attempt, max_attempts=max_attempts)
    issuer = dispatcher.context.node_id

    def _finish(action: ReassignmentAction, scope: str, detail: str, **extra: Any) -> ReassignmentOutcome:
        outcome = ReassignmentOutcome(
            task_id=task_id,
            action=action,
            reason=verdict.reason,
            reason_class=verdict.reason_class,
            failed_agent=failed_key,
            target=extra.get("target"),
            scope=scope,
            attempt=attempt,
            max_attempts=max_attempts,
            detail=detail,
            decision=extra.get("decision"),
            escalation=extra.get("escalation"),
        )
        _record(
            task_id=task_id,
            from_ref=failed_key,
            to_ref=str(outcome.target or "(none)"),
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            details={
                "action": str(action),
                "reason_class": klass,
                "scope": scope,
                "detail": detail[:1000],
                "decided_by": issuer,
                "method": DISPATCH_METHOD,
            },
        )
        return outcome

    # A deliberate transfer is not a fault: nothing to retry, nothing to escalate.
    if verdict.action == ACTION_NONE:
        return _finish(ReassignmentAction.NOTHING, "", verdict.detail)

    if verdict.action == ACTION_STOP:
        return _finish(ReassignmentAction.STOP, "", verdict.detail)

    # Transient / crash: the SAME agent may get through next time. Retrying a
    # timeout somewhere else just moves a good task to an unlucky agent.
    if verdict.action in (ACTION_RETRY, ACTION_RESUME):
        action = ReassignmentAction.RETRY_IN_PLACE if verdict.action == ACTION_RETRY else ReassignmentAction.RESUME_IN_PLACE
        if failed_key and reg.get_bot(failed_key) is not None:
            return _finish(action, RETRY_SCOPE_IN_PLACE, f"{verdict.detail}; @failed is still eligible for this work", target=failed_key)
        # The agent is gone from the roster, so "in place" is impossible; move on
        # rather than pretend a retry happened.
        decision = _reassign_elsewhere(
            dispatcher,
            task_id,
            objective,
            required_capability_tags,
            attempt=attempt,
            max_attempts=max_attempts,
            exclude=[*(exclude or []), failed_key],
        )
        if decision.ok:
            return _finish(ReassignmentAction.REASSIGN, REASSIGN_SCOPE_ELSEWHERE, f"{failed_key} left the roster; work moved to a capable agent", target=decision.target, decision=decision)
        return _finish(ReassignmentAction.UNROUTABLE, REASSIGN_SCOPE_ELSEWHERE, f"{verdict.detail}; no capable successor: {decision.reason}", decision=decision)

    # The attempt ceiling is authoritative and outranks the class.
    if verdict.action == ACTION_ESCALATE:
        escalation = _escalate(task_id=task_id, agent=failed_key, reason=verdict.reason, detail=f"{failed_key} failed '{task_id}' ({klass}): {error}"[:2000], attempt=attempt, max_attempts=max_attempts)
        return _finish(ReassignmentAction.ESCALATE, "", f"{verdict.detail}; escalated to a human", escalation=escalation)

    # Capability gap: this agent cannot do this work, so a retry here is waste.
    # Re-select a DIFFERENT capable agent; the failed one is excluded, which also
    # feeds the cycle guard (a task must not ping-pong back to its last owner).
    if verdict.action == ACTION_REASSIGN:
        decision = _reassign_elsewhere(
            dispatcher,
            task_id,
            objective,
            required_capability_tags,
            attempt=attempt,
            max_attempts=max_attempts,
            exclude=[*(exclude or []), failed_key],
        )
        if decision.ok and decision.target != failed_key:
            return _finish(ReassignmentAction.REASSIGN, REASSIGN_SCOPE_ELSEWHERE, f"{verdict.detail}; @{failed_key} cannot do this work, routed to @{decision.target}", target=decision.target, decision=decision)
        return _finish(ReassignmentAction.UNROUTABLE, REASSIGN_SCOPE_ELSEWHERE, f"{verdict.detail}; no capable successor: {decision.reason}", decision=decision)

    # Unreachable in practice (decide_failure is exhaustive); fail loudly rather
    # than silently retrying, which is the behaviour this module exists to stop.
    logger.error("Unmapped failure action %r for task %s; escalating", verdict.action, task_id)
    return _finish(ReassignmentAction.ESCALATE, "", f"unmapped failure action {verdict.action!r}; escalated rather than retried blindly")


def _reassign_elsewhere(
    dispatcher: CapabilityDispatcher,
    task_id: str,
    objective: str,
    required_capability_tags: Sequence[str] | None,
    *,
    attempt: int,
    max_attempts: int,
    exclude: Sequence[str],
) -> DispatchDecision:
    """Re-run capability selection with the failed agent excluded."""
    return dispatcher.dispatch(
        task_id,
        objective,
        required_capability_tags=required_capability_tags,
        exclude=list(exclude),
        attempt=attempt,
        max_attempts=max_attempts,
    )


def _escalate(
    *,
    task_id: str,
    agent: str,
    reason: str,
    detail: str,
    attempt: int,
    max_attempts: int,
) -> dict[str, Any] | None:
    """Open a durable escalation record for work a human must own."""
    try:
        from alpha.bots.handoff import escalate_task

        return escalate_task(
            task_id,
            agent,
            reason,
            attempt=attempt,
            max_attempts=max_attempts,
        )
    except Exception:
        logger.warning("Could not escalate failed task %s", task_id, exc_info=True)
        return None


__all__ = [
    "DISPATCH_DOMAIN",
    "REASSIGN_SCOPE_ELSEWHERE",
    "RETRY_SCOPE_IN_PLACE",
    "ReassignmentAction",
    "ReassignmentOutcome",
    "reassign_after_failure",
]
