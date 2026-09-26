"""Alpha affective memory: remembered emotional tone, mood, and mood-aware recall.

This package fills row 15 (Affective) in ``docs/MEMORY_TYPES.md``: it stores
valence-arousal moments, computes decayed mood trajectories, and supplies a
bounded recall surface. Valence and arousal follow Russell's circumplex model
(*A circumplex model of affect*, 1980); the implementation is original.

The package is opt-in and self-contained. It does not read or mutate the shared
``MemoryConfig`` and exposes no process-wide singleton, so a host must inject
an :class:`AffectiveConfig` and wire the documented capture/recall seams.

Exports are installed lazily (:pep:`562`, via
:func:`alpha.memory._lazy_exports.install_lazy_exports`) so the shared config
schema can import :class:`AffectiveConfig` without dragging this package's
facade, store, and models back through the config layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "AffectiveConfig": "config",
    "AffectiveExtractor": "extraction",
    "AffectEvent": "models",
    "AffectSource": "models",
    "AffectSubject": "models",
    "AffectiveEventStore": "store",
    "AffectiveMemory": "memory",
    "DEFAULT_HALF_LIFE_HOURS": "mood",
    "DEFAULT_MOOD_SHIFT_THRESHOLD": "mood",
    "ExtractionOutcome": "models",
    "ExtractionStatus": "models",
    "IngestResult": "models",
    "IngestStatus": "models",
    "MoodShift": "mood",
    "MoodState": "models",
    "MoodStatus": "models",
    "StoreUnavailableError": "store",
    "StoreWriteResult": "store",
    "append_entry": "provenance",
    "append_event_ingest": "provenance",
    "append_mood_computation": "provenance",
    "compute_mood": "mood",
    "extraction_status_known": "extraction",
    "mood_boost": "recall",
    "mood_shift": "mood",
    "mood_state": "recall",
    "mood_trajectory": "mood",
    "parse_extraction_response": "extraction",
    "read_entries": "provenance",
    "recent_moments": "recall",
    "render_block": "recall",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed lazily
    # below, so this block exists purely for type checkers and IDEs.
    from .config import AffectiveConfig as AffectiveConfig
    from .extraction import (
        AffectiveExtractor as AffectiveExtractor,
    )
    from .extraction import (
        extraction_status_known as extraction_status_known,
    )
    from .extraction import (
        parse_extraction_response as parse_extraction_response,
    )
    from .memory import AffectiveMemory as AffectiveMemory
    from .models import AffectEvent as AffectEvent
    from .models import AffectSource as AffectSource
    from .models import AffectSubject as AffectSubject
    from .models import ExtractionOutcome as ExtractionOutcome
    from .models import ExtractionStatus as ExtractionStatus
    from .models import IngestResult as IngestResult
    from .models import IngestStatus as IngestStatus
    from .models import MoodState as MoodState
    from .models import MoodStatus as MoodStatus
    from .mood import DEFAULT_HALF_LIFE_HOURS as DEFAULT_HALF_LIFE_HOURS
    from .mood import DEFAULT_MOOD_SHIFT_THRESHOLD as DEFAULT_MOOD_SHIFT_THRESHOLD
    from .mood import MoodShift as MoodShift
    from .mood import compute_mood as compute_mood
    from .mood import mood_shift as mood_shift
    from .mood import mood_trajectory as mood_trajectory
    from .provenance import append_entry as append_entry
    from .provenance import append_event_ingest as append_event_ingest
    from .provenance import append_mood_computation as append_mood_computation
    from .provenance import read_entries as read_entries
    from .recall import mood_boost as mood_boost
    from .recall import mood_state as mood_state
    from .recall import recent_moments as recent_moments
    from .recall import render_block as render_block
    from .store import AffectiveEventStore as AffectiveEventStore
    from .store import StoreUnavailableError as StoreUnavailableError
    from .store import StoreWriteResult as StoreWriteResult

install_lazy_exports(__name__, _EXPORTS)
