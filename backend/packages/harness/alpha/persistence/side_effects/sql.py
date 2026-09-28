"""SQL implementation of the side-effect ledger.

This class satisfies :class:`alpha.runtime.side_effects.ledger.SideEffectLedger`
and therefore inherits that module's semantics -- the lease, the reclaim, the
``UNKNOWN``-only exit through reconciliation. What it adds is that those
semantics hold **across processes**, which the in-memory reference implementation
cannot promise.

Concurrency, and why each transition is conditional
---------------------------------------------------
Every state change is an ``UPDATE ... WHERE <primary key> AND status = <expected>``
rather than a read-modify-write, and the affected-row count is the answer to "did
my transition apply?". That is what makes the ledger safe with two gateway
instances observing the same effect:

- ``begin`` relies on the primary key, so a duplicate announce collides rather
  than creating a second row for one call.
- ``mark_in_flight`` / ``complete`` / ``fail`` are guarded by the status the
  caller believed it was in. A reaper that already moved the row to ``unknown``
  wins, and the late worker's transition reports "not applied" instead of
  silently overwriting the unknown with a guess.
- ``reconcile`` is guarded on ``unknown``, so two reconcilers cannot both settle
  an entry, and a settled entry cannot be re-settled.

That last point is the whole design: **the process that would have known the
answer is the one that died**, so a later worker has no standing to decide. It can
record a transition it was already mid-way through, and nothing more.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alpha.persistence.side_effects.model import RECLAIMABLE_LEDGER_STATUSES, ToolSideEffectRow
from alpha.runtime.side_effects.ledger import DEFAULT_SIDE_EFFECT_LEVEL, ReconciliationResult, arguments_digest, normalize_level, result_digest
from alpha.runtime.side_effects.statuses import (
    ReconciliationVerdict,
    SideEffectEntry,
    SideEffectLevel,
    SideEffectStatus,
    validate_transition,
    verdict_is_settled,
)


class SideEffectTransitionLost(RuntimeError):
    """The row was no longer in the state the caller expected.

    Raised instead of silently succeeding, because the two causes mean opposite
    things: a reaper reclaimed the effect into ``UNKNOWN`` (a reconciler is now
    needed) or the effect was already settled (the work is done). Collapsing them
    into a successful no-op would let a late worker report a completed effect
    that nobody ever actually completed.
    """


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a possibly-naive timestamp read back from SQLite."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlSideEffectLedger:
    """A durable :class:`SideEffectLedger` over the ``tool_side_effects`` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # -- lifecycle -----------------------------------------------------------

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
        if not tool_call_id:
            raise ValueError("tool_call_id must be non-empty: it is the correlation key for the whole ledger")
        now = _now()
        resolved = normalize_level(tool_name, level)
        async with self._sf() as session, session.begin():
            existing = await session.get(ToolSideEffectRow, tool_call_id)
            if existing is not None:
                return _to_entry(existing)
            row = ToolSideEffectRow(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status=SideEffectStatus.PENDING.value,
                level=resolved.value,
                thread_id=thread_id or None,
                run_id=run_id or None,
                user_id=user_id or None,
                arguments_digest=arguments_digest(arguments) if arguments is not None else None,
                owner_worker_id=owner_worker_id,
                lease_expires_at=(now + timedelta(seconds=lease_seconds)) if lease_seconds > 0 else None,
                attempt=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                # A concurrent begin from another process won the primary key.
                # That is the expected outcome of an idempotent call, not an error.
                await session.rollback()
                row = await session.get(ToolSideEffectRow, tool_call_id)
                if row is None:  # pragma: no cover - the winner rolled back too
                    raise
                return _to_entry(row)
            return _to_entry(row)

    async def mark_in_flight(self, tool_call_id: str, *, owner_worker_id: str, lease_seconds: float = 60.0) -> SideEffectEntry:
        return await self._transition(tool_call_id, SideEffectStatus.IN_FLIGHT, expected={SideEffectStatus.PENDING}, owner_worker_id=owner_worker_id, lease_seconds=lease_seconds)

    async def complete(self, tool_call_id: str, *, result: object = None, detail: str = "") -> SideEffectEntry:
        return await self._transition(tool_call_id, SideEffectStatus.COMPLETED, expected={SideEffectStatus.PENDING, SideEffectStatus.IN_FLIGHT}, detail=detail, result=result)

    async def fail(self, tool_call_id: str, *, detail: str = "", retryable: bool = True) -> SideEffectEntry:
        del retryable  # Owned by the caller's retry policy, not by the ledger.
        return await self._transition(tool_call_id, SideEffectStatus.FAILED, expected={SideEffectStatus.PENDING, SideEffectStatus.IN_FLIGHT}, detail=detail)

    async def reconcile(self, tool_call_id: str, verdict: ReconciliationVerdict, *, detail: str = "", evidence: object = None) -> ReconciliationResult:
        """Resolve an ``UNKNOWN`` entry. The only exit from that state.

        An ``UNDETERMINED`` verdict *reopens* the entry rather than settling it.
        Recording "I looked and still cannot tell" as a failure is precisely how
        a duplicate side effect gets created.
        """
        now = _now()
        async with self._sf() as session, session.begin():
            row = await session.get(ToolSideEffectRow, tool_call_id)
            if row is None:
                raise KeyError(f"no side-effect entry for tool_call_id {tool_call_id!r}; record it with begin() before the call")
            current = SideEffectStatus(row.status)
            if not verdict_is_settled(verdict):
                target = SideEffectStatus.UNKNOWN
                validate_transition(current, target) if current is not SideEffectStatus.UNKNOWN else None
                values: dict[str, Any] = {"status": target.value, "verdict": None, "updated_at": now}
                if detail:
                    values["detail"] = detail
                result = await session.execute(update(ToolSideEffectRow).where(and_(ToolSideEffectRow.tool_call_id == tool_call_id)).values(**values))
                if not result.rowcount:  # pragma: no cover - row vanished mid-transaction
                    raise SideEffectTransitionLost(tool_call_id, current, target)
                return _reconciliation_result(_to_entry(await session.get(ToolSideEffectRow, tool_call_id)), verdict, escalated=False)

            target = SideEffectStatus.RECONCILED
            validate_transition(current, target)
            values = {
                "status": target.value,
                "verdict": verdict.value,
                "detail": detail,
                "owner_worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
            if evidence is not None:
                values["result_digest"] = result_digest(evidence)
            # Guarded on UNKNOWN: two reconcilers cannot both settle, and a
            # settled entry cannot be re-settled.
            result = await session.execute(update(ToolSideEffectRow).where(and_(ToolSideEffectRow.tool_call_id == tool_call_id, ToolSideEffectRow.status == SideEffectStatus.UNKNOWN.value)).values(**values))
            if not result.rowcount:
                raise SideEffectTransitionLost(tool_call_id, current, target)
            settled = _to_entry(await session.get(ToolSideEffectRow, tool_call_id))
            return _reconciliation_result(settled, verdict, escalated=bool(settled.level in (SideEffectLevel.HIGH_RISK, SideEffectLevel.DESTRUCTIVE) and verdict is ReconciliationVerdict.CONFIRMED_FAILURE))

    async def reclaim_expired(self, *, now: float | None = None) -> tuple[SideEffectEntry, ...]:
        """Convert ``pending``/``in_flight`` rows with a dead lease into ``unknown``.

        ``PENDING`` is included on purpose: a worker that died between writing the
        entry and marking it in flight leaves it exactly as unaccounted-for as one
        that died mid-call. Treating that as "definitely nothing ran" is the
        optimistic guess the ledger exists to avoid.
        """
        moment = _now() if now is None else datetime.fromtimestamp(float(now), tz=UTC)
        async with self._sf() as session, session.begin():
            candidates = (
                await session.scalars(
                    select(ToolSideEffectRow.tool_call_id).where(
                        ToolSideEffectRow.status.in_(sorted(RECLAIMABLE_LEDGER_STATUSES)),
                        ToolSideEffectRow.lease_expires_at.is_not(None),
                        ToolSideEffectRow.lease_expires_at < moment,
                    )
                )
            ).all()
            reclaimed: list[SideEffectEntry] = []
            for call_id in candidates:
                row = await session.get(ToolSideEffectRow, call_id)
                owner = row.owner_worker_id if row is not None else None
                result = await session.execute(
                    update(ToolSideEffectRow)
                    .where(and_(ToolSideEffectRow.tool_call_id == call_id, ToolSideEffectRow.status.in_(sorted(RECLAIMABLE_LEDGER_STATUSES))))
                    .values(
                        status=SideEffectStatus.UNKNOWN.value,
                        detail=f"worker {owner or 'unknown'} stopped reporting before the outcome was known",
                        owner_worker_id=None,
                        lease_expires_at=None,
                        updated_at=moment,
                    )
                )
                if result.rowcount:
                    reclaimed.append(_to_entry(await session.get(ToolSideEffectRow, call_id)))
            if reclaimed:
                logger.warning("reclaimed %d side effect(s) with an expired lease into UNKNOWN", len(reclaimed))
            return tuple(reclaimed)

    # -- reading -------------------------------------------------------------

    async def get(self, tool_call_id: str) -> SideEffectEntry | None:
        async with self._sf() as session:
            row = await session.get(ToolSideEffectRow, tool_call_id)
            return _to_entry(row) if row is not None else None

    async def list_unknown(self, *, thread_id: str = "", run_id: str = "", user_id: str = "", limit: int = 100) -> tuple[SideEffectEntry, ...]:
        """Entries that still owe an answer: ``UNKNOWN`` only.

        Deliberately narrower than ``OPEN_LEDGER_STATUSES``. A ``reconciled``
        row is open in the sense that its *history* is still live, but it no
        longer needs attention -- ``SideEffectEntry.needs_reconciliation`` is
        ``UNKNOWN`` alone, and this must agree with it or a reconciler would be
        handed a queue of already-settled work.
        """
        statement = select(ToolSideEffectRow).where(ToolSideEffectRow.status == SideEffectStatus.UNKNOWN.value).order_by(ToolSideEffectRow.updated_at, ToolSideEffectRow.tool_call_id).limit(max(0, limit))
        if thread_id:
            statement = statement.where(ToolSideEffectRow.thread_id == thread_id)
        if run_id:
            statement = statement.where(ToolSideEffectRow.run_id == run_id)
        if user_id:
            statement = statement.where(ToolSideEffectRow.user_id == user_id)
        async with self._sf() as session:
            return tuple(_to_entry(row) for row in (await session.scalars(statement)).all())

    async def all(self) -> tuple[SideEffectEntry, ...]:
        async with self._sf() as session:
            return tuple(_to_entry(row) for row in (await session.scalars(select(ToolSideEffectRow).order_by(ToolSideEffectRow.created_at))).all())

    # -- internals -----------------------------------------------------------

    async def _transition(
        self,
        tool_call_id: str,
        requested: SideEffectStatus,
        *,
        expected: set[SideEffectStatus],
        owner_worker_id: str | None = None,
        lease_seconds: float = 0.0,
        detail: str = "",
        result: object = None,
    ) -> SideEffectEntry:
        now = _now()
        async with self._sf() as session, session.begin():
            row = await session.get(ToolSideEffectRow, tool_call_id)
            if row is None:
                raise KeyError(f"no side-effect entry for tool_call_id {tool_call_id!r}; record it with begin() before the call")
            current = SideEffectStatus(row.status)
            validate_transition(current, requested)
            values: dict[str, Any] = {"status": requested.value, "updated_at": now, "owner_worker_id": owner_worker_id, "lease_expires_at": (now + timedelta(seconds=lease_seconds)) if lease_seconds > 0 else None}
            if detail:
                values["detail"] = detail
            if result is not None:
                values["result_digest"] = result_digest(result)
            outcome = await session.execute(update(ToolSideEffectRow).where(and_(ToolSideEffectRow.tool_call_id == tool_call_id, ToolSideEffectRow.status.in_([status.value for status in expected]))).values(**values))
            if not outcome.rowcount:
                raise SideEffectTransitionLost(tool_call_id, current, requested)
            return _to_entry(await session.get(ToolSideEffectRow, tool_call_id))


def _to_entry(row: ToolSideEffectRow | None) -> SideEffectEntry:
    assert row is not None, "_to_entry called with no row"
    return SideEffectEntry(
        tool_call_id=row.tool_call_id,
        tool_name=row.tool_name,
        status=SideEffectStatus(row.status),
        level=SideEffectLevel(row.level or DEFAULT_SIDE_EFFECT_LEVEL.value),
        thread_id=row.thread_id or "",
        run_id=row.run_id or "",
        user_id=row.user_id or "",
        arguments_digest=row.arguments_digest,
        result_digest=row.result_digest,
        verdict=ReconciliationVerdict(row.verdict) if row.verdict else None,
        detail=row.detail or "",
        owner_worker_id=row.owner_worker_id,
        lease_expires_at=_as_utc(row.lease_expires_at).timestamp() if _as_utc(row.lease_expires_at) else None,
        attempt=row.attempt,
        created_at=_as_utc(row.created_at).timestamp() if _as_utc(row.created_at) else 0.0,
        updated_at=_as_utc(row.updated_at).timestamp() if _as_utc(row.updated_at) else 0.0,
    )


def _reconciliation_result(entry: SideEffectEntry, verdict: ReconciliationVerdict, *, escalated: bool) -> Any:
    from alpha.runtime.side_effects.ledger import ReconciliationResult

    return ReconciliationResult(entry=entry, verdict=verdict, escalated=escalated)


logger = logging.getLogger(__name__)

__all__ = ["SqlSideEffectLedger", "SideEffectTransitionLost"]
