"""Full-text index over displayable run-event messages for session recall.

Revision ID: 0023_run_events_fts
Revises: 0022_scheduled_occurrence_seq
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023_run_events_fts"
down_revision: str | Sequence[str] | None = "0022_scheduled_occurrence_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alpha.persistence.migrations.run_events_fts_ddl import PG_INDEX_DDL, SQLITE_FTS_DDL

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for statement in SQLITE_FTS_DDL:
            op.get_bind().exec_driver_sql(statement)
    elif dialect == "postgresql":
        op.execute(PG_INDEX_DDL)
    else:  # pragma: no cover - only sqlite/postgres backends exist
        raise RuntimeError(f"run_events FTS migration has no branch for dialect {dialect!r}")


def downgrade() -> None:
    from alpha.persistence.migrations.run_events_fts_ddl import PG_INDEX_DROP, SQLITE_FTS_DROP

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for statement in SQLITE_FTS_DROP:
            op.get_bind().exec_driver_sql(statement)
    elif dialect == "postgresql":
        op.execute(PG_INDEX_DROP)
