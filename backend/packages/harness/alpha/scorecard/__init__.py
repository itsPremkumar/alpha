"""Capability scorecard: score an Alpha install against the frontier taxonomy.

- :data:`~alpha.scorecard.taxonomy.TAXONOMY` — the capability checklist
- :func:`~alpha.scorecard.scanner.scan` — probe an install and produce a report
"""

from __future__ import annotations

from alpha.scorecard.scanner import CapabilityStatus, ScorecardReport, scan
from alpha.scorecard.taxonomy import CATEGORIES, TAXONOMY, Capability

__all__ = [
    "CATEGORIES",
    "TAXONOMY",
    "Capability",
    "CapabilityStatus",
    "ScorecardReport",
    "scan",
]
