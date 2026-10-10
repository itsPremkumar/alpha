"""Repository for durable network waits.

The three concurrency properties this must hold
----------------------------------------------
1. **One open wait per thread.** Enforced twice on purpose: the partial unique
   index ``uq_network_waits_thread_open`` is the backstop for direct callers and
   legacy interleavings, and :meth:`NetworkWaitRepository.park` catches the
   constraint violation and returns the existing row rather than raising a
   driver error at a caller that was only trying to record a fact.
2. **A resume is claimed, not assumed.** ``claim_due`` is a conditional update:
   the row is only taken if it is still ``waiting`` and its backoff has elapsed.
   A stale scan therefore cannot double-launch a continuation.
3. **A failed resume is released, not lost.** ``release`` returns the row to
   ``waiting`` with the next backoff already written, so a crash between claim
   and launch cannot strand the wait in ``resuming`` forever.

Timestamps are stored timezone-aware and compared in UTC, matching the rest of
the persistence layer. ``next_attempt_at`` is the durable backoff, which is why
a restart cannot turn a backoff into a hot loop.
"""

from __future__ import annotations

import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alpha.persistence.network_waits.model import NetworkWaitRow

#: How long a claim lease is held before another pass may take the row. Long
#: enough for a launch call to complete, short enough that a crashed pass
#: recovers within a single backoff window.
DEFAULT_CLAIM_LEASE_SECONDS = 30.0

_OPEN_STATES = ("waiting", "resuming")


class NetworkWaitRepository:
    """CRUD plus the two conditional transitions a recovery pass needs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # -- writing ------------------------------------------------------------

    async def park(
        self,
        *,
        thread_id: str,
        run_id: str | None = None,
        user_id: str | None = None,
        reason: str = "connectivity_lost",
        next_attempt_in_seconds: float = 0.0,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        """Record that a session is parked, or return the wait it already has.

        Idempotent per thread. A second park for a thread that is already
        waiting updates the *newer* evidence (the run id, the error, the next
        attempt time) rather than creating a competing row, because two open
        rows for one thread would mean two continuations racing for it.
        """
        now = datetime.now(UTC)
        due = now + timedelta(seconds=max(0.0, float(next_attempt_in_seconds)))
        async with self._sf() as session, session.begin():
            existing = await session.scalar(select(NetworkWaitRow).where(NetworkWaitRow.thread_id == thread_id, NetworkWaitRow.state.in_(_OPEN_STATES)))
            if existing is not None:
                existing.run_id = run_id or existing.run_id
                existing.user_id = user_id or existing.user_id
                existing.reason = reason
                existing.next_attempt_at = due
                if last_error:
                    existing.last_error = last_error
                existing.lease_owner = None
                existing.lease_expires_at = None
                existing.updated_at = now
                return _to_dict(existing)
            row = NetworkWaitRow(
                id=_new_id(),
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                state="waiting",
                reason=reason,
                attempt=0,
                next_attempt_at=due,
                last_error=last_error,
                first_waited_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                # The partial unique index is the real guard; a concurrent park
                # won the race, so fall back to reading its row.
                await session.rollback()
                return await self.get_open_for_thread(thread_id) or {"thread_id": thread_id, "state": "waiting"}
            return _to_dict(row)

    async def claim_due(
        self,
        *,
        limit: int = 10,
        lease_seconds: float = DEFAULT_CLAIM_LEASE_SECONDS,
        owner: str | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically take up to *limit* waits whose backoff has elapsed.

        The claim is a conditional ``UPDATE ... WHERE state='waiting' AND
        next_attempt_at <= now`` per candidate, so two concurrent passes cannot
        both take the same row: the second finds ``state`` already ``resuming``.
        """
        moment = datetime.now(UTC)
        worker = owner or _default_owner()
        async with self._sf() as session, session.begin():
            candidates = (await session.scalars(select(NetworkWaitRow.id).where(NetworkWaitRow.state == "waiting", NetworkWaitRow.next_attempt_at <= moment).order_by(NetworkWaitRow.next_attempt_at).limit(max(1, limit)))).all()
            claimed: list[dict[str, Any]] = []
            for row_id in candidates:
                result = await session.execute(
                    update(NetworkWaitRow)
                    .where(and_(NetworkWaitRow.id == row_id, NetworkWaitRow.state == "waiting"))
                    .values(state="resuming", attempt=NetworkWaitRow.attempt + 1, lease_owner=worker, lease_expires_at=moment + timedelta(seconds=lease_seconds), updated_at=moment)
                )
                if result.rowcount:
                    row = await session.get(NetworkWaitRow, row_id)
                    if row is not None:
                        claimed.append(_to_dict(row))
            return claimed

    async def release(
        self,
        wait_id: str,
        *,
        next_attempt_in_seconds: float,
        last_error: str | None = None,
        attempt: int | None = None,
    ) -> dict[str, Any] | None:
        """Return a claimed wait to ``waiting`` with its next backoff written.

        Called when a resume attempt did not happen or did not succeed. The
        attempt count is preserved (or overwritten by a caller that knows the
        real one) because it is the bound that eventually gives up.
        """
        now = datetime.now(UTC)
        due = now + timedelta(seconds=max(0.0, float(next_attempt_in_seconds)))
        async with self._sf() as session, session.begin():
            values: dict[str, Any] = {"state": "waiting", "next_attempt_at": due, "lease_owner": None, "lease_expires_at": None, "updated_at": now}
            if last_error is not None:
                values["last_error"] = last_error
            if attempt is not None:
                values["attempt"] = attempt
            await session.execute(update(NetworkWaitRow).where(and_(NetworkWaitRow.id == wait_id, NetworkWaitRow.state == "resuming")).values(**values))
            row = await session.get(NetworkWaitRow, wait_id)
            return _to_dict(row) if row is not None else None

    async def mark_terminal(self, wait_id: str, *, state: str, resumed_from_run_id: str | None = None, last_error: str | None = None) -> dict[str, Any] | None:
        """Settle a wait as ``resumed``, ``completed``, or ``gave_up``.

        ``terminal_at`` is stamped here and nowhere else, so the moment a wait
        stopped waiting is a fact rather than something a reader infers from
        ``updated_at`` — which ``release()`` also moves, and which would
        therefore report a scheduled retry as a recovered link.
        """
        if state not in ("resumed", "completed", "gave_up"):
            raise ValueError(f"{state!r} is not a terminal network-wait state")
        now = datetime.now(UTC)
        async with self._sf() as session, session.begin():
            values: dict[str, Any] = {"state": state, "lease_owner": None, "lease_expires_at": None, "updated_at": now, "terminal_at": now}
            if resumed_from_run_id is not None:
                values["resumed_from_run_id"] = resumed_from_run_id
            if last_error is not None:
                values["last_error"] = last_error
            await session.execute(update(NetworkWaitRow).where(NetworkWaitRow.id == wait_id).values(**values))
            row = await session.get(NetworkWaitRow, wait_id)
            return _to_dict(row) if row is not None else None

    async def reclaim_expired_leases(self, *, next_attempt_in_seconds: float = 0.0) -> int:
        """Return ``resuming`` rows whose claim lease expired to ``waiting``.

        A pass that claimed a row and then died leaves it in ``resuming`` with
        no way back. This is the reaper that prevents a wait being stranded in a
        non-open-looking state after a crash.
        """
        moment = datetime.now(UTC)
        due = moment + timedelta(seconds=max(0.0, float(next_attempt_in_seconds)))
        async with self._sf() as session, session.begin():
            result = await session.execute(
                update(NetworkWaitRow)
                .where(and_(NetworkWaitRow.state == "resuming", NetworkWaitRow.lease_expires_at.is_not(None), NetworkWaitRow.lease_expires_at < moment))
                .values(state="waiting", next_attempt_at=due, lease_owner=None, lease_expires_at=None, updated_at=moment)
            )
            return int(result.rowcount or 0)

    # -- reading ------------------------------------------------------------

    async def get(self, wait_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(NetworkWaitRow, wait_id)
            return _to_dict(row) if row is not None else None

    async def get_open_for_thread(self, thread_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.scalar(select(NetworkWaitRow).where(NetworkWaitRow.thread_id == thread_id, NetworkWaitRow.state.in_(_OPEN_STATES)))
            return _to_dict(row) if row is not None else None

    async def list_open(self, *, user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        statement = select(NetworkWaitRow).where(NetworkWaitRow.state.in_(_OPEN_STATES)).order_by(NetworkWaitRow.next_attempt_at).limit(max(1, limit))
        if user_id is not None:
            statement = statement.where(NetworkWaitRow.user_id == user_id)
        async with self._sf() as session:
            return [_to_dict(row) for row in (await session.scalars(statement)).all()]

    async def list_for_thread(self, thread_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Every wait this thread has ever parked on, newest first.

        Settled rows are included on purpose: an outage that ended an hour ago
        is still part of the session's story, and a UI that read only open rows
        would show a thread as never having been parked the moment it resumed.

        Owner-scoped by the caller, not here — the repository is the harness
        layer and the route above it owns the ``user_id`` decision.
        """
        statement = select(NetworkWaitRow).where(NetworkWaitRow.thread_id == thread_id).order_by(NetworkWaitRow.first_waited_at.desc()).limit(max(1, limit))
        async with self._sf() as session:
            return [_to_dict(row) for row in (await session.scalars(statement)).all()]

    async def count_by_state(self) -> dict[str, int]:
        async with self._sf() as session:
            rows = await session.execute(select(NetworkWaitRow.state, func.count()).group_by(NetworkWaitRow.state))
            return {state: int(count) for state, count in rows.all()}


def _to_dict(row: NetworkWaitRow) -> dict[str, Any]:
    return row.to_dict()


def _new_id() -> str:
    return uuid.uuid4().hex


def _default_owner() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex}"
