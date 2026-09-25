"""L1 typed working memory: scene segmentation + typed extraction + dedup.

This subpackage implements Alpha's L1 (working) memory layer: a debounced
capture -> extraction -> conflict-detection (dedup) -> retention -> persona
pipeline with per-run provenance and per-user quotas, plus the hybrid-recall
seam used at injection time.

Design provenance (English adaptation of Chinese prompts; MIT source):
TencentDB-Agent-Memory ``MemoryCore/src/core/`` — see
``docs/THIRD_PARTY_MEMORY_NOTICES.md`` for the per-file table.

Module map:

- :mod:`~alpha.agents.memory.l1.gates` — the two-level enable gate.
- :mod:`~alpha.agents.memory.l1.prompts` — English-adapted extraction /
  conflict / persona prompts (chat + work dialects).
- :mod:`~alpha.agents.memory.l1.models` — records, decisions, run reports.
- :mod:`~alpha.agents.memory.l1.parser` — tolerant LLM response parsers.
- :mod:`~alpha.agents.memory.l1.paths` — on-disk layout helpers.
- :mod:`~alpha.agents.memory.l1.store` — per-scope JSON store + hybrid search.
- :mod:`~alpha.agents.memory.l1.extractor` / :mod:`~alpha.agents.memory.l1.dedup`
  — the two LLM stages.
- :mod:`~alpha.agents.memory.l1.quota` / :mod:`~alpha.agents.memory.l1.provenance`
  — limits and the generation log.
- :mod:`~alpha.agents.memory.l1.cleaner` / :mod:`~alpha.agents.memory.l1.persona`
  — retention sweep and persona synthesis.
- :mod:`~alpha.agents.memory.l1.pipeline` — orchestration + recall seam.

``L1MemoryConfig`` lives in :mod:`alpha.config.memory_config` (import
direction: config must not depend on the agent memory package).

Exports are installed lazily (PEP 562) rather than importing the pipeline at
package-import time. The pipeline reads the shared ``MemoryConfig`` at module
level, so an eager re-export meant that importing a leaf of this package — for
example :mod:`~alpha.agents.memory.l1.paths`, which other memory subsystems
reuse for their path helpers — also loaded the pipeline, the config layer and
the backend manager. That made leaf imports expensive and kept the import graph
on the edge of a cycle. With lazy exports the graph is acyclic, leaf imports
stay cheap, and ``from alpha.agents.memory.l1 import L1Pipeline`` plus every
submodule import behave exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "DedupDecision": "models",
    "DedupOutcome": "models",
    "ExtractedMemory": "models",
    "ExtractionOutcome": "models",
    "L1Pipeline": "pipeline",
    "L1RecordStore": "store",
    "MemoryRecord": "models",
    "RunReport": "models",
    "SceneSegment": "models",
    "get_l1_pipeline": "pipeline",
    "get_l1_store": "store",
    "l1_enabled": "gates",
    "reset_l1_pipeline": "pipeline",
    "reset_l1_store": "store",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed lazily
    # above, so this block exists for type checkers and IDEs only.
    from alpha.agents.memory.l1.gates import l1_enabled as l1_enabled
    from alpha.agents.memory.l1.models import DedupDecision as DedupDecision
    from alpha.agents.memory.l1.models import DedupOutcome as DedupOutcome
    from alpha.agents.memory.l1.models import ExtractedMemory as ExtractedMemory
    from alpha.agents.memory.l1.models import ExtractionOutcome as ExtractionOutcome
    from alpha.agents.memory.l1.models import MemoryRecord as MemoryRecord
    from alpha.agents.memory.l1.models import RunReport as RunReport
    from alpha.agents.memory.l1.models import SceneSegment as SceneSegment
    from alpha.agents.memory.l1.pipeline import L1Pipeline as L1Pipeline
    from alpha.agents.memory.l1.pipeline import (
        get_l1_pipeline as get_l1_pipeline,
    )
    from alpha.agents.memory.l1.pipeline import (
        reset_l1_pipeline as reset_l1_pipeline,
    )
    from alpha.agents.memory.l1.store import L1RecordStore as L1RecordStore
    from alpha.agents.memory.l1.store import get_l1_store as get_l1_store
    from alpha.agents.memory.l1.store import reset_l1_store as reset_l1_store

install_lazy_exports(__name__, _EXPORTS)
