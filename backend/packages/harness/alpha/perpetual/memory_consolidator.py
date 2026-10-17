"""Perpetual Memory Consolidator: Background worker that converts execution traces into semantic graph knowledge."""

from __future__ import annotations

import logging

from alpha.perpetual.models import MemoryConsolidationReport

logger = logging.getLogger(__name__)


class PerpetualMemoryConsolidator:
    """Consolidates episodic trajectories into semantic facts and prunes dead memory context."""

    def __init__(self, project_id: str = "default"):
        self.project_id = project_id
        self._reports: list[MemoryConsolidationReport] = []

    def consolidate(self, trace_count: int = 5) -> MemoryConsolidationReport:
        """Report the consolidation outcome.

        No extractor is wired yet, so this returns a real zero-count report
        marked ``unavailable`` rather than numbers derived from ``trace_count``.
        The previous implementation computed ``facts = trace_count // 2`` and
        friends — arithmetic dressed as extraction, which a caller could not
        distinguish from measured output.  Every count below is therefore a
        genuine zero.
        """
        reason = "No memory-consolidation extractor is wired: no traces were read and no facts were written. Counts are real zeros, not estimates."
        report = MemoryConsolidationReport(
            traces_analyzed=0,
            facts_extracted=0,
            skills_indexed=0,
            pruned_tokens=0,
            summary=reason,
            status="unavailable",
            unavailable_reason=reason,
        )
        self._reports.append(report)
        logger.info("Memory consolidation unavailable: %s", reason)
        return report

    def get_latest_report(self) -> MemoryConsolidationReport | None:
        return self._reports[-1] if self._reports else None

    def get_reports(self) -> list[MemoryConsolidationReport]:
        return list(self._reports)
