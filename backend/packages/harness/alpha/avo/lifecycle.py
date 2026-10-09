"""The governed run lifecycle: a durable state machine with legal transitions.

Alpha's ordinary run states describe an agent turn. A governed variation run
needs states ordinary runs do not have, because its high-risk boundaries are
different: it evaluates candidates, waits on approval gates, rolls back, and
recovers from an interrupted transition. Those states are declared here as one
closed graph rather than a collection of booleans, and every transition is
validated server-side.

The load-bearing rules:

* **A transition the graph does not name is refused, not coerced.** The previous
  shape -- ``if paused: skip`` scattered across call sites -- let any caller set
  any combination of flags and be believed.
* **Nothing a model sends is a state.** :meth:`transition` takes an *actor*, and
  an actor of ``model`` is only ever allowed to propose, never to move the run
  into or out of a terminal state.
* **Every transition is recorded.** Each entry carries the prior state, the new
  state, the actor, the reason code and a correlation set, so "how did this run
  get here" is answerable after the fact.
* **A terminal state is final.** There is no transition out of ``FAILED``,
  ``CANCELLED`` or ``BUDGET_EXHAUSTED``; an operator who wants to redo the work
  admits a new run with a new idempotency key, which is a legible act.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "Actor",
    "AvoState",
    "IllegalTransition",
    "StateTransition",
    "RunLifecycle",
    "TERMINAL_STATES",
    "ACTIVE_STATES",
]


class AvoState(StrEnum):
    QUEUED = "queued"
    PREFLIGHT = "preflight"
    RUNNING = "running"
    CHECKPOINTING = "checkpointing"
    WAITING_APPROVAL = "waiting_approval"
    EVALUATING = "evaluating"
    CANDIDATE_REJECTED = "candidate_rejected"
    ROLLING_BACK = "rolling_back"
    PAUSED = "paused"
    RECOVERY_REQUIRED = "recovery_required"
    PROMOTION_ELIGIBLE = "promotion_eligible"
    PROMOTED = "promoted"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


#: States no transition leaves. A run that reaches one of these is finished;
#: re-admission is a new run, not a resume.
TERMINAL_STATES: frozenset[AvoState] = frozenset({AvoState.PROMOTED, AvoState.FAILED, AvoState.BUDGET_EXHAUSTED, AvoState.CANCELLED})

#: States in which an action may execute. Everything else is a gate: the run is
#: admitted, evaluating, parked on an approval, recovering, or finished.
ACTIVE_STATES: frozenset[AvoState] = frozenset({AvoState.RUNNING, AvoState.CHECKPOINTING})


class Actor(StrEnum):
    """Who moved the run. ``model`` may never reach a terminal state."""

    OPERATOR = "operator"
    POLICY_GATE = "policy_gate"
    RUNNER = "runner"
    EVALUATOR = "evaluator"
    RECOVERY = "recovery"
    MODEL = "model"


class IllegalTransition(ValueError):
    """Raised when a caller asks for a transition the graph does not allow."""

    def __init__(self, current: AvoState, requested: AvoState, actor: Actor) -> None:
        self.current = current
        self.requested = requested
        self.actor = actor
        super().__init__(f"illegal governed-run transition: {current.value} -> {requested.value} by {actor.value}; the lifecycle graph does not contain that edge")


@dataclass(frozen=True)
class StateTransition:
    """One recorded move, with everything needed to explain it later."""

    prior_state: AvoState
    new_state: AvoState
    actor: Actor
    reason_code: str
    correlation_ids: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prior_state": self.prior_state.value,
            "new_state": self.new_state.value,
            "actor": self.actor.value,
            "reason_code": self.reason_code,
            "correlation_ids": dict(self.correlation_ids),
            "timestamp": self.timestamp,
        }


#: The legal edges. Declared as data so ``RunLifecycle.allowed`` and the tests
#: read one table rather than two implementations that can drift.
_TRANSITIONS: dict[AvoState, frozenset[AvoState]] = {
    AvoState.QUEUED: frozenset({AvoState.PREFLIGHT, AvoState.CANCELLED}),
    AvoState.PREFLIGHT: frozenset({AvoState.RUNNING, AvoState.WAITING_APPROVAL, AvoState.RECOVERY_REQUIRED, AvoState.CANCELLED, AvoState.BUDGET_EXHAUSTED}),
    AvoState.RUNNING: frozenset(
        {
            AvoState.CHECKPOINTING,
            AvoState.WAITING_APPROVAL,
            AvoState.EVALUATING,
            AvoState.ROLLING_BACK,
            AvoState.PAUSED,
            AvoState.RECOVERY_REQUIRED,
            AvoState.CANDIDATE_REJECTED,
            AvoState.PROMOTION_ELIGIBLE,
            AvoState.FAILED,
            AvoState.BUDGET_EXHAUSTED,
            AvoState.CANCELLED,
        }
    ),
    AvoState.CHECKPOINTING: frozenset({AvoState.RUNNING, AvoState.RECOVERY_REQUIRED, AvoState.FAILED, AvoState.CANCELLED}),
    AvoState.WAITING_APPROVAL: frozenset({AvoState.RUNNING, AvoState.PROMOTION_ELIGIBLE, AvoState.ROLLING_BACK, AvoState.CANCELLED, AvoState.BUDGET_EXHAUSTED}),
    AvoState.EVALUATING: frozenset({AvoState.RUNNING, AvoState.PROMOTION_ELIGIBLE, AvoState.CANDIDATE_REJECTED, AvoState.ROLLING_BACK, AvoState.RECOVERY_REQUIRED, AvoState.FAILED}),
    AvoState.CANDIDATE_REJECTED: frozenset({AvoState.RUNNING, AvoState.ROLLING_BACK, AvoState.FAILED, AvoState.BUDGET_EXHAUSTED, AvoState.CANCELLED}),
    AvoState.ROLLING_BACK: frozenset({AvoState.RUNNING, AvoState.FAILED, AvoState.CANCELLED}),
    AvoState.PAUSED: frozenset({AvoState.RUNNING, AvoState.CANCELLED, AvoState.RECOVERY_REQUIRED, AvoState.BUDGET_EXHAUSTED}),
    AvoState.RECOVERY_REQUIRED: frozenset({AvoState.RUNNING, AvoState.ROLLING_BACK, AvoState.WAITING_APPROVAL, AvoState.FAILED, AvoState.BUDGET_EXHAUSTED, AvoState.CANCELLED}),
    AvoState.PROMOTION_ELIGIBLE: frozenset({AvoState.PROMOTED, AvoState.ROLLING_BACK, AvoState.FAILED, AvoState.CANCELLED, AvoState.BUDGET_EXHAUSTED, AvoState.WAITING_APPROVAL}),
    # Terminal: no outgoing edges at all.
    AvoState.PROMOTED: frozenset(),
    AvoState.FAILED: frozenset(),
    AvoState.BUDGET_EXHAUSTED: frozenset(),
    AvoState.CANCELLED: frozenset(),
}


def _may_enter_terminal(actor: Actor) -> bool:
    """Only the control plane may reach a terminal state.

    A model that can write ``failed`` can end a run whose result is inconvenient
    and a model that can write ``promoted`` can end a run whose result is
    favourable. Neither is available.
    """
    return actor is not Actor.MODEL


@dataclass
class RunLifecycle:
    """One run's lifecycle. The state machine is in-process, by design.

    Durability is :mod:`alpha.avo.checkpointing`'s job; this object is the
    authority on which states exist, which edges are legal, and what happened.
    """

    state: AvoState = AvoState.QUEUED
    avo_run_id: str = ""
    history: list[StateTransition] = field(default_factory=list)

    # -- queries -----------------------------------------------------------
    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def active(self) -> bool:
        """True only when an action may execute. Never trust a caller's claim."""
        return self.state in ACTIVE_STATES and not self.terminal

    @property
    def paused(self) -> bool:
        return self.state is AvoState.PAUSED

    @property
    def cancelled(self) -> bool:
        return self.state is AvoState.CANCELLED

    @property
    def awaiting_approval(self) -> bool:
        return self.state is AvoState.WAITING_APPROVAL

    @property
    def recovery_required(self) -> bool:
        return self.state is AvoState.RECOVERY_REQUIRED

    def allowed(self, target: AvoState) -> bool:
        return target in _TRANSITIONS.get(self.state, frozenset())

    def allowed_targets(self) -> tuple[AvoState, ...]:
        return tuple(sorted(_TRANSITIONS.get(self.state, frozenset()), key=lambda s: s.value))

    def can_execute_actions(self) -> bool:
        return self.active

    # -- mutation ----------------------------------------------------------
    def transition(
        self,
        target: AvoState,
        *,
        actor: Actor,
        reason_code: str,
        **correlation: str,
    ) -> StateTransition:
        """Move to ``target``, refusing anything the graph does not name."""
        if self.state is target:
            raise IllegalTransition(self.state, target, actor)
        if target in TERMINAL_STATES and not _may_enter_terminal(actor):
            raise IllegalTransition(self.state, target, actor)
        if not self.allowed(target):
            raise IllegalTransition(self.state, target, actor)

        entry = StateTransition(
            prior_state=self.state,
            new_state=target,
            actor=actor,
            reason_code=reason_code,
            correlation_ids={key: value for key, value in correlation.items()},
        )
        self.state = target
        self.history.append(entry)
        return entry

    def on_run_id(self) -> str:
        return self.avo_run_id

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "avo_run_id": self.avo_run_id,
            "state": self.state.value,
            "terminal": self.terminal,
            "history": [entry.to_dict() for entry in self.history],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunLifecycle:
        """Rebuild a lifecycle. An unknown state is refused, not defaulted.

        Silently defaulting to ``QUEUED`` would let a checkpoint written by a
        newer build resume as a fresh run, which is the same failure as
        resuming the wrong branch.
        """
        try:
            state = AvoState(str(payload.get("state")))
        except ValueError as exc:
            raise ValueError(f"checkpoint carries an unknown lifecycle state {payload.get('state')!r}") from exc
        history: list[StateTransition] = []
        for entry in payload.get("history") or []:
            if not isinstance(entry, dict):
                raise ValueError("lifecycle history entry is not an object")
            try:
                history.append(
                    StateTransition(
                        prior_state=AvoState(str(entry.get("prior_state"))),
                        new_state=AvoState(str(entry.get("new_state"))),
                        actor=Actor(str(entry.get("actor"))),
                        reason_code=str(entry.get("reason_code", "")),
                        correlation_ids=dict(entry.get("correlation_ids") or {}),
                        timestamp=float(entry.get("timestamp", 0.0)),
                    )
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"lifecycle history entry is malformed: {entry!r}") from exc
        run = cls(state=state, avo_run_id=str(payload.get("avo_run_id", "")), history=history)
        # Re-derive the state from the last transition so a mismatched ``state``
        # field is caught rather than believed.
        if history and history[-1].new_state is not state:
            raise ValueError(f"checkpoint lifecycle state {state.value} disagrees with its last transition {history[-1].new_state.value}")
        return run

    def reason_codes(self) -> Iterable[str]:
        return (entry.reason_code for entry in self.history)
