"""A per-instance circuit breaker on the injected clock.

Closed -> open after N consecutive failures. Open -> half-open after a reset
timeout, admitting exactly ONE probe. A successful probe closes the circuit and
clears the failure count; a failed probe re-opens it for another window.

Why it exists here: a retry loop on its own keeps hammering a dependency that
is already down, which is how a long-running host turns a provider outage into
a self-inflicted CPU burn. The breaker is the "stop calling for a while" half
of the standard pair (Fowler's *Circuit Breaker* / Nygard's *Release It!*
ch. 4); :mod:`alpha.runtime.resilience.retry` is the "try again carefully"
half.

Guarantees and deliberate differences
-------------------------------------
* **Per-instance state.** There is no module-level breaker and no shared global.
  Two breakers in one process are independent; a host owns their lifetime.
* **The half-open probe is single-flight.** Under the lock, one caller wins the
  probe; every other caller is refused with :class:`CircuitOpenError` carrying
  the same ``state_reason`` the winner is running under. This mirrors
  ``alpha/models/system_one.py::SystemOneClient`` (which uses a
  ``_half_open_probe`` flag plus a generation fence) and
  ``alpha/agents/middlewares/llm_error_handling_middleware.py`` (which uses a
  probe token), so a future delegation is a behaviour-preserving swap.
* **Late results cannot corrupt a newer window.** :meth:`acquire` returns a
  :class:`CircuitPermit` stamped with the breaker's current *generation*, and
  ``record_success``/``record_failure``/``release_probe`` ignore a permit from an
  older generation. A call admitted before the breaker opened therefore cannot
  re-open or close the window that replaced it. The same generation fence exists
  in ``SystemOneClient._release_request_slot`` and the middleware's probe token;
  without it a slow in-flight call can trip a breaker that has already recovered.
  Passing no permit is the explicit, unfenced path.
* **Every refusal is disclosed.** ``state_reason`` is always a non-empty human
  string; the exception carries it plus ``retry_after`` seconds.
* **Any exception counts as a failure** in :meth:`CircuitBreaker.call`. Callers
  that must not open the circuit for a specific error (e.g. an invalid request,
  which the LLM middleware deliberately excludes) should use the explicit
  :meth:`record_success`/:meth:`record_failure` API around their own call
  instead of :meth:`call`. That difference is intentional and documented rather
  than silently baked in.
* **Reset is monotonic.** The breaker only ever moves forward within a window;
  the only state change is caused by a recorded outcome or by clock expiry.

References (paraphrased, nothing copied):
* Martin Fowler, *Circuit Breaker* (bliki, 2004): three states, a failure
  threshold, and a single probe to test recovery.
* Michael Nygard, *Release It!*, 2nd ed. (2018), ch. 4: the breaker protects the
  caller from a dependency that is already broken, and needs a timeout, not a
  retry, to probe for recovery.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha.runtime.resilience.clock import Clock, coerce_clock, require_delay
from alpha.runtime.resilience.errors import CircuitOpenError

__all__ = ["CircuitBreaker", "CircuitOpenError", "CircuitPermit", "CircuitSnapshot", "CircuitState"]


class CircuitState(StrEnum):
    """Closed set of breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitPermit:
    """Admission token: which generation of the breaker a call was admitted to.

    ``generation`` increments every time the breaker changes state, so an outcome
    from a call admitted before a transition cannot be applied to the state that
    replaced it.
    """

    generation: int
    is_probe: bool = False
    name: str = ""


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    """Immutable view of a breaker, for metrics and honest diagnostics."""

    name: str
    state: CircuitState
    state_reason: str
    failure_count: int
    consecutive_failures: int
    opened_at: float | None
    reset_timeout: float
    retry_after: float
    """Seconds until a probe is admitted; 0 when not open."""

    @property
    def is_closed(self) -> bool:
        return self.state is CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        return self.state is CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        return self.state is CircuitState.HALF_OPEN


class CircuitBreaker:
    """Failure-threshold breaker with a single-flight half-open probe.

    ``state``/``state_reason`` are computed on read, so an expired open window
    is reported as half-open without a background thread mutating anything.
    """

    __slots__ = (
        "_clock",
        "_consecutive_failures",
        "_failure_count",
        "_failure_threshold",
        "_generation",
        "_half_open_probe",
        "_lock",
        "_name",
        "_opened_at",
        "_reason",
        "_reset_timeout",
    )

    def __init__(
        self,
        *,
        name: str = "circuit",
        failure_threshold: int = 3,
        reset_timeout: float = 30.0,
        clock: Clock | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._name = name
        self._failure_threshold = int(failure_threshold)
        self._reset_timeout = require_delay(reset_timeout, name="reset_timeout")
        self._clock = coerce_clock(clock)
        self._lock = threading.RLock()
        self._consecutive_failures = 0
        self._failure_count = 0
        self._opened_at: float | None = None
        self._half_open_probe = False
        self._generation = 0
        self._reason = "closed: no failures recorded"

    # -- introspection ----------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def failure_threshold(self) -> int:
        return self._failure_threshold

    @property
    def reset_timeout(self) -> float:
        return self._reset_timeout

    def _derive(self) -> tuple[CircuitState, float, bool]:
        """Return ``(state, retry_after, probe_in_flight)`` under the lock."""
        if self._opened_at is None:
            return CircuitState.CLOSED, 0.0, False
        elapsed = self._clock.now() - self._opened_at
        if elapsed >= self._reset_timeout:
            # The window elapsed: the breaker is half-open and a probe is pending
            # admission. ``_half_open_probe`` gates who may take it.
            return CircuitState.HALF_OPEN, 0.0, self._half_open_probe
        return CircuitState.OPEN, max(0.0, self._reset_timeout - elapsed), self._half_open_probe

    def state(self) -> CircuitState:
        """Current state, with the open window already expired."""
        with self._lock:
            return self._derive()[0]

    def state_reason(self) -> str:
        """Non-empty disclosure of why the breaker is in its current state."""
        with self._lock:
            state, retry_after, probe = self._derive()
            if state is CircuitState.CLOSED:
                return self._reason
            if state is CircuitState.HALF_OPEN:
                in_flight = "probe in flight" if probe else "awaiting single probe"
                return f"half_open: reset timeout elapsed after {self._reset_timeout!r}s; {in_flight}"
            return f"{self._reason}; retry in {retry_after:.3f}s"

    def snapshot(self) -> CircuitSnapshot:
        """Immutable snapshot for metrics/diagnostics."""
        with self._lock:
            state, retry_after, _probe = self._derive()
            return CircuitSnapshot(
                name=self._name,
                state=state,
                state_reason=self.state_reason(),
                failure_count=self._failure_count,
                consecutive_failures=self._consecutive_failures,
                opened_at=self._opened_at,
                reset_timeout=self._reset_timeout,
                retry_after=retry_after,
            )

    # -- admission --------------------------------------------------------

    def allow_request(self) -> bool:
        """True when a call may proceed right now (does not take the probe)."""
        with self._lock:
            state, _retry_after, _probe = self._derive()
            return state is not CircuitState.OPEN

    def acquire(self) -> CircuitPermit:
        """Admit one call, taking the half-open probe slot if applicable.

        Returns a :class:`CircuitPermit` stamped with the current generation;
        pass it back to ``record_success``/``record_failure``/
        :meth:`release_probe` so a late result cannot be applied to a newer
        window. Raises :class:`CircuitOpenError` when the breaker refuses.
        """
        with self._lock:
            state, retry_after, probe = self._derive()
            if state is CircuitState.OPEN:
                raise CircuitOpenError(
                    f"circuit {self._name!r} is open",
                    state_reason=self.state_reason(),
                    retry_after=retry_after,
                )
            if state is CircuitState.HALF_OPEN:
                if probe:
                    raise CircuitOpenError(
                        f"circuit {self._name!r} half-open probe already in flight",
                        state_reason=self.state_reason(),
                        retry_after=0.0,
                    )
                self._half_open_probe = True
                self._reason = f"half_open: probing recovery after {self._reset_timeout!r}s open window"
                return CircuitPermit(self._generation, is_probe=True, name=self._name)
            return CircuitPermit(self._generation, is_probe=False, name=self._name)

    def _stale(self, permit: CircuitPermit | None) -> bool:
        """True when a permit belongs to a superseded breaker generation."""
        return permit is not None and permit.generation != self._generation

    def release_probe(self, permit: CircuitPermit | None = None) -> None:
        """Release the half-open probe without recording an outcome.

        For callers whose admission is consumed by something other than a
        success/failure (a cancellation, a control-flow signal, a request that
        was abandoned). Without this the breaker would fast-fail forever waiting
        for a probe nobody is running. A stale ``permit`` is ignored so an older
        call cannot release a newer call's probe.
        """
        with self._lock:
            if self._stale(permit):
                return
            self._half_open_probe = False

    # -- outcome recording ------------------------------------------------

    def record_success(self, permit: CircuitPermit | None = None) -> CircuitSnapshot:
        """Record a success: reset the consecutive-failure count and close.

        A stale ``permit`` (from before the breaker last changed state) is
        ignored: the call it belongs to describes a window that no longer
        exists.
        """
        with self._lock:
            if self._stale(permit):
                return self.snapshot()
            self._consecutive_failures = 0
            self._failure_count = 0
            self._opened_at = None
            self._half_open_probe = False
            self._generation += 1
            self._reason = "closed: last call succeeded"
            return self.snapshot()

    def record_failure(self, reason: str = "", permit: CircuitPermit | None = None) -> CircuitSnapshot:
        """Record a failure; open the circuit at the threshold.

        A failure while the window has elapsed (i.e. the failing call was the
        half-open probe) re-opens immediately, even if the count is below the
        threshold: the probe is the evidence that recovery did not happen. A
        stale ``permit`` is ignored (see :meth:`record_success`).
        """
        with self._lock:
            if self._stale(permit):
                return self.snapshot()
            self._consecutive_failures += 1
            self._failure_count += 1
            self._half_open_probe = False
            detail = f": {reason}" if reason else ""
            if self._opened_at is not None and self._derive()[0] is CircuitState.HALF_OPEN:
                self._opened_at = self._clock.now()
                self._reason = f"open: half-open probe failed{detail}"
                self._generation += 1
            elif self._consecutive_failures >= self._failure_threshold:
                self._opened_at = self._clock.now()
                self._reason = f"open: {self._consecutive_failures} consecutive failures reached threshold {self._failure_threshold}{detail}"
                self._generation += 1
            else:
                self._reason = f"closed: {self._consecutive_failures}/{self._failure_threshold} consecutive failures{detail}"
            return self.snapshot()

    def reset(self, reason: str = "manual reset") -> CircuitSnapshot:
        """Force the breaker closed and clear all counters."""
        with self._lock:
            self._consecutive_failures = 0
            self._failure_count = 0
            self._opened_at = None
            self._half_open_probe = False
            self._generation += 1
            self._reason = f"closed: {reason}"
            return self.snapshot()

    # -- call helper ------------------------------------------------------

    def call(self, operation: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Run ``operation`` under the breaker and return its real value.

        Any exception from ``operation`` is recorded as a failure and
        re-raised unchanged; the return value is whatever ``operation``
        returned (``None`` included). A :class:`CircuitOpenError` from admission
        is raised before ``operation`` runs, so a refused call has no side
        effect at all. The outcome is fenced by the admission permit, so a call
        that started before a transition cannot record into the new state.
        """
        permit = self.acquire()
        try:
            value = operation(*args, **kwargs)
        except BaseException as exc:
            self.record_failure(reason=type(exc).__name__, permit=permit)
            raise
        self.record_success(permit=permit)
        return value

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        snap = self.snapshot()
        return f"CircuitBreaker(name={self._name!r}, state={snap.state.value!r})"
