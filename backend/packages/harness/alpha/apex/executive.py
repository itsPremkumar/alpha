"""The APEX executive cycle: spec §63/§64, as a bounded, observable pass.

This is the layer the repository was actually missing. Everything else in the
spec — the contract, the policies, the workflow engine, the swarm, the
verifiers, the leases — existed. What did not exist is anything that
**sequences** them: ``alpha.orchestration.autopilot.ExecutiveAutopilot`` is a
regex intent classifier that returns a preset name, the mission goal loop is a
per-turn continuation gated by a judge, and the DWE and the swarm each own their
own scheduler. Nothing read the contract and chose a next action.

**Two properties define this module.**

1. **It decides, it does not execute.** One cycle selects the next action and
   *records the decision*; a host adapter performs it. That is spec §2.1
   ("Executive, not executor") and it is also the only way to keep APEX from
   becoming a second lifecycle owner. The Gateway host adapter starts and
   observes runs through ``RunManager``; this cycle never creates a run, never
   cancels one, and never touches a sandbox.

2. **Every decision is attributable and every refusal is a real refusal.**
   ``ExecutiveDecision`` carries the reason codes that produced it, and a step
   that cannot proceed returns ``blocked`` with the actual blocker rather than
   proceeding optimistically. A cycle that cannot read its state refuses to
   decide, which is why the first step is a load that can fail.

The cycle is deliberately **model-free**. Spec §177 asks for a debug mode showing
"why next action selected"; a deterministic substrate is what makes that answer
reproducible, and model-driven planning enters through the adapters the host
supplies, not from inside the cycle.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.apex.contract import AutonomyContract
from alpha.apex.store import ApexSession, ApexSessionState, ApexStore

logger = logging.getLogger(__name__)

__all__ = [
    "CycleStep",
    "ExecutiveDecision",
    "ExecutiveResult",
    "NextAction",
    "run_cycle",
    "select_next_action",
]


class NextAction(StrEnum):
    """What the cycle decided to do.

    ``NONE`` is the terminal answer for a cycle: either the work is done or it is
    blocked, and both are real outcomes. A cycle that always returns an action
    would be a loop with no exit.
    """

    NONE = "none"
    CREATE_MISSION = "create_mission"
    DELEGATE = "delegate"
    AWAIT_VERIFICATION = "await_verification"
    PLAN = "plan"
    DISPATCH = "dispatch"
    RECOVER = "recover"
    REPLAN = "replan"
    REPORT = "report"


#: Why the cycle chose what it chose. Recorded verbatim in the decision ledger
#: (spec §54) so "why did it do that" is answerable after the fact.
REASON_PROFILE_OFF = "profile_off"
REASON_NO_SESSION = "no_session"
REASON_FLEET_STOPPED = "fleet_stopped"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_ACCEPTED = "acceptance_passed"
REASON_ACCEPTANCE_PENDING = "acceptance_pending"
REASON_ACCEPTANCE_FAILED = "acceptance_failed"
REASON_REPLAN_LIMIT = "replan_limit_exhausted"
REASON_STALLED = "no_progress"
REASON_BLOCKED = "blocked"
REASON_IN_PROGRESS = "work_in_progress"
REASON_SESSION_PAUSED = "session_paused"
REASON_AWAITING_APPROVAL = "awaiting_operator_approval"
REASON_POLICY_DRIFT = "policy_drift"


@dataclass(frozen=True, slots=True)
class CycleStep:
    """One evaluated step of the cycle, for the debug view (spec §177)."""

    name: str
    outcome: str
    detail: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 2),
        }


@dataclass
class ExecutiveDecision:
    """The cycle's answer: what to do, and why."""

    action: NextAction
    reason: str
    confidence: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    #: A refusal is recorded as an action too, so a blocked cycle is visible.
    blocked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "detail": self.detail,
            "blocked": self.blocked,
        }


@dataclass
class ExecutiveResult:
    """One full cycle: the steps, the decision, and whether anything changed."""

    session_id: str
    decision: ExecutiveDecision
    steps: list[CycleStep] = field(default_factory=list)
    state_before: str = ""
    state_after: str = ""
    changed: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "decision": self.decision.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "state_before": self.state_before,
            "state_after": self.state_after,
            "changed": self.changed,
            "duration_ms": round(self.duration_ms, 2),
        }


def _fleet_stopped() -> tuple[bool, str]:
    """Is fleet control refusing work right now?

    Delegates to ``alpha.runtime.control``, which is the emergency stop's only
    home. The supervisor already gates its loops through the same call; this
    mirrors it rather than inventing a second check, so an operator who engaged
    ESTOP gets the same answer from both paths.
    """
    try:
        from alpha.runtime.control import read_state
        from alpha.runtime.estop import get_estop_manager

        if get_estop_manager().is_engaged():
            return True, "estop_sentinel"
        state = read_state()
        if state.mode.value != "run":
            return True, f"fleet_control:{state.mode.value}"
        return False, ""
    except Exception as exc:
        # Fail closed: a stop mechanism that cannot read its state must stop.
        return True, f"control_unreadable:{type(exc).__name__}"


def select_next_action(
    *,
    contract: AutonomyContract,
    session: ApexSession | None,
    store: ApexStore,
    usage_provider: Callable[[ApexSession], dict[str, Any]] | None = None,
) -> ExecutiveDecision:
    """Choose the next action for one session. Pure with respect to the store.

    Split out from :func:`run_cycle` so the decision is unit-testable without a
    store, and so a host can ask "what would you do next" without acting on it.
    """
    if not contract.enabled:
        return ExecutiveDecision(action=NextAction.NONE, reason=REASON_PROFILE_OFF, confidence=1.0)

    if session is None:
        return ExecutiveDecision(
            action=NextAction.NONE,
            reason=REASON_NO_SESSION,
            confidence=1.0,
            blocked=True,
            detail={"note": "no session row was resolved; the cycle will not decide without one"},
        )

    if session.is_terminal:
        return ExecutiveDecision(
            action=NextAction.NONE,
            reason=REASON_ACCEPTED if session.state is ApexSessionState.COMPLETED else REASON_BLOCKED,
            confidence=1.0,
            detail={"state": session.state.value},
        )

    # A paused session decides nothing. Pause is the operator's act
    # (spec §30), and the only exit is the matching resume — a cycle
    # that decided through a pause would make the pause advisory.
    if session.state is ApexSessionState.PAUSED:
        return ExecutiveDecision(
            action=NextAction.NONE,
            reason=REASON_SESSION_PAUSED,
            confidence=1.0,
            blocked=True,
            detail={"note": "paused by operator; /apex resume or POST /api/apex/resume clears it"},
        )

    # A parked session waits for an operator, not for another cycle.
    # This is the approval gate: the executive parked itself here
    # (see run_cycle's blocked path), and autonomy cannot un-park
    # itself — an approval, a replan, or a stop is required.
    if session.state is ApexSessionState.BLOCKED:
        return ExecutiveDecision(
            action=NextAction.NONE,
            reason=REASON_AWAITING_APPROVAL,
            confidence=1.0,
            blocked=True,
            detail={"note": "parked; POST /api/apex/approvals/{id}/approve, /apex replan or /apex stop moves it"},
        )

    stopped, why = _fleet_stopped()
    if stopped:
        return ExecutiveDecision(
            action=NextAction.NONE,
            reason=REASON_FLEET_STOPPED,
            confidence=1.0,
            blocked=True,
            detail={"source": why},
        )

    usage = usage_provider(session) if usage_provider else None
    if usage:
        calls = usage.get("tool_calls")
        limit = contract.budget.max_tool_calls
        if isinstance(calls, int) and limit is not None and calls >= limit:
            return ExecutiveDecision(
                action=NextAction.NONE,
                reason=REASON_BUDGET_EXHAUSTED,
                confidence=1.0,
                blocked=True,
                detail={"tool_calls": calls, "max_tool_calls": limit},
            )

    # A measured report outranks every other consideration. Merely declaring
    # criteria cannot mean "verify now": a new goal has no evidence before its
    # first action. The host adapter writes an unverified report only once work
    # reaches its verification boundary.
    if session.acceptance_criteria:
        try:
            from alpha.mission.acceptance import (
                AcceptanceReport,
                assert_acceptance_passed,
            )

            stored = getattr(session, "acceptance", None)
            report = AcceptanceReport.from_dict(stored) if isinstance(stored, dict) else None
            if report is not None and report.criteria:
                declared = [str(criterion) for criterion in session.acceptance_criteria]
                reported = [criterion.criterion for criterion in report.criteria]
                declared_counts = Counter(declared)
                reported_counts = Counter(reported)
                missing = sorted((declared_counts - reported_counts).elements())
                unexpected = sorted((reported_counts - declared_counts).elements())
                duplicated = sorted(criterion for criterion, count in reported_counts.items() if count > 1)
                if any(count > 1 for count in declared_counts.values()) or missing or unexpected or duplicated:
                    refusal = "acceptance report must cover every declared criterion exactly once"
                    if missing:
                        refusal += f"; missing: {', '.join(missing)}"
                    if unexpected:
                        refusal += f"; undeclared: {', '.join(unexpected)}"
                    if duplicated:
                        refusal += f"; duplicated: {', '.join(duplicated)}"
                    return ExecutiveDecision(
                        action=NextAction.AWAIT_VERIFICATION,
                        reason=REASON_ACCEPTANCE_PENDING,
                        confidence=1.0,
                        detail={"refusal": refusal},
                        blocked=True,
                    )
                try:
                    assert_acceptance_passed(report)
                except Exception as exc:
                    reason = str(exc)
                    failed = "did not hold" in reason
                    replan_limit = contract.budget.max_replans
                    replans_used = max(0, int(session.usage.replans or 0))
                    if failed and replan_limit is not None and replans_used >= replan_limit:
                        return ExecutiveDecision(
                            # REPLAN names the recovery path the executive
                            # would take, while `blocked` ensures the shared
                            # approval gate parks it instead of dispatching.
                            action=NextAction.REPLAN,
                            reason=REASON_REPLAN_LIMIT,
                            confidence=1.0,
                            detail={"replans_used": replans_used, "max_replans": replan_limit, "failure": reason},
                            blocked=True,
                        )
                    return ExecutiveDecision(
                        action=NextAction.RECOVER if failed else NextAction.AWAIT_VERIFICATION,
                        reason=REASON_ACCEPTANCE_FAILED if failed else REASON_ACCEPTANCE_PENDING,
                        confidence=1.0,
                        detail={"refusal": reason},
                        blocked=not failed,
                    )
                return ExecutiveDecision(action=NextAction.REPORT, reason=REASON_ACCEPTED, confidence=1.0)
        except ImportError:
            # The acceptance module is the one site that may not be absent. If it
            # is, refusing is strictly safer than deciding.
            logger.error("alpha.mission.acceptance is unavailable; APEX will not decide")
            return ExecutiveDecision(
                action=NextAction.NONE,
                reason=REASON_BLOCKED,
                confidence=0.0,
                blocked=True,
                detail={"note": "acceptance module unavailable"},
            )

    if not session.mission_id:
        return ExecutiveDecision(action=NextAction.CREATE_MISSION, reason="no_mission", confidence=1.0)

    if session.state is ApexSessionState.ACTIVE:
        return ExecutiveDecision(
            action=NextAction.DISPATCH,
            reason=REASON_IN_PROGRESS,
            confidence=0.8,
            detail={"mission_id": session.mission_id},
        )

    return ExecutiveDecision(action=NextAction.PLAN, reason="awaiting_plan", confidence=0.6)


def run_cycle(
    store: ApexStore,
    session_id: str,
    contract: AutonomyContract,
    *,
    usage_provider: Callable[[ApexSession], dict[str, Any]] | None = None,
) -> ExecutiveResult:
    """Run one bounded executive cycle for a session.

    The cycle records its decision and, where the decision implies a state
    change, applies it. It performs **no** external work: creating a mission,
    dispatching a task and running a verification are all host adapters, because
    those are exactly the operations whose owners must not be duplicated.
    """
    started = time.perf_counter()
    steps: list[CycleStep] = []

    def step(name: str, fn: Callable[[], tuple[str, str]]) -> Any:
        step_started = time.perf_counter()
        try:
            outcome, detail = fn()
        except Exception as exc:
            outcome, detail = "error", f"{type(exc).__name__}: {exc}"
            logger.warning("APEX cycle step %s failed for %s: %s", name, session_id, detail)
        steps.append(
            CycleStep(
                name=name,
                outcome=outcome,
                detail=detail,
                duration_ms=(time.perf_counter() - step_started) * 1000.0,
            )
        )
        return outcome, detail

    session_holder: dict[str, ApexSession | None] = {}

    def _load() -> tuple[str, str]:
        session = store.get(session_id)
        session_holder["session"] = session
        return ("loaded" if session is not None else "absent"), (session.objective[:80] if session else "")

    def _policy() -> tuple[str, str]:
        session = session_holder.get("session")
        if session is None:
            return "skipped", "no session"
        from alpha.apex.contract import contract_digest_matches

        if session.contract_digest and not contract_digest_matches(contract, session.contract_digest):
            # Spec §163: policy drift must be visible, not silently adopted.
            return "drift", f"session policy {session.contract_digest} != active contract {contract.digest()}"
        return "ok", session.contract_digest or "(unsigned)"

    step("load_session", _load)
    policy_outcome, policy_detail = step("check_policy", _policy)

    session = session_holder.get("session")
    state_before = session.state.value if session is not None else ""
    if policy_outcome == "drift":
        # A session's frozen contract is part of its authority boundary. A new
        # profile or mission contract must not silently take over an existing
        # session, even when the newly selected action would otherwise be safe.
        decision = ExecutiveDecision(
            action=NextAction.NONE,
            reason=REASON_POLICY_DRIFT,
            confidence=1.0,
            detail={"refusal": policy_detail},
            blocked=True,
        )
    elif policy_outcome == "error":
        # The frozen contract is an authority boundary. If it cannot be
        # checked, selecting work with the caller's current contract would
        # silently widen or replace the session's policy.
        decision = ExecutiveDecision(
            action=NextAction.NONE,
            reason=REASON_POLICY_DRIFT,
            confidence=1.0,
            detail={"refusal": "stored session policy could not be checked", "error": policy_detail},
            blocked=True,
        )
    else:
        try:
            decision = select_next_action(contract=contract, session=session, store=store, usage_provider=usage_provider)
        except Exception as exc:
            # Status/usage providers are host adapters. Their failure must not
            # take down the supervisor tick or be mistaken for permission to
            # continue without the observation they were asked to supply.
            detail = f"{type(exc).__name__}: {exc}"
            logger.warning("APEX decision selection failed for %s: %s", session_id, detail)
            decision = ExecutiveDecision(
                action=NextAction.NONE,
                reason=REASON_BLOCKED,
                confidence=1.0,
                detail={"refusal": "decision inputs could not be read", "error": detail},
                blocked=True,
            )
            steps.append(CycleStep(name="select_decision", outcome="error", detail=detail))

    def _apply() -> tuple[str, str]:
        if session is None:
            return "noop", decision.reason
        if decision.blocked:
            # A blocked decision parks the session and asks an operator
            # to decide (spec §24/§27/§30). Parking — rather than
            # writing ``blocked_reason`` onto a session that keeps
            # running — is what makes the block observable in state and
            # the ask a real approval record. Autonomy cannot un-park
            # itself: the exits are an approval, a replan, or a stop.
            store.set_state(session_id, ApexSessionState.BLOCKED, reason=decision.reason)
            if decision.reason not in {REASON_REPLAN_LIMIT, REASON_POLICY_DRIFT}:
                store.request_approval(session_id, note=decision.reason, requester="apex.executive")
            store.emit(session_id, "cycle.blocked", decision=decision.to_dict())
            return "blocked", decision.reason
        if decision.action is NextAction.NONE:
            return "noop", decision.reason
        if decision.action is NextAction.REPORT:
            store.set_state(session_id, ApexSessionState.COMPLETED, reason="acceptance passed")
            return "completed", "acceptance passed"
        if decision.action is NextAction.RECOVER:
            if decision.reason == REASON_ACCEPTANCE_FAILED:
                recovered = store.recover_after_acceptance_failure(session_id, reason=decision.reason)
                if recovered is None:
                    return "refused", "session became terminal before recovery was committed"
                return "recovery_queued", decision.reason
            store.set_state(session_id, ApexSessionState.ACTIVE, reason=decision.reason)
            return "resumed", decision.reason
        if decision.action is NextAction.AWAIT_VERIFICATION:
            store.emit(session_id, "cycle.awaiting_verification", decision=decision.to_dict())
            return "awaiting", decision.reason
        # This cycle only chose and journaled an action. The Gateway's host
        # adapter owns execution; calling this event ``dispatched`` claimed a
        # run/tool had started when this module has no such capability.
        store.emit(session_id, "cycle.decision_recorded", decision=decision.to_dict())
        return "recorded", decision.action.value

    step("apply_decision", _apply)

    def _checkpoint() -> tuple[str, str]:
        # Both numbers a reader sees here have to be measurements. This used
        # to be ``lambda: ("recorded", f"cycle {len(steps)}")``, where
        # ``len(steps)`` is how many probes ran *in this pass* — a first cycle
        # reported "cycle 3" while the API's ``cycle_count`` stayed 0 forever,
        # because nothing anywhere incremented it. One is the step count
        # wearing a cycle's name; the other is a measured zero.
        current = store.get(session_id)
        if current is None:
            return "absent", "session row is gone"
        if decision.action is NextAction.NONE:
            # A cycle that decided to do nothing writes nothing. Parking is
            # reached from here (paused, blocked on an approval, profile off,
            # no session), and "a parked session's repeated cycles change
            # nothing" is the invariant those paths are tested against: an
            # unconditional increment bumped ``updated_at`` on every pass
            # while the decision — and the state it left behind — stayed
            # identical. The count measures cycles that decided something.
            return "skipped", "no decision to record"
        updated = store.update(session_id, cycle_count=current.cycle_count + 1)
        if updated is None:
            return "absent", "session row is gone"
        return "recorded", f"cycle {updated.cycle_count}"

    step("checkpoint", _checkpoint)

    updated = store.get(session_id)
    state_after = updated.state.value if updated is not None else ""
    return ExecutiveResult(
        session_id=session_id,
        decision=decision,
        steps=steps,
        state_before=state_before,
        state_after=state_after,
        changed=state_after != state_before,
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )
