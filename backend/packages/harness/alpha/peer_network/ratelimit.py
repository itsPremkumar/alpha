"""Bounded throttle for the unauthenticated Alpha pairing ingress.

``POST /api/peer-network/remote/pair`` is intentionally reachable without a
browser session so a second Alpha installation can pair itself.  The shared
pairing code is 256 bits of ``secrets.token_urlsafe(32)``, so the credential
itself is not brute-forceable, but the route is still an unauthenticated,
state-mutating endpoint and must not be an unlimited write amplifier.

This module is the throttle for that route.  It is deliberately dependency-free
(synchronous, in-memory, ``time.monotonic``) so it can run on the event loop
without yielding, and it mirrors the login throttle in
``app/gateway/routers/auth.py``: fixed windows, escalating lockout for
consecutive failures, a global circuit breaker, bounded key tracking, and
fail-closed refusals.

Two tiers, because a per-caller key alone is worthless here:

* **Per claimed identity** — the ``agent_id`` in the pairing card.  The caller
  supplies it, so it is fully attacker-controlled and trivially rotated; this
  tier only stops a *targeted* flood at one identity.
* **Global** — one reserved, never-evicted record for the whole installation.
  This is the tier that cannot be side-stepped by rotating the claimed
  identity, and it is the actual bound on how fast an unauthenticated caller
  can mutate this installation.

A successful pairing clears the caller tier and the global failure count: a
legitimate pairing proves the code is in the operator's hands, and refusing
further attempts after that would only be hostile to the operator.

Nothing here logs or returns a pairing code, a token, or a key: the only
observable state is attempt/failure counts, which are safe to publish in
``/api/peer-network/status``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Reserved key for the installation-wide budget.  A NUL-prefixed string can
# never collide with a validated ``agent_id`` (^[A-Za-z0-9_.:-]{1,128}$).
GLOBAL_KEY = "\x00alpha-peer-pairing-global"

# Per claimed identity.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_WINDOW_SECONDS = 300.0
DEFAULT_MAX_FAILURES = 3
DEFAULT_BASE_LOCKOUT_SECONDS = 5.0
# Installation-wide budget (the tier a rotated identity cannot escape).
DEFAULT_GLOBAL_MAX_ATTEMPTS = 30
DEFAULT_GLOBAL_WINDOW_SECONDS = 60.0
# Circuit breaker: trips after this many consecutive failures, globally.
DEFAULT_BREAKER_FAILURES = 10
DEFAULT_BREAKER_COOLDOWN_SECONDS = 60.0
DEFAULT_MAX_LOCKOUT_SECONDS = 900.0
# Bounded key tracking.  The map holds one small record per *live* caller.
DEFAULT_MAX_TRACKED_KEYS = 512

# Hard ceilings.  An operator may tighten the throttle but cannot raise it past
# these values: a config knob that can disable its own security control is not
# a knob, it is a bypass.
ATTEMPTS_CEILING = 100
GLOBAL_ATTEMPTS_CEILING = 1000
FAILURES_CEILING = 100
WINDOW_FLOOR_SECONDS = 1.0
WINDOW_CEILING_SECONDS = 3600.0
LOCKOUT_FLOOR_SECONDS = 1.0
LOCKOUT_CEILING_SECONDS = 3600.0
TRACKED_KEYS_CEILING = 100_000


def _clamp(value: int | float, low: int | float, high: int | float) -> int | float:
    return max(low, min(value, high))


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    """A refusal.  ``retry_after_seconds`` is a floor for the caller to wait."""

    reason: str
    retry_after_seconds: float

    def as_message(self) -> str:
        seconds = max(1, int(math.ceil(self.retry_after_seconds)))
        return f"Pairing throttled: {self.reason}. Retry in {seconds}s."


@dataclass(slots=True)
class _Record:
    window_started: float
    attempts: int = 0
    failures: int = 0
    locked_until: float = 0.0
    trips: int = 0

    def expiry(self, window_seconds: float) -> float:
        return max(self.locked_until, self.window_started + window_seconds)


class PairingThrottle:
    """Two-tier fixed-window throttle with escalating backoff and a breaker.

    All state is in-process.  The peer network is installation-scoped and
    single-process by design (see the module docstring in ``service.py``), so a
    shared limiter would add a dependency without adding a guarantee; the
    global tier still bounds an unauthenticated flood to one process.
    """

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_failures: int = DEFAULT_MAX_FAILURES,
        base_lockout_seconds: float = DEFAULT_BASE_LOCKOUT_SECONDS,
        max_lockout_seconds: float = DEFAULT_MAX_LOCKOUT_SECONDS,
        global_max_attempts: int = DEFAULT_GLOBAL_MAX_ATTEMPTS,
        global_window_seconds: float = DEFAULT_GLOBAL_WINDOW_SECONDS,
        breaker_failures: int = DEFAULT_BREAKER_FAILURES,
        breaker_cooldown_seconds: float = DEFAULT_BREAKER_COOLDOWN_SECONDS,
        max_tracked_keys: int = DEFAULT_MAX_TRACKED_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = int(_clamp(int(max_attempts), 1, ATTEMPTS_CEILING))
        self.window_seconds = float(_clamp(float(window_seconds), WINDOW_FLOOR_SECONDS, WINDOW_CEILING_SECONDS))
        self.max_failures = int(_clamp(int(max_failures), 1, FAILURES_CEILING))
        self.base_lockout_seconds = float(_clamp(float(base_lockout_seconds), LOCKOUT_FLOOR_SECONDS, LOCKOUT_CEILING_SECONDS))
        self.max_lockout_seconds = float(_clamp(float(max_lockout_seconds), LOCKOUT_FLOOR_SECONDS, LOCKOUT_CEILING_SECONDS))
        self.global_max_attempts = int(_clamp(int(global_max_attempts), 1, GLOBAL_ATTEMPTS_CEILING))
        self.global_window_seconds = float(
            _clamp(float(global_window_seconds), WINDOW_FLOOR_SECONDS, WINDOW_CEILING_SECONDS)
        )
        self.breaker_failures = int(_clamp(int(breaker_failures), 1, FAILURES_CEILING))
        self.breaker_cooldown_seconds = float(
            _clamp(float(breaker_cooldown_seconds), LOCKOUT_FLOOR_SECONDS, LOCKOUT_CEILING_SECONDS)
        )
        self.max_tracked_keys = int(_clamp(int(max_tracked_keys), 1, TRACKED_KEYS_CEILING))
        self._clock = clock
        self._records: dict[str, _Record] = {}
        self._refusals = 0
        self._last_reason: str | None = None
        self._last_refusal_at: float | None = None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _now(self) -> float:
        return float(self._clock())

    def _window_for(self, key: str) -> float:
        return self.global_window_seconds if key == GLOBAL_KEY else self.window_seconds

    def _budget_for(self, key: str) -> int:
        return self.global_max_attempts if key == GLOBAL_KEY else self.max_attempts

    def _refresh(self, record: _Record, now: float, window_seconds: float) -> None:
        """Roll a record's window and failure streak without touching its lock."""

        if now >= record.window_started + window_seconds:
            record.window_started = now
            record.attempts = 0
            record.failures = 0

    def _sweep(self, now: float) -> None:
        """Drop records that can no longer refuse anything, then bound the map."""

        for key in [key for key, record in self._records.items() if now >= record.expiry(self._window_for(key))]:
            del self._records[key]
        if len(self._records) <= self.max_tracked_keys:
            return
        # Capacity pressure.  The global record is never evicted: it is the
        # only tier a rotated identity cannot escape, so evicting it would let
        # a caller buy a fresh installation-wide budget by flooding keys.  For
        # caller records we do evict live ones (soonest effective expiry
        # first) rather than refusing outright, because refusing here would
        # hand an attacker a way to lock every legitimate peer out of pairing.
        # The eviction is still bounded by the global budget, so a caller that
        # churns keys still cannot exceed the installation-wide attempt rate.
        evictions = len(self._records) - self.max_tracked_keys
        ranked = sorted(
            (key for key in self._records if key != GLOBAL_KEY),
            key=lambda key: self._records[key].expiry(self._window_for(key)),
        )
        for key in ranked[:evictions]:
            del self._records[key]

    def _record_for(self, key: str, now: float) -> _Record:
        record = self._records.get(key)
        if record is None:
            record = _Record(window_started=now)
            self._records[key] = record
        self._refresh(record, now, self._window_for(key))
        return record

    def _tiers(self, key: str) -> tuple[str, ...]:
        """Return the tiers to charge/evaluate, global first and deduplicated."""

        return (GLOBAL_KEY,) if key == GLOBAL_KEY else (GLOBAL_KEY, key)

    def _evaluate(self, key: str, now: float) -> ThrottleDecision | None:
        record = self._records.get(key)
        if record is None:
            return None
        if record.locked_until > now:
            return ThrottleDecision(reason="too many failed pairing attempts", retry_after_seconds=record.locked_until - now)
        # ``attempts`` counts admissions already granted in this window, so the
        # budget admits exactly ``max_attempts`` of them and refuses the next
        # one — the Nth attempt inside the window is the one that is turned away.
        if record.attempts >= self._budget_for(key):
            window_end = record.window_started + self._window_for(key)
            return ThrottleDecision(
                reason="pairing attempt budget exhausted for this window",
                retry_after_seconds=max(0.0, window_end - now),
            )
        return None

    def _refuse(self, decision: ThrottleDecision) -> ThrottleDecision:
        self._refusals += 1
        self._last_reason = decision.reason
        self._last_refusal_at = self._now()
        return decision

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def check(self, key: str) -> ThrottleDecision | None:
        """Consume one attempt for *key*; return a refusal or ``None``.

        The global tier is evaluated first so an installation-wide flood is
        refused before any per-caller bookkeeping is touched.  The attempt is
        charged whether or not the caller turns out to be legitimate, so a
        wrong-code flood is bounded by exactly the same budget.
        """

        now = self._now()
        self._sweep(now)
        for tier in self._tiers(key):
            decision = self._evaluate(tier, now)
            if decision is not None:
                return self._refuse(decision)
        for tier in self._tiers(key):
            self._record_for(tier, now).attempts += 1
        return None

    def record_failure(self, key: str) -> None:
        """Apply one failed pairing attempt to both tiers."""

        now = self._now()
        self._sweep(now)
        breaker = self._record_for(GLOBAL_KEY, now)
        breaker.failures += 1
        if breaker.failures >= self.breaker_failures:
            cooldown = min(
                self.breaker_cooldown_seconds * (2 ** max(0, breaker.trips)),
                self.max_lockout_seconds,
            )
            breaker.trips += 1
            breaker.failures = 0
            breaker.locked_until = max(breaker.locked_until, now + cooldown)
        if key == GLOBAL_KEY:
            return
        caller = self._record_for(key, now)
        caller.failures += 1
        if caller.failures >= self.max_failures:
            lockout = min(
                self.base_lockout_seconds * (2 ** max(0, caller.failures - self.max_failures)),
                self.max_lockout_seconds,
            )
            caller.locked_until = max(caller.locked_until, now + lockout)

    def record_success(self, key: str) -> None:
        """Clear the failure streak after a pairing; keep the attempt budget.

        A success proves the caller holds the credential, so the escalating
        lockout is lifted — an operator who mistyped the code must not be
        punished for the rest of the window.  The attempt counters are *not*
        reset: the route mutates state on every admitted attempt, so the budget
        bounds how fast that mutation can happen whether or not the caller is
        legitimate.
        """

        for tier in self._tiers(key):
            record = self._records.get(tier)
            if record is None:
                continue
            record.failures = 0
            record.locked_until = 0.0

    def retry_after(self, key: str) -> ThrottleDecision | None:
        """Report the current refusal for *key* without charging an attempt."""

        now = self._now()
        self._sweep(now)
        for tier in self._tiers(key):
            decision = self._evaluate(tier, now)
            if decision is not None:
                return decision
        return None

    @property
    def tracked_keys(self) -> int:
        """Number of live caller records. A property, not ``__len__``.

        ``__len__`` would make a freshly built, empty throttle falsy, and the
        obvious ``pairing_throttle or _build_throttle()`` wiring would then
        silently discard an injected limiter — exactly the kind of
        fail-open-by-typo this module exists to prevent.
        """

        return len(self._records)

    def reset(self) -> None:
        self._records.clear()
        self._refusals = 0
        self._last_reason = None
        self._last_refusal_at = None

    def snapshot(self) -> dict[str, Any]:
        """Return credential-free throttle state for the status route."""

        now = self._now()
        return {
            "max_attempts_per_identity": self.max_attempts,
            "window_seconds": self.window_seconds,
            "max_failures": self.max_failures,
            "global_max_attempts": self.global_max_attempts,
            "global_window_seconds": self.global_window_seconds,
            "breaker_failures": self.breaker_failures,
            "breaker_cooldown_seconds": self.breaker_cooldown_seconds,
            "max_lockout_seconds": self.max_lockout_seconds,
            "max_tracked_keys": self.max_tracked_keys,
            "tracked_keys": self.tracked_keys,
            "refused_attempts": self._refusals,
            "last_refusal_reason": self._last_reason,
            "last_refusal_age_seconds": None if self._last_refusal_at is None else max(0.0, now - self._last_refusal_at),
            "open": self.retry_after(GLOBAL_KEY) is not None,
        }


__all__ = [
    "DEFAULT_BASE_LOCKOUT_SECONDS",
    "DEFAULT_BREAKER_COOLDOWN_SECONDS",
    "DEFAULT_BREAKER_FAILURES",
    "DEFAULT_GLOBAL_MAX_ATTEMPTS",
    "DEFAULT_GLOBAL_WINDOW_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_FAILURES",
    "DEFAULT_MAX_LOCKOUT_SECONDS",
    "DEFAULT_MAX_TRACKED_KEYS",
    "DEFAULT_WINDOW_SECONDS",
    "GLOBAL_KEY",
    "PairingThrottle",
    "ThrottleDecision",
]
