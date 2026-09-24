"""Cognitive memory engines (wave P4 boundary documentation).

Alpha has TWO memory trees and this one is the COGNITIVE side:

* ``alpha.memory`` (this package) — process-level cognitive engines:
  ``cognitive`` (beliefs, traces, consolidation, hybrid recall), ``kibitzer``
  (resident memory nudges), ``session_search`` (past-session FTS/BM25 search),
  ``active_memory``, ``dreaming``, ``wiki_vault``. These answer "what does the
  runtime know/believe, and what did it learn from past sessions".
* ``alpha.agents.memory`` — the AGENT-FACING pluggable backend contract
  (:class:`MemoryManager` + backends such as DeerMem and OpenViking), which
  answers "where are this user's memories stored and how are they injected".

Boundary rule (do not collapse the trees): this package must not depend on
``alpha.agents.memory`` backends, and the agent-facing manager must not reach
into these engines directly. Unifying the two views is a separate, reviewed
decision — never a side effect of an import.
"""
from alpha.memory.cognitive import (
    BeliefStatus,
    CognitiveMemorySystem,
    CognitiveTier,
    ConsolidationReport,
    HybridRecallQuery,
    ScoredMemoryItem,
    TraceOutcome,
    get_cognitive_memory_system,
)
from alpha.memory.kibitzer import (
    KibitzerMemoryBank,
    KibitzerObserver,
)

__all__ = [
    "KibitzerMemoryBank",
    "KibitzerObserver",
    "CognitiveMemorySystem",
    "CognitiveTier",
    "BeliefStatus",
    "TraceOutcome",
    "HybridRecallQuery",
    "ScoredMemoryItem",
    "ConsolidationReport",
    "get_cognitive_memory_system",
]
