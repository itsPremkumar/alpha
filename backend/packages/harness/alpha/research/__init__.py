"""Research and Deep Investigation capabilities for Alpha."""

from __future__ import annotations

from .engine import (
    CONFLICT,
    JUXTAPOSITION,
    MAX_RESEARCH_DEADLINE_SECONDS,
    MAX_RESEARCH_DEPTH,
    MAX_RESEARCH_SOURCES,
    MAX_SOURCE_CHARS,
    RETRIEVAL_PAGE,
    RETRIEVAL_SNIPPET_ONLY,
    ContradictionFinding,
    DeepResearchEngine,
    DeepResearchReport,
    EvidenceSource,
    ResearchFailure,
    ResearchGap,
)
from .five_pass import (
    CompiledSearchLane,
    FivePassSearchCompiler,
    FivePassSearchPlan,
    SearchPassType,
)

__all__ = [
    "CONFLICT",
    "CompiledSearchLane",
    "ContradictionFinding",
    "DeepResearchEngine",
    "DeepResearchReport",
    "EvidenceSource",
    "FivePassSearchCompiler",
    "FivePassSearchPlan",
    "JUXTAPOSITION",
    "MAX_RESEARCH_DEADLINE_SECONDS",
    "MAX_RESEARCH_DEPTH",
    "MAX_RESEARCH_SOURCES",
    "MAX_SOURCE_CHARS",
    "RETRIEVAL_PAGE",
    "RETRIEVAL_SNIPPET_ONLY",
    "ResearchFailure",
    "ResearchGap",
    "SearchPassType",
]
