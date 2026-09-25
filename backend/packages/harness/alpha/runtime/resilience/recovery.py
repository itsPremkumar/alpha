"""``recover``: retry + circuit + budget + convergence guard, one honest status.

This is the composition the rest of the package exists to enable. One call
gives an operation a bounded, escalating, self-describing recovery ladder and
returns a :class:`RecoveryOutcome` from a **closed** status set:

===================== ==========================================================
``succeeded``         the operation really returned; ``value`` is its return
``exhausted``         bounds ran out (attempts, guard termination, a rung whose
                      hook failed, or the DELEGATE rung with no delegate)
``aborted``           an external abort signal, a control signal, or a
                      duplicate idempotency key fired
``circuit_open``      the breaker refused; the dependency is presumed down
``budget_exhausted``  attempt / cost / wall-clock budget ran out
``not_retryable``     the policy classified the failure as permanent
===================== ==========================================================

Honesty contract
----------------
* ``succeeded`` is only ever constructed from a real return value. Returning
  ``None`` is a success (``returned=True``, ``value=None``); a failure is never
  laundered into one. ``NO_VALUE`` marks "the operation never returned".
* An expected failure is a **return value with a reason**; the exception object
  and its type stay attached (``last_error`` / ``error_type``), and per-rung
  disclosures land in ``notes``.
* An unexpected ``BaseException`` (KeyboardInterrupt, SystemExit,
  ``asyncio.CancelledError``) is **not** captured into a status. It propagates
  after the claim is settled and the trail is flushed, so a host shutdown stays
  a shutdown.
* With ``enabled=False`` this is a strict pass-through: one call, no retry, no
  circuit, no budget, no guard. An exception is reported as ``not_retryable``
  with its type recorded (that is what "the caller's own error handling saw"
  looks like from here) and never as ``succeeded``.

The escalation ladder
---------------------
``recover`` runs a loop whose every iteration is a *round*:

1. The previous round's rung is applied (if the caller supplied a hook):
   ``CHANGE_CONTEXT`` -> ``on_change_context``,
   ``REQUEST_DISCRIMINATING_EVIDENCE`` -> ``on_evidence``,
   ``CHANGE_STRATEGY`` -> ``on_change_strategy``, ``DELEGATE`` -> ``delegate``
   (a delegate that returns a value ends the recovery with ``succeeded``). A
   hook that is *absent* is disclosed in ``notes`` and the ladder continues; a
   hook that *fails* ends the recovery as ``exhausted`` with the exception type
   recorded - a broken escalation hook is a ladder failure, never a success.
2. The operation runs through :func:`~alpha.runtime.resilience.retry.retry_call`
   under the breaker and the budget: per attempt, budget reservation first (a
   refused attempt never starts side effects), then breaker admission, then the
   operation.
3. A retryable round failure is recorded on the
   :class:`~alpha.runtime.resilience.convergence.ConvergenceGuard` as an
   observation (action, state, failure signature) and the guard's decision
   becomes the next rung. ``TERMINATE``/``exhausted`` ends the ladder with
   ``exhausted``.

The rung a round runs under also selects the observation *kind* used for cap
accounting: plain retries are ``ATTEMPT``, context/evidence rounds are
``REFLECTION``, strategy/delegate rounds are ``REPLAN``. So the guard's
``max_reflections``/``max_replans`` are real bounds on this loop, not
decorative knobs.

Idempotency composition
-----------------------
With ``registry`` + ``key`` the whole ladder runs under one at-most-once claim:
a duplicate key is reported as ``aborted`` with a disclosed reason and the
operation is **not** re-run. The claim is settled once, whatever the status:
``succeeded`` keeps it sticky (later callers see the first outcome), every other
status releases it so a deliberate retry with the same key can run again. This
is the composition the at-most-once contract wants - the claim wraps the whole
recovery, not each attempt inside it.

Termination argument
--------------------
Every round advances at least one monotone counter, and three independent
bounds can end the loop:

* inner: ``policy.attempts`` executions per round (the retry loop's own bound);
* outer: the guard - a six-rung ratchet that only increases, pinned at
  ``TERMINATE`` once a cap fires or a prohibited transition appears. A cap fires
  on the *first* observation that exceeds it, so a guard with
  ``max_attempts=N`` runs at most ``N+1`` rounds and then returns the same
  terminal decision for every later observation (a state transition that cannot
  repeat). **With no guard at all the ladder runs exactly one round**, so
  omitting a guard is a tighter bound, never a looser one;
* wall clock: ``budget.deadline`` is passed to ``retry_call`` and enforced by
  the budget, so a round cannot outrun it.

Whichever fires first ends the ladder; no path returns to the top of the loop
without one of them having advanced.
``test_no_policy_space_loops_forever`` in the package's test module fuzzes the
policy space to pin this.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.runtime.resilience.budget import BudgetSnapshot, ResourceBudget
from alpha.runtime.resilience.circuit import CircuitBreaker, CircuitOpenError, CircuitSnapshot
from alpha.runtime.resilience.clock import Clock, coerce_clock
from alpha.runtime.resilience.convergence import (
    ConvergenceDecision,
    ConvergenceGuard,
    GuardStatus,
    Intervention,
    ObservationKind,
)
from alpha.runtime.resilience.errors import (
    BudgetExhaustedError,
    ControlSignal,
    DeadlineExceeded,
    RetryAbortedError,
    RetryExhaustedError,
)
from alpha.runtime.resilience.idempotency import IdempotencyClaim, IdempotencyKey, IdempotencyRegistry, IdempotencyStatus, RunOutcome
from alpha.runtime.resilience.retry import AttemptRecord, AttemptTrail, RetryPolicy, retry_call

__all__ = ["NO_VALUE", "RecoveryOutcome", "RecoveryStatus", "RecoveryStep", "recover"]

_MAX_SIGNATURE_CHARS = 200
_HOOK_ABSENT = "absent"
_HOOK_APPLIED = "applied"
_HOOK_FAILED = "failed"


class _NoValue:
    """Sentinel for "the operation never returned"."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<no value>"

    def __bool__(self) -> bool:
        return False


#: Distinguishes "returned None" from "never returned"; exported so callers can
#: assert on it without importing a private name.
NO_VALUE: Any = _NoValue()


class RecoveryStatus(StrEnum):
    """Closed status set. No other value is ever returned."""

    SUCCEEDED = "succeeded"
    EXHAUSTED = "exhausted"
    ABORTED = "aborted"
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NOT_RETRYABLE = "not_retryable"


@dataclass(frozen=True, slots=True)
class RecoveryStep:
    """What a hook or a delegate is told about the round it is serving."""

    rung: int
    decision: ConvergenceDecision
    round_index: int
    last_error: BaseException | None


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """The result of a recovery ladder: a status, a reason, and the evidence."""

    status: RecoveryStatus
    reason: str
    """Non-empty disclosure of why this status; safe to log verbatim."""

    value: Any = NO_VALUE
    returned: bool = False
    """True only when ``value`` came from a real return of the operation."""

    attempts: int = 0
    trail: AttemptTrail = field(default_factory=AttemptTrail)
    last_error: BaseException | None = None
    error_type: str = ""
    interventions: tuple[Intervention, ...] = ()
    decisions: tuple[ConvergenceDecision, ...] = ()
    notes: tuple[str, ...] = ()
    """Per-rung disclosures (e.g. "rung CHANGE_CONTEXT had no hook")."""
    budget: BudgetSnapshot | None = None
    circuit: CircuitSnapshot | None = None

    @property
    def succeeded(self) -> bool:
        """True only for a real return. Never inferred from a truthy value."""
        return self.status is RecoveryStatus.SUCCEEDED and self.returned

    @property
    def error(self) -> BaseException | None:
        return self.last_error


_NO_DECISION = ConvergenceDecision(
    status=GuardStatus.CONTINUE,
    intervention=Intervention.NONE,
    signal=None,
    reason="no intervention yet",
    rung=-1,
    kind=ObservationKind.ATTEMPT,
)

#: Which cap a rung spends: retries are attempts, re-framing is reflection,
#: re-approaching is a replan.
_RUNG_KIND: dict[Intervention, ObservationKind] = {
    Intervention.NONE: ObservationKind.ATTEMPT,
    Intervention.WARN: ObservationKind.ATTEMPT,
    Intervention.CHANGE_CONTEXT: ObservationKind.REFLECTION,
    Intervention.REQUEST_DISCRIMINATING_EVIDENCE: ObservationKind.REFLECTION,
    Intervention.CHANGE_STRATEGY: ObservationKind.REPLAN,
    Intervention.DELEGATE: ObservationKind.REPLAN,
    Intervention.TERMINATE: ObservationKind.ATTEMPT,
}

_RUNG_HOOKS: dict[Intervention, str] = {
    Intervention.CHANGE_CONTEXT: "on_change_context",
    Intervention.REQUEST_DISCRIMINATING_EVIDENCE: "on_evidence",
    Intervention.CHANGE_STRATEGY: "on_change_strategy",
}


@dataclass(slots=True)
class _Ladder:
    """Mutable bookkeeping for one recovery ladder (never shared)."""

    trail: AttemptTrail
    interventions: list[Intervention]
    decisions: list[ConvergenceDecision]
    notes: list[str]
    breaker: CircuitBreaker | None = None
    budget: ResourceBudget | None = None
    observer: Callable[[AttemptRecord], None] | None = None
    emitted: int = 0

    def flush(self) -> None:
        """Hand every not-yet-reported attempt record to the observer.

        Bookkeeping is on :attr:`AttemptTrail.total`, not on the record list, so
        a trimmed (bounded) trail still reports each record exactly once.
        """
        if self.observer is None:
            return
        unreported = self.trail.total - self.emitted
        if unreported <= 0:
            return
        records = self.trail.records
        start = max(0, len(records) - unreported)
        for record in records[start:]:
            self.observer(record)
        self.emitted = self.trail.total

    def outcome(
        self,
        status: RecoveryStatus,
        reason: str,
        *,
        value: Any = NO_VALUE,
        returned: bool = False,
        error: BaseException | None = None,
        attempts: int | None = None,
    ) -> RecoveryOutcome:
        return RecoveryOutcome(
            status=status,
            reason=reason,
            value=value,
            returned=returned,
            attempts=self.trail.attempts if attempts is None else attempts,
            trail=self.trail,
            last_error=error,
            error_type=type(error).__name__ if error is not None else "",
            interventions=tuple(self.interventions),
            decisions=tuple(self.decisions),
            notes=tuple(self.notes),
            budget=None if self.budget is None else self.budget.snapshot(),
            circuit=None if self.breaker is None else self.breaker.snapshot(),
        )


def _wants_step(callable_object: Callable[..., Any]) -> bool:
    """True when the callable accepts a single positional argument.

    Lets callers pass either ``fn()`` or ``fn(step)`` without a second API.
    """
    try:
        signature = inspect.signature(callable_object)
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        return False
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            return True
    return False


def _bind_step(callable_object: Callable[..., Any]) -> Callable[[RecoveryStep], Any]:
    """Resolve arity once, so a per-attempt call never re-inspects a signature.

    Callers may pass either ``fn()`` or ``fn(step)``; inspecting on every attempt
    would put ``inspect.signature`` in the hot path of every recovery.
    """
    if _wants_step(callable_object):
        return callable_object
    return lambda _step: callable_object()


def _signature_of(error: BaseException | None) -> str:
    """Bounded failure signature used for the guard's alternation detection."""
    if error is None:
        return ""
    return f"{type(error).__name__}: {error}"[:_MAX_SIGNATURE_CHARS]


@dataclass(frozen=True, slots=True)
class _Round:
    """Internal per-round result; never leaves this module."""

    status: RecoveryStatus
    reason: str
    value: Any = NO_VALUE
    returned: bool = False
    error: BaseException | None = None
    signature: str = ""


def _run_round(
    attempt: Callable[[], Any],
    ladder: _Ladder,
    *,
    policy: RetryPolicy,
    clock: Clock,
    abort: Callable[[], bool] | None,
) -> _Round:
    """One escalation round: bounded retries of ``attempt`` under the bounds."""
    breaker = ladder.breaker
    budget = ladder.budget

    def _guarded_attempt() -> Any:
        if budget is not None:
            budget.begin_attempt()
        if breaker is not None:
            return breaker.call(attempt)
        return attempt()

    try:
        result = retry_call(
            _guarded_attempt,
            policy,
            clock=clock,
            deadline=budget.deadline if budget is not None else None,
            abort=abort,
            trail=ladder.trail,
        )
    except CircuitOpenError as exc:
        return _Round(RecoveryStatus.CIRCUIT_OPEN, f"circuit open: {exc}", error=exc)
    except BudgetExhaustedError as exc:
        return _Round(RecoveryStatus.BUDGET_EXHAUSTED, f"budget exhausted: {exc}", error=exc)
    except RetryAbortedError as exc:
        return _Round(RecoveryStatus.ABORTED, f"aborted: {exc}", error=exc)
    except DeadlineExceeded as exc:
        # The wall-clock budget is gone; report it as the budget, not as a
        # generic abort, so a caller can alert on the right thing.
        return _Round(RecoveryStatus.BUDGET_EXHAUSTED, f"deadline exceeded: {exc}", error=exc)
    except RetryExhaustedError as exc:
        if exc.reason == "deadline":
            return _Round(RecoveryStatus.BUDGET_EXHAUSTED, f"wall-clock budget exhausted: {exc}", error=exc.last_error or exc)
        return _Round(
            RecoveryStatus.EXHAUSTED,
            f"retryable failures exhausted the round: {exc}",
            error=exc.last_error,
            signature=_signature_of(exc.last_error) or "RetryExhaustedError",
        )
    except ControlSignal as exc:
        return _Round(RecoveryStatus.ABORTED, f"control signal: {exc}", error=exc)
    except Exception as exc:
        return _Round(
            RecoveryStatus.NOT_RETRYABLE,
            f"non-retryable failure: {type(exc).__name__}: {exc}",
            error=exc,
            signature=_signature_of(exc),
        )
    finally:
        ladder.flush()

    return _Round(RecoveryStatus.SUCCEEDED, "operation returned", value=result.value, returned=True)


def _apply_hook(hook: Callable[[RecoveryStep], Any] | None, step: RecoveryStep) -> tuple[str, str, BaseException | None]:
    """Run an escalation hook: ``absent`` / ``applied`` / ``failed`` + detail."""
    if hook is None:
        return _HOOK_ABSENT, "no hook supplied for this rung", None
    try:
        hook(step)
    except Exception as exc:
        return _HOOK_FAILED, f"{type(exc).__name__}: {exc}", exc
    return _HOOK_APPLIED, "applied", None


def _run_ladder(
    operation: Callable[[RecoveryStep], Any],
    ladder: _Ladder,
    *,
    policy: RetryPolicy,
    clock: Clock,
    guard: ConvergenceGuard | None,
    abort: Callable[[], bool] | None,
    action: str,
    hooks: dict[str, Callable[[RecoveryStep], Any] | None],
    delegate: Callable[[RecoveryStep], Any] | None,
) -> RecoveryOutcome:
    """The bounded round loop. See the module docstring for the argument.

    ``operation``, ``hooks`` and ``delegate`` arrive already arity-bound by
    :func:`_bind_step`, so the per-attempt path does no reflection.
    """
    last_error: BaseException | None = None
    previous = _NO_DECISION
    round_index = 0

    while True:
        if abort is not None and abort():
            return ladder.outcome(RecoveryStatus.ABORTED, f"abort signal before round {round_index + 1}", error=last_error)
        if ladder.budget is not None and ladder.budget.snapshot().exhausted:
            return ladder.outcome(RecoveryStatus.BUDGET_EXHAUSTED, f"budget exhausted before round {round_index + 1}", error=last_error)

        round_index += 1
        step = RecoveryStep(rung=previous.rung, decision=previous, round_index=round_index, last_error=last_error)

        hook_name = _RUNG_HOOKS.get(previous.intervention)
        if hook_name is not None:
            state, detail, hook_error = _apply_hook(hooks[hook_name], step)
            if state == _HOOK_FAILED:
                return ladder.outcome(
                    RecoveryStatus.EXHAUSTED,
                    f"{previous.intervention.value} hook failed: {detail}",
                    error=hook_error,
                )
            if state == _HOOK_ABSENT:
                ladder.notes.append(f"{previous.intervention.value}: {detail} (rung disclosed, ladder continues)")

        if previous.intervention is Intervention.DELEGATE:
            return _run_delegate(delegate, step, ladder, last_error)

        result = _run_round(lambda: operation(step), ladder, policy=policy, clock=clock, abort=abort)

        if result.status is RecoveryStatus.SUCCEEDED:
            return ladder.outcome(
                RecoveryStatus.SUCCEEDED,
                f"operation returned at round {round_index} after {ladder.trail.attempts} attempt(s)",
                value=result.value,
                returned=True,
            )
        if result.status is RecoveryStatus.NOT_RETRYABLE:
            return ladder.outcome(RecoveryStatus.NOT_RETRYABLE, result.reason, error=result.error)
        if result.status in (RecoveryStatus.CIRCUIT_OPEN, RecoveryStatus.BUDGET_EXHAUSTED, RecoveryStatus.ABORTED):
            return ladder.outcome(result.status, result.reason, error=result.error)

        # Retryable round failure -> ask the guard which rung comes next.
        last_error = result.error
        if guard is None:
            # No guard: exactly one round. A missing guard is a tighter bound,
            # never a licence to loop.
            return ladder.outcome(
                RecoveryStatus.EXHAUSTED,
                f"{result.reason} (no convergence guard supplied: one round only)",
                error=last_error,
            )

        previous = guard.observe(
            action=action,
            state=type(last_error).__name__ if last_error is not None else "",
            kind=_RUNG_KIND[previous.intervention],
            progressed=False,
            failure_signature=result.signature,
        )
        ladder.decisions.append(previous)
        if previous.status is not GuardStatus.CONTINUE:
            ladder.interventions.append(previous.intervention)
        if previous.status is GuardStatus.EXHAUSTED or previous.intervention is Intervention.TERMINATE:
            return ladder.outcome(RecoveryStatus.EXHAUSTED, f"convergence guard terminated: {previous.reason}", error=last_error)


def _run_delegate(
    delegate: Callable[[RecoveryStep], Any] | None,
    step: RecoveryStep,
    ladder: _Ladder,
    last_error: BaseException | None,
) -> RecoveryOutcome:
    """Hand the work over. A delegate that returns a value ends the recovery."""
    if delegate is None:
        ladder.notes.append("DELEGATE rung reached with no delegate supplied")
        return ladder.outcome(RecoveryStatus.EXHAUSTED, "reached the DELEGATE rung with no delegate supplied", error=last_error)
    breaker = ladder.breaker
    if breaker is not None and not breaker.allow_request():
        return ladder.outcome(
            RecoveryStatus.CIRCUIT_OPEN,
            f"circuit open at the DELEGATE rung: {breaker.state_reason()}",
            error=last_error,
        )
    try:
        value = delegate(step)
    except Exception as exc:
        return ladder.outcome(RecoveryStatus.EXHAUSTED, f"delegate failed: {type(exc).__name__}: {exc}", error=exc)
    ladder.flush()
    return ladder.outcome(
        RecoveryStatus.SUCCEEDED,
        f"delegated at round {step.round_index} after escalating to DELEGATE",
        value=value,
        returned=True,
        error=last_error,
    )


def _settle(registry: IdempotencyRegistry, key: IdempotencyKey, claim: IdempotencyClaim, outcome: RecoveryOutcome) -> None:
    """Settle the at-most-once claim exactly once, whatever the status was.

    ``succeeded`` keeps the claim sticky: later callers see the first outcome.
    Every other status releases it (``release=True``) so a deliberate retry with
    the same key can run - the at-most-once guarantee covers concurrent
    duplicates, not a caller's explicit re-request.
    """
    if outcome.succeeded:
        registry.complete(key, claim, RunOutcome(ok=True, value=outcome.value))
    else:
        registry.fail(key, claim, RunOutcome(ok=False, error=outcome.reason), release=True)


def recover(
    operation: Callable[..., Any],
    *,
    policy: RetryPolicy,
    clock: Clock | None = None,
    breaker: CircuitBreaker | None = None,
    budget: ResourceBudget | None = None,
    guard: ConvergenceGuard | None = None,
    abort: Callable[[], bool] | None = None,
    enabled: bool = True,
    action: str = "operation",
    on_change_context: Callable[..., Any] | None = None,
    on_evidence: Callable[..., Any] | None = None,
    on_change_strategy: Callable[..., Any] | None = None,
    delegate: Callable[..., Any] | None = None,
    registry: IdempotencyRegistry | None = None,
    key: IdempotencyKey | None = None,
    observer: Callable[[AttemptRecord], None] | None = None,
) -> RecoveryOutcome:
    """Run ``operation`` under the full resilience ladder.

    ``operation`` may take no arguments or a single :class:`RecoveryStep`. Every
    bound is injected and every status comes from the closed
    :class:`RecoveryStatus` set; see the module docstring for the ladder and the
    termination argument.
    """
    resolved_clock = coerce_clock(clock)
    bound_operation = _bind_step(operation)
    ladder = _Ladder(
        trail=AttemptTrail(),
        interventions=[],
        decisions=[],
        notes=[],
        breaker=breaker,
        budget=budget,
        observer=observer,
    )

    if not enabled:
        step = RecoveryStep(rung=-1, decision=_NO_DECISION, round_index=1, last_error=None)
        try:
            value = bound_operation(step)
        except Exception as exc:
            return ladder.outcome(
                RecoveryStatus.NOT_RETRYABLE,
                f"disabled pass-through: {type(exc).__name__}: {exc}",
                error=exc,
                attempts=1,
            )
        return ladder.outcome(
            RecoveryStatus.SUCCEEDED,
            "disabled pass-through: one call, no bounds",
            value=value,
            returned=True,
            attempts=1,
        )

    claim: IdempotencyClaim | None = None
    if registry is not None and key is not None:
        claim = registry.begin(key)
        if claim.status is not IdempotencyStatus.ACQUIRED:
            if claim.outcome is not None:
                reason = f"idempotency {claim.status.value}: first outcome ok={claim.outcome.ok} error={claim.outcome.error!r}"
            else:
                reason = f"idempotency {claim.status.value}: another execution owns this key"
            return ladder.outcome(RecoveryStatus.ABORTED, reason)

    try:
        outcome = _run_ladder(
            bound_operation,
            ladder,
            policy=policy,
            clock=resolved_clock,
            guard=guard,
            abort=abort,
            action=action,
            hooks={
                "on_change_context": None if on_change_context is None else _bind_step(on_change_context),
                "on_evidence": None if on_evidence is None else _bind_step(on_evidence),
                "on_change_strategy": None if on_change_strategy is None else _bind_step(on_change_strategy),
            },
            delegate=None if delegate is None else _bind_step(delegate),
        )
    except BaseException:
        # An unexpected BaseException propagates, but it must not leave a
        # permanently held claim behind: release it so the caller's own retry
        # is not silently suppressed as a "duplicate".
        if claim is not None and registry is not None and key is not None:
            registry.fail(key, claim, RunOutcome(ok=False, error="recovery raised"), release=True)
        raise
    finally:
        ladder.flush()
    if claim is not None and registry is not None and key is not None:
        _settle(registry, key, claim, outcome)
    return outcome
