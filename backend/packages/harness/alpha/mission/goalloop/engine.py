"""The goal loop: gates, then the judge, then a plain user-role continuation.

The order below is the whole design, and it is not negotiable:

1. **No goal, or not active -> idle.** Nothing runs.
2. **Gates run first.** A red gate is a deterministic exit code, so the judge is
   *not called at all*. This is the property that stops a model from talking its
   way to ``done`` against a failing test suite: the model's opinion is never
   consulted while the evidence says no.
3. **A gate that exhausted its retries pauses the goal** with text that says
   what to do, rather than looping on a failure nothing can fix.
4. **A red-but-not-exhausted gate becomes the continuation prompt**, carrying the
   gate's exit code and output tail. The agent iterates against the real failure
   text, not a vibe.
5. **Only then does the judge run.** ``done`` ends the loop, ``blocked`` pauses
   it with a reason (an impossible goal is reported, never waved through),
   ``continue`` spends a turn, ``wait`` parks against an absolute deadline.
6. **The turn budget is the backstop.** Exhaustion pauses and tells the operator
   exactly how to continue.
7. **A user-initiated turn never produces a continuation.** The gates and the
   judge still run - so a user's message can finish the goal - but the loop
   yields to the human instead of re-poking.

Prompt-cache stability is structural rather than promised: this module has no
access to a system prompt, a tool list, or a message history. It returns
``TurnDecision.continuation`` - a plain ``str`` that the caller appends as a
user-role message - and nothing else that could perturb a cached prefix. There
is no code path here that rewrites prior context.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from alpha.mission.goalloop.gates import DEFAULT_MAX_GATE_FAILURES, GateReport, GateResult, GateRunner
from alpha.mission.goalloop.judge import GoalJudge, JudgeOutcome, build_request
from alpha.mission.goalloop.state import GoalState, GoalStatus
from alpha.mission.goalloop.verdict import GoalVerdict

logger = logging.getLogger(__name__)

#: A judge-requested park is capped so a forgotten barrier expires on its own.
DEFAULT_WAIT_SECONDS: Final[float] = 30 * 60.0

#: The role a continuation is delivered as. Hard-coded because the whole
#: prompt-cache argument depends on it never being anything else.
CONTINUATION_ROLE: Final[str] = "user"


class TurnAction(StrEnum):
    """What the loop decided at this turn boundary."""

    IDLE = "idle"
    CONTINUE = "continue"
    DONE = "done"
    BLOCKED = "blocked"
    WAIT = "wait"
    PAUSED = "paused"


#: Actions after which the loop is no longer running.
TERMINAL_ACTIONS: Final[frozenset[TurnAction]] = frozenset(
    {TurnAction.DONE, TurnAction.BLOCKED, TurnAction.PAUSED, TurnAction.IDLE},
)

BUDGET_EXHAUSTED_MESSAGE: Final[str] = (
    "Goal paused - {used}/{max_turns} turns used. "
    "Use /goal resume to keep going (the turn counter resets to zero), "
    "or /goal clear to stop."
)

GATE_EXHAUSTED_MESSAGE: Final[str] = (
    "Goal paused - quality gate exhausted its retries without exiting 0: {command}\n"
    "Fix the failure by hand, or use /goal gate remove {index} to drop the gate, "
    "then /goal resume to continue."
)


@dataclass(frozen=True)
class TurnDecision:
    """The loop's answer for one boundary.

    ``continuation`` is a plain user-role message body, or ``""`` when the loop
    has nothing to send. There is no field carrying a system message, a tool
    list, or a rewritten history, which is what makes "a continuation does not
    change the system prompt or toolset" a structural property rather than a
    promise.
    """

    action: TurnAction
    reason: str = ""
    continuation: str = ""
    verdict: JudgeOutcome | None = None
    gate_report: GateReport | None = None
    turns_used: int = 0
    budget_remaining: int = 0
    user_initiated: bool = False
    wait_until: float = 0.0

    @property
    def should_continue(self) -> bool:
        return self.action is TurnAction.CONTINUE and bool(self.continuation)

    def continuation_message(self) -> dict[str, str] | None:
        """The exact message to append, or ``None`` when there is none.

        Always ``{"role": "user", ...}``. The role is not a parameter because
        it must never be negotiable.
        """
        if not self.continuation:
            return None
        return {"role": CONTINUATION_ROLE, "content": self.continuation}

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "continuation": self.continuation,
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "gates": self.gate_report.to_dict() if self.gate_report else None,
            "turns_used": self.turns_used,
            "budget_remaining": self.budget_remaining,
            "user_initiated": self.user_initiated,
            "wait_until": self.wait_until,
        }


def build_continuation_prompt(
    state: GoalState,
    *,
    reason: str = "",
    gate_report: GateReport | None = None,
    gate_failures: dict[int, int] | None = None,
) -> str:
    """Assemble the next turn's message.

    Deliberately includes the *evidence the judge gave* ("here is why that is
    not done") and any red gate output, so the agent's next turn is aimed at the
    specific remaining work rather than at re-reading the whole objective.
    """
    blocks = ["[continuing toward your standing goal]"]
    blocks.append(f"GOAL (turn {state.turns_used + 1}/{state.max_turns}):\n{state.objective}")
    if not state.contract.is_empty:
        blocks.append(f"COMPLETION CONTRACT:\n{state.contract.render()}")
    if state.subgoals:
        numbered = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(state.subgoals))
        blocks.append(f"ADDITIONAL CRITERIA THE USER ADDED MID-LOOP (all must hold):\n{numbered}")
    if gate_report is not None and not gate_report.all_passed:
        blocks.append(
            "QUALITY GATES ARE RED. The exit codes below are deterministic evidence; "
            "the goal cannot be judged complete until they exit 0.\n"
            f"{gate_report.render_failures(attempts=gate_failures)}"
        )
    if reason:
        blocks.append(f"JUDGE: not done yet - {reason}")
    return "\n\n".join(blocks)


#: Signature of the persistence seam. The engine never assumes the store works;
#: a store that raises is logged and the loop still decides.
Saver = Callable[[GoalState], Any]


class GoalLoopEngine:
    """Drives one goal across turn boundaries.

    The engine is stateless apart from its collaborators: all mutable goal
    state lives on the :class:`GoalState` it is handed, which is what lets a
    caller swap in a different session, replay a boundary, or run two goals
    concurrently without interference.
    """

    def __init__(
        self,
        *,
        judge: GoalJudge | None = None,
        gate_runner: GateRunner | None = None,
        save: Saver | None = None,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
        max_gate_failures: int = DEFAULT_MAX_GATE_FAILURES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._judge = judge or GoalJudge()
        self._gates = gate_runner or GateRunner()
        self._save = save
        self._wait_seconds = wait_seconds
        self._max_gate_failures = max(1, int(max_gate_failures))
        self._clock = clock

    def _persist(self, state: GoalState) -> None:
        if self._save is None:
            return
        try:
            self._save(state)
        except Exception as exc:  # noqa: BLE001 - persistence failure must not wedge the loop
            logger.warning("Goal state persistence failed for %s: %s", state.goal_id, exc)

    # ------------------------------------------------------------- boundary

    async def on_turn_end(
        self,
        state: GoalState,
        response_text: str,
        *,
        user_initiated: bool = False,
    ) -> TurnDecision:
        """Run the boundary for one finished turn and decide what happens next.

        *user_initiated* marks a turn the human started. Such a turn still runs
        the gates and the judge (so a user can finish the goal), but it can never
        produce a continuation - the human preempts the loop.
        """
        if state.status is not GoalStatus.ACTIVE or not state.objective.strip():
            return self._idle(state, user_initiated=user_initiated, reason="no active goal")

        # --- 2/3/4: gates first, always re-executed, never cached ---------
        gate_report = GateReport(results=self._gates.run_all(state.gates))
        if gate_report.results:
            for position, result in enumerate(gate_report.results, start=1):
                state.record_gate_outcome(position, result.passed)

        exhausted = self._exhausted_gates(state, gate_report)
        if exhausted:
            return self._pause_on_gate_exhaustion(state, gate_report, user_initiated, exhausted)

        if not gate_report.all_passed:
            return self._continue_on_red_gate(
                state,
                gate_report,
                user_initiated=user_initiated,
                reason="a quality gate did not exit 0",
            )

        # --- 5: the judge only sees a green boundary ----------------------
        request = build_request(
            state.objective,
            response_text,
            contract=state.contract,
            subgoals=state.subgoals,
            turn=state.turns_used + 1,
            max_turns=state.max_turns,
        )
        outcome = await self._judge.judge(request)
        state.last_verdict = outcome.verdict.value
        state.last_reason = outcome.reason

        if outcome.verdict is GoalVerdict.DONE:
            state.complete(outcome.reason)
            self._persist(state)
            return TurnDecision(
                action=TurnAction.DONE,
                reason=outcome.reason,
                verdict=outcome,
                gate_report=gate_report,
                turns_used=state.turns_used,
                budget_remaining=state.budget_remaining,
                user_initiated=user_initiated,
            )

        if outcome.verdict is GoalVerdict.BLOCKED:
            # An unachievable goal is paused with a reason. It is never marked
            # done, and it is never retried against a budget it cannot spend.
            state.block(outcome.reason or "judge reported the goal unachievable")
            self._persist(state)
            return TurnDecision(
                action=TurnAction.BLOCKED,
                reason=state.pause_reason,
                verdict=outcome,
                gate_report=gate_report,
                turns_used=state.turns_used,
                budget_remaining=state.budget_remaining,
                user_initiated=user_initiated,
            )

        if outcome.verdict is GoalVerdict.WAIT:
            if state.wait_expired:
                # A stale barrier can never wedge the loop.
                state.wait_until = 0.0
                state.wait_reason = ""
                return self._continue(state, gate_report, user_initiated, reason=outcome.reason or "wait expired")
            deadline = state.park(outcome.reason or "judge parked the loop", self._clock() + self._wait_seconds)
            self._persist(state)
            return TurnDecision(
                action=TurnAction.WAIT,
                reason=outcome.reason,
                verdict=outcome,
                gate_report=gate_report,
                turns_used=state.turns_used,
                budget_remaining=state.budget_remaining,
                user_initiated=user_initiated,
                wait_until=deadline,
            )

        # `continue`, including every fail-open case (unavailable judge,
        # unreadable verdict). A degraded verdict is reported as such so the
        # operator can see the loop is running without a judge.
        reason = outcome.reason or "judge asked to continue"
        if outcome.degraded:
            reason = f"{reason} (degraded: no usable judge verdict)"
        return self._continue(state, gate_report, user_initiated, reason=reason, outcome=outcome)

    # ------------------------------------------------------------- outcomes

    def _idle(self, state: GoalState, *, user_initiated: bool, reason: str) -> TurnDecision:
        return TurnDecision(
            action=TurnAction.IDLE,
            reason=reason,
            turns_used=state.turns_used,
            budget_remaining=state.budget_remaining,
            user_initiated=user_initiated,
        )

    def _exhausted_gates(self, state: GoalState, gate_report: GateReport) -> list[GateResult]:
        """Red gates that have spent their cross-boundary failure budget."""
        return [
            result
            for position, result in enumerate(gate_report.results, start=1)
            if not result.passed and state.gate_failures.get(position, 0) >= self._max_gate_failures
        ]

    def _pause_on_gate_exhaustion(
        self,
        state: GoalState,
        gate_report: GateReport,
        user_initiated: bool,
        exhausted: list[GateResult],
    ) -> TurnDecision:
        first = exhausted[0]
        index = next(i for i, result in enumerate(gate_report.results, start=1) if result is first)
        message = GATE_EXHAUSTED_MESSAGE.format(command=first.gate.name, index=index)
        state.pause(message)
        self._persist(state)
        return TurnDecision(
            action=TurnAction.PAUSED,
            reason=message,
            gate_report=gate_report,
            turns_used=state.turns_used,
            budget_remaining=state.budget_remaining,
            user_initiated=user_initiated,
        )

    def _continue_on_red_gate(
        self,
        state: GoalState,
        gate_report: GateReport,
        *,
        user_initiated: bool,
        reason: str,
    ) -> TurnDecision:
        # The judge is deliberately NOT called here. A red gate is evidence.
        if user_initiated:
            return TurnDecision(
                action=TurnAction.IDLE,
                reason=f"{reason}; user turn takes precedence over the continuation loop",
                gate_report=gate_report,
                turns_used=state.turns_used,
                budget_remaining=state.budget_remaining,
                user_initiated=True,
            )
        if state.budget_exhausted:
            return self._pause_on_budget(state, gate_report, user_initiated)
        state.consume_turn()
        self._persist(state)
        return TurnDecision(
            action=TurnAction.CONTINUE,
            reason=reason,
            continuation=build_continuation_prompt(
                state,
                reason=reason,
                gate_report=gate_report,
                gate_failures=state.gate_failures,
            ),
            gate_report=gate_report,
            turns_used=state.turns_used,
            budget_remaining=state.budget_remaining,
            user_initiated=False,
        )

    def _continue(
        self,
        state: GoalState,
        gate_report: GateReport,
        user_initiated: bool,
        *,
        reason: str,
        outcome: JudgeOutcome | None = None,
    ) -> TurnDecision:
        if user_initiated:
            # The human preempts the continuation loop. The verdict above still
            # counts - a user turn really can complete the goal - but nothing
            # is fed back automatically, and no continuation turn is spent.
            return TurnDecision(
                action=TurnAction.IDLE,
                reason=f"{reason}; user turn takes precedence over the continuation loop",
                verdict=outcome,
                gate_report=gate_report,
                turns_used=state.turns_used,
                budget_remaining=state.budget_remaining,
                user_initiated=True,
            )
        if state.budget_exhausted:
            return self._pause_on_budget(state, gate_report, user_initiated, outcome=outcome)
        state.consume_turn()
        self._persist(state)
        return TurnDecision(
            action=TurnAction.CONTINUE,
            reason=reason,
            continuation=build_continuation_prompt(state, reason=reason, gate_report=gate_report),
            verdict=outcome,
            gate_report=gate_report,
            turns_used=state.turns_used,
            budget_remaining=state.budget_remaining,
            user_initiated=False,
        )

    def _pause_on_budget(
        self,
        state: GoalState,
        gate_report: GateReport,
        user_initiated: bool,
        *,
        outcome: JudgeOutcome | None = None,
    ) -> TurnDecision:
        message = BUDGET_EXHAUSTED_MESSAGE.format(used=state.turns_used, max_turns=state.max_turns)
        state.pause(message)
        self._persist(state)
        return TurnDecision(
            action=TurnAction.PAUSED,
            reason=message,
            verdict=outcome,
            gate_report=gate_report,
            turns_used=state.turns_used,
            budget_remaining=0,
            user_initiated=user_initiated,
        )


__all__ = [
    "BUDGET_EXHAUSTED_MESSAGE",
    "CONTINUATION_ROLE",
    "DEFAULT_WAIT_SECONDS",
    "GATE_EXHAUSTED_MESSAGE",
    "TERMINAL_ACTIONS",
    "GoalLoopEngine",
    "TurnAction",
    "TurnDecision",
    "build_continuation_prompt",
]
