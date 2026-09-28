"""Create the durable side-effect ledger table.

Revision ID: 0027_side_effect_ledger
Revises: 0026_network_waits

One row per external effect the agent attempted. The point of the table is the
``status`` column: a worker that dies mid-call leaves its row ``in_flight`` with
an expired lease, and a reaper converts that into ``unknown`` -- the state a
reconciler can find, list, and work off. Without a row the gap is invisible.

Four indexes carry the behaviour, and all four are declared in the ORM
``__table_args__`` as well as here, because the empty-database bootstrap path
runs ``create_all`` + ``stamp head`` and never executes this revision:

* ``ix_tool_side_effects_unknown`` -- the reconciliation queue, exactly the
  ``(status, updated_at)`` lookup an operator runs to find the effects that still
  owe an answer.
* ``ix_tool_side_effects_lease`` -- the reclaim scan, over in-flight rows whose
  owner stopped reporting.
* ``ix_tool_side_effects_thread_status`` / ``ix_tool_side_effects_run_status`` --
  per-scope audits in one index range scan rather than a full table filter.

The primary key is the provider's ``tool_call_id``, which is what makes ``begin``
idempotent across processes: a duplicate announce collides here instead of
creating a second row for one call.

Purely additive: no other table gains a column, no data is backfilled, and an old
binary that does not know the table keeps working. Digests are stored rather than
arguments or results, because a ledger row is durable, lands in support bundles,
and outlives the thread.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_side_effect_ledger"
down_revision: str | Sequence[str] | None = "0026_network_waits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tool_side_effects"


def _has_table() -> bool:
    return sa.inspect(op.get_bind()).has_table(TABLE)


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="moderate"),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("arguments_digest", sa.String(length=64), nullable=True),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
        sa.Column("verdict", sa.String(length=24), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("owner_worker_id", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tool_call_id"),
    )
    op.create_index("ix_tool_side_effects_tool_name", TABLE, ["tool_name"])
    op.create_index("ix_tool_side_effects_status", TABLE, ["status"])
    op.create_index("ix_tool_side_effects_level", TABLE, ["level"])
    op.create_index("ix_tool_side_effects_thread_id", TABLE, ["thread_id"])
    op.create_index("ix_tool_side_effects_run_id", TABLE, ["run_id"])
    op.create_index("ix_tool_side_effects_user_id", TABLE, ["user_id"])
    op.create_index("ix_tool_side_effects_unknown", TABLE, ["status", "updated_at"])
    op.create_index("ix_tool_side_effects_lease", TABLE, ["status", "lease_expires_at"])
    op.create_index("ix_tool_side_effects_thread_status", TABLE, ["thread_id", "status"])
    op.create_index("ix_tool_side_effects_run_status", TABLE, ["run_id", "status"])


def downgrade() -> None:
    if not _has_table():
        return
    for name in (
        "ix_tool_side_effects_run_status",
        "ix_tool_side_effects_thread_status",
        "ix_tool_side_effects_lease",
        "ix_tool_side_effects_unknown",
        "ix_tool_side_effects_user_id",
        "ix_tool_side_effects_run_id",
        "ix_tool_side_effects_thread_id",
        "ix_tool_side_effects_level",
        "ix_tool_side_effects_status",
        "ix_tool_side_effects_tool_name",
    ):
        op.drop_index(name, table_name=TABLE)
    op.drop_table(TABLE)
