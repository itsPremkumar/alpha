"""The bridge between connectivity facts and durable parked sessions.

The gap this closes
-------------------
`alpha.runtime.network` knows the link is down, and `alpha.runtime.sessions` has
the vocabulary for "alive but parked", but between them a parked session was
re-derived from live signals on every boot. That is fine while the process lives
and useless across a restart: a task that parked on a dead network at 02:00 and
whose machine rebooted at 02:01 had no record that it was ever waiting.

This service is the join. It records a park durably, and on a connectivity
recovery it hands the due rows to the existing recovery owner. It decides
nothing about whether work may continue.

Why it delegates instead of resuming
------------------------------------
The resume itself must go through ``SafeRunRecoveryService``, because that
service is fail-closed around side effects: it inspects the checkpoint and only
auto-resumes a pending model node, and it turns anything that might have taken an
external effect into ``recovery_confirmation_required``. A network-aware resumer
that launched runs itself would quietly bypass that guarantee — the one rule
this whole layer exists to keep. So the default launcher is
:mod:`app.gateway.run_recovery`, injected as a callable, and this module only
supplies the durable bookkeeping and the retry bound.

Bounded, and honest when it stops
---------------------------------
:attr:`NetworkWaitPolicy.max_attempts` is the bound. When it is reached the row
becomes ``gave_up`` with a reason, and that is a *reported* outcome — a parked
task that is retried forever is the outage equivalent of the restart loop the
supervisor refuses to write. Nothing here silently drops a wait: every path ends
in a terminal state with a reason, and :meth:`NetworkWaitService.status` reports
what is open.

The backoff is durable
----------------------
``next_attempt_at`` is written when a resume fails, not computed in memory. A
reboot therefore cannot turn a five-minute backoff into a hot retry loop against a
link that is still down.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from alpha.runtime.network.states import NetworkState
from alpha.runtime.sessions.states import SessionState, SessionStateSignal, derive_session_state

logger = logging.getLogger(__name__)

__all__ = [
    "NetworkWaitPolicy",
    "NetworkWaitService",
    "NetworkWaitStore",
    "NetworkWaitStatus",
    "ParkOutcome",
    "ResumeOutcome",
]


#: What a host must be able to inject. Kept as a Protocol so this service can be
#: tested against an in-memory double, and so the SQL repository
#: (``alpha.persistence.network_waits``) satisfies it without this module
#: depending on SQLAlchemy.
@runtime_checkable
class NetworkWaitStore(Protocol):
    """What a host must be able to inject.

    Deadlines cross this boundary as a **relative number of seconds**, never as a
    timestamp. The store owns the conversion, so a SQL store produces a
    timezone-aware ``datetime`` while a test double compares a float, and this
    service never has to know which kind of store it is talking to.
    """

    async def park(self, *, thread_id: str, run_id: str | None = None, user_id: str | None = None, reason: str = "connectivity_lost", next_attempt_in_seconds: float = 0.0, last_error: str | None = None) -> dict[str, Any]: ...

    async def claim_due(self, *, limit: int = 10, lease_seconds: float = 30.0, owner: str | None = None) -> list[dict[str, Any]]: ...

    async def release(self, wait_id: str, *, next_attempt_in_seconds: float, last_error: str | None = None, attempt: int | None = None) -> dict[str, Any] | None: ...

    async def mark_terminal(self, wait_id: str, *, state: str, resumed_from_run_id: str | None = None, last_error: str | None = None) -> dict[str, Any] | None: ...

    async def reclaim_expired_leases(self, *, next_attempt_in_seconds: float = 0.0) -> int: ...

    async def get_open_for_thread(self, thread_id: str) -> dict[str, Any] | None: ...

    async def list_open(self, *, user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class NetworkWaitPolicy:
    """Bounds for parked-session retries."""

    #: Attempts before a parked session is given up on and reported.
    max_attempts: int = 24
    #: First backoff, doubling per attempt.
    backoff_initial_seconds: float = 15.0
    backoff_max_seconds: float = 900.0
    backoff_multiplier: float = 2.0
    #: How often the recovery pass sweeps for due waits.
    poll_interval_seconds: float = 30.0
    #: Claim lease, so two gateway instances cannot both launch a continuation.
    claim_lease_seconds: float = 30.0
    #: How many waits one pass may claim. Bounded so a large backlog does not
    #: stampede the provider the instant the link returns.
    max_claims_per_pass: int = 5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1: a wait that may not be retried is not a wait")
        if self.backoff_initial_seconds <= 0:
            raise ValueError("backoff_initial_seconds must be > 0")
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise ValueError("backoff_max_seconds must be >= backoff_initial_seconds")
        if self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be >= 1.0")
        if self.max_claims_per_pass < 1:
            raise ValueError("max_claims_per_pass must be >= 1")

    def backoff_for(self, attempt: int) -> float:
        """Seconds before retry *attempt* (1-based), capped."""
        rung = max(1, int(attempt))
        raw = self.backoff_initial_seconds * (self.backoff_multiplier ** (rung - 1))
        return min(self.backoff_max_seconds, raw)


@dataclass(frozen=True, slots=True)
class ParkOutcome:
    """What happened to a park request."""

    recorded: bool
    wait_id: str
    thread_id: str
    state: str
    already_waiting: bool
    next_attempt_at: Any = None

    def to_dict(self) -> dict[str, object]:
        return {
            "recorded": self.recorded,
            "wait_id": self.wait_id,
            "thread_id": self.thread_id,
            "state": self.state,
            "already_waiting": self.already_waiting,
            "next_attempt_at": self.next_attempt_at,
        }


@dataclass(frozen=True, slots=True)
class ResumeOutcome:
    """One attempt to resume a parked session."""

    wait_id: str
    thread_id: str
    state: str
    attempt: int
    run_id: str | None = None
    error: str = ""
    gave_up: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "wait_id": self.wait_id,
            "thread_id": self.thread_id,
            "state": self.state,
            "attempt": self.attempt,
            "run_id": self.run_id,
            "error": self.error,
            "gave_up": self.gave_up,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class NetworkWaitStatus:
    """What the service is doing, for an ops endpoint."""

    network_state: NetworkState
    open_waits: int
    claimed: int
    resumed: int
    gave_up: int
    running: bool = False
    last_pass_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "network_state": self.network_state.value,
            "open_waits": self.open_waits,
            "claimed": self.claimed,
            "resumed": self.resumed,
            "gave_up": self.gave_up,
            "running": self.running,
            "last_pass_at": self.last_pass_at,
        }


#: Signature of the injected launcher. The default implementation lives in
#: ``app.gateway.run_recovery``; keeping it a callable here is what stops this
#: module from acquiring a harness->app import edge.
Launcher = Callable[[Mapping[str, Any]], Awaitable[str | None]]

#: Signature of the connectivity reader. A host injects a callable that reports
#: the current :class:`NetworkState`, so this module never constructs a monitor.
NetworkStateReader = Callable[[], NetworkState]

#: Signature of the clock. Injected so the backoff schedule is testable without
#: real time passing.
NowFn = Callable[[], float]


class NetworkWaitService:
    """Records parks durably and hands due waits back to the recovery owner."""

    def __init__(
        self,
        store: NetworkWaitStore,
        *,
        launcher: Launcher | None = None,
        network_state: NetworkStateReader | None = None,
        policy: NetworkWaitPolicy | None = None,
        now_fn: NowFn | None = None,
    ) -> None:
        self._store = store
        self._launcher = launcher
        self._network_state = network_state or (lambda: NetworkState.UNKNOWN)
        self._policy = policy or NetworkWaitPolicy()
        self._now = now_fn or (lambda: 0.0)
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._claimed = 0
        self._resumed = 0
        self._gave_up = 0
        self._last_pass = 0.0

    @property
    def policy(self) -> NetworkWaitPolicy:
        return self._policy

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- parking ------------------------------------------------------------

    async def park(
        self,
        *,
        thread_id: str,
        run_id: str | None = None,
        user_id: str | None = None,
        reason: str = "connectivity_lost",
        last_error: str | None = None,
    ) -> ParkOutcome:
        """Record that a session is parked on connectivity.

        The backoff is written now, not at resume time, so a reboot cannot turn
        the first retry into an immediate one.
        """
        if not thread_id:
            raise ValueError("thread_id must be non-empty: a park with no thread cannot be resumed")
        existing = await self._store.get_open_for_thread(thread_id)
        row = await self._store.park(thread_id=thread_id, run_id=run_id, user_id=user_id, reason=reason, last_error=last_error, next_attempt_in_seconds=self._policy.backoff_for(1))
        return ParkOutcome(
            recorded=True,
            wait_id=str(row.get("id") or ""),
            thread_id=thread_id,
            state=str(row.get("state") or "waiting"),
            already_waiting=existing is not None,
            next_attempt_at=row.get("next_attempt_at"),
        )

    # -- resuming -----------------------------------------------------------

    async def resume_due(self, *, limit: int | None = None) -> list[ResumeOutcome]:
        """Claim and attempt every due wait, up to *limit*.

        Refuses outright while connectivity is not usable. This is the important
        gate: a wait exists *because* the link was gone, so attempting a resume
        while it is still gone would spend the attempt budget on a certainty.
        ``UNKNOWN`` permits an attempt for the same reason the monitor does --
        not knowing is not knowing the link is down.
        """
        state = self._network_state()
        if state is NetworkState.OFFLINE:
            logger.debug("skipping the network-wait pass while connectivity is offline")
            return []
        if self._launcher is None:
            logger.warning("no resume launcher is configured; parked sessions will not resume")
            return []

        claimed = await self._store.claim_due(limit=limit or self._policy.max_claims_per_pass, lease_seconds=self._policy.claim_lease_seconds, owner=_owner())
        self._claimed += len(claimed)
        outcomes: list[ResumeOutcome] = []
        for row in claimed:
            outcomes.append(await self._resume_one(row))
        return outcomes

    async def _resume_one(self, row: Mapping[str, Any]) -> ResumeOutcome:
        wait_id = str(row.get("id"))
        thread_id = str(row.get("thread_id"))
        attempt = int(row.get("attempt") or 0)

        if attempt >= self._policy.max_attempts:
            # Bounded, and reported. This is the outage equivalent of a restart
            # loop: a session retried forever against a link that never returns
            # helps nobody and hides the problem.
            await self._store.mark_terminal(wait_id, state="gave_up", last_error=f"gave up after {attempt} resume attempts while connectivity was unavailable")
            self._gave_up += 1
            return ResumeOutcome(wait_id=wait_id, thread_id=thread_id, state="gave_up", attempt=attempt, gave_up=True, detail="attempt budget exhausted")

        try:
            launched_run_id = await self._launcher(row)
        except asyncio.CancelledError:
            await self._release(row, wait_id, attempt, "resume cancelled before a run was launched")
            raise
        except BaseException as exc:  # noqa: BLE001 - one failed resume must not strand the wait
            logger.warning("resume attempt failed for thread %s: %s", thread_id, exc)
            await self._release(row, wait_id, attempt, f"{type(exc).__name__}: {exc}")
            return ResumeOutcome(wait_id=wait_id, thread_id=thread_id, state="waiting", attempt=attempt, error=f"{type(exc).__name__}: {exc}")

        if not launched_run_id:
            # The recovery owner refused -- most likely a side-effect-unsafe
            # checkpoint, which is the correct outcome, not a failure to retry
            # blindly. The wait is settled as attempted so it is not retried
            # forever against a checkpoint that will never become safe.
            await self._store.mark_terminal(wait_id, state="gave_up", last_error="the recovery owner declined to auto-resume this checkpoint")
            self._gave_up += 1
            return ResumeOutcome(wait_id=wait_id, thread_id=thread_id, state="gave_up", attempt=attempt, gave_up=True, detail="recovery owner declined the checkpoint")

        await self._store.mark_terminal(wait_id, state="resumed", resumed_from_run_id=launched_run_id)
        self._resumed += 1
        return ResumeOutcome(wait_id=wait_id, thread_id=thread_id, state="resumed", attempt=attempt, run_id=launched_run_id)

    async def _release(self, row: Mapping[str, Any], wait_id: str, attempt: int, error: str) -> None:
        """Put a claimed wait back to ``waiting`` with the next backoff written."""
        del row
        await self._store.release(wait_id, next_attempt_in_seconds=self._policy.backoff_for(attempt + 1), last_error=error, attempt=attempt)

    # -- reporting ----------------------------------------------------------

    def session_state_for(self, *, run_status: object = None) -> SessionState:
        """Derive the session state a parked thread should be reported as.

        Exposed so a UI, a channel reply, or an API surface reads one derivation
        rather than re-deciding "is this waiting or failed" for itself.
        """
        return derive_session_state(SessionStateSignal(run_status=run_status, network_unavailable=self._network_state() is NetworkState.OFFLINE)).state

    async def status(self) -> NetworkWaitStatus:
        open_rows = await self._store.list_open() if hasattr(self._store, "list_open") else []
        return NetworkWaitStatus(
            network_state=self._network_state(),
            open_waits=len(open_rows),
            claimed=self._claimed,
            resumed=self._resumed,
            gave_up=self._gave_up,
            running=self.running,
            last_pass_at=self._last_pass,
        )

    # -- internals ----------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "claimed": self._claimed,
            "resumed": self._resumed,
            "gave_up": self._gave_up,
            "last_pass_at": self._last_pass,
        }

    # -- loop ---------------------------------------------------------------

    def start(self) -> asyncio.Task[None]:
        """Start the recovery pass as a supervised task; idempotent."""
        if self.running:
            assert self._task is not None
            return self._task
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self.run(), name="alpha-network-wait-recovery")
        return self._task

    async def run(self) -> None:
        """Sweep for due waits until stopped. Never raises out of one failure."""
        while not self._stopping.is_set():
            self._last_pass = self._now()
            try:
                await self.resume_due()
            except asyncio.CancelledError:
                raise
            except BaseException:  # noqa: BLE001 - a failed pass must not kill the loop
                logger.warning("network-wait recovery pass failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._policy.poll_interval_seconds)
            except TimeoutError:
                continue

    async def stop(self, *, timeout: float = 5.0) -> None:
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, float(timeout)))

    # -- internals ----------------------------------------------------------


def _owner() -> str:
    """A claim owner id, unique per process."""
    import socket

    return f"{socket.gethostname()}:{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Process-wide registration
# ---------------------------------------------------------------------------
# The harness cannot import ``app.*`` (``tests/test_harness_boundary.py``), so a
# harness-side worker that needs to park a session resolves the service through
# these accessors rather than receiving it. The Gateway installs the instance at
# startup. This is the same shape as ``alpha.events.bus.get_event_bus()`` and
# ``alpha.orchestrator.restart.register_restart_hook()``.
#
# ``None`` is the normal case in a harness-only process, and every caller must
# treat it as "parking is unavailable" rather than raising: a missing optional
# service must never turn a run failure into a different run failure.

_wait_service: NetworkWaitService | None = None


def set_network_wait_service(service: NetworkWaitService | None) -> None:
    """Install (or clear, with ``None``) the process-wide wait service."""
    global _wait_service
    _wait_service = service


def get_network_wait_service() -> NetworkWaitService | None:
    """Return the installed wait service, or ``None`` when there is none."""
    return _wait_service


async def park_session_if_available(
    *,
    thread_id: str,
    run_id: str | None = None,
    user_id: str | None = None,
    reason: str = "connectivity_lost",
    last_error: str | None = None,
) -> bool:
    """Park a session when a service is installed. Never raises.

    Returns whether a park was actually recorded. A harness-only process, a
    deployment with ``network.enabled: false``, and a store outage all return
    ``False`` rather than propagating, because the caller's real failure has
    already been recorded by the run ledger and this must not become a second,
    noisier one.
    """
    service = get_network_wait_service()
    if service is None:
        return False
    try:
        await service.park(thread_id=thread_id, run_id=run_id, user_id=user_id, reason=reason, last_error=last_error)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("could not record a network wait for thread %s", thread_id, exc_info=True)
        return False
    return True
