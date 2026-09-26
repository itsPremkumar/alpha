"""Thread-safe, atomic, bounded per-user store for entity memory.

One JSON document per trusted user contains entities, mentions, links, and
retention tombstones.  Locks and caches are process-local, matching the L1
store's single-process ownership contract.  Atomic replacement prevents torn
documents but does not make independent store instances or multiple processes a
transactional database.  Genuinely corrupt documents are preserved as
``.corrupt-*`` siblings; a document that parses but declares a format this
build does not implement is instead refused and left byte-for-byte in place.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.memory._store_format import (
    STORE_FORMAT_UNSUPPORTED,
    StoreFormatVerdict,
    classify_store_format,
    format_disclosure,
)

from .config import EntityConfig
from .linking import AliasIndex, alias_root, flatten_alias_chains, rank_merge_candidates
from .models import (
    Entity,
    EntityCandidate,
    EntityEviction,
    EntityLink,
    Mention,
    make_entity_id,
    normalized_name,
)
from .paths import atomic_write_text, entity_root, store_path

logger = logging.getLogger(__name__)

_SCHEMA = 1
_STORE_ID = "entities.store"


class StoreUnavailableError(RuntimeError):
    """Raised when writing could risk replacing an unreadable document."""


@dataclass(frozen=True, slots=True)
class StoreWriteResult:
    """Counts and identifiers from one atomic candidate-ingest mutation."""

    stored_mentions: int
    new_entities: int
    resolved_entities: int
    merged_entities: int
    entity_ids: tuple[str, ...]
    evicted_entity_ids: tuple[str, ...]


@dataclass(slots=True)
class _UserDocument:
    entities: dict[str, Entity]
    mentions: list[Mention]
    links: list[EntityLink]
    evictions: list[EntityEviction]


def _mention_key(mention: Mention) -> tuple[str, str, str, tuple[int, int] | None]:
    return (
        mention.record_id,
        mention.entity_id,
        normalized_name(mention.surface_form),
        mention.span,
    )


def _bounded_aliases(entity: Entity, additions: Iterable[str], limit: int) -> list[str]:
    canonical_key = entity.normalized_name
    output: list[str] = []
    seen = {canonical_key}
    for alias in (*entity.aliases, *additions):
        cleaned = " ".join(str(alias).split()).strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned[:256])
        if len(output) >= limit:
            break
    return output


def _entity_order(entity: Entity) -> tuple[int, float, str, str]:
    return entity.mention_count, entity.last_seen, entity.normalized_name, entity.id


class EntityStore:
    """Atomic entity graph with deterministic alias resolution and retention."""

    def __init__(
        self,
        storage_path: str | None = None,
        *,
        config: EntityConfig | None = None,
    ) -> None:
        if config is not None and storage_path is not None:
            raise ValueError("pass storage_path or config, not both")
        self._config = config if config is not None else EntityConfig(storage_path=storage_path)
        self._root = entity_root(self._config.storage_path)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._cache: dict[str, _UserDocument] = {}
        self._statuses: dict[str, str] = {}
        self._write_blocked: set[str] = set()
        self._format_refusals: dict[str, StoreFormatVerdict] = {}

    @property
    def root(self) -> Path:
        return self._root

    @property
    def config(self) -> EntityConfig:
        return self._config

    def _lock_for(self, user_id: str) -> threading.RLock:
        key = user_id.strip()
        if not key:
            raise ValueError("user_id is required for entity memory")
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def _path(self, user_id: str) -> Path:
        return store_path(self._root, user_id)

    def _validate_document(self, raw: Any) -> _UserDocument:
        if not isinstance(raw, Mapping):
            raise ValueError("entity document must be an object")
        required_arrays = ("entities", "mentions", "links", "evictions")
        if any(not isinstance(raw.get(key), list) for key in required_arrays):
            raise ValueError("entity document must contain all record arrays")
        entities = [Entity.model_validate(item) for item in raw["entities"]]
        by_id: dict[str, Entity] = {}
        for entity in entities:
            if entity.id in by_id:
                raise ValueError(f"duplicate entity id: {entity.id}")
            by_id[entity.id] = entity
        mentions = [Mention.model_validate(item) for item in raw["mentions"]]
        if any(mention.entity_id not in by_id for mention in mentions):
            raise ValueError("mention references an unknown entity")
        links = flatten_alias_chains(EntityLink.model_validate(item) for item in raw["links"])
        if any(link.source_id not in by_id or link.target_id not in by_id for link in links):
            raise ValueError("entity link references an unknown endpoint")
        evictions = [EntityEviction.model_validate(item) for item in raw["evictions"]]
        return _UserDocument(entities=by_id, mentions=mentions, links=links, evictions=evictions)

    def _load(self, user_id: str) -> _UserDocument:
        key = user_id.strip()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = self._path(key)
        empty = _UserDocument(entities={}, mentions=[], links=[], evictions=[])
        if not path.exists():
            self._statuses[key] = "ok"
            self._cache[key] = empty
            return empty
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            self._statuses[key] = "read_error"
            self._write_blocked.add(key)
            logger.error("Entity store: could not read %s (%s)", path, exc)
            self._cache[key] = empty
            return empty
        except (json.JSONDecodeError, ValueError, TypeError, UnicodeError) as exc:
            document = self._preserve_corrupt(key, path, exc)
            self._cache[key] = document
            return document

        # The bytes parsed. A marker this build does not implement means another
        # build owns the file; quarantining it here would destroy that data.
        verdict = classify_store_format(store=_STORE_ID, path=path, raw=raw, supported_version=_SCHEMA)
        if verdict.refusal:
            self._format_refusals[key] = verdict
            self._statuses[key] = STORE_FORMAT_UNSUPPORTED
            self._write_blocked.add(key)
            logger.error(
                "Entity store: refusing %s (%s); document left in place and writes blocked",
                verdict.path,
                format_disclosure(verdict),
            )
            self._cache[key] = empty
            return empty
        try:
            document = self._validate_document(raw)
            self._statuses[key] = "ok"
        except (ValueError, TypeError) as exc:
            document = self._preserve_corrupt(key, path, exc)
        self._cache[key] = document
        return document

    def _preserve_corrupt(self, key: str, path: Path, exc: BaseException) -> _UserDocument:
        """Preserve genuinely unreadable bytes; never used for a version mismatch."""

        backup = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
        try:
            path.replace(backup)
        except OSError as backup_exc:
            self._statuses[key] = "corrupt_document_unpreserved"
            self._write_blocked.add(key)
            logger.error(
                "Entity store: corrupt document %s could not be preserved as %s (%s; original error: %s)",
                path,
                backup.name,
                backup_exc,
                exc,
            )
        else:
            self._statuses[key] = "corrupt_document_preserved"
            logger.error(
                "Entity store: corrupt document %s preserved as %s; current user starts empty (%s)",
                path,
                backup.name,
                exc,
            )
        return _UserDocument(entities={}, mentions=[], links=[], evictions=[])

    def format_refusal(self, user_id: str) -> StoreFormatVerdict | None:
        """Return the version refusal held for a user, or ``None``."""

        return self._format_refusals.get(user_id.strip())

    def _persist(self, user_id: str, document: _UserDocument) -> None:
        payload = {
            "schema": _SCHEMA,
            "entities": [document.entities[key].model_dump(mode="json") for key in sorted(document.entities)],
            "mentions": [mention.model_dump(mode="json") for mention in document.mentions],
            "links": [link.model_dump(mode="json") for link in document.links],
            "evictions": [event.model_dump(mode="json") for event in document.evictions],
        }
        atomic_write_text(
            self._path(user_id),
            json.dumps(payload, ensure_ascii=False, indent=1),
        )

    def _require_writable(self, user_id: str) -> None:
        key = user_id.strip()
        status = self.read_status(key)
        if key in self._write_blocked or status not in {"ok", "corrupt_document_preserved"}:
            raise StoreUnavailableError(status)

    def read_status(self, user_id: str) -> str:
        """Return the latest explicit load status for this user."""
        key = user_id.strip()
        if not key:
            raise ValueError("user_id is required for entity memory")
        with self._lock_for(key):
            self._load(key)
            return self._statuses.get(key, "ok")

    def get_entity(self, entity_id: str, *, user_id: str) -> Entity | None:
        with self._lock_for(user_id):
            entity = self._load(user_id).entities.get(entity_id)
            return entity.model_copy(deep=True) if entity is not None else None

    def list_entities(
        self,
        user_id: str,
        *,
        promoted_only: bool = False,
    ) -> list[Entity]:
        """Return a deep, deterministic snapshot; pending entities are opt-in."""
        with self._lock_for(user_id):
            entities = list(self._load(user_id).entities.values())
        if promoted_only:
            entities = [entity for entity in entities if entity.is_promoted(self._config.min_mentions_to_promote)]
        return [entity.model_copy(deep=True) for entity in sorted(entities, key=lambda item: (item.normalized_name, item.id))]

    def list_mentions(self, user_id: str, *, entity_id: str | None = None) -> list[Mention]:
        with self._lock_for(user_id):
            mentions = list(self._load(user_id).mentions)
        if entity_id is not None:
            mentions = [mention for mention in mentions if mention.entity_id == entity_id]
        return sorted(
            (mention.model_copy(deep=True) for mention in mentions),
            key=lambda item: (item.record_id, item.entity_id, item.span or (-1, -1)),
        )

    def list_links(self, user_id: str) -> list[EntityLink]:
        with self._lock_for(user_id):
            links = list(self._load(user_id).links)
        return [link.model_copy(deep=True) for link in links]

    def list_evictions(self, user_id: str) -> list[EntityEviction]:
        with self._lock_for(user_id):
            events = list(self._load(user_id).evictions)
        return [event.model_copy(deep=True) for event in events]

    def read_snapshot(
        self,
        user_id: str,
    ) -> tuple[list[Entity], list[Mention], list[EntityLink], list[EntityEviction]]:
        """Return one atomic deep snapshot for compound graph recall."""
        with self._lock_for(user_id):
            document = self._load(user_id)
            return (
                [entity.model_copy(deep=True) for entity in document.entities.values()],
                [mention.model_copy(deep=True) for mention in document.mentions],
                [link.model_copy(deep=True) for link in document.links],
                [event.model_copy(deep=True) for event in document.evictions],
            )

    def _new_entity(self, candidate: EntityCandidate, created_at: float) -> Entity:
        entity_id = make_entity_id(candidate.canonical_name, candidate.entity_type)
        aliases = _bounded_aliases(
            Entity(
                id=entity_id,
                canonical_name=candidate.canonical_name,
                normalized_name=normalized_name(candidate.canonical_name),
                entity_type=candidate.entity_type,
                aliases=[],
                mention_count=0,
                first_seen=created_at,
                last_seen=created_at,
                confidence=candidate.confidence,
            ),
            candidate.aliases,
            self._config.max_aliases_per_entity,
        )
        return Entity(
            id=entity_id,
            canonical_name=candidate.canonical_name,
            normalized_name=normalized_name(candidate.canonical_name),
            entity_type=candidate.entity_type,
            aliases=aliases,
            mention_count=0,
            first_seen=created_at,
            last_seen=created_at,
            confidence=candidate.confidence,
            source_record_ids=[],
            metadata={
                "extractor": candidate.extractor,
                "extraction_metadata": candidate.metadata,
            },
            merged_from=[],
        )

    def _attach_mention(
        self,
        document: _UserDocument,
        entity: Entity,
        *,
        record_id: str,
        surface_form: str,
        span: tuple[int, int] | None,
        created_at: float,
        confidence: float,
    ) -> bool:
        mention = Mention(
            record_id=record_id,
            entity_id=entity.id,
            surface_form=surface_form,
            span=span,
            created_at=created_at,
        )
        existing_keys = {_mention_key(item) for item in document.mentions}
        key = _mention_key(mention)
        if key in existing_keys:
            if record_id not in entity.source_record_ids:
                entity.source_record_ids.append(record_id)
                entity.source_record_ids.sort()
            return False
        document.mentions.append(mention)
        entity.mention_count += 1
        if record_id not in entity.source_record_ids:
            entity.source_record_ids.append(record_id)
            entity.source_record_ids.sort()
        entity.first_seen = min(entity.first_seen, created_at)
        entity.last_seen = max(entity.last_seen, created_at)
        entity.confidence = max(entity.confidence, confidence)
        return True

    def _add_aliases(self, entity: Entity, candidate: EntityCandidate) -> None:
        additions = [candidate.canonical_name, *candidate.aliases]
        entity.aliases = _bounded_aliases(
            entity,
            additions,
            self._config.max_aliases_per_entity,
        )
        entity.confidence = max(entity.confidence, candidate.confidence)
        extractor = candidate.extractor
        existing = entity.metadata.get("extractors")
        if isinstance(existing, list) and extractor not in existing:
            existing.append(extractor)
        else:
            entity.metadata["extractors"] = [extractor]

    def _merge_pair_locked(
        self,
        document: _UserDocument,
        target_id: str,
        source_id: str,
    ) -> Entity:
        target = document.entities[target_id]
        source = document.entities[source_id]
        aliases = _bounded_aliases(
            target,
            [source.canonical_name, *source.aliases],
            self._config.max_aliases_per_entity,
        )
        merged_from = _stable_unique([*target.merged_from, *source.merged_from, target.id, source.id])
        source_records = sorted(_stable_unique([*target.source_record_ids, *source.source_record_ids]))
        target.aliases = aliases
        target.mention_count += source.mention_count
        target.first_seen = min(target.first_seen, source.first_seen)
        target.last_seen = max(target.last_seen, source.last_seen)
        target.confidence = max(target.confidence, source.confidence)
        target.source_record_ids = source_records
        target.merged_from = merged_from
        target.metadata = {
            **source.metadata,
            **target.metadata,
            "merged_entity_ids": merged_from,
        }

        retained_mentions: list[Mention] = []
        seen_mentions: set[tuple[str, str, str, tuple[int, int] | None]] = set()
        for mention in document.mentions:
            updated = mention
            if mention.entity_id == source.id:
                updated = mention.model_copy(update={"entity_id": target.id}, deep=True)
            key = _mention_key(updated)
            if key in seen_mentions:
                continue
            seen_mentions.add(key)
            retained_mentions.append(updated)
        document.mentions = retained_mentions

        rewired: list[EntityLink] = []
        for link in document.links:
            if source.id not in {link.source_id, link.target_id}:
                rewired.append(link)
                continue
            rewired.append(
                link.model_copy(
                    update={
                        "source_id": target.id if link.source_id == source.id else link.source_id,
                        "target_id": target.id if link.target_id == source.id else link.target_id,
                    },
                    deep=True,
                )
            )
        document.links = flatten_alias_chains(rewired)
        document.entities.pop(source.id, None)
        return target

    def _merge_locked(self, document: _UserDocument, target_id: str, source_id: str) -> Entity:
        if target_id not in document.entities or source_id not in document.entities:
            raise ValueError("both entities must exist before merge")
        if target_id == source_id:
            return document.entities[target_id]

        target_root = alias_root(target_id, document.links)
        source_root = alias_root(source_id, document.links)
        survivor_id = target_root if target_root in document.entities else target_id
        merge_order: list[str] = []
        for entity_id in (survivor_id, target_root, target_id, source_root, source_id):
            if entity_id in document.entities and entity_id not in merge_order:
                merge_order.append(entity_id)
        survivor = document.entities[survivor_id]
        for entity_id in merge_order:
            if entity_id != survivor_id:
                survivor = self._merge_pair_locked(document, survivor_id, entity_id)
        return survivor

    def _enforce_alias_caps(self, document: _UserDocument) -> None:
        for entity in document.entities.values():
            entity.aliases = _bounded_aliases(
                entity,
                (),
                self._config.max_aliases_per_entity,
            )

    def _enforce_entity_cap(self, document: _UserDocument, *, now: float) -> list[str]:
        overflow = len(document.entities) - self._config.max_entities_per_user
        if overflow <= 0:
            return []
        ranked = sorted(document.entities.values(), key=_entity_order)
        evicted_ids = [entity.id for entity in ranked[:overflow]]
        for entity_id in evicted_ids:
            entity = document.entities.get(entity_id)
            if entity is None:
                continue
            linked_ids = sorted({link.target_id if link.source_id == entity_id else link.source_id for link in document.links if entity_id in {link.source_id, link.target_id}})
            removed_mentions = [mention for mention in document.mentions if mention.entity_id == entity_id]
            removed_links = [link for link in document.links if entity_id in {link.source_id, link.target_id}]
            document.evictions.append(
                EntityEviction(
                    entity_id=entity.id,
                    canonical_name=entity.canonical_name,
                    aliases=list(entity.aliases),
                    mention_count=entity.mention_count,
                    source_record_ids=list(entity.source_record_ids),
                    merged_from=list(entity.merged_from),
                    linked_entity_ids=linked_ids,
                    removed_mention_count=len(removed_mentions),
                    removed_link_count=len(removed_links),
                    evicted_at=now,
                )
            )
            document.mentions = [mention for mention in document.mentions if mention.entity_id != entity_id]
            document.links = [link for link in document.links if entity_id not in {link.source_id, link.target_id}]
            document.entities.pop(entity_id, None)
        return evicted_ids

    def ingest_candidates(
        self,
        candidates: Iterable[EntityCandidate],
        *,
        user_id: str,
        record_times: Mapping[str, float] | None = None,
        now: float | None = None,
    ) -> StoreWriteResult:
        """Resolve, threshold-merge, index, bound, and persist candidates atomically."""
        materialized = list(candidates)
        if not materialized:
            return StoreWriteResult(0, 0, 0, 0, (), ())
        moment = time.time() if now is None else float(now)
        times = dict(record_times or {})
        with self._lock_for(user_id):
            self._require_writable(user_id)
            document = self._load(user_id)
            before = copy.deepcopy(document)
            stored_mentions = 0
            new_entities = 0
            resolved_entities = 0
            merged_entities = 0
            result_ids: list[str] = []

            for candidate in materialized:
                created_at = max(0.0, float(times.get(candidate.record_id, moment)))
                alias_index = AliasIndex(document.entities.values(), document.links)
                existing = alias_index.resolve(
                    candidate.canonical_name,
                    entity_type=candidate.entity_type,
                    allow_generated_base=False,
                )
                if existing is None:
                    for alias in candidate.aliases:
                        existing = alias_index.resolve(
                            alias,
                            entity_type=candidate.entity_type,
                            allow_generated_base=False,
                        )
                        if existing is not None:
                            break
                merge_target: Entity | None = None
                if existing is None:
                    ranked = rank_merge_candidates(
                        candidate.canonical_name,
                        candidate.entity_type,
                        document.entities.values(),
                    )
                    if ranked and ranked[0].score >= self._config.merge_similarity_threshold:
                        merge_target = document.entities.get(ranked[0].entity_id)

                if existing is None and merge_target is None:
                    entity = self._new_entity(candidate, created_at)
                    document.entities[entity.id] = entity
                    new_entities += 1
                elif existing is None and merge_target is not None:
                    provisional = self._new_entity(candidate, created_at)
                    document.entities[provisional.id] = provisional
                    entity = self._merge_locked(document, merge_target.id, provisional.id)
                    merged_entities += 1
                else:
                    entity = existing
                    self._add_aliases(entity, candidate)
                    resolved_entities += 1

                mention_added = self._attach_mention(
                    document,
                    entity,
                    record_id=candidate.record_id,
                    surface_form=candidate.canonical_name,
                    span=candidate.span,
                    created_at=created_at,
                    confidence=candidate.confidence,
                )
                stored_mentions += int(mention_added)
                result_ids.append(entity.id)

            self._enforce_alias_caps(document)
            evicted_ids = self._enforce_entity_cap(document, now=moment)
            try:
                self._persist(user_id, document)
            except Exception:
                self._cache[user_id.strip()] = before
                raise
            return StoreWriteResult(
                stored_mentions=stored_mentions,
                new_entities=new_entities,
                resolved_entities=resolved_entities,
                merged_entities=merged_entities,
                entity_ids=tuple(result_ids),
                evicted_entity_ids=tuple(evicted_ids),
            )

    def add_link(self, link: EntityLink, *, user_id: str) -> EntityLink | None:
        """Add/update one link; alias cycles are rejected by deterministic flattening."""
        with self._lock_for(user_id):
            self._require_writable(user_id)
            document = self._load(user_id)
            if link.source_id == link.target_id:
                return None
            if link.source_id not in document.entities or link.target_id not in document.entities:
                raise ValueError("both link endpoints must exist")
            before = copy.deepcopy(document)
            document.links = flatten_alias_chains([*document.links, link])
            try:
                self._persist(user_id, document)
            except Exception:
                self._cache[user_id.strip()] = before
                raise
            for stored in document.links:
                if stored.source_id == link.source_id and stored.target_id == link.target_id and stored.relation is link.relation:
                    return stored.model_copy(deep=True)
            return None

    def merge_entities(
        self,
        target_id: str,
        source_id: str,
        *,
        user_id: str,
    ) -> Entity | None:
        """Explicitly merge two retained nodes, following alias chains to their root."""
        with self._lock_for(user_id):
            self._require_writable(user_id)
            document = self._load(user_id)
            if target_id not in document.entities or source_id not in document.entities:
                return None
            before = copy.deepcopy(document)
            merged = self._merge_locked(document, target_id, source_id)
            self._enforce_alias_caps(document)
            try:
                self._persist(user_id, document)
            except Exception:
                self._cache[user_id.strip()] = before
                raise
            return merged.model_copy(deep=True)

    def reset(self) -> None:
        """Drop process-local caches and locks (tests/config lifecycle only)."""
        with self._locks_guard:
            self._cache.clear()
            self._locks.clear()
            self._statuses.clear()
            self._write_blocked.clear()

    def path_for(self, user_id: str) -> Path:
        """Expose the exact per-user path for diagnostics and hermetic tests."""
        return self._path(user_id)


def _stable_unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


__all__ = ["EntityStore", "StoreUnavailableError", "StoreWriteResult"]
