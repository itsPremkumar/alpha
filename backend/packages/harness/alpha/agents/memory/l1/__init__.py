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
"""

from alpha.agents.memory.l1.gates import l1_enabled
from alpha.agents.memory.l1.models import (
    DedupDecision,
    DedupOutcome,
    ExtractedMemory,
    ExtractionOutcome,
    MemoryRecord,
    RunReport,
    SceneSegment,
)
from alpha.agents.memory.l1.pipeline import (
    L1Pipeline,
    get_l1_pipeline,
    reset_l1_pipeline,
)
from alpha.agents.memory.l1.store import L1RecordStore, get_l1_store, reset_l1_store

__all__ = [
    "DedupDecision",
    "DedupOutcome",
    "ExtractedMemory",
    "ExtractionOutcome",
    "L1Pipeline",
    "L1RecordStore",
    "MemoryRecord",
    "RunReport",
    "SceneSegment",
    "get_l1_pipeline",
    "get_l1_store",
    "l1_enabled",
    "reset_l1_pipeline",
    "reset_l1_store",
]
