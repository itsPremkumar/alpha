"""The production bracket: record that an irreversible effect was *attempted*.

The gap this closes
-------------------
:mod:`alpha.runtime.side_effects.ledger` was complete and correct and executed
zero times. ``begin`` / ``mark_in_flight`` / ``reclaim_expired`` /
``list_unknown`` had tests, a SQL implementation, and a migration, and nothing
called them, so every irreversible effect Alpha performed was unaccounted for at
runtime. This module is the piece that makes the ledger *reachable*: a
fail-open bracket an effect site wraps its call in, plus the process-wide
accessor the Gateway installs the durable ledger through.

It is deliberately **not** a lifecycle owner. It writes rows; it never cancels a
run, resumes a run, replays a tool call, or decides that an effect may be
retried. ``RunManager`` remains the sole run lifecycle owner and
``app.gateway.run_recovery.SafeRunRecoveryService`` remains the only
safe-continuation authority. The worst thing a bracket can do here is record
nothing.

Fail-open, and the direction that matters
----------------------------------------
**A ledger failure must never fail the user's action.** The effect the user asked
for has already been decided by the time this code runs; a bookkeeping outage is
a *worse* outcome than an unrecorded effect, never a second failure. So every
ledger call here is individually guarded, and a failure:

* does not propagate,
* does not change the effect's outcome,
* is counted and logged at WARNING, and
* is retained in a bounded, in-process record so it is *visible* rather than
  silently dropped.

That last point is the requirement this module exists to satisfy. An effect that
vanishes because the ledger was unavailable is the bug the whole durable-runtime
layer is supposed to prevent, so the vanishing is itself reported — loudly, and
through a counter an operator surface can read.

The three ways an effect can end, and why the exception case is not "failed"
-------------------------------------------------------------------------
The interesting decision here is what happens when the wrapped call **raises**.
For a reversible read, "it raised" means "it did not happen". For an irreversible
external effect it does not: a submit that times out after the remote accepted it
has *happened*, and recording that as a failure is precisely the optimistic
guess this ledger exists to avoid (``UNDETERMINED`` reopens for the same reason).
So the bracket does **not** settle on an exception. It leaves the entry
``IN_FLIGHT`` with its lease, and the process-wide
:class:`~alpha.runtime.side_effects.ledger.SideEffectReclaimer` converts the
expired lease into ``UNKNOWN`` — the one state that says "we cannot tell" without
guessing. A caller that genuinely *knows* the effect did not happen (a validation
refusal, a locally-detected failure) says so explicitly with
:meth:`EffectHandle.failed`, which is the only path to ``FAILED``.

``UNKNOWN`` is therefore reachable in practice, from exactly three failures:

1. The process dies between ``begin``/``mark_in_flight`` and the settle — a hard
   restart, a kill, an OOM. The lease expires and the reclaimer reclaims it.
2. The irreversible call raises, so the bracket leaves it in flight (above).
3. The effect outlives its lease: a live but very slow submit is reclaimed while
   it still runs. Its later ``complete`` is refused by the transition table, so
   the entry stays ``UNKNOWN`` for a human. That is the honest outcome — the
   ledger cannot tell a slow effect from a dead worker, and prefers to ask.

Why a process-wide accessor rather than a constructor argument
-------------------------------------------------------------
The harness cannot import ``app.*`` (``tests/test_harness_boundary.py``), so an
effect site inside the harness resolves the ledger through these accessors rather
than receiving it. The Gateway installs the durable instance at startup. This is
the same shape as ``set_network_wait_service`` in ``alpha.runtime.network`` and
``get_event_bus()`` in ``alpha.events.bus``.

``None`` is the normal case in a harness-only process and in a memory-backend
Gateway, and it degrades to *doing nothing at all* — never to raising.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from alpha.runtime.side_effects.ledger import SideEffectLedger
from alpha.runtime.side_effects.statuses import SideEffectLevel

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SIDE_EFFECT_LEASE_SECONDS",
    "EffectHandle",
    "SideEffectRecorder",
    "SideEffectRecorderStats",
    "UnaccountedEffect",
    "announce_effect",
    "get_side_effect_recorder",
    "set_side_effect_recorder",
    "side_effect_recorder_stats",
]

#: How long a live worker's claim on an in-flight effect is trusted before the
#: reclaimer may call it ``UNKNOWN``. It must exceed the longest effect the
#: bracket is ever wrapped around, or a slow-but-alive submit is reclaimed while
#: it is still running. 300s is comfortably above every irreversible effect
#: Alpha issues today (an MCP task submit, bounded by the MCP server's own tool
#: call timeout) and short enough that a crashed effect surfaces for a human
#: within one reclaim interval.
DEFAULT_SIDE_EFFECT_LEASE_SECONDS = 300.0

#: Upper bound on the in-process record of effects the ledger failed to account
#: for. Bounded because this is a diagnostic, not a store: the durable answer is
#: the ledger row, and when the ledger is down the point is only that the gap is
#: *noticed*. Oldest entries are evicted first.
UNACCOUNTED_EFFECT_MEMORY = 64

#: ``tool_side_effects.tool_name`` is ``String(128)``. Effect sites compose names
#: from provider-supplied parts, so the composed value is bounded here rather
#: than trusted to fit.
MAX_TOOL_NAME_LENGTH = 128


def _process_worker_id() -> str:
    """A lease owner id identifying this process for the life of the process."""
    return f"{socket.gethostname()}:{uuid.uuid4().hex}"


@dataclass(frozen=True, slots=True)
class UnaccountedEffect:
    """One effect the ledger could not be told about.

    Process-local and *not durable* — that is stated here so nobody later reads
    it as a substitute for the ledger row it replaces. It exists so that a
    bookkeeping outage is reported rather than absorbed.

    Deliberately carries no timestamp. The record is bounded and evicted, and a
    wall-clock field on an entry that cannot be correlated to anything durable
    would invite reading it as one.
    """

    tool_call_id: str
    tool_name: str
    phase: str
    error: str


@dataclass(frozen=True, slots=True)
class SideEffectRecorderStats:
    """Counters describing what the ledger has and has not been told."""

    #: Effects handed to the recorder. The denominator for every other number.
    announced: int = 0
    #: Effects whose announce could not even be attempted (no ledger installed,
    #: or no correlation id). Nothing was written.
    unavailable: int = 0
    #: Effects the ledger accepted and later settled.
    settled: int = 0
    #: Ledger calls that raised. The effect happened; the ledger does not know.
    unrecorded: int = 0
    #: Effects deliberately left ``IN_FLIGHT`` because the outcome is not
    #: established. These become ``UNKNOWN`` when the lease expires.
    unaccountable: int = 0
    recent_unaccounted: tuple[UnaccountedEffect, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "announced": self.announced,
            "unavailable": self.unavailable,
            "settled": self.settled,
            "unrecorded": self.unrecorded,
            "unaccountable": self.unaccountable,
            "recent_unaccounted": [
                {
                    "tool_call_id": item.tool_call_id,
                    "tool_name": item.tool_name,
                    "phase": item.phase,
                    "error": item.error,
                }
                for item in self.recent_unaccounted
            ],
        }


class EffectHandle:
    """The caller's end of one bracketed effect.

    Settling is idempotent and last-write-wins within a call: a caller that
    already settled gets a no-op for every later call, so a site may settle in
    both a ``try`` and a ``finally`` without producing a second transition.
    """

    __slots__ = ("_recorder", "_settled", "_tool_call_id", "_tool_name")

    def __init__(self, *, tool_call_id: str, tool_name: str, recorder: SideEffectRecorder) -> None:
        self._recorder = recorder
        self._tool_call_id = tool_call_id
        self._tool_name = tool_name
        self._settled = False

    @property
    def tool_call_id(self) -> str:
        return self._tool_call_id

    @property
    def tool_name(self) -> str:
        return self._tool_name

    @property
    def is_settled(self) -> bool:
        """True once this handle has handed the ledger an outcome (or none)."""
        return self._settled

    @property
    def is_recorded(self) -> bool:
        """True when this effect is accounted for in the ledger (or was never recordable)."""
        return self._settled or not self._recorder.enabled

    async def completed(self, result: object = None, detail: str = "") -> bool:
        """Record that the effect definitely took place.

        Returns whether the ledger accepted it. A ``False`` here is a reported
        failure, not a silent one — the effect still happened.
        """
        if self._settled:
            return True
        self._settled = True
        return await self._recorder._settle_completed(self._tool_call_id, self._tool_name, result=result, detail=detail)

    async def failed(self, detail: str = "", *, retryable: bool = True) -> bool:
        """Record that the effect definitely did **not** take place.

        Only for a caller that *knows*: a local validation refusal, a refused
        capability check, a connection that was never established. A call that
        merely raised is not this — see the module docstring.
        """
        if self._settled:
            return True
        self._settled = True
        return await self._recorder._settle_failed(self._tool_call_id, self._tool_name, detail=detail, retryable=retryable)

    def leave_unaccountable(self, reason: str) -> None:
        """Give up on settling; the lease will make this ``UNKNOWN``."""
        if self._settled:
            return
        self._settled = True
        self._recorder._note_unaccountable(self._tool_call_id, self._tool_name, reason)


@dataclass
class _Counters:
    announced: int = 0
    unavailable: int = 0
    settled: int = 0
    unrecorded: int = 0
    unaccountable: int = 0
    recent: list[UnaccountedEffect] = field(default_factory=list)

    def note(self, item: UnaccountedEffect) -> None:
        self.recent.append(item)
        if len(self.recent) > UNACCOUNTED_EFFECT_MEMORY:
            del self.recent[: len(self.recent) - UNACCOUNTED_EFFECT_MEMORY]

    def snapshot(self) -> SideEffectRecorderStats:
        return SideEffectRecorderStats(
            announced=self.announced,
            unavailable=self.unavailable,
            settled=self.settled,
            unrecorded=self.unrecorded,
            unaccountable=self.unaccountable,
            recent_unaccounted=tuple(self.recent),
        )


class SideEffectRecorder:
    """Brackets one irreversible effect so the ledger knows it was attempted.

    Holds no run state and decides nothing. With ``ledger=None`` every call is a
    no-op that still counts, which is what a harness-only process and a
    memory-backend Gateway both get.
    """

    def __init__(
        self,
        ledger: SideEffectLedger | None = None,
        *,
        worker_id: str | None = None,
        lease_seconds: float = DEFAULT_SIDE_EFFECT_LEASE_SECONDS,
    ) -> None:
        self._ledger = ledger
        self._worker_id = worker_id or _process_worker_id()
        self._lease_seconds = max(0.0, float(lease_seconds))
        self._counters = _Counters()

    @property
    def ledger(self) -> SideEffectLedger | None:
        return self._ledger

    @property
    def enabled(self) -> bool:
        return self._ledger is not None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def lease_seconds(self) -> float:
        return self._lease_seconds

    def stats(self) -> SideEffectRecorderStats:
        return self._counters.snapshot()

    @contextlib.asynccontextmanager
    async def announce(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        thread_id: str = "",
        run_id: str = "",
        user_id: str = "",
        arguments: object = None,
        level: SideEffectLevel | None = None,
        lease_seconds: float | None = None,
    ) -> AsyncIterator[EffectHandle]:
        """Record the effect before it is attempted, and settle it honestly after.

        Normal exit settles ``COMPLETED``. An exception settles *nothing* — see
        the module docstring for why "it raised" is not "it failed" here.
        """
        self._counters.announced += 1
        name = _bounded_tool_name(tool_name)
        if not tool_call_id or self._ledger is None:
            self._counters.unavailable += 1
            if not tool_call_id:
                logger.warning(
                    "side effect %r has no correlation id, so the ledger cannot be told about it; the effect still runs",
                    name,
                )
            handle = EffectHandle(tool_call_id=tool_call_id, tool_name=name, recorder=self)
            yield handle
            return

        lease = self._lease_seconds if lease_seconds is None else max(0.0, float(lease_seconds))
        # ``_guard``'s own parameters are positional-only, so a ledger keyword
        # named ``tool_call_id`` (which every one of them has) cannot collide
        # with them. That is the whole reason for the ``/``.
        announced = await self._guard(
            "begin",
            tool_call_id,
            name,
            self._ledger.begin,
            tool_call_id=tool_call_id,
            tool_name=name,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            arguments=arguments,
            level=level,
            owner_worker_id=self._worker_id,
            lease_seconds=lease,
        )
        if announced is not None:
            await self._guard(
                "mark_in_flight",
                tool_call_id,
                name,
                self._ledger.mark_in_flight,
                tool_call_id,
                owner_worker_id=self._worker_id,
                lease_seconds=lease,
            )

        handle = EffectHandle(tool_call_id=tool_call_id, tool_name=name, recorder=self)
        try:
            yield handle
        except BaseException as exc:  # noqa: BLE001 - re-raised immediately below
            # Deliberately unsettled. For an irreversible effect, "it raised" is
            # not evidence that it did not happen, and writing FAILED here is
            # how a duplicate gets created. The lease does the honest work.
            handle.leave_unaccountable(f"the effect raised {type(exc).__name__} and its outcome was never established")
            raise
        if not handle.is_settled:
            await handle.completed()

    # -- internals: every ledger call goes through _guard -------------------

    async def _guard(self, phase: str, tool_call_id: str, tool_name: str, call: Any, /, *args: Any, **kwargs: Any) -> Any:
        """Run one ledger call. Never raises; a failure is counted and reported.

        ``phase``/``tool_call_id``/``tool_name``/``call`` are positional-only
        because the ledger methods are called with their own ``tool_call_id``
        keyword: without the ``/`` this would be a duplicate-argument
        ``TypeError`` on the very first real write.
        """
        ledger = self._ledger
        if ledger is None:  # pragma: no cover - callers check ``enabled`` first
            return None
        try:
            return await call(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail-open is the whole contract
            self._counters.unrecorded += 1
            self._counters.note(
                UnaccountedEffect(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    phase=phase,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            logger.warning(
                "the side-effect ledger rejected a %s for %s (%s); the effect is unaccounted for and the user's action continues",
                phase,
                tool_call_id,
                type(exc).__name__,
                exc_info=True,
            )
            return None

    async def _settle_completed(self, tool_call_id: str, tool_name: str, *, result: object, detail: str) -> bool:
        ledger = self._ledger
        if ledger is None:
            return False
        settled = await self._guard("complete", tool_call_id, tool_name, ledger.complete, tool_call_id, result=result, detail=detail)
        if settled is not None:
            self._counters.settled += 1
            return True
        return False

    async def _settle_failed(self, tool_call_id: str, tool_name: str, *, detail: str, retryable: bool) -> bool:
        ledger = self._ledger
        if ledger is None:
            return False
        settled = await self._guard("fail", tool_call_id, tool_name, ledger.fail, tool_call_id, detail=detail, retryable=retryable)
        if settled is not None:
            self._counters.settled += 1
            return True
        return False

    def _note_unaccountable(self, tool_call_id: str, tool_name: str, reason: str) -> None:
        self._counters.unaccountable += 1
        self._counters.note(
            UnaccountedEffect(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                phase="left_in_flight",
                error=reason,
            )
        )
        logger.warning(
            "side effect %s (%s) was left in flight and will become UNKNOWN when its lease expires: %s",
            tool_call_id,
            tool_name,
            reason,
        )


def _bounded_tool_name(tool_name: str) -> str:
    """Keep a composed tool name inside the ledger's column width."""
    name = (tool_name or "unknown").strip() or "unknown"
    return name if len(name) <= MAX_TOOL_NAME_LENGTH else name[: MAX_TOOL_NAME_LENGTH - 1] + "…"


# ---------------------------------------------------------------------------
# Process-wide registration
# ---------------------------------------------------------------------------
# The harness cannot import ``app.*`` (``tests/test_harness_boundary.py``), so an
# effect site resolves the ledger through these accessors rather than receiving
# it. The Gateway installs the durable instance at startup; everything else --
# embedded ``AlphaClient``, a harness-only test, a memory-backend Gateway --
# keeps the default disabled recorder, whose every call is a counted no-op.
#
# There is deliberately always a recorder. ``None`` would make "bookkeeping is
# not installed" and "bookkeeping failed" indistinguishable to a caller, and
# only one of those is worth a counter.

_recorder = SideEffectRecorder(None)


def set_side_effect_recorder(recorder: SideEffectRecorder | None) -> None:
    """Install (or clear, with ``None``) the process-wide recorder."""
    global _recorder
    _recorder = recorder if recorder is not None else SideEffectRecorder(None)


def get_side_effect_recorder() -> SideEffectRecorder:
    """Return the installed recorder. Never ``None``; possibly disabled."""
    return _recorder


def side_effect_recorder_stats() -> dict[str, object]:
    """Process-wide counters, for an operator surface to read."""
    return _recorder.stats().to_dict()


@contextlib.asynccontextmanager
async def announce_effect(**kwargs: Any) -> AsyncIterator[EffectHandle]:
    """Bracket an irreversible effect through the process-wide recorder.

    The form an effect site uses. Never raises on the recorder's behalf: a
    bookkeeping failure is reported and the wrapped effect proceeds.
    """
    async with _recorder.announce(**kwargs) as handle:
        yield handle
