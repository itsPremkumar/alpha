"""The side-effect ledger: durable, enumerable record of external effects.

What the ledger is for
----------------------
Alpha already refuses to blindly replay a side effect: `SafeRunRecoveryService`
will not auto-resume a checkpoint whose pending node is a tool, MCP, shell,
browser, write/delete, payment, or unknown node, and records
`stop_reason="recovery_confirmation_required"` instead. That is correct, and it is
a *run-level* signal.

The ledger is the per-effect counterpart. It answers the question the run-level
stop reason cannot: **which specific external effects are unaccounted for?** A
support engineer needs a list, a reconciler needs a queue, and a diagnostic agent
needs something to check. "Run 42 might have charged someone" is not actionable;
"tool call `call_abc` may have charged someone" is.

The crash rule
--------------
The whole design turns on one observation: a side effect has three honest
outcomes, and a worker that dies mid-call produces the third. So:

1. An entry is written ``PENDING`` **before** the call is attempted. A crash
   there means nothing ran, and recovery is trivial.
2. It becomes ``IN_FLIGHT`` with an owner and a lease **before** the call.
3. It becomes ``COMPLETED`` or ``FAILED`` only when the live worker observes the
   real outcome.
4. A worker that vanishes leaves the entry ``IN_FLIGHT`` with an expired lease.
   :meth:`SideEffectLedger.reclaim_expired` is what converts that to ``UNKNOWN``.

Step 4 is the load-bearing one, and it is why the lease is on the entry rather
than only in memory: **the process that would have known the answer is the one
that died.** Nobody else can supply it, so the honest state is ``UNKNOWN`` and the
entry becomes enumerable work rather than a silent gap.

Reconciliation, not optimism
----------------------------
:meth:`SideEffectLedger.reconcile` is the only exit from ``UNKNOWN``, and it takes
a :class:`ReconciliationVerdict`. ``UNDETERMINED`` reopens the entry for another
attempt rather than being recorded as a failure — conflating "I could not tell"
with "it did not happen" is precisely how a duplicate gets created. High-risk and
destructive entries whose reconciliation is undetermined are additionally
*escalated*, because an unreconcilable charge or delete is an event that needs a
person even when the automated answer was inconclusive.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from alpha.runtime.resilience.clock import Clock, coerce_clock
from alpha.runtime.side_effects.statuses import (
    IllegalSideEffectTransition,
    ReconciliationVerdict,
    SideEffectEntry,
    SideEffectLevel,
    SideEffectStatus,
    can_transition,
    status_for_verdict,
    validate_transition,
    verdict_is_settled,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SIDE_EFFECT_LEVEL",
    "InMemorySideEffectLedger",
    "IllegalSideEffectTransition",
    "ReconciliationResult",
    "SideEffectLedger",
    "SideEffectReclaimer",
    "arguments_digest",
    "can_transition",
    "new_tool_call_id",
    "normalize_level",
    "result_digest",
    "status_for_verdict",
    "validate_transition",
    "verdict_is_settled",
]


def arguments_digest(arguments: object) -> str:
    """Return a stable, non-reversible digest of tool *arguments*.

    A side-effect record is durable, is exported into support bundles, and
    outlives the thread, so it must not be a place arguments accumulate — those
    routinely carry a prompt, a file body, or a token. ``sha256`` over
    canonical JSON gives two attempts of the same call the same digest, which is
    exactly what a reconciler needs to compare them, without keeping either
    payload. Sorted keys make the digest independent of dict ordering.
    """
    try:
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - default=str covers most
        encoded = repr(arguments)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def result_digest(result: object) -> str:
    """Return a stable digest of a tool *result*, for the same reason."""
    try:
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover
        encoded = repr(result)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


#: Default risk when a caller does not classify the tool. ``MODERATE`` is the
#: honest middle: not enough to claim safety, not enough to page a human.
DEFAULT_SIDE_EFFECT_LEVEL = SideEffectLevel.MODERATE

#: Tool-name -> level, for the tools whose effect Alpha already knows. This is a
#: *default* classification, deliberately short: a tool absent from this map gets
#: :data:`DEFAULT_SIDE_EFFECT_LEVEL` and a real caller can always say more with
#: :meth:`SideEffectLedger.begin`. Overstating risk here would train operators to
#: ignore escalations, which is worse than understating one entry.
_TOOL_LEVELS: Mapping[str, SideEffectLevel] = {
    "read_file": SideEffectLevel.READ_ONLY,
    "ls": SideEffectLevel.READ_ONLY,
    "glob": SideEffectLevel.READ_ONLY,
    "grep": SideEffectLevel.READ_ONLY,
    "web_search": SideEffectLevel.READ_ONLY,
    "web_fetch": SideEffectLevel.READ_ONLY,
    "git_diff": SideEffectLevel.READ_ONLY,
    "git_log": SideEffectLevel.READ_ONLY,
    "git_status": SideEffectLevel.READ_ONLY,
    "write_file": SideEffectLevel.MODERATE,
    "str_replace": SideEffectLevel.MODERATE,
    "bash": SideEffectLevel.HIGH_RISK,
    "git_commit": SideEffectLevel.MODERATE,
    "git_push": SideEffectLevel.HIGH_RISK,
    "git_rebase": SideEffectLevel.HIGH_RISK,
    "git_merge": SideEffectLevel.HIGH_RISK,
}


def normalize_level(tool_name: str, level: SideEffectLevel | None = None) -> SideEffectLevel:
    """Resolve the risk for *tool_name*, preferring an explicit *level*."""
    if level is not None:
        return level
    return _TOOL_LEVELS.get(tool_name, DEFAULT_SIDE_EFFECT_LEVEL)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """The outcome of reconciling one entry."""

    entry: SideEffectEntry
    verdict: ReconciliationVerdict
    escalated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_call_id": self.entry.tool_call_id,
            "tool_name": self.entry.tool_name,
            "verdict": self.verdict.value,
            "escalated": self.escalated,
            "status": self.entry.status.value,
            "detail": self.entry.detail,
        }


@runtime_checkable
class SideEffectLedger(Protocol):
    """Durable record of external effects, keyed by provider ``tool_call_id``.

    ``tool_call_id`` is the key for the same reason it is the correlation key for
    a ``ToolMessage`` elsewhere in Alpha: it is the one identifier the model, the
    journal, and the stream all agree on. It is deliberately *not* a registry
    ownership key for execution — that lesson is already learned in
    `alpha/subagents/` and does not need repeating here.
    """

    async def begin(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        thread_id: str = "",
        run_id: str = "",
        user_id: str = "",
        arguments: object = None,
        level: SideEffectLevel | None = None,
        owner_worker_id: str | None = None,
        lease_seconds: float = 60.0,
    ) -> SideEffectEntry:
        """Record an effect before it is attempted. Idempotent per ``tool_call_id``."""

    async def mark_in_flight(self, tool_call_id: str, *, owner_worker_id: str, lease_seconds: float = 60.0) -> SideEffectEntry:
        """Take ownership of an attempt right before the call is issued."""

    async def complete(self, tool_call_id: str, *, result: object = None, detail: str = "") -> SideEffectEntry:
        """Record that the effect definitely took place."""

    async def fail(self, tool_call_id: str, *, detail: str = "", retryable: bool = True) -> SideEffectEntry:
        """Record that the effect definitely did not take place."""

    async def reconcile(self, tool_call_id: str, verdict: ReconciliationVerdict, *, detail: str = "", evidence: object = None) -> ReconciliationResult:
        """Resolve an ``UNKNOWN`` entry. The only exit from that state."""

    async def reclaim_expired(self, *, now: float | None = None) -> tuple[SideEffectEntry, ...]:
        """Convert ``IN_FLIGHT`` entries whose worker is gone into ``UNKNOWN``."""

    async def list_unknown(self, *, thread_id: str = "", run_id: str = "", user_id: str = "", limit: int = 100) -> tuple[SideEffectEntry, ...]:
        """Enumerate entries that still owe an answer."""

    async def get(self, tool_call_id: str) -> SideEffectEntry | None:
        """Return one entry, or None."""

    async def all(self) -> tuple[SideEffectEntry, ...]:
        """Every entry in the ledger, for summaries and owner-scoped reads.

        Both implementations provide this; declaring it on the protocol is what
        lets an HTTP surface count statuses through the protocol instead of
        type-narrowing to one implementation (and silently getting it wrong on
        the other).
        """


class InMemorySideEffectLedger:
    """Reference implementation, used by tests and as the no-persistence default.

    Not a substitute for the SQL repository across processes; the same caveat the
    rest of Alpha's JSON state carries applies here. Per-entry it *is* the whole
    contract, including the lease and reclaim semantics, so a test against this
    class pins behaviour the durable implementation must also satisfy.
    """

    def __init__(self, *, clock: Clock | None = None, escalate: Callable[[SideEffectEntry, ReconciliationVerdict], bool] | None = None) -> None:
        self._clock = coerce_clock(clock)
        self._entries: dict[str, SideEffectEntry] = {}
        self._lock = asyncio.Lock()
        self._escalate = escalate or _default_escalation

    def now(self) -> float:
        return self._clock.now()

    async def begin(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        thread_id: str = "",
        run_id: str = "",
        user_id: str = "",
        arguments: object = None,
        level: SideEffectLevel | None = None,
        owner_worker_id: str | None = None,
        lease_seconds: float = 60.0,
    ) -> SideEffectEntry:
        if not tool_call_id:
            raise ValueError("tool_call_id must be non-empty: it is the correlation key for the whole ledger")
        async with self._lock:
            existing = self._entries.get(tool_call_id)
            if existing is not None:
                # Idempotent: re-announcing an effect that is already recorded
                # must not reset it, or a retried announce would erase the very
                # UNKNOWN state it exists to preserve.
                return existing
            now = self.now()
            entry = SideEffectEntry(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status=SideEffectStatus.PENDING,
                level=normalize_level(tool_name, level),
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                arguments_digest=arguments_digest(arguments) if arguments is not None else None,
                owner_worker_id=owner_worker_id,
                lease_expires_at=now + lease_seconds if lease_seconds > 0 else None,
                created_at=now,
                updated_at=now,
            )
            self._entries[tool_call_id] = entry
            return entry

    async def mark_in_flight(self, tool_call_id: str, *, owner_worker_id: str, lease_seconds: float = 60.0) -> SideEffectEntry:
        return await self._move(tool_call_id, SideEffectStatus.IN_FLIGHT, owner_worker_id=owner_worker_id, lease_seconds=lease_seconds)

    async def complete(self, tool_call_id: str, *, result: object = None, detail: str = "") -> SideEffectEntry:
        return await self._move(tool_call_id, SideEffectStatus.COMPLETED, detail=detail, result=result)

    async def fail(self, tool_call_id: str, *, detail: str = "", retryable: bool = True) -> SideEffectEntry:
        del retryable  # Recorded by the caller's retry policy, not by the ledger.
        return await self._move(tool_call_id, SideEffectStatus.FAILED, detail=detail)

    async def reconcile(self, tool_call_id: str, verdict: ReconciliationVerdict, *, detail: str = "", evidence: object = None) -> ReconciliationResult:
        async with self._lock:
            entry = self._require(tool_call_id)
            if not verdict_is_settled(verdict):
                # Undetermined: reopen rather than settle. Recording this as a
                # failure is how a duplicate side effect gets created.
                if entry.status is not SideEffectStatus.UNKNOWN:
                    validate_transition(entry.status, SideEffectStatus.RECONCILED)
                reopened = _replace(
                    entry,
                    status=SideEffectStatus.UNKNOWN,
                    verdict=None,
                    detail=detail or f"reconciliation undetermined: {entry.detail}" if detail else entry.detail,
                    updated_at=self.now(),
                )
                self._entries[tool_call_id] = reopened
                return ReconciliationResult(entry=reopened, verdict=verdict, escalated=False)

            validate_transition(entry.status, SideEffectStatus.RECONCILED)
            settled = _replace(
                entry,
                status=status_for_verdict(verdict),
                verdict=verdict,
                detail=detail,
                result_digest=result_digest(evidence) if evidence is not None else entry.result_digest,
                owner_worker_id=None,
                lease_expires_at=None,
                updated_at=self.now(),
            )
            self._entries[tool_call_id] = settled
            escalated = bool(self._escalate(settled, verdict))
            return ReconciliationResult(entry=settled, verdict=verdict, escalated=escalated)

    async def reclaim_expired(self, *, now: float | None = None) -> tuple[SideEffectEntry, ...]:
        """Turn every ``IN_FLIGHT``/``PENDING`` entry with a dead lease into ``UNKNOWN``.

        ``PENDING`` is included on purpose. An entry recorded before the call was
        attempted but never advanced means the worker died in the window between
        the two writes, and whether the call went out is exactly as unknown as if
        it had died mid-flight. Treating that as "definitely nothing ran" is the
        optimistic guess this ledger exists to avoid.
        """
        moment = self.now() if now is None else float(now)
        reclaimed: list[SideEffectEntry] = []
        async with self._lock:
            for tool_call_id, entry in list(self._entries.items()):
                if entry.status not in (SideEffectStatus.PENDING, SideEffectStatus.IN_FLIGHT):
                    continue
                if entry.lease_expires_at is None or entry.lease_expires_at > moment:
                    continue
                unknown = _replace(
                    entry,
                    status=SideEffectStatus.UNKNOWN,
                    detail=f"worker {entry.owner_worker_id or 'unknown'} stopped reporting before the outcome was known",
                    owner_worker_id=None,
                    lease_expires_at=None,
                    updated_at=moment,
                )
                self._entries[tool_call_id] = unknown
                reclaimed.append(unknown)
        if reclaimed:
            logger.warning("reclaimed %d side effect(s) with an expired lease into UNKNOWN", len(reclaimed))
        return tuple(reclaimed)

    async def list_unknown(self, *, thread_id: str = "", run_id: str = "", user_id: str = "", limit: int = 100) -> tuple[SideEffectEntry, ...]:
        async with self._lock:
            matches = [entry for entry in self._entries.values() if entry.needs_reconciliation and (not thread_id or entry.thread_id == thread_id) and (not run_id or entry.run_id == run_id) and (not user_id or entry.user_id == user_id)]
        matches.sort(key=lambda entry: (entry.updated_at, entry.tool_call_id))
        return tuple(matches[: max(0, limit)])

    async def get(self, tool_call_id: str) -> SideEffectEntry | None:
        async with self._lock:
            return self._entries.get(tool_call_id)

    async def all(self) -> tuple[SideEffectEntry, ...]:
        async with self._lock:
            return tuple(self._entries.values())

    # -- internals -------------------------------------------------------------

    async def _move(
        self,
        tool_call_id: str,
        requested: SideEffectStatus,
        *,
        owner_worker_id: str | None = None,
        lease_seconds: float = 0.0,
        detail: str = "",
        result: object = None,
    ) -> SideEffectEntry:
        async with self._lock:
            entry = self._require(tool_call_id)
            status = validate_transition(entry.status, requested)
            now = self.now()
            updated = _replace(
                entry,
                status=status,
                detail=detail or entry.detail,
                owner_worker_id=owner_worker_id,
                lease_expires_at=(now + lease_seconds) if lease_seconds > 0 else None,
                result_digest=result_digest(result) if result is not None else entry.result_digest,
                updated_at=now,
            )
            self._entries[tool_call_id] = updated
            return updated

    def _require(self, tool_call_id: str) -> SideEffectEntry:
        entry = self._entries.get(tool_call_id)
        if entry is None:
            raise KeyError(f"no side-effect entry for tool_call_id {tool_call_id!r}; record it with begin() before the call")
        return entry


def _replace(entry: SideEffectEntry, **changes: Any) -> SideEffectEntry:
    """Return a copy of *entry* with *changes* applied (frozen dataclass)."""
    from dataclasses import replace as _dc_replace

    return _dc_replace(entry, **changes)


def _default_escalation(entry: SideEffectEntry, verdict: ReconciliationVerdict) -> bool:
    """Escalate when a high-risk effect is settled as confirmed failure.

    A confirmed *success* needs no escalation -- somebody asked for it and it
    happened. A confirmed *failure* on a high-risk or destructive tool does: a
    ``git_push`` or ``bash`` that did not do what was intended needs a person,
    and the ledger is where that fact is recorded.
    """
    return verdict is ReconciliationVerdict.CONFIRMED_FAILURE and entry.level.requires_reconciliation


class SideEffectReclaimer:
    """Drives :meth:`SideEffectLedger.reclaim_expired` on a bounded interval.

    Kept separate from the ledger so the ledger stays a pure, synchronously
    testable object and the *loop* owns nothing but a timer. A reclaim pass that
    raises is logged and the loop continues: a transient database error must not
    stop the reclaimer, because the entries it would have converted are exactly
    the ones whose answer died with their worker.
    """

    __slots__ = ("_interval_seconds", "_ledger", "_logger", "_stopping", "_task")

    def __init__(self, ledger: SideEffectLedger, *, interval_seconds: float = 30.0, logger_: logging.Logger | None = None) -> None:
        self._ledger = ledger
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._logger = logger_ or logger
        self._stopping: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> tuple[SideEffectEntry, ...]:
        """One pass. Never raises."""
        try:
            return await self._ledger.reclaim_expired()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.warning("side-effect reclaim pass failed", exc_info=True)
            return ()

    async def run(self) -> None:
        if self._stopping is None:
            self._stopping = asyncio.Event()
        while not self._stopping.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self.run(), name="alpha-side-effect-reclaimer")
        return self._task

    async def stop(self, *, timeout: float = 5.0) -> None:
        if self._stopping is not None:
            self._stopping.set()
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()


def new_tool_call_id(prefix: str = "call") -> str:
    """Mint a ledger id for a tool call that has no provider-assigned one."""
    return f"{prefix}_{uuid.uuid4().hex}"
