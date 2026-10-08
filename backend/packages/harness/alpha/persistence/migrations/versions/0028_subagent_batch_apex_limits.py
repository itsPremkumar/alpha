"""Add persisted APEX concurrency limits to durable subagent batches.

Revision ID: 0028_subagent_batch_apex_limits
Revises: 0027_side_effect_ledger
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_subagent_batch_apex_limits"
down_revision: str | Sequence[str] | None = "0027_side_effect_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alpha.persistence.migrations._helpers import safe_add_column

    safe_add_column("subagent_batches", sa.Column("apex_session_id", sa.String(length=64), nullable=True))
    safe_add_column("subagent_batches", sa.Column("apex_concurrency_limit", sa.Integer(), nullable=True))
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("subagent_batches") and "ix_subagent_batches_apex_session_id" not in {index["name"] for index in inspector.get_indexes("subagent_batches")}:
        op.create_index("ix_subagent_batches_apex_session_id", "subagent_batches", ["apex_session_id"])
    if not sa.inspect(op.get_bind()).has_table("subagent_batch_session_locks"):
        op.create_table(
            "subagent_batch_session_locks",
            sa.Column("apex_session_id", sa.String(length=64), nullable=False),
            sa.Column("lock_version", sa.Integer(), server_default="0", nullable=False),
            sa.PrimaryKeyConstraint("apex_session_id"),
        )


def downgrade() -> None:
    from alpha.persistence.migrations._helpers import safe_drop_column

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("subagent_batch_session_locks"):
        op.drop_table("subagent_batch_session_locks")
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("subagent_batches") and "ix_subagent_batches_apex_session_id" in {index["name"] for index in inspector.get_indexes("subagent_batches")}:
        op.drop_index("ix_subagent_batches_apex_session_id", table_name="subagent_batches")
    safe_drop_column("subagent_batches", "apex_concurrency_limit")
    safe_drop_column("subagent_batches", "apex_session_id")
