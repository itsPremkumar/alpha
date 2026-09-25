"""Bounded retry: policy, classification, deadlines and an attempt trail.

The point of this module is not "try again" - it is **try again a bounded
number of times, and be able to prove which bound stopped you**. Three rules
make that true:

1. **Bounded attempts.** ``policy.attempts`` is a hard ceiling; the loop
   variable increases on every pass, so the loop provably exits.
2. **A shared deadline.** When a :class:`~alpha.runtime.resilience.clock.Deadline`
   is supplied, a wait that does not fit inside the remaining budget ends the
   sequence with ``RetryExhaustedError(reason="deadline")`` instead of starting
   an attempt that cannot finish in time.
3. **Non-retryable errors propagate immediately.** A programming error (a bad
   call site, a ``TypeError``, a validation failure) is re-raised on the first
   attempt. Retrying it only multiplies the damage - the "retry storm" failure
   mode described by Kleppmann (DDIA ch. 8) and the AWS Architecture Blog post
   *Exponential Backoff and Jitter* (Brooker, 2015).

A failed attempt is classified into a closed set (:class:`RetryDecision`):
``retryable`` (sleep and try again), ``terminal`` (propagate now) and ``abort``
(a control signal - the resilience envelope itself failed). Control signals
(:class:`~alpha.runtime.resilience.errors.ControlSignal`) are never retried even
if a caller classifier says so: retrying "the breaker is open" or "the budget is
gone" is the loop this package exists to prevent. The attempt trail
(:class:`AttemptRecord`) records what actually happened, including success.

Jitter is **injected**, never sampled from module-global randomness, so a test
pins the exact delay sequence. :class:`FullJitter` takes a ``random_fn``; the
default (``random.random``) is only used when a host builds a policy itself.

References (paraphrased, nothing copied):
* Marc Brooker, *Exponential Backoff And Jitter* (2015): jitter decorrelates a
  fleet, and has to be a parameter to stay testable.
* AWS Builders' Library, *Timeouts, retries and backoff with jitter* (2019): the
  retry budget belongs to the whole request and is bounded by a deadline.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.runtime.resilience.clock import Clock, Deadline, coerce_clock, require_delay, resolve_sleeper
from alpha.runtime.resilience.errors import (
    ControlSignal,
    RetryAbortedError,
    RetryExhaustedError,
    TransientError,
)

__all__ = [
    "AttemptOutcome",
    "AttemptRecord",
    "AttemptTrail",
    "FullJitter",
    "NoJitter",
    "RetryDecision",
    "RetryPolicy",
    "RetryResult",
    "classify_exception",
    "retry_call",
]


class RetryDecision(StrEnum):
    """Closed classification of one *failed* attempt."""

    RETRYABLE = "retryable"
    """Transient: wait, then attempt again inside the remaining bounds."""

    TERMINAL = "terminal"
    """Permanent for this policy: propagate immediately, no further attempts."""

    ABORT = "abort"
    """The resilience envelope failed (circuit / budget / deadline / abort)."""


class AttemptOutcome(StrEnum):
    """Closed per-attempt outcome recorded in the trail."""

    SUCCEEDED = "succeeded"
    """The operation returned (including returning ``None``)."""

    RETRY_SCHEDULED = "retry_scheduled"
    """Retryable error; another attempt will run after ``delay_after``."""

    EXHAUSTED = "exhausted"
    """Retryable error, but the attempt ceiling or the deadline stopped the loop."""

    NOT_RETRYABLE = "not_retryable"
    """Terminal error; it propagated on this attempt."""

    ABORTED = "aborted"
    """A control signal stopped the loop on this attempt."""


def _identity_delay(nominal: float, attempt: int) -> float:
    """No-op jitter: return the nominal delay untouched."""
    return nominal


@dataclass(frozen=True, slots=True)
class Jitter:
    """Injectable delay decorator: ``fn(nominal, attempt) -> delay``.

    ``Jitter()`` (the default identity ``fn``) is equivalent to :class:`NoJitter`;
    it exists so a caller can wrap its own deterministic function without
    importing a third strategy type.
    """

    fn: Callable[[float, int], float] = _identity_delay

    def __call__(self, nominal: float, attempt: int) -> float:
        return require_delay(self.fn(float(nominal), int(attempt)), name="jittered delay")


@dataclass(frozen=True, slots=True)
class NoJitter:
    """Deterministic, un-jittered schedule (the nominal backoff)."""

    def __call__(self, nominal: float, attempt: int) -> float:
        return require_delay(nominal, name="nominal delay")


@dataclass(frozen=True, slots=True)
class FullJitter:
    """Uniform jitter in ``[0, nominal]`` (Brooker 2015, "full jitter").

    ``random_fn`` is injected: a test passes ``lambda: 0.5`` and gets a fully
    deterministic half-nominal schedule, while a host passes ``random.random``
    (the default) to decorrelate a fleet.
    """

    random_fn: Callable[[], float] = random.random

    def __call__(self, nominal: float, attempt: int) -> float:
        base = require_delay(nominal, name="nominal delay")
        return require_delay(self.random_fn() * base, name="jittered delay")


def classify_exception(exc: BaseException) -> RetryDecision:
    """Default classifier.

    ``retryable``
        :class:`TransientError` plus the two unambiguous transport shapes
        (``TimeoutError``, ``ConnectionError``) - failures a retry can plausibly
        fix. ``ConnectionError``/``TimeoutError`` are used rather than the wider
        ``OSError`` so a ``FileNotFoundError`` (a bug, not an outage) is never
        retried.
    ``abort``
        any :class:`ControlSignal` - the envelope, not the dependency, failed.
    ``terminal``
        everything else, propagated on the first attempt.
    """
    if isinstance(exc, ControlSignal):
        return RetryDecision.ABORT
    if isinstance(exc, (TransientError, TimeoutError, ConnectionError)):
        return RetryDecision.RETRYABLE
    return RetryDecision.TERMINAL


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Immutable retry shape. ``attempts`` is a hard ceiling, not a hint."""

    attempts: int = 3
    base_delay: float = 0.1
    multiplier: float = 2.0
    max_delay: float = 30.0
    jitter: Any = field(default_factory=NoJitter)
    """A ``Jitter``/``NoJitter``/``FullJitter`` callable ``(nominal, attempt)``."""

    classifier: Callable[[BaseException], RetryDecision] = classify_exception
    """Injected outcome classifier; returns one of :class:`RetryDecision`."""

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RetryPolicy.attempts must be >= 1")
        for name in ("base_delay", "multiplier", "max_delay"):
            value = float(getattr(self, name))
            if value < 0:
                raise ValueError(f"RetryPolicy.{name} must be >= 0, got {value!r}")
        if self.multiplier < 1:
            raise ValueError("RetryPolicy.multiplier must be >= 1")
        if not callable(self.jitter):
            raise TypeError("RetryPolicy.jitter must be callable as jitter(nominal, attempt)")
        if not callable(self.classifier):
            raise TypeError("RetryPolicy.classifier must be callable")

    def nominal_delay(self, attempt: int) -> float:
        """Un-jittered delay before retry ``attempt`` (1-based), capped."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        if self.base_delay == 0:
            return 0.0
        raw = self.base_delay * (self.multiplier ** (attempt - 1))
        return min(self.max_delay, raw) if self.max_delay > 0 else raw

    def delay_for(self, attempt: int) -> float:
        """Jittered, capped delay before retry ``attempt`` (1-based)."""
        nominal = self.nominal_delay(attempt)
        if nominal == 0:
            return 0.0
        jittered = float(self.jitter(nominal, attempt))
        return min(self.max_delay, jittered) if self.max_delay > 0 else jittered


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One line of the retry trail: what ran, how long, and why we stopped."""

    attempt: int
    """1-based attempt number *within one retry call*. A recovery ladder runs
    several retry calls against one shared trail, so the numbering restarts per
    round; ``RecoveryOutcome.attempts`` is the ladder-wide total."""

    outcome: AttemptOutcome
    started_at: float
    """Clock reading when the attempt began."""

    finished_at: float
    """Clock reading when the attempt ended."""

    delay_after: float
    """Delay waited (or refused) after this attempt; always 0 on success."""

    error_type: str = ""
    error: str = ""

    @property
    def elapsed(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def succeeded(self) -> bool:
        return self.outcome is AttemptOutcome.SUCCEEDED


@dataclass(slots=True)
class AttemptTrail:
    """Bounded, append-only trail of attempts for one logical operation.

    ``records`` keeps at most ``max_records`` entries (oldest trimmed first) so
    an unbounded caller cannot grow it forever, while ``total`` counts every
    record ever added - the honest "how much work did this take" number that
    survives trimming.
    """

    max_records: int = 256
    records: list[AttemptRecord] = field(default_factory=list)
    total: int = 0

    def add(self, record: AttemptRecord) -> None:
        self.records.append(record)
        self.total += 1
        overflow = len(self.records) - self.max_records
        if overflow > 0:
            del self.records[:overflow]

    @property
    def attempts(self) -> int:
        """Total attempts recorded, including any trimmed from the buffer."""
        return self.total

    @property
    def retained(self) -> int:
        return len(self.records)

    def last(self) -> AttemptRecord | None:
        return self.records[-1] if self.records else None

    def __iter__(self) -> Iterator[AttemptRecord]:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)


@dataclass(frozen=True, slots=True)
class RetryResult:
    """A real return value plus the trail that produced it."""

    value: Any
    attempts: int
    trail: AttemptTrail

    @property
    def succeeded(self) -> bool:
        return True


def retry_call(
    operation: Callable[[], Any],
    policy: RetryPolicy,
    *,
    clock: Clock | None = None,
    deadline: Deadline | None = None,
    sleeper: Callable[[float], None] | None = None,
    abort: Callable[[], bool] | None = None,
    trail: AttemptTrail | None = None,
) -> RetryResult:
    """Call ``operation`` under ``policy``; return a real value or raise.

    Contract:

    * success -> :class:`RetryResult` whose ``value`` is what ``operation``
      actually returned. Returning ``None`` is a success, not a failure.
    * retryable error with attempts left -> wait ``policy.delay_for(attempt)`` on
      the injected sleeper, then try again.
    * retryable error with no attempts left -> raise
      :class:`~alpha.runtime.resilience.errors.RetryExhaustedError`
      (``reason="attempts_exhausted"``) from the final error.
    * deadline that cannot fit the next wait -> raise the same error with
      ``reason="deadline"``, from the final error. A wait that would consume the
      whole remaining budget is refused too: an attempt starting at expiry is an
      attempt that will breach the budget.
    * terminal error -> the original exception propagates immediately.
    * control signal (``CircuitOpen``, ``BudgetExhausted``, ...) -> propagates
      immediately; it is never retried.
    * ``abort()`` truthy at the top of an iteration -> raise
      :class:`~alpha.runtime.resilience.errors.RetryAbortedError`.

    Termination: the loop variable ``attempt`` increases once per iteration and
    the loop returns or raises on the iteration where ``attempt ==
    policy.attempts``; a deadline can only end it earlier. So the call performs
    at most ``policy.attempts`` executions of ``operation``.
    """
    resolved_clock = coerce_clock(clock)
    wait = resolve_sleeper(resolved_clock, sleeper)
    record_trail = trail if trail is not None else AttemptTrail()
    attempt = 0
    last_error: BaseException | None = None

    while True:
        if abort is not None and abort():
            raise RetryAbortedError(f"retry aborted before attempt {attempt + 1}", attempts=attempt, last_error=last_error)
        if deadline is not None and deadline.expired():
            raise RetryExhaustedError(
                f"deadline reached before attempt {attempt + 1}",
                attempts=attempt,
                reason="deadline",
                last_error=last_error,
            )

        attempt += 1
        started_at = resolved_clock.now()
        try:
            value = operation()
        except BaseException as exc:  # classified below; terminal/abort re-raised as-is
            stop_reason = ""
            if isinstance(exc, ControlSignal):
                # The envelope itself failed (breaker / budget / deadline /
                # abort). Retrying it is the loop this package exists to
                # prevent, so a caller classifier cannot override this.
                decision = RetryDecision.ABORT
            else:
                decision = policy.classifier(exc)
            if decision is RetryDecision.ABORT:
                outcome = AttemptOutcome.ABORTED
            elif decision is RetryDecision.TERMINAL:
                outcome = AttemptOutcome.NOT_RETRYABLE
            elif attempt >= policy.attempts:
                outcome = AttemptOutcome.EXHAUSTED
                stop_reason = "attempts_exhausted"
            else:
                outcome = AttemptOutcome.RETRY_SCHEDULED

            delay = policy.delay_for(attempt) if outcome is AttemptOutcome.RETRY_SCHEDULED else 0.0
            if outcome is AttemptOutcome.RETRY_SCHEDULED and deadline is not None and not deadline.fits(delay):
                # The backoff does not fit the shared deadline. Starting an
                # attempt at expiry would breach the budget we promised, so the
                # sequence ends here and says so.
                outcome = AttemptOutcome.EXHAUSTED
                stop_reason = "deadline"
                delay = 0.0
            record_trail.add(AttemptRecord(attempt, outcome, started_at, resolved_clock.now(), delay, type(exc).__name__, str(exc)))

            if outcome is AttemptOutcome.NOT_RETRYABLE or outcome is AttemptOutcome.ABORTED:
                raise
            last_error = exc
            if outcome is AttemptOutcome.EXHAUSTED:
                raise RetryExhaustedError(
                    f"retryable error after {attempt} attempt(s) ({stop_reason})",
                    attempts=attempt,
                    reason=stop_reason,
                    last_error=exc,
                ) from exc
            wait(delay)
            continue

        record_trail.add(AttemptRecord(attempt, AttemptOutcome.SUCCEEDED, started_at, resolved_clock.now(), 0.0))
        return RetryResult(value=value, attempts=attempt, trail=record_trail)
