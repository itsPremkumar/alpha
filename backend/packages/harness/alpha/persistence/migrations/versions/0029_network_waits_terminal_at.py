"""Stamp when a durable network wait stopped waiting.

Revision ID: 0029_network_waits_terminal_at
Revises: 0028_subagent_batch_apex_limits

``network_waits.terminal_at`` is the *connection* half of the outage story:
``first_waited_at`` says when the link died, and this column says when the wait
ended. Without it a UI rendering "internet was lost at 14:02, restored at …" has
to read the connection time off ``updated_at``, which ``release()`` also moves
every time a failed resume attempt writes its next backoff — so a retry that
was merely *scheduled* would be presented as the moment the link came back.

Purely additive and nullable, exactly like the revisions the rollback-floor
binary already tolerates: an open wait has not stopped waiting, so it keeps
``NULL``, and ``"still waiting"`` is a state rather than a zero timestamp.
No backfill — a row settled before this revision existed has no measured
connection time, and inventing one from ``updated_at`` would be the wrong
number this column exists to stop.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from alpha.persistence.migrations._helpers import safe_add_column

revision: str = "0029_network_waits_terminal_at"
down_revision: str | Sequence[str] | None = "0028_subagent_batch_apex_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "network_waits"


def upgrade() -> None:
    safe_add_column(TABLE, sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "terminal_at")
