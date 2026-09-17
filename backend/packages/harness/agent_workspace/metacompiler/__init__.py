"""Agent Meta-Compiler and Self-Replication package."""

from __future__ import annotations

from .benchmark import MetaBenchmarkHarness
from .compiler import AgentMetaCompiler
from .hotswap import AgentHotSwapCoordinator
from .lineage import MetaLineageStore, get_meta_compiler_lineage
from .models import (
    AgentBlueprint,
    BenchmarkScorecard,
    HotSwapOutcome,
    MemoryLayout,
    ReasoningStrategy,
)

__all__ = [
    "AgentBlueprint",
    "BenchmarkScorecard",
    "HotSwapOutcome",
    "MemoryLayout",
    "ReasoningStrategy",
    "AgentMetaCompiler",
    "MetaBenchmarkHarness",
    "AgentHotSwapCoordinator",
    "MetaLineageStore",
    "get_meta_compiler_lineage",
]
