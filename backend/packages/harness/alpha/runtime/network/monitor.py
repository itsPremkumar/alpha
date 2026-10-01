"""The network monitor: observe connectivity, publish changes, back off politely.

Shape
-----
The monitor is a **state machine plus a poll loop**, and the two are separated
on purpose:

* :meth:`NetworkMonitor.check_once` performs exactly one probe, folds it into a
  candidate :class:`~alpha.runtime.network.states.NetworkState`, applies
  hysteresis, and returns an immutable :class:`NetworkObservation`. It never
  sleeps and never raises. Every decision in this file is testable by calling
  it a fixed number of times with a
  :class:`~alpha.runtime.network.probe.ScriptedProbe` and a
  :class:`~alpha.runtime.resilience.clock.ManualClock` -- no real time passes.
* :meth:`NetworkMonitor.run` is a thin loop over ``check_once`` that waits
  between polls. The wait is ``asyncio.sleep`` on a delay *decided* by the
  clock, so a test can assert the backoff schedule without waiting for it.
* :meth:`NetworkMonitor.recheck` is the operator "measure now" entry point. It
  runs the *same* transition the loop runs, under the same lock, so a manual
  retry from the UI adds a real reading to the hysteresis history instead of
  being a second, parallel decision about the same link.

Who keeps trying
----------------
The poll loop runs for the life of the process and it does **not** stop while
the link is down -- that is the whole reason a host which boots offline ever
recovers. It only slows down: the interval grows by ``backoff_multiplier`` to
``backoff_max_seconds`` (default 5s -> 300s, with jitter only ever reducing it),
so a machine that lost its link keeps re-probing forever at a bounded rate. It
never gives up, never burns a tight loop, and never announces a false recovery.

There is deliberately no attempt ceiling here. The bound on *work* parked on an
outage belongs to the durable registry (``NetworkWaitPolicy.max_attempts``); a
probe loop that gave up would turn a ten-minute outage into permanent silence,
which is the outage equivalent of the restart loop the supervisor refuses to
write.

Hysteresis is the load-bearing rule
-----------------------------------
A single failed probe must never park the fleet, and a single successful probe
must never declare the fleet healthy. Confirming a move to ``OFFLINE`` and a
move to ``ONLINE`` each require N consecutive agreeing observations
(``offline_after_consecutive`` / ``online_after_consecutive``, both 2 by
default). ``DEGRADED`` and ``UNKNOWN`` are not stop-the-world decisions, so they
publish on the first observation.

Backoff, and why it is not optional
-----------------------------------
A disconnected machine that keeps polling every few seconds is a machine whose
battery, DNS cache, and network stack are being worked for nothing, and a
flapping link that resets to the fast interval on every blip hammers a provider
that is already struggling. So the poll interval grows exponentially with
decorrelated jitter while connectivity is not ``ONLINE``, and shrinks back
immediately on a confirmed recovery. The jitter is injectable, which is the
whole reason the decision lives behind the injected clock rather than inline.

Honesty rules
-------------
* ``UNKNOWN`` is reported, never rounded to ``OFFLINE``. A probe that cannot run
  is a broken probe, and saying "you are offline" because the probe is
  misconfigured would be a lie that parks every session.
* A ``DEGRADED`` observation never parks a session by itself. Partial
  connectivity is not an outage, and the provider retry path handles a single
  unreachable endpoint better than a global state flip does.
* Observers are notified on *change* only, plus once at subscribe time so a
  late subscriber is never left guessing the initial state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from alpha.runtime.network.probe import ConnectivityProbe, ProbeOutcome, ProbeTarget, TcpConnectivityProbe, normalize_targets
from alpha.runtime.network.states import NETWORK_STATE_DETAIL, NETWORK_STATE_ORDER, NetworkState, is_connected
from alpha.runtime.resilience.clock import Clock, coerce_clock

logger = logging.getLogger(__name__)

__all__ = [
    "NetworkMonitor",
    "NetworkMonitorConfig",
    "NetworkObservation",
    "NetworkWaitDecision",
]

#: Event names published on the in-process bus (`alpha.events.bus`).
NETWORK_STATE_CHANGED_EVENT = "network.state.changed"
NETWORK_RESTORED_EVENT = "network.restored"
NETWORK_LOST_EVENT = "network.lost"

Observer = Callable[["NetworkObservation"], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class NetworkMonitorConfig:
    """Monitor policy. Constructed from ``config.yaml -> network``.

    Every field has a production default, so a deployment that declares nothing
    still gets a working monitor.
    """

    enabled: bool = True
    poll_interval_seconds: float = 15.0
    #: Consecutive agreeing observations required before publishing OFFLINE.
    offline_after_consecutive: int = 2
    #: Consecutive agreeing observations required before publishing ONLINE.
    online_after_consecutive: int = 2
    #: Poll interval ceiling while connectivity is not ONLINE.
    backoff_initial_seconds: float = 5.0
    backoff_max_seconds: float = 300.0
    backoff_multiplier: float = 2.0
    backoff_jitter_ratio: float = 0.25
    #: Consecutive probe failures that degrade an otherwise-good link to
    #: UNKNOWN instead of leaving the last state standing.
    unknown_after_consecutive: int = 3
    #: Hard cap on observations retained per monitor instance.
    max_observations: int = 50

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.offline_after_consecutive < 1:
            raise ValueError("offline_after_consecutive must be >= 1")
        if self.online_after_consecutive < 1:
            raise ValueError("online_after_consecutive must be >= 1")
        if self.unknown_after_consecutive < 1:
            raise ValueError("unknown_after_consecutive must be >= 1")
        if self.backoff_initial_seconds <= 0:
            raise ValueError("backoff_initial_seconds must be > 0")
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise ValueError("backoff_max_seconds must be >= backoff_initial_seconds")
        if self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be >= 1.0")
        if not 0.0 <= self.backoff_jitter_ratio <= 1.0:
            raise ValueError("backoff_jitter_ratio must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class NetworkObservation:
    """One decided observation: the published state and why it was published."""

    state: NetworkState
    previous_state: NetworkState
    changed: bool
    outcomes: tuple[ProbeOutcome, ...]
    consecutive_agreeing: int
    consecutive_probe_failures: int
    observed_at: float
    detail: str = ""

    @property
    def reachable_targets(self) -> tuple[str, ...]:
        return tuple(outcome.target for outcome in self.outcomes if outcome.reachable)

    @property
    def unreachable_targets(self) -> tuple[str, ...]:
        return tuple(outcome.target for outcome in self.outcomes if not outcome.reachable)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "previous_state": self.previous_state.value,
            "changed": self.changed,
            "reachable_targets": list(self.reachable_targets),
            "unreachable_targets": list(self.unreachable_targets),
            "consecutive_agreeing": self.consecutive_agreeing,
            "consecutive_probe_failures": self.consecutive_probe_failures,
            "observed_at": self.observed_at,
            "detail": self.detail or NETWORK_STATE_DETAIL[self.state],
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


@dataclass(frozen=True, slots=True)
class NetworkWaitDecision:
    """What a caller should do about connectivity right now.

    Returned by :meth:`NetworkMonitor.wait_decision` so a run/thread owner asks
    one question and gets a complete, already-reasoned answer -- including the
    user-facing line, so no surface reword "waiting for network" for itself.
    """

    #: True when new work that needs the network should be admitted.
    admit_network_work: bool
    #: True when a session parked on connectivity should be resumed now.
    resume_parked_work: bool
    state: NetworkState
    reason: str
    #: Suggested seconds before the next probe; already jittered and bounded.
    next_poll_seconds: float
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "admit_network_work": self.admit_network_work,
            "resume_parked_work": self.resume_parked_work,
            "state": self.state.value,
            "reason": self.reason,
            "next_poll_seconds": round(self.next_poll_seconds, 3),
            "message": self.message,
        }


class NetworkMonitor:
    """Poll connectivity, publish transitions, and back off while it is bad."""

    def __init__(
        self,
        config: NetworkMonitorConfig | None = None,
        *,
        probe: ConnectivityProbe | None = None,
        targets: Sequence[ProbeTarget] | None = None,
        clock: Clock | None = None,
        random_fn: Callable[[], float] | None = None,
        bus: object | None = None,
    ) -> None:
        self._config = config or NetworkMonitorConfig()
        self._targets = normalize_targets(targets)
        self._probe: ConnectivityProbe = probe or TcpConnectivityProbe()
        self._clock = coerce_clock(clock)
        # ``random.Random`` rather than the module-level ``random`` so parallel
        # monitors and other jitter users cannot consume each other's stream, and
        # so a test can pin the whole schedule.
        self._random = random.Random(0) if random_fn is None else _FixedRandom(random_fn)
        self._bus = bus

        self._state = NetworkState.UNKNOWN
        self._has_published = False
        self._candidate: NetworkState | None = None
        self._consecutive_agreeing = 0
        self._consecutive_probe_failures = 0
        self._consecutive_degraded = 0
        self._backoff_seconds = self._config.backoff_initial_seconds
        self._next_poll_seconds = self._config.poll_interval_seconds
        self._observations: list[NetworkObservation] = []
        self._observers: dict[int, Observer] = {}
        self._next_observer_id = 1
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_observation: NetworkObservation | None = None
        # One measurement at a time. ``check_once`` is the whole state
        # transition -- it probes, folds, applies hysteresis, advances the
        # backoff ladder, and publishes -- so two overlapping calls would
        # interleave those steps and double-count corroboration. The poll loop
        # is the only caller that used to exist; the operator recheck route is
        # the second, and a click landing on the same tick as a poll must not be
        # able to move a state the ladder has not seen yet.
        self._probe_lock = asyncio.Lock()

    # -- introspection ---------------------------------------------------------

    @property
    def state(self) -> NetworkState:
        """The last published state. ``UNKNOWN`` until the first observation."""
        return self._state

    @property
    def config(self) -> NetworkMonitorConfig:
        return self._config

    @property
    def targets(self) -> tuple[ProbeTarget, ...]:
        return self._targets

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def pending_confirmations(self) -> int:
        """Agreeing observations collected so far toward the *next* publish.

        ``0`` means the state is settled. A positive value means a candidate is
        waiting on corroboration — an outage that needs
        ``offline_after_consecutive`` agrees, or a recovery that needs
        ``online_after_consecutive``.

        This is the number an operator-facing "retry" has to report. A manual
        recheck that legitimately cannot publish (the hysteresis gate is doing
        its job) looks identical to a broken button unless it says how many
        confirmations are still outstanding, and ``NetworkObservation`` cannot
        carry it: ``check_once`` reports the branch it took, which is ``0``
        whenever the probe itself ran.
        """
        return max(0, self._consecutive_agreeing)

    def last_observation(self) -> NetworkObservation | None:
        return self._last_observation

    def observation_age_seconds(self) -> float | None:
        """Seconds since the last completed probe, or ``None`` if there is none.

        Computed **here** rather than by a reader, because ``observed_at`` is
        stamped from the injected clock -- and the production clock is
        ``SystemClock``, which is :func:`time.monotonic`. A consumer outside
        this package therefore cannot turn that stamp into an age without mixing
        two different timelines, and would end up reporting a plausible-looking
        number that is wrong (wall time minus a monotonic reading is negative on
        any host). Reporting the age is the honest answer; reporting ``0``
        because the subtraction clamped is the one thing this must not do,
        because a reader would render that as "measured just now".
        """
        last = self._last_observation
        if last is None:
            return None
        try:
            return max(0.0, self._clock.now() - float(last.observed_at))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return None

    def observations(self) -> tuple[NetworkObservation, ...]:
        """Bounded history, oldest first."""
        return tuple(self._observations)

    # -- observation -----------------------------------------------------------

    async def check_once(self) -> NetworkObservation:
        """Probe once and fold the result into a published state.

        Never raises: a probe that blows up is reported as ``UNKNOWN`` with the
        failure counted, because "the probe is broken" and "the link is gone"
        must never be reported as the same thing.

        Serialized against other callers. :meth:`recheck` exists so an operator
        can force a measurement *through the same code path the poll loop uses*
        rather than beside it, which is what makes "Retry" in the UI a real
        second reading instead of a UI-local guess.
        """
        async with self._probe_lock:
            return await self._observe_once()

    async def recheck(self) -> NetworkObservation:
        """Force an immediate measurement and return the observation it produced.

        This is the operator "retry now" entry point, and it deliberately does
        **not** bypass hysteresis: a single successful connect against a link
        that is still flapping will not publish ``ONLINE``. A caller that has to
        explain that to a human reads :attr:`pending_confirmations` for how many
        confirmations are still outstanding — the returned observation cannot
        carry it, because it reports the branch the probe took.

        It also does not restart or reschedule the poll loop. The loop keeps its
        own bounded backoff and is the only thing that can notice a recovery the
        user did not ask about; a manual retry adds a reading, it does not
        become a second scheduler.
        """
        return await self.check_once()

    async def _observe_once(self) -> NetworkObservation:
        previous = self._state
        outcomes: tuple[ProbeOutcome, ...] = ()
        probe_failure_type = ""
        try:
            outcomes = tuple(await self._probe.probe(self._targets))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - a broken probe is a result, not a crash
            # ``exc`` is unbound once the handler block exits, so the type name
            # is captured here rather than read after the try statement.
            probe_failure_type = type(exc).__name__
            logger.warning("network probe failed to execute: %s", probe_failure_type, exc_info=True)

        if probe_failure_type:
            self._consecutive_probe_failures += 1
            candidate = NetworkState.UNKNOWN
            agreeing = self._consecutive_probe_failures
            detail = f"probe could not be executed ({probe_failure_type}); connectivity is not known"
        elif not outcomes:
            self._consecutive_probe_failures += 1
            candidate = NetworkState.UNKNOWN
            agreeing = self._consecutive_probe_failures
            detail = "probe returned no results; connectivity is not known"
        else:
            self._consecutive_probe_failures = 0
            reachable = sum(1 for outcome in outcomes if outcome.reachable)
            if reachable == len(outcomes):
                candidate = NetworkState.ONLINE
            elif reachable == 0:
                candidate = NetworkState.OFFLINE
            else:
                candidate = NetworkState.DEGRADED
            agreeing = 0
            detail = NETWORK_STATE_DETAIL[candidate]

        state = self._settle(candidate, agreeing, detail)
        changed = state is not previous
        observation = NetworkObservation(
            state=state,
            previous_state=previous,
            changed=changed,
            outcomes=outcomes,
            consecutive_agreeing=agreeing,
            consecutive_probe_failures=self._consecutive_probe_failures,
            observed_at=self._clock.now(),
            detail=detail,
        )
        self._last_observation = observation
        self._observations.append(observation)
        if len(self._observations) > self._config.max_observations:
            del self._observations[: len(self._observations) - self._config.max_observations]

        if changed:
            self._on_state_changed(observation)
        return observation

    def _settle(self, candidate: NetworkState, agreeing: int, detail: str) -> NetworkState:
        """Apply hysteresis and update the backoff schedule.

        The confirmation counts differ by intent: entering ``OFFLINE`` and
        re-entering ``ONLINE`` are the two moves that stop or restart work, so
        they need corroboration. ``DEGRADED`` and ``UNKNOWN`` publish on the
        first observation because neither stops work.

        One exception, and it matters: **the very first observation always
        publishes.** Hysteresis exists to stop a *flapping* link from parking
        and un-parking the fleet, and there is nothing to flap against before
        the first measurement. Requiring corroboration on a cold start would
        only delay a truthful reading by one poll interval, and would make a
        Gateway that boots on a dead network report "unknown" while it already
        knows the answer.
        """
        required = self._required_confirmations(candidate)
        if not self._has_published:
            required = 1

        if candidate is not self._candidate:
            self._candidate = candidate
            self._consecutive_agreeing = 1
        else:
            self._consecutive_agreeing += 1

        if candidate is NetworkState.DEGRADED:
            self._consecutive_degraded += 1
        else:
            self._consecutive_degraded = 0

        agreeing = max(agreeing, self._consecutive_agreeing)
        if self._consecutive_agreeing < required:
            # Not yet corroborated: keep the last published state rather than
            # acting on a single unconvincing sample.
            return self._state

        if self._consecutive_agreeing > required:
            # Already settled on this candidate; stop recounting.
            self._consecutive_agreeing = required

        self._candidate = None
        self._consecutive_agreeing = 0
        return self._publish(candidate, detail)

    def _required_confirmations(self, candidate: NetworkState) -> int:
        """How many agreeing observations a candidate needs before it is published.

        ``OFFLINE`` and ``ONLINE`` are the two moves that stop and restart work,
        so they are corroborated. ``DEGRADED`` publishes immediately because a
        partial link never stops anything. ``UNKNOWN`` is corroborated like the
        rest: a single probe that blew up should not degrade a link that was
        measured healthy moments earlier, otherwise every transient probe error
        would report a connectivity regression.
        """
        if candidate is NetworkState.OFFLINE:
            return self._config.offline_after_consecutive
        if candidate is NetworkState.ONLINE:
            return self._config.online_after_consecutive
        if candidate is NetworkState.UNKNOWN:
            return self._config.unknown_after_consecutive
        return 1

    def _publish(self, state: NetworkState, detail: str) -> NetworkState:
        self._state = state
        self._has_published = True
        self._next_poll_seconds = self._advance_backoff(state)
        return state

    def _advance_backoff(self, state: NetworkState) -> float:
        """Draw the delay to use now, and advance the stored schedule.

        Use-then-grow: the delay that is drawn is the one already in effect, and
        the schedule steps for the poll after it. So the first offline poll still
        uses ``backoff_initial_seconds`` rather than skipping to the next rung.

        The drawn delay is *stored* (:attr:`_next_poll_seconds`) rather than
        handed to the loop and forgotten, so :meth:`wait_decision` reports the
        exact value the loop will wait and a caller reading the schedule sees
        jittered reality instead of an un-jittered ideal that no one sleeps on.

        Jitter is a proportional reduction applied *after* the ceiling, so the
        declared maximum stays a real ceiling: a link that is down backs off to
        ``backoff_max_seconds`` and stays there rather than occasionally
        overshooting into a tighter loop.
        """
        if state is NetworkState.ONLINE:
            self._backoff_seconds = self._config.backoff_initial_seconds
            return self._config.poll_interval_seconds
        current = self._backoff_seconds
        self._backoff_seconds = min(current * self._config.backoff_multiplier, self._config.backoff_max_seconds)
        ratio = self._config.backoff_jitter_ratio
        jittered = current * (1.0 - ratio * self._random.random())
        return max(self._config.poll_interval_seconds, jittered)

    # -- decisions --------------------------------------------------------------

    def wait_decision(self) -> NetworkWaitDecision:
        """Answer "may network work proceed, and may parked work resume?".

        The resume edge is a *rising* edge: work resumes when connectivity is
        confirmed (``ONLINE`` or ``DEGRADED``) having previously been
        ``OFFLINE``. Returning to ``ONLINE`` from a ``DEGRADED`` link does not
        re-trigger a resume, because nothing was parked for a degraded link.
        """
        state = self._state
        offline = not is_connected(state)
        last = self._last_observation
        previous_offline = bool(last and not is_connected(last.previous_state))
        recovered = state is NetworkState.ONLINE and previous_offline
        if state is NetworkState.DEGRADED and last is not None and last.previous_state is NetworkState.OFFLINE:
            recovered = True
        return NetworkWaitDecision(
            admit_network_work=not offline,
            resume_parked_work=recovered,
            state=state,
            reason=self._reason_for(state, recovered),
            next_poll_seconds=self._next_poll_seconds,
            message=NETWORK_STATE_DETAIL[state],
        )

    def _reason_for(self, state: NetworkState, recovered: bool) -> str:
        if recovered:
            return "connectivity_restored"
        if state is NetworkState.OFFLINE:
            return "connectivity_lost"
        if state is NetworkState.DEGRADED:
            return "connectivity_degraded"
        if state is NetworkState.UNKNOWN:
            return "connectivity_unknown"
        return "connectivity_ok"

    def is_worse_than(self, other: NetworkState) -> bool:
        """True when the current state is a regression relative to *other*."""
        return NETWORK_STATE_ORDER[self._state] > NETWORK_STATE_ORDER[other]

    # -- observers ---------------------------------------------------------------

    def subscribe(self, observer: Observer) -> Callable[[], None]:
        """Register *observer* for state changes; returns an unsubscribe callable.

        The observer is called once immediately with the current state, because a
        subscriber that has to wait a whole poll interval to learn "we are
        already offline" is a subscriber that will make a decision on stale
        information.
        """
        observer_id = self._next_observer_id
        self._next_observer_id += 1
        self._observers[observer_id] = observer
        self._notify_one(observer, self._snapshot_observation(changed=False))
        return lambda: self._observers.pop(observer_id, None)

    def _snapshot_observation(self, *, changed: bool) -> NetworkObservation:
        return NetworkObservation(
            state=self._state,
            previous_state=self._state,
            changed=changed,
            outcomes=(),
            consecutive_agreeing=self._consecutive_agreeing,
            consecutive_probe_failures=self._consecutive_probe_failures,
            observed_at=self._clock.now(),
            detail=NETWORK_STATE_DETAIL[self._state],
        )

    def _on_state_changed(self, observation: NetworkObservation) -> None:
        logger.info("network state %s -> %s (%s)", observation.previous_state.value, observation.state.value, observation.detail)
        for observer in list(self._observers.values()):
            self._notify_one(observer, observation)
        if self._bus is not None:
            self._publish_bus(observation)

    def _notify_one(self, observer: Observer, observation: NetworkObservation) -> None:
        """Deliver to one observer; a broken observer cannot break the monitor."""
        try:
            result = observer(observation)
        except Exception:
            logger.warning("network observer raised", exc_info=True)
            return
        if asyncio.iscoroutine(result):
            task = asyncio.ensure_future(result)
            task.add_done_callback(_log_observer_failure)

    def _publish_bus(self, observation: NetworkObservation) -> None:
        """Fan the transition out on the in-process bus without blocking the poll.

        Scheduled rather than awaited: a slow or wedged subscriber must never
        delay the next connectivity measurement.
        """
        publish = getattr(self._bus, "publish", None)
        if not callable(publish):
            return
        payload = {
            "state": observation.state.value,
            "previous_state": observation.previous_state.value,
            "detail": observation.detail,
            "reachable_targets": list(observation.reachable_targets),
            "unreachable_targets": list(observation.unreachable_targets),
        }
        name = NETWORK_RESTORED_EVENT if observation.state is NetworkState.ONLINE else NETWORK_LOST_EVENT if observation.state is NetworkState.OFFLINE else NETWORK_STATE_CHANGED_EVENT
        with contextlib.suppress(RuntimeError):
            task = asyncio.ensure_future(publish(name, payload, source="alpha.runtime.network"))
            task.add_done_callback(_log_bus_failure)

    # -- lifecycle ---------------------------------------------------------------

    async def run(self) -> None:
        """Poll until stopped. Never raises out of a probe.

        The wait between polls is the *decision* from :meth:`wait_decision`, so
        the backoff schedule is the same one a caller would read, and a test can
        assert that schedule by calling :meth:`check_once` without ever entering
        this loop.
        """
        while not self._stopping.is_set():
            await self.check_once()
            if self._stopping.is_set():
                break
            delay = self.wait_decision().next_poll_seconds
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=max(0.0, float(delay)))
            except TimeoutError:
                continue

    def start(self) -> asyncio.Task[None]:
        """Start the poll loop as a supervised task; idempotent.

        A disabled monitor refuses to start a loop: callers that want a
        one-shot answer call :meth:`check_once` instead, so "disabled" means
        "no background work", never "silently no state".
        """
        if self.running:
            assert self._task is not None
            return self._task
        if not self._config.enabled:
            raise RuntimeError("network monitor is disabled by configuration; call check_once() directly instead of start()")
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self.run(), name="alpha-network-monitor")
        return self._task

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Stop the poll loop, waiting up to *timeout* for the in-flight probe.

        The probe is not cancelled: a bounded connect is cheap, and abandoning
        one mid-flight would leave a half-observed state the next start would
        have to disambiguate.
        """
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, float(timeout)))
        if not task.done():  # pragma: no cover - defensive
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


def _log_observer_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("async network observer failed: %s", exc)


def _log_bus_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.debug("network bus publish failed: %s", exc)


class _FixedRandom:
    """Adapter so an injected ``Callable[[], float]`` satisfies ``random()``."""

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[], float]) -> None:
        self._fn = fn

    def random(self) -> float:
        return float(self._fn())
