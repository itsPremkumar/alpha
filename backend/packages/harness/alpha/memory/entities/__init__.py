"""Alpha entity memory: named-entity mentions, aliases, and scoped recall.

This package implements row 17 (``Entity / graph-linked``) in
``docs/MEMORY_TYPES.md`` as an additive layer over raw L1 memories.  It answers
which named things a user/project keeps mentioning and which stored records
mention them.  It complements — and does not replace — the structured
subject-predicate-object belief graph in
:mod:`alpha.memory.cognitive.semantic_graph`.

The extract/resolve/merge pattern is conceptual, inspired by Mem0 entity
linking and Zep/Graphiti.  The code, storage contract, deterministic ranking,
and tests are original; no third-party code is copied.  The feature is disabled
by default, owns no global singleton, and requires an explicit config/store
wiring seam."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "AliasIndex": "linking",
    "Entity": "models",
    "EntityCandidate": "models",
    "EntityConfig": "config",
    "EntityEviction": "models",
    "EntityExtractor": "extraction",
    "EntityExtractorMode": "models",
    "EntityIngestResult": "models",
    "EntityLink": "models",
    "EntityLinkRelation": "models",
    "EntityMemory": "memory",
    "EntityRecall": "recall",
    "EntityStore": "store",
    "EntityType": "models",
    "ExtractionOutcome": "models",
    "ExtractionStatus": "models",
    "IngestStatus": "models",
    "MAX_NEIGHBORHOOD_DEPTH": "recall",
    "Mention": "models",
    "MergeCandidate": "linking",
    "NORMALIZATION_DISCLOSURE": "models",
    "StoreUnavailableError": "store",
    "StoreWriteResult": "store",
    "alias_root": "linking",
    "append_entity_ingest": "provenance",
    "append_entity_merge": "provenance",
    "entities_enabled": "config",
    "entity_root": "paths",
    "extract_entities": "extraction",
    "extraction_status_known": "extraction",
    "flatten_alias_chains": "linking",
    "make_entity_id": "models",
    "merge_similarity": "linking",
    "normalize_display_name": "models",
    "normalized_name": "models",
    "parse_model_response": "extraction",
    "provenance_log_path": "paths",
    "rank_merge_candidates": "linking",
    "read_entries": "provenance",
    "resolve_entity_config": "config",
    "store_path": "paths",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed
    # lazily above, so this block exists for type checkers and IDEs only.
    from .config import (
        EntityConfig as EntityConfig,
    )
    from .config import (
        entities_enabled as entities_enabled,
    )
    from .config import (
        resolve_entity_config as resolve_entity_config,
    )
    from .extraction import (
        EntityExtractor as EntityExtractor,
    )
    from .extraction import (
        extract_entities as extract_entities,
    )
    from .extraction import (
        extraction_status_known as extraction_status_known,
    )
    from .extraction import (
        parse_model_response as parse_model_response,
    )
    from .linking import (
        AliasIndex as AliasIndex,
    )
    from .linking import (
        MergeCandidate as MergeCandidate,
    )
    from .linking import (
        alias_root as alias_root,
    )
    from .linking import (
        flatten_alias_chains as flatten_alias_chains,
    )
    from .linking import (
        merge_similarity as merge_similarity,
    )
    from .linking import (
        rank_merge_candidates as rank_merge_candidates,
    )
    from .memory import EntityMemory as EntityMemory
    from .models import (
        NORMALIZATION_DISCLOSURE as NORMALIZATION_DISCLOSURE,
    )
    from .models import (
        Entity as Entity,
    )
    from .models import (
        EntityCandidate as EntityCandidate,
    )
    from .models import (
        EntityEviction as EntityEviction,
    )
    from .models import (
        EntityExtractorMode as EntityExtractorMode,
    )
    from .models import (
        EntityIngestResult as EntityIngestResult,
    )
    from .models import (
        EntityLink as EntityLink,
    )
    from .models import (
        EntityLinkRelation as EntityLinkRelation,
    )
    from .models import (
        EntityType as EntityType,
    )
    from .models import (
        ExtractionOutcome as ExtractionOutcome,
    )
    from .models import (
        ExtractionStatus as ExtractionStatus,
    )
    from .models import (
        IngestStatus as IngestStatus,
    )
    from .models import (
        Mention as Mention,
    )
    from .models import (
        make_entity_id as make_entity_id,
    )
    from .models import (
        normalize_display_name as normalize_display_name,
    )
    from .models import (
        normalized_name as normalized_name,
    )
    from .paths import (
        entity_root as entity_root,
    )
    from .paths import (
        provenance_log_path as provenance_log_path,
    )
    from .paths import (
        store_path as store_path,
    )
    from .provenance import (
        append_entity_ingest as append_entity_ingest,
    )
    from .provenance import (
        append_entity_merge as append_entity_merge,
    )
    from .provenance import (
        read_entries as read_entries,
    )
    from .recall import MAX_NEIGHBORHOOD_DEPTH as MAX_NEIGHBORHOOD_DEPTH
    from .recall import EntityRecall as EntityRecall
    from .store import (
        EntityStore as EntityStore,
    )
    from .store import (
        StoreUnavailableError as StoreUnavailableError,
    )
    from .store import (
        StoreWriteResult as StoreWriteResult,
    )

install_lazy_exports(__name__, _EXPORTS)
