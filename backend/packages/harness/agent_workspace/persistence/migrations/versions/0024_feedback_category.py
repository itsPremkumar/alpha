"""Add optional feedback category for targeted eval exports.

Revision ID: 0024_feedback_category
Revises: 0023_run_events_fts
"""

from __future__ import annotations

import sqlalchemy as sa

revision = "0024_feedback_category"
down_revision = "0023_run_events_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from agent_workspace.persistence.migrations._helpers import safe_add_column

    safe_add_column("feedback", sa.Column("category", sa.String(32), nullable=True))


def downgrade() -> None:
    from agent_workspace.persistence.migrations._helpers import safe_drop_column

    safe_drop_column("feedback", "category")
