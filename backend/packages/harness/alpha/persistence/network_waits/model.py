"""ORM model for durable network waits.

What a row is
-------------
One **parked session**: a run that needed connectivity, lost it, was terminalized
with a recoverable stop reason, and is waiting for the link to come back. The row
exists so that fact survives a process restart. Without it, a task parked on a
dead network is re-derived from live signals on every boot and is effectively
lost between the moment it parks and the moment a boot happens to observe a
restored link.

What a row is **not**
---------------------
It is not a queue of work and it does not execute anything. It records *where the
work got to* so the existing recovery owner
(:class:`app.gateway.run_recovery.SafeRunRecoveryService`) can decide, through
the normal ``start_run`` path, whether that work may continue. Park and resume
remain that service's decision, exactly as they are for a crash.

The ``attempt``/``next_attempt_at`` pair is the bounded-retry surface, and
``state`` is a closed vocabulary rather than a free string, so "is this still
waiting or has it been given up on?" is a database question.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from alpha.persistence.base import Base

#: Terminal states: a parked session that reached one of these is done, and a
#: recovery pass must not re-admit it. ``COMPLETED`` means the resumed run
#: finished; ``GAVE_UP`` means the attempt budget ran out and a human is needed.
TERMINAL_NETWORK_WAIT_STATES: frozenset[str] = frozenset({"resumed", "completed", "gave_up"})

#: States that still owe an attempt.
OPEN_NETWORK_WAIT_STATES: frozenset[str] = frozenset({"waiting", "resuming"})


class NetworkWaitRow(Base):
    __tablename__ = "network_waits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    #: The run that parked. NULL for a park recorded before a run row existed.
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    #: One open wait per thread: a thread has one active run, so a second open
    #: row for the same thread is a duplicate, not a second task.
    state: Mapped[str] = mapped_column(String(16), default="waiting", index=True)
    #: Why the session parked. ``connectivity_lost`` today; the column exists so
    #: a provider outage recorded the same way does not need a new table.
    reason: Mapped[str] = mapped_column(String(32), default="connectivity_lost")
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Earliest time the next resume attempt may run. Backoff is durable, so a
    #: restart does not reset it and turn a backoff into a hot loop.
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: Human-readable, already-redacted. Never an exception repr.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Lease for the pass that is about to resume this wait, so two gateway
    #: instances cannot both launch a continuation for the same wait row.
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Resume lineage, mirroring ``runs.metadata`` on the recovery path.
    resumed_from_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: When this wait stopped waiting: set by ``mark_terminal`` for ``resumed``,
    #: ``completed`` and ``gave_up`` alike.
    #:
    #: Deliberately NOT ``updated_at``. That column is also moved by
    #: ``release()`` — a failed resume attempt rewrites the backoff — so reading
    #: the connection time off it would report the moment a retry was *scheduled*
    #: as the moment the link came back. An open row keeps ``None``, and "still
    #: waiting" is therefore a state, not a zero timestamp.
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_waited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        # The due-wait scan: state + next_attempt_at, exactly what a recovery
        # pass reads on every tick. Declared in ORM __table_args__ as well as
        # the migration because the empty-DB bootstrap path runs create_all +
        # stamp head and never executes the migration.
        Index("ix_network_waits_due", "state", "next_attempt_at"),
        # At most one open wait per thread. A thread has a single active run, so
        # two open rows would mean two continuations racing for one thread --
        # the durable equivalent of ``uq_runs_thread_active`` for parked work.
        Index(
            "uq_network_waits_thread_open",
            "thread_id",
            unique=True,
            sqlite_where=text("state IN ('waiting', 'resuming')"),
            postgresql_where=text("state IN ('waiting', 'resuming')"),
        ),
    )
