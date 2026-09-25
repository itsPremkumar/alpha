"""L1 retention: age sweep + record-count cap.

Design provenance: the age-based retention sweep (delete records older than
``retentionDays``) and the "keep at least a floor of records" caution follow
``tencentdb-agent-memory`` ``MemoryCore/src/utils/memory-cleaner.ts`` (MIT).
Alpha's cleaner runs inline after each pipeline run (no background timer) and
adds a count cap that drops lowest-priority-oldest first — the same ordering
rule the source's priority bands imply.

Deletion order (deterministic, documented):

1. Expired records (TTL passed) — always removed first.
2. Records older than ``max_age_days``.
3. If still above ``max_records``, drop by (lowest priority, oldest
   ``updated_at``, then id) until at or below the cap.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .models import MemoryRecord
from .store import L1RecordStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetentionReport:
    """Counts of what one retention sweep removed."""

    expired: int = 0
    aged_out: int = 0
    capped: int = 0
    removed_ids: list[str] = field(default_factory=list)

    @property
    def total_removed(self) -> int:
        return self.expired + self.aged_out + self.capped

    def to_dict(self) -> dict[str, object]:
        return {
            "expired": self.expired,
            "aged_out": self.aged_out,
            "capped": self.capped,
            "total_removed": self.total_removed,
        }


def _is_pinned(record: MemoryRecord) -> bool:
    """Pinned records (priority -1 strict orders) are never age-evicted."""
    return record.priority < 0 or bool(record.metadata.get("pinned"))


def sweep(
    store: L1RecordStore,
    *,
    user_id: str | None = None,
    agent_name: str | None = None,
    max_records: int,
    max_age_days: int,
    now: float | None = None,
) -> RetentionReport:
    """Run one retention sweep for a scope; returns what was removed."""
    ts = time.time() if now is None else now
    report = RetentionReport()
    records = store.list_records(user_id, agent_name)
    if not records:
        return report

    doomed: dict[str, str] = {}  # id -> reason

    # 1. TTL expiry.
    for record in records:
        if record.expires_at is not None and record.expires_at <= ts:
            doomed[record.id] = "expired"

    # 2. Age sweep (pinned records exempt).
    if max_age_days and max_age_days > 0:
        cutoff = ts - max_age_days * 86400.0
        for record in records:
            if record.id in doomed or _is_pinned(record):
                continue
            if record.created_at and record.created_at < cutoff:
                doomed[record.id] = "aged_out"

    # 3. Count cap: drop lowest-priority oldest until within budget.
    survivors = [r for r in records if r.id not in doomed]
    if max_records and max_records > 0 and len(survivors) > max_records:
        ordered = sorted(
            survivors,
            key=lambda r: (r.priority, r.updated_at or r.created_at, r.id),
        )
        excess = len(survivors) - max_records
        for record in ordered[:excess]:
            if _is_pinned(record):
                continue
            doomed[record.id] = "capped"

    if not doomed:
        return report

    for record in records:
        if record.id in doomed:
            reason = doomed[record.id]
            if reason == "expired":
                report.expired += 1
            elif reason == "aged_out":
                report.aged_out += 1
            else:
                report.capped += 1
            report.removed_ids.append(record.id)

    removed = store.delete_records(
        report.removed_ids, user_id=user_id, agent_name=agent_name
    )
    if removed:
        logger.info(
            "L1 retention: removed %d record(s) for user=%s agent=%s (%s)",
            removed,
            user_id,
            agent_name,
            report.to_dict(),
        )
    return report


__all__ = ["RetentionReport", "sweep"]
