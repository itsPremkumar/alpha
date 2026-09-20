"""ORM model for user feedback on runs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from alpha.persistence.base import Base

#: Feedback categories (DeepSeek-Harness-style feedback taxonomy): polarity
#: (``rating``) says *whether* the run was good, the category says *what* was
#: good or bad so feedback can drive targeted eval cases.
FEEDBACK_CATEGORIES: frozenset[str] = frozenset(
    {
        "correctness",
        "completeness",
        "grounding",
        "format",
        "latency",
        "other",
    }
)


def validate_category(category: str | None) -> str | None:
    """Validate an optional feedback category. ``None`` stays ``None`` (uncategorized)."""
    if category is None:
        return None
    if category not in FEEDBACK_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(FEEDBACK_CATEGORIES)}, got {category!r}")
    return category


class FeedbackRow(Base):
    __tablename__ = "feedback"

    __table_args__ = (UniqueConstraint("thread_id", "run_id", "user_id", name="uq_feedback_thread_run_user"),)

    feedback_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    message_id: Mapped[str | None] = mapped_column(String(64))
    # message_id is an optional RunEventStore event identifier —
    # allows feedback to target a specific message or the entire run

    rating: Mapped[int] = mapped_column(nullable=False)
    # +1 (thumbs-up) or -1 (thumbs-down)

    category: Mapped[str | None] = mapped_column(String(32))
    # Optional category from FEEDBACK_CATEGORIES — what the feedback is about.
    # NULL means uncategorized (all pre-category rows).

    comment: Mapped[str | None] = mapped_column(Text)
    # Optional text feedback from the user

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
