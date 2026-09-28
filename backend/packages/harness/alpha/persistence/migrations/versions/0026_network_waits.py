"""Create the durable network-wait table.

Revision ID: 0026_network_waits
Revises: 0025_run_recovery_index

A *network wait* is one parked session: a run that needed connectivity, lost it,
and is waiting for the link to return. The row makes that fact survive a process
restart, so a task parked on a dead network is not lost between the moment it
parks and the moment a boot happens to observe a restored link.

Two indexes carry the behaviour, and both are declared here as well as in the ORM
because the empty-database bootstrap path runs ``create_all`` + ``stamp head``
and never executes this revision:

* ``ix_network_waits_due`` — the ``(state, next_attempt_at)`` lookup the recovery
  pass runs on every tick.
* ``uq_network_waits_thread_open`` — a partial unique index over
  ``state IN ('waiting', 'resuming')``. A thread has a single active run, so two
  open rows for one thread would mean two continuations racing for it. This is
  the durable sibling of ``uq_runs_thread_active``, extended to parked work.

The table is purely additive: no other table gains a column, no data is backfilled,
and an old binary that does not know it keeps working. ``downgrade`` only drops
what this revision created.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_network_waits"
down_revision: str | Sequence[str] | None = "0025_run_recovery_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "network_waits"


def _has_table() -> bool:
    return sa.inspect(op.get_bind()).has_table(TABLE)


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_from_run_id", sa.String(length=64), nullable=True),
        sa.Column("first_waited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_network_waits_thread_id", TABLE, ["thread_id"])
    op.create_index("ix_network_waits_run_id", TABLE, ["run_id"])
    op.create_index("ix_network_waits_user_id", TABLE, ["user_id"])
    op.create_index("ix_network_waits_state", TABLE, ["state"])
    op.create_index("ix_network_waits_next_attempt_at", TABLE, ["next_attempt_at"])
    op.create_index("ix_network_waits_due", TABLE, ["state", "next_attempt_at"])
    # A duplicate thread_id is legal for a *settled* row -- history accumulates
    # -- so the guard is partial, exactly like uq_runs_thread_active.
    op.create_index(
        "uq_network_waits_thread_open",
        TABLE,
        ["thread_id"],
        unique=True,
        sqlite_where=sa.text("state IN ('waiting', 'resuming')"),
        postgresql_where=sa.text("state IN ('waiting', 'resuming')"),
    )


def downgrade() -> None:
    if not _has_table():
        return
    op.drop_index("uq_network_waits_thread_open", table_name=TABLE)
    op.drop_index("ix_network_waits_due", table_name=TABLE)
    op.drop_index("ix_network_waits_next_attempt_at", table_name=TABLE)
    op.drop_index("ix_network_waits_state", table_name=TABLE)
    op.drop_index("ix_network_waits_user_id", table_name=TABLE)
    op.drop_index("ix_network_waits_run_id", table_name=TABLE)
    op.drop_index("ix_network_waits_thread_id", table_name=TABLE)
    op.drop_table(TABLE)
