"""Typed failures for the resilience kit.

The kit's normal contract is a **return value**: an expected failure is a
status on a closed enum plus a human-readable reason, never a swallowed
exception and never a fake success (see :mod:`alpha.runtime.resilience.recovery`
for the closed status set). The exceptions here exist only for the cases a
caller must not be allowed to ignore, so a bug cannot hide behind a status:

* :class:`ResilienceError` - base class, so a host can catch the whole kit.
* :class:`TransientError` - the *declared* transient shape. The default retry
  classifier in :mod:`alpha.runtime.resilience.retry` retries this type and
  nothing else by default, which is what keeps a programming error (a
  ``TypeError`` in a call site, a missing attribute) from turning into a retry
  storm on a host that must survive years unattended.
* :class:`ControlSignal` - a **control-flow** signal, never a dependency
  failure. ``CircuitOpen``, ``BudgetExhausted`` and ``DeadlineExceeded`` are
  control signals: the retry loop must stop and report, not retry, because the
  thing that failed is the resilience envelope itself.
* Concrete errors: :class:`DeadlineExceeded`, :class:`RetryExhaustedError`,
  :class:`RetryAbortedError`, :class:`CircuitOpenError`,
  :class:`BudgetExhaustedError`, :class:`ProhibitedTransitionError`,
  :class:`IdempotencyError`.

Every one of them is re-exported lazily from the package facade so a host can
``except ResilienceError`` without importing a submodule.
"""

from __future__ import annotations

__all__ = [
    "BudgetExhaustedError",
    "CircuitOpenError",
    "ControlSignal",
    "DeadlineExceeded",
    "IdempotencyError",
    "ProhibitedTransitionError",
    "ResilienceError",
    "RetryAbortedError",
    "RetryExhaustedError",
    "TransientError",
]


class ResilienceError(Exception):
    """Base class for every error raised by the resilience kit."""


class TransientError(ResilienceError):
    """Declared-transient failure.

    Raise this (or let a caller's own classifier return ``True``) for a
    dependency failure a retry can plausibly fix: a rate limit, a dropped
    connection, a provider returning 503. Everything else defaults to
    terminal.
    """


class ControlSignal(ResilienceError):
    """Base class for signals that must stop a retry loop, not be retried."""


class DeadlineExceeded(ControlSignal):
    """A :class:`~alpha.runtime.resilience.clock.Deadline` expired."""

    def __init__(self, message: str, *, at: float | None = None) -> None:
        super().__init__(message)
        self.at = at


class RetryExhaustedError(ResilienceError):
    """The retry policy ran out of attempts, or the deadline cut it short.

    ``reason`` is one of ``"attempts_exhausted"`` or ``"deadline"`` and is
    part of the public contract: a caller branches on the reason, not on the
    message text. The final failure is kept on ``last_error`` (and chained via
    ``__cause__``) so nothing is lost.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        reason: str = "attempts_exhausted",
        last_error: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.reason = reason
        self.last_error = last_error


class RetryAbortedError(ControlSignal):
    """An external abort signal fired; the retry loop stopped on purpose."""

    def __init__(self, message: str, *, attempts: int = 0, last_error: BaseException | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


class CircuitOpenError(ControlSignal):
    """The circuit breaker refused the call (open window, or probe in flight)."""

    def __init__(self, message: str, *, state_reason: str = "", retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.state_reason = state_reason
        self.retry_after = retry_after


class BudgetExhaustedError(ControlSignal):
    """A :class:`~alpha.runtime.resilience.budget.ResourceBudget` refused the work.

    ``reason`` is one of ``"attempt_budget_exhausted"``,
    ``"cost_budget_exhausted"`` or ``"wall_clock_budget_exhausted"``.
    """

    def __init__(self, message: str, *, reason: str = "attempt_budget_exhausted", used: float = 0.0, limit: float = 0.0) -> None:
        super().__init__(message)
        self.reason = reason
        self.used = used
        self.limit = limit


class ProhibitedTransitionError(ResilienceError):
    """A healing loop tried to "fix" a symptom by weakening its own check."""

    def __init__(self, message: str, *, kind: str = "", source: str = "", target: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.source = source
        self.target = target


class IdempotencyError(ResilienceError):
    """An idempotency registry operation that cannot be honoured as asked.

    Raised (never silently repaired) for a completion/failure written for a key
    that was never claimed, and for a second write to a key that already holds
    an outcome - the at-most-once contract is not negotiable in-process.
    """


class AsyncOperationRefused(ControlSignal):
    """An ``async def`` operation was handed to a synchronous resilience helper.

    This is a fail-CLOSED guard, and it exists because the alternative is the
    worst possible bug in a resilience primitive: the sync wrappers call
    ``operation()``, receive a coroutine object, and report ``succeeded`` -
    while the work never ran and Python emits only a "coroutine was never
    awaited" warning that is easy to miss in a busy log. A resilience kit that
    can certify work that did not happen is worse than no kit at all, so the
    call is refused loudly instead.

    Async callers keep their own ``await``/``asyncio.sleep`` and adopt the
    policy math and the breaker; see the package docstring for the exact
    adoption pattern.
    """
