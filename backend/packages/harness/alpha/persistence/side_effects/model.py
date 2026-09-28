"""ORM model for the durable side-effect ledger.

One row per external effect the agent attempted. The point of the table is the
``status`` column: a worker that dies mid-call leaves its row ``in_flight`` with
an expired lease, and a reaper converts that into ``unknown`` -- the state a
reconciler can find, list, and work off. Without a row, the gap is invisible.

The transition vocabulary and its rules live in
:mod:`alpha.runtime.side_effects.statuses`; this module only persists them. The
database's own backstop is ``uq_tool_side_effects_call`` (one row per
``tool_call_id``), which makes ``begin`` idempotent across processes rather than
merely across a single ledger instance.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alpha.persistence.base import Base

#: Mirrors ``alpha.runtime.side_effects.statuses.SideEffectStatus`` as stored
#: strings. Declared here so a query can filter without importing the harness
#: package, and asserted against it by a test so the two cannot drift.
LEDGER_STATUSES: frozenset[str] = frozenset({"pending", "in_flight", "completed", "failed", "unknown", "reconciled"})

#: The default risk when a caller does not classify the tool. Mirrors the
#: harness default so the stored column and the in-memory reference agree.
DEFAULT_LEVEL: str = "moderate"

#: Statuses that are neither settled nor finished: something still owes an answer.
OPEN_LEDGER_STATUSES: frozenset[str] = frozenset({"unknown", "reconciled"})

#: Statuses a live worker owns. A row in one of these with an expired lease is
#: what ``reclaim_expired`` converts into ``unknown``.
RECLAIMABLE_LEDGER_STATUSES: frozenset[str] = frozenset({"pending", "in_flight"})


class ToolSideEffectRow(Base):
    __tablename__ = "tool_side_effects"

    #: The provider's ``tool_call_id``. This is the ledger's primary key, so a
    #: duplicate ``begin`` from any process collides here rather than creating a
    #: second row for one call.
    tool_call_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(128), index=True)
    # ``server_default`` is declared on both defaulted columns, not only the
    # Python-side ``default``: the empty-database bootstrap path runs
    # ``create_all`` while a versioned database takes the alembic chain, and
    # ``test_create_all_and_alembic_upgrade_produce_same_schema`` fails closed
    # if the two paths disagree about a default. ``NetworkWaitRow`` gets away
    # with a bare ``default`` only because its revision omits ``server_default``
    # too; declaring one side without the other is what this note prevents.
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    #: Recorded with the entry rather than re-derived: a tool's risk can change
    #: between releases, and re-deriving would silently reclassify history.
    level: Mapped[str] = mapped_column(String(16), default=DEFAULT_LEVEL, server_default=DEFAULT_LEVEL, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    #: SHA-256 digests only. A ledger row is durable, lands in support bundles,
    #: and outlives the thread, so it must not become where arguments or results
    #: accumulate -- they routinely carry a prompt, a file body, or a token.
    arguments_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``confirmed_success`` / ``confirmed_failure`` / ``undetermined``. Both
    #: settled verdicts land on status ``reconciled``, so the verdict is the only
    #: record of *which* outcome was established.
    verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: Human-readable, already-redacted. Never an exception repr.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        # The reconciliation queue: exactly the query a reconciler or an operator
        # runs to find the effects that still owe an answer.
        Index("ix_tool_side_effects_unknown", "status", "updated_at"),
        # The reclaim scan: in-flight rows whose owner stopped reporting.
        Index("ix_tool_side_effects_lease", "status", "lease_expires_at"),
        # Scope lookups, so a per-thread or per-run audit is one index range scan
        # rather than a full table filter.
        Index("ix_tool_side_effects_thread_status", "thread_id", "status"),
        Index("ix_tool_side_effects_run_status", "run_id", "status"),
    )
