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

Exports are installed lazily (:pep:`562`) so importing any leaf of this
package — e.g. the typed-memory subsystems under ``alpha.memory.<type>/`` — does
not drag the whole cognitive engine (and, through it, the shared config layer)
into the import graph. That keeps two properties true: a leaf subsystem config
can be imported by the shared config schema without a circular import, and an
agent that only needs one memory surface does not pay for the others' import
cost. Submodule imports (``from alpha.memory import dreaming``,
``from alpha.memory.cognitive import get_cognitive_memory_system``) are
unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "BeliefStatus": "cognitive",
    "CognitiveMemorySystem": "cognitive",
    "CognitiveTier": "cognitive",
    "ConsolidationReport": "cognitive",
    "HybridRecallQuery": "cognitive",
    "KibitzerMemoryBank": "kibitzer",
    "KibitzerObserver": "kibitzer",
    "ScoredMemoryItem": "cognitive",
    "TraceOutcome": "cognitive",
    "get_cognitive_memory_system": "cognitive",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed lazily
    # above, so this block exists for type checkers and IDEs only.
    from alpha.memory.cognitive import BeliefStatus as BeliefStatus
    from alpha.memory.cognitive import CognitiveMemorySystem as CognitiveMemorySystem
    from alpha.memory.cognitive import CognitiveTier as CognitiveTier
    from alpha.memory.cognitive import ConsolidationReport as ConsolidationReport
    from alpha.memory.cognitive import HybridRecallQuery as HybridRecallQuery
    from alpha.memory.cognitive import ScoredMemoryItem as ScoredMemoryItem
    from alpha.memory.cognitive import TraceOutcome as TraceOutcome
    from alpha.memory.cognitive import (
        get_cognitive_memory_system as get_cognitive_memory_system,
    )
    from alpha.memory.kibitzer import KibitzerMemoryBank as KibitzerMemoryBank
    from alpha.memory.kibitzer import KibitzerObserver as KibitzerObserver

install_lazy_exports(__name__, _EXPORTS)
