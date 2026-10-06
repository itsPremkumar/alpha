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
   becoming a second lifecycle owner. The cycle calls into ``RunManager`` and
   the mission lifecycle — it never creates a run, never cancels one, never
   touches a sandbox.

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
REASON_STALLED = "no_progress"
REASON_BLOCKED = "blocked"
REASON_IN_PROGRESS = "work_in_progress"


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
        if isinstance(calls, int) and limit and calls >= limit:
            return ExecutiveDecision(
                action=NextAction.NONE,
                reason=REASON_BUDGET_EXHAUSTED,
                confidence=1.0,
                blocked=True,
                detail={"tool_calls": calls, "max_tool_calls": limit},
            )

    # Acceptance outranks every other consideration: a session whose criteria all
    # hold is done, and one whose criteria failed needs recovery, not more work.
    if session.acceptance_criteria:
        try:
            from alpha.mission.acceptance import AcceptanceReport, assert_acceptance_passed

            stored = getattr(session, "acceptance", None)
            report = AcceptanceReport.from_dict(stored) if isinstance(stored, dict) else None
            try:
                assert_acceptance_passed(report)
            except Exception as exc:
                reason = str(exc)
                failed = "did not hold" in reason
                return ExecutiveDecision(
                    action=NextAction.RECOVER if failed else NextAction.AWAIT_VERIFICATION,
                    reason=REASON_ACCEPTANCE_FAILED if failed else REASON_ACCEPTANCE_PENDING,
                    confidence=1.0,
                    detail={"refusal": reason},
                    blocked=not failed,
                )
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

        return ExecutiveDecision(action=NextAction.REPORT, reason=REASON_ACCEPTED, confidence=1.0)

    if not session.mission_id:
        return ExecutiveDecision(action=NextAction.CREATE_MISSION, reason="no_mission", confidence=1.0)

    if session.state is ApexSessionState.ACTIVE:
        return ExecutiveDecision(
            action=NextAction.DISPATCH,
            reason=REASON_IN_PROGRESS,
            confidence=0.8,
            detail={"mission_id": session.mission_id},
        )

    if session.state is ApexSessionState.BLOCKED:
        return ExecutiveDecision(
            action=NextAction.RECOVER,
            reason=REASON_BLOCKED,
            confidence=0.5,
            detail={"blocked_reason": session.blocked_reason},
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
        if contract.mission_id and session.contract_digest and session.contract_digest != contract.digest():
            # Spec §163: policy drift must be visible, not silently adopted.
            return "drift", f"session policy {session.contract_digest} != active contract {contract.digest()}"
        return "ok", session.contract_digest or "(unsigned)"

    step("load_session", _load)
    step("check_policy", _policy)

    session = session_holder.get("session")
    state_before = session.state.value if session is not None else ""
    decision = select_next_action(contract=contract, session=session, store=store, usage_provider=usage_provider)

    def _apply() -> tuple[str, str]:
        if session is None or decision.action is NextAction.NONE:
            return "noop", decision.reason
        if decision.blocked:
            store.update(session_id, blocked_reason=decision.reason)
            store.emit(session_id, "cycle.blocked", decision=decision.to_dict())
            return "blocked", decision.reason
        if decision.action is NextAction.REPORT:
            store.set_state(session_id, ApexSessionState.COMPLETED, reason="acceptance passed")
            return "completed", "acceptance passed"
        if decision.action is NextAction.RECOVER:
            store.set_state(session_id, ApexSessionState.ACTIVE, reason=decision.reason)
            return "resumed", decision.reason
        if decision.action is NextAction.AWAIT_VERIFICATION:
            store.emit(session_id, "cycle.awaiting_verification", decision=decision.to_dict())
            return "awaiting", decision.reason
        store.emit(session_id, "cycle.dispatched", action=decision.action.value, reason=decision.reason)
        return "dispatched", decision.action.value

    step("apply_decision", _apply)
    step("checkpoint", lambda: ("recorded", f"cycle {len(steps)}"))

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
