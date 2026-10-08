"""Restart policy for a supervised process — and the refusal to loop forever.

The failure this prevents
-------------------------
The naive supervisor is `while not healthy: start()` with a sleep. It is a
liability: a backend that cannot start — a bad config, a port already bound, a
migration that will not apply — is restarted forever, and each attempt burns CPU,
writes the same error log, and makes the machine unusable for the operator who
needs to fix it. The spec calls this out directly: *never create a blind infinite
restart loop*.

Why the budget is a sliding window, not a counter
-------------------------------------------------
`alpha.runtime.resilience.RetryPolicy` already owns the backoff maths
(`nominal_delay`/`delay_for`, with injectable jitter), so this module reuses it
rather than adding a fifth backoff implementation to a package that already
warns about having several.

What it deliberately does *not* reuse is `Policy.attempts` as the crash
limiter, because an attempt ceiling and a restart budget are different things.
A backend that crashes **once an hour, forever**, has one consecutive failure at
any instant and would sail through a consecutive-failure breaker and a
three-attempt ceiling — restarting several times a day, indefinitely, while
looking healthy to both. That is the exact shape of a real crash loop: rare
enough to pass every consecutive counter, frequent enough to be useless.

So the limiter here is a **sliding window**: at most `restart_budget` restarts
in any `restart_window_seconds` span. It refills with time, which is what makes
"once an hour forever" eventually stop while "three times in ten seconds" is
caught immediately.

Three outcomes, not two
-----------------------
* :attr:`RestartAction.RESTART` — within budget; wait `delay_seconds` and go.
* :attr:`RestartAction.SAFE_MODE` — the budget is spent, but the supervisor
  believes a reduced configuration might still boot. Tried **once** per
  crash-loop episode. If safe mode also fails, the next decision is
  ``GIVE_UP``.
* :attr:`RestartAction.GIVE_UP` — stop and report. The backend stays down and
  says why, which is a better outcome than a machine that cannot be typed on.

Safe mode is deliberately *one* attempt, not a second tier of the ladder.
A ladder implies it might work after enough tries, and for a startup failure
there is nothing between "this configuration boots" and "it does not".

Uptime resets the episode
-------------------------
A child that stayed up for at least `healthy_run_seconds` did not crash-loop, so
the episode ends: the restart timestamps and the safe-mode latch are cleared.
Without that, a service that runs fine for a week between rare crashes would
eventually exhaust a window it had already served its purpose in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from alpha.runtime.resilience.clock import Clock, coerce_clock
from alpha.runtime.resilience.retry import RetryPolicy

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SUPERVISOR_POLICY",
    "RestartAction",
    "RestartDecision",
    "RestartLedger",
    "SupervisorPolicy",
    "SupervisorReason",
]


class SupervisorReason(StrEnum):
    """Why a child process ended. Decides how much it counts against the budget."""

    #: Exited 0 on request, or after a clean shutdown.
    NORMAL = "normal"
    #: Non-zero exit, or killed by a signal it did not ask for.
    CRASHED = "crashed"
    #: Started, then failed its health check within the grace period.
    HEALTH_FAILED = "health_failed"
    #: The process could not be started at all (bad argv, missing binary).
    SPAWN_FAILED = "spawn_failed"
    #: Ended because someone asked it to - `stop()`, or a supervised shutdown.
    #:
    #: Distinct from NORMAL because the *exit code* is not meaningful here: the
    #: supervisor killed the child itself, and the OS reports that as a nonzero
    #: status on Windows. Without a name for this case a deliberate stop was
    #: indistinguishable from a crash, and `CRASHED.counts_against_budget` then
    #: charged the crash-loop budget for an operator-requested stop.
    STOPPED = "stopped"

    @property
    def counts_against_budget(self) -> bool:
        """Whether this ending consumes restart budget.

        A normal exit never does, and neither does an operator-requested stop -
        an operator stopping the service, or a supervised shutdown, must not
        spend the crash budget. The budget exists to stop a backend that
        *cannot start*; a deploy or a reload that stops a healthy child would
        otherwise walk the budget down until the supervisor entered SAFE_MODE and
        then GIVE_UP, refusing to start the very process it exists to keep up.

        A spawn failure always counts and is treated as the *worst* case, because
        it means the process never ran at all, so there is no reason to expect
        the next attempt to behave differently.
        """
        return self not in {SupervisorReason.NORMAL, SupervisorReason.STOPPED}


class RestartAction(StrEnum):
    """What the supervisor does about an ended child."""

    RESTART = "restart"
    SAFE_MODE = "safe_mode"
    GIVE_UP = "give_up"


@dataclass(frozen=True, slots=True)
class SupervisorPolicy:
    """Operator policy for one supervised process."""

    #: Restarts permitted in any ``restart_window_seconds`` span before the
    #: budget is spent. This is the crash-loop limiter.
    restart_budget: int = 5
    restart_window_seconds: float = 300.0
    #: Uptime after which the child is considered healthy and the episode ends.
    healthy_run_seconds: float = 60.0
    #: Try a reduced configuration once when the budget is spent. Without this
    #: the supervisor gives up the moment the budget runs out.
    safe_mode_enabled: bool = True
    #: Stop after this many consecutive safe-mode failures.
    safe_mode_attempts: int = 1
    #: Backoff shape. Reused from ``alpha.runtime.resilience.retry`` so the
    #: supervisor adds no new backoff maths to a package that already has
    #: several; ``jitter`` is injected so a test pins the exact schedule.
    backoff: RetryPolicy = field(default_factory=lambda: RetryPolicy(attempts=64, base_delay=1.0, multiplier=2.0, max_delay=60.0))
    #: How long to wait for a health check before declaring the start failed.
    health_timeout_seconds: float = 30.0
    #: Bound on retained restart timestamps, so a long-lived supervisor's ledger
    #: cannot grow without limit.
    max_recorded_restarts: int = 256

    def __post_init__(self) -> None:
        if self.restart_budget < 1:
            raise ValueError("restart_budget must be >= 1: a supervisor that may not restart is not a supervisor")
        if self.restart_window_seconds <= 0:
            raise ValueError("restart_window_seconds must be > 0")
        if self.healthy_run_seconds < 0:
            raise ValueError("healthy_run_seconds must be >= 0")
        if self.safe_mode_attempts < 1:
            raise ValueError("safe_mode_attempts must be >= 1")
        if self.health_timeout_seconds <= 0:
            raise ValueError("health_timeout_seconds must be > 0")

    def backoff_delay(self, attempt: int) -> float:
        """Jittered delay before restart *attempt* (1-based), via ``RetryPolicy``."""
        return self.backoff.delay_for(max(1, attempt))


@dataclass(frozen=True, slots=True)
class RestartDecision:
    """What to do next, and the evidence behind it."""

    action: RestartAction
    reason: str
    delay_seconds: float
    #: Restarts inside the current sliding window, including the one just
    #: recorded. Reported so an operator can see the budget filling up.
    restarts_in_window: int
    #: Budget that applied to this decision.
    restart_budget: int
    #: True when this decision concluded a crash loop rather than one failure.
    crash_loop: bool
    safe_mode_attempts_used: int = 0

    @property
    def should_restart(self) -> bool:
        return self.action is not RestartAction.GIVE_UP

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "delay_seconds": round(self.delay_seconds, 3),
            "restarts_in_window": self.restarts_in_window,
            "restart_budget": self.restart_budget,
            "crash_loop": self.crash_loop,
            "safe_mode_attempts_used": self.safe_mode_attempts_used,
        }


@dataclass
class _Restart:
    at: float
    reason: SupervisorReason
    attempt: int


#: Enough for a long window at a realistic budget, and the ledger prunes
#: regardless, so this is only a backstop against unbounded growth.
_RECORD_CAP: Final[int] = 1024


class RestartLedger:
    """Sliding-window restart budget, crash-loop detection, and the safe-mode latch.

    Pure policy: it records what happened and answers what to do next. It never
    starts a process, so the whole crash-loop behaviour is testable with a
    :class:`~alpha.runtime.resilience.clock.ManualClock` and no subprocesses.
    """

    __slots__ = ("_clock", "_policy", "_restarts", "_safe_mode_used", "_episode")

    def __init__(self, policy: SupervisorPolicy | None = None, *, clock: Clock | None = None) -> None:
        self._policy = policy or SupervisorPolicy()
        self._clock = coerce_clock(clock)
        self._restarts: list[_Restart] = []
        self._safe_mode_used = 0
        self._episode = 0

    # -- introspection ------------------------------------------------------

    @property
    def policy(self) -> SupervisorPolicy:
        return self._policy

    @property
    def episode(self) -> int:
        """How many distinct crash episodes this ledger has seen.

        Incremented when a crash episode ends and a new one begins, which is what
        lets a caller correlate its diagnostics with a single bad stretch rather
        than with the whole process lifetime.
        """
        return self._episode

    @property
    def safe_mode_used(self) -> int:
        return self._safe_mode_used

    def restarts(self) -> tuple[_Restart, ...]:
        return tuple(self._restarts)

    def restarts_in_window(self, *, now: float | None = None) -> int:
        moment = self._clock.now() if now is None else float(now)
        self._prune(moment)
        return len(self._restarts)

    # -- recording ----------------------------------------------------------

    def record_start(self) -> int:
        """Note that a child was started; returns its 1-based attempt number.

        Informational: the budget is charged by :meth:`record_end`, because only
        the ending establishes whether an attempt was wasted.
        """
        return len(self._restarts) + 1

    def record_end(self, *, reason: SupervisorReason, started_at: float, ended_at: float | None = None) -> RestartDecision:
        """Record how a child ended and decide what happens next.

        ``started_at`` is when the child was launched and ``ended_at`` is the
        clock reading now. The gap is the child's uptime, and it is what
        separates a crash loop from an unlucky afternoon: a child that ran longer
        than ``healthy_run_seconds`` ends the episode, so the budget it was
        spending is returned.
        """
        moment = self._clock.now() if ended_at is None else float(ended_at)
        uptime = moment - float(started_at)
        healthy = uptime >= self._policy.healthy_run_seconds

        if healthy:
            self._end_episode()

        if not reason.counts_against_budget:
            # A clean stop spends nothing, but a long-enough clean run still
            # ends the episode (handled above).
            #
            # An operator-requested stop also ends the episode outright, rather
            # than returning RESTART. RESTART means "wait the backoff and spawn
            # again", and the caller has just been told to stop - returning
            # RESTART here would only be undone by the caller noticing
            # `_stopping`, and would report a next-step the supervisor is not
            # going to take. GIVE_UP is the honest action: it means "not
            # restarting", and the stop reason already says why.
            if reason is SupervisorReason.STOPPED:
                self._end_episode()
                return self._decision(RestartAction.GIVE_UP, "stop requested by the operator", uptime_healthy=healthy)
            return self._decision(RestartAction.RESTART, f"child exited normally after {uptime:.1f}s", uptime_healthy=healthy)

        self._restarts.append(_Restart(at=moment, reason=reason, attempt=len(self._restarts) + 1))
        self._prune(moment)
        if len(self._restarts) > _RECORD_CAP:  # pragma: no cover - backstop
            del self._restarts[: len(self._restarts) - _RECORD_CAP]

        return self._decide_after_failure(reason, healthy=healthy)

    # -- decision -----------------------------------------------------------

    def decide_next(self) -> RestartDecision:
        """Re-ask what to do without recording a new failure.

        Used by a supervisor that restarts on a timer and needs the current
        verdict — for example after a manual stop/start, or to decide whether
        safe mode is still available.
        """
        return self._decide_after_failure(None, healthy=False)

    def _decide_after_failure(self, reason: SupervisorReason | None, *, healthy: bool) -> RestartDecision:
        moment = self._clock.now()
        self._prune(moment)
        in_window = len(self._restarts)
        # ``restart_budget`` is the number of restarts *permitted*, so the
        # budget is spent once the window holds MORE than the budget. Testing
        # ``>=`` here would refuse the very first restart of a budget-of-one
        # policy, which is the opposite of what a budget of one means.
        crash_loop = in_window > self._policy.restart_budget

        if not crash_loop:
            attempt = in_window
            # Sampled exactly once per decision. ``_decision`` used to recompute
            # this from the window count, which is invisible with a deterministic
            # ladder but wrong under FullJitter: each call draws again, so the
            # reported delay was a *different* sample from the one the ladder
            # produced and the two disagreed loudly. Passing it down makes one
            # decision one draw.
            delay = self._policy.backoff_delay(attempt)
            described = reason.value if reason is not None else "no failure recorded"
            return self._decision(
                RestartAction.RESTART,
                f"{described}; {in_window}/{self._policy.restart_budget} restarts in the window",
                crash_loop=crash_loop,
                delay_seconds=delay,
            )

        # The budget is spent. A spawn failure is the clearest evidence that
        # another identical attempt cannot help, so it skips safe mode entirely
        # rather than spending the one attempt on a process that never ran.
        if reason is SupervisorReason.SPAWN_FAILED:
            return self._decision(RestartAction.GIVE_UP, f"the process could not be started ({reason.value}); retrying cannot change that", crash_loop=True)

        if self._policy.safe_mode_enabled and self._safe_mode_used < self._policy.safe_mode_attempts:
            self._safe_mode_used += 1
            return self._decision(
                RestartAction.SAFE_MODE,
                f"crash loop: {in_window} restarts within {self._policy.restart_window_seconds:.0f}s; trying a reduced configuration (attempt {self._safe_mode_used}/{self._policy.safe_mode_attempts})",
                crash_loop=True,
            )

        return self._decision(
            RestartAction.GIVE_UP,
            f"crash loop: {in_window} restarts within {self._policy.restart_window_seconds:.0f}s and safe mode "
            + ("is disabled" if not self._policy.safe_mode_enabled else f"already tried {self._safe_mode_used} time(s)")
            + "; stopping instead of looping",
            crash_loop=True,
        )

    def _decision(
        self,
        action: RestartAction,
        reason: str,
        *,
        crash_loop: bool = False,
        uptime_healthy: bool = False,
        delay_seconds: float | None = None,
    ) -> RestartDecision:
        del uptime_healthy
        attempt = max(1, len(self._restarts))
        # ``delay_seconds`` is supplied by callers that already sampled the
        # ladder, so a decision never draws twice. Callers that need no backoff
        # (or did not compute one) fall through to the single draw below.
        if delay_seconds is None:
            delay_seconds = 0.0 if action is not RestartAction.RESTART else self._policy.backoff_delay(attempt)
        return RestartDecision(
            action=action,
            reason=reason,
            delay_seconds=delay_seconds,
            restarts_in_window=len(self._restarts),
            restart_budget=self._policy.restart_budget,
            crash_loop=crash_loop,
            safe_mode_attempts_used=self._safe_mode_used,
        )

    def _end_episode(self) -> None:
        if self._restarts or self._safe_mode_used:
            self._episode += 1
        self._restarts.clear()
        self._safe_mode_used = 0

    def _prune(self, now: float) -> None:
        """Drop restarts that have fallen out of the sliding window."""
        cutoff = now - self._policy.restart_window_seconds
        if self._restarts and self._restarts[0].at <= cutoff:
            self._restarts = [restart for restart in self._restarts if restart.at > cutoff]

    def reset(self) -> None:
        """Forget the whole episode. Exposed for an operator-triggered retry."""
        self._end_episode()


DEFAULT_SUPERVISOR_POLICY: Final[SupervisorPolicy] = SupervisorPolicy()
