"""The kit's only source of time: an injectable clock, sleeper and deadline.

Nothing else in :mod:`alpha.runtime.resilience` calls ``time.time()``,
``time.monotonic()`` or ``time.sleep()``. Every elapsed-time question ("is the
deadline gone?", "how long until the breaker may probe?", "has this
idempotency record aged out?") is asked of an injected :class:`Clock`, and
every wait is performed by an injected sleeper. That is what makes the package
deterministic under test: :class:`ManualClock` advances only when a test says
so, so retry delays, circuit reset windows, budget expiry and idempotency TTL
all replay identically with zero real sleeping.

Design notes
------------
* ``SystemClock`` uses ``time.monotonic()``, never ``time.time()``: deadlines
  and cooldowns must not move when the host clock steps (NTP correction, a
  manual change). It is the only module in the package allowed to touch
  :mod:`time`; a test pins that by AST-scanning the package.
* :class:`ManualClock` never sleeps for real. ``sleep(d)`` records ``d`` and
  jumps the clock forward by ``d``, which is exactly the semantics a retry
  loop needs to be testable: a "sleep" is a *decision to wait*, not a wait.
* The clock is monotonic by construction: ``advance`` rejects negatives and
  ``set`` refuses to move time backwards, so a test can never accidentally
  make a timeout look satisfied.

References (paraphrased, nothing copied):
* Marc Brooker, *Exponential Backoff And Jitter* (AWS Architecture Blog, 2015)
  and *Timeouts, retries, and backoff with jitter* (AWS Builders' Library,
  2019): a backoff must be bounded by a deadline, and jitter must be
  injectable to be testable at all.
* Michael Nygard, *Release It!* (2nd ed., 2018), ch. 4 (circuit breaker) and
  ch. 3 (timeouts): a timeout without an injected clock is a test-time flake.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from alpha.runtime.resilience.errors import DeadlineExceeded

__all__ = [
    "Clock",
    "Deadline",
    "ManualClock",
    "Sleeper",
    "SystemClock",
    "SystemSleeper",
    "coerce_clock",
    "require_delay",
    "resolve_sleeper",
]


def require_delay(seconds: float, *, name: str = "delay") -> float:
    """Validate a wait duration: finite and non-negative."""
    value = float(seconds)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {seconds!r}")
    if value < 0.0:
        raise ValueError(f"{name} must be >= 0, got {seconds!r}")
    return value


@runtime_checkable
class Clock(Protocol):
    """A source of elapsed time. The kit never reads time any other way."""

    def now(self) -> float:
        """Seconds on an arbitrary monotonic timeline."""


@runtime_checkable
class Sleeper(Protocol):
    """Something that can wait for a duration of *clock* time."""

    def sleep(self, seconds: float) -> None:
        """Wait ``seconds`` (already validated non-negative)."""


class SystemSleeper:
    """Real waiting, via :func:`time.sleep`. Used only by :class:`SystemClock`."""

    __slots__ = ()

    def sleep(self, seconds: float) -> None:
        delay = require_delay(seconds, name="seconds")
        if delay:
            time.sleep(delay)


class SystemClock:
    """Production clock: :func:`time.monotonic` plus real sleeping."""

    __slots__ = ()

    def now(self) -> float:
        """Monotonic seconds. Immune to wall-clock steps, unlike ``time.time``."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Really wait. Tests must inject a :class:`ManualClock` instead."""
        delay = require_delay(seconds, name="seconds")
        if delay:
            time.sleep(delay)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "SystemClock()"


class ManualClock:
    """A clock that only moves when told to. Every test in the kit uses one.

    ``sleep`` is instant: it records the requested duration and advances the
    timeline by exactly that amount, so a retry loop that slept for 30 virtual
    seconds costs no wall-clock time and lands on the same instant on every run.
    """

    __slots__ = ("_now", "sleeps")

    def __init__(self, start: float = 0.0) -> None:
        self._now = require_delay(start, name="start")
        self.sleeps: list[float] = []

    def now(self) -> float:
        """Current virtual time."""
        return self._now

    def sleep(self, seconds: float) -> None:
        """Record and instantly elapse a wait. Never blocks."""
        delay = require_delay(seconds, name="seconds")
        self.sleeps.append(delay)
        self._now += delay

    def advance(self, seconds: float) -> float:
        """Move time forward by ``seconds``; returns the new time."""
        delta = require_delay(seconds, name="seconds")
        self._now += delta
        return self._now

    def set(self, value: float) -> float:
        """Jump to an absolute time. Never allowed to move backwards."""
        moment = require_delay(value, name="value")
        if moment < self._now:
            raise ValueError(f"ManualClock is monotonic: cannot set {moment!r} before {self._now!r}")
        self._now = moment
        return self._now

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"ManualClock(now={self._now!r}, sleeps={len(self.sleeps)})"


def coerce_clock(clock: Clock | None) -> Clock:
    """Return ``clock`` or a :class:`SystemClock` when it is ``None``."""
    if clock is None:
        return SystemClock()
    if not callable(getattr(clock, "now", None)):
        raise TypeError(f"clock must expose now(), got {type(clock).__name__}")
    return clock


def resolve_sleeper(clock: Clock, sleeper: Callable[[float], None] | None = None) -> Callable[[float], None]:
    """Resolve the wait function: explicit injection wins, else the clock's own.

    A clock that can wait (both shipped clocks can) is the default, which is
    what lets a ``ManualClock`` make a whole retry ladder virtual. A read-only
    third-party clock falls back to :class:`SystemSleeper` rather than failing.
    """
    if sleeper is not None:
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        return sleeper
    candidate = getattr(clock, "sleep", None)
    if callable(candidate):
        return candidate
    return SystemSleeper().sleep


class Deadline:
    """A wall-clock budget measured on an injected clock.

    ``Deadline.after(clock, 30)`` is 30 *clock* seconds, not 30 wall seconds:
    under a :class:`ManualClock` it expires when the test advances the clock.
    ``remaining()`` clamps at zero so a caller can compare without guarding.
    """

    __slots__ = ("_clock", "_label", "expires_at")

    def __init__(self, clock: Clock, expires_at: float, *, label: str = "") -> None:
        self._clock = coerce_clock(clock)
        self._label = label
        self.expires_at = float(expires_at)

    @classmethod
    def after(cls, clock: Clock, seconds: float, *, label: str = "") -> Deadline:
        """Build a deadline ``seconds`` of clock time from now."""
        wait = require_delay(seconds, name="seconds")
        resolved = coerce_clock(clock)
        return cls(resolved, resolved.now() + wait, label=label)

    @property
    def clock(self) -> Clock:
        """The clock this deadline is measured on."""
        return self._clock

    @property
    def label(self) -> str:
        return self._label

    def now(self) -> float:
        """Current time on the deadline's clock."""
        return self._clock.now()

    def remaining(self) -> float:
        """Seconds left, clamped at zero."""
        return max(0.0, self.expires_at - self._clock.now())

    def expired(self) -> bool:
        """True once the clock has reached ``expires_at``."""
        return self._clock.now() >= self.expires_at

    def fits(self, seconds: float) -> bool:
        """True when a wait of ``seconds`` would land strictly before expiry."""
        return require_delay(seconds, name="seconds") < self.remaining()

    def check(self) -> None:
        """Raise :class:`DeadlineExceeded` when the budget is gone."""
        if self.expired():
            suffix = f" ({self._label})" if self._label else ""
            raise DeadlineExceeded(f"deadline exceeded at {self.expires_at!r}{suffix}", at=self.expires_at)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Deadline(expires_at={self.expires_at!r}, remaining={self.remaining()!r}, label={self._label!r})"
