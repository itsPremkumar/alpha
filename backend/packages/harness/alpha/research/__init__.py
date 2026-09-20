"""Research and Deep Investigation capabilities for Alpha."""

from __future__ import annotations

from .engine import (
    ContradictionFinding,
    DeepResearchEngine,
    DeepResearchReport,
    EvidenceSource,
    ResearchGap,
)
from .five_pass import (
    CompiledSearchLane,
    FivePassSearchCompiler,
    FivePassSearchPlan,
    SearchPassType,
)

__all__ = [
    "CompiledSearchLane",
    "ContradictionFinding",
    "DeepResearchEngine",
    "DeepResearchReport",
    "EvidenceSource",
    "FivePassSearchCompiler",
    "FivePassSearchPlan",
    "ResearchGap",
    "SearchPassType",
]
