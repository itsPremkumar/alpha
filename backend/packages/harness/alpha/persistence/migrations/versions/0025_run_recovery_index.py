"""Index terminal run recovery candidates.

Revision ID: 0025_run_recovery_index
Revises: 0024_feedback_category
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_run_recovery_index"
down_revision: str | Sequence[str] | None = "0024_feedback_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(name: str, table: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table in inspector.get_table_names() and any(index.get("name") == name for index in inspector.get_indexes(table))


def upgrade() -> None:
    if not _has_index("ix_runs_status_stop_reason", "runs"):
        op.create_index("ix_runs_status_stop_reason", "runs", ["status", "stop_reason"], unique=False)


def downgrade() -> None:
    if _has_index("ix_runs_status_stop_reason", "runs"):
        op.drop_index("ix_runs_status_stop_reason", table_name="runs")
