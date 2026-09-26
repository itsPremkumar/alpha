"""Alpha narrative (autobiographical) memory.

This package fills row 16, **Autobiographical / narrative**, in
``docs/MEMORY_TYPES.md``.  It turns dated, source-backed events into an ordered
and bounded life/project/agent story: day/week/month chapters, deterministic
fallback prose, optional importance-weighted model reflection, explicit chapter
change tracking, and a small prompt-recall surface.

The design follows the reflection and importance-weighted retrieval idea in
*Generative Agents: Interactive Simulacra of Human Behavior* (Park et al.,
UIST 2023), but the code, schemas, persistence, and failure contract are
original to Alpha.  It is opt-in, consumes plain dictionaries at capture seams,
owns no global singleton, and never fabricates a story when synthesis or storage
fails."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "IngestResult": "models",
    "IngestStatus": "models",
    "KNOWN_SYNTHESIS_STATUSES": "synthesis",
    "MAX_CHAPTERS": "models",
    "NarrativeConfig": "config",
    "NarrativeEvent": "models",
    "NarrativeEventStore": "store",
    "NarrativeMemory": "memory",
    "NarrativeMemoryStore": "store",
    "NarrativeRecall": "recall",
    "NarrativeScope": "models",
    "NarrativeStore": "store",
    "NarrativeStoryMemory": "memory",
    "NarrativeSubsystem": "memory",
    "NarrativeTimeline": "timeline",
    "RegenerationResult": "models",
    "RegenerationStatus": "models",
    "SCOPE_TYPES": "models",
    "SYNTHESIS_STATUSES": "models",
    "StoreUnavailableError": "store",
    "StoreWriteResult": "store",
    "StoryChapter": "models",
    "StoryDocument": "models",
    "StoryStore": "store",
    "SynthesisOutcome": "models",
    "SynthesisStatus": "models",
    "append_entry": "provenance",
    "append_event_ingest": "provenance",
    "append_ingest": "provenance",
    "append_regeneration": "provenance",
    "append_story_regeneration": "provenance",
    "build_chapters": "synthesis",
    "choose_bucket": "synthesis",
    "deterministic_chapter": "synthesis",
    "deterministic_entry": "synthesis",
    "event_store_path": "paths",
    "events_path": "paths",
    "moments": "timeline",
    "narrative_enabled": "config",
    "narrative_root": "paths",
    "parse_model_response": "synthesis",
    "periods": "timeline",
    "provenance_log_path": "paths",
    "provenance_path": "provenance",
    "range_summary": "timeline",
    "read_entries": "provenance",
    "regenerate": "synthesis",
    "regenerate_story": "synthesis",
    "resolve_narrative_config": "config",
    "rewrite_day": "provenance",
    "scope_dir": "paths",
    "scope_key": "paths",
    "story_block": "recall",
    "story_path": "paths",
    "story_store_path": "paths",
    "synthesis_status_known": "synthesis",
    "synthesize": "synthesis",
    "timeline": "timeline",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed
    # lazily above, so this block exists for type checkers and IDEs only.
    from .config import (
        NarrativeConfig as NarrativeConfig,
    )
    from .config import (
        narrative_enabled as narrative_enabled,
    )
    from .config import (
        resolve_narrative_config as resolve_narrative_config,
    )
    from .memory import (
        NarrativeMemory as NarrativeMemory,
    )
    from .memory import (
        NarrativeStoryMemory as NarrativeStoryMemory,
    )
    from .memory import (
        NarrativeSubsystem as NarrativeSubsystem,
    )
    from .models import (
        MAX_CHAPTERS as MAX_CHAPTERS,
    )
    from .models import (
        SCOPE_TYPES as SCOPE_TYPES,
    )
    from .models import (
        SYNTHESIS_STATUSES as SYNTHESIS_STATUSES,
    )
    from .models import (
        IngestResult as IngestResult,
    )
    from .models import (
        IngestStatus as IngestStatus,
    )
    from .models import (
        NarrativeEvent as NarrativeEvent,
    )
    from .models import (
        NarrativeScope as NarrativeScope,
    )
    from .models import (
        RegenerationResult as RegenerationResult,
    )
    from .models import (
        RegenerationStatus as RegenerationStatus,
    )
    from .models import (
        StoryChapter as StoryChapter,
    )
    from .models import (
        StoryDocument as StoryDocument,
    )
    from .models import (
        SynthesisOutcome as SynthesisOutcome,
    )
    from .models import (
        SynthesisStatus as SynthesisStatus,
    )
    from .paths import (
        event_store_path as event_store_path,
    )
    from .paths import (
        events_path as events_path,
    )
    from .paths import (
        narrative_root as narrative_root,
    )
    from .paths import (
        provenance_log_path as provenance_log_path,
    )
    from .paths import (
        scope_dir as scope_dir,
    )
    from .paths import (
        scope_key as scope_key,
    )
    from .paths import (
        story_path as story_path,
    )
    from .paths import (
        story_store_path as story_store_path,
    )
    from .provenance import (
        append_entry as append_entry,
    )
    from .provenance import (
        append_event_ingest as append_event_ingest,
    )
    from .provenance import (
        append_ingest as append_ingest,
    )
    from .provenance import (
        append_regeneration as append_regeneration,
    )
    from .provenance import (
        append_story_regeneration as append_story_regeneration,
    )
    from .provenance import (
        provenance_path as provenance_path,
    )
    from .provenance import (
        read_entries as read_entries,
    )
    from .provenance import (
        rewrite_day as rewrite_day,
    )
    from .recall import NarrativeRecall as NarrativeRecall
    from .recall import story_block as story_block
    from .store import (
        NarrativeEventStore as NarrativeEventStore,
    )
    from .store import (
        NarrativeMemoryStore as NarrativeMemoryStore,
    )
    from .store import (
        NarrativeStore as NarrativeStore,
    )
    from .store import (
        StoreUnavailableError as StoreUnavailableError,
    )
    from .store import (
        StoreWriteResult as StoreWriteResult,
    )
    from .store import (
        StoryStore as StoryStore,
    )
    from .synthesis import (
        KNOWN_SYNTHESIS_STATUSES as KNOWN_SYNTHESIS_STATUSES,
    )
    from .synthesis import (
        build_chapters as build_chapters,
    )
    from .synthesis import (
        choose_bucket as choose_bucket,
    )
    from .synthesis import (
        deterministic_chapter as deterministic_chapter,
    )
    from .synthesis import (
        deterministic_entry as deterministic_entry,
    )
    from .synthesis import (
        parse_model_response as parse_model_response,
    )
    from .synthesis import (
        regenerate as regenerate,
    )
    from .synthesis import (
        regenerate_story as regenerate_story,
    )
    from .synthesis import (
        synthesis_status_known as synthesis_status_known,
    )
    from .synthesis import (
        synthesize as synthesize,
    )
    from .timeline import (
        NarrativeTimeline as NarrativeTimeline,
    )
    from .timeline import (
        moments as moments,
    )
    from .timeline import (
        periods as periods,
    )
    from .timeline import (
        range_summary as range_summary,
    )
    from .timeline import (
        timeline as timeline,
    )

install_lazy_exports(__name__, _EXPORTS)
