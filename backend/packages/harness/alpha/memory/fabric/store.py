"""Thread-safe, atomic, per-scope store for canonical memory envelopes.

The persistence discipline follows Alpha's L1 store: one JSON document per
scope, per-scope re-entrant locks, temp-file replacement, and fail-closed
corruption preservation.  Locks and indexes are process-local; independent
processes writing the same document require an external coordinator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.agents.memory.l1.paths import atomic_write_text, safe_segment

from .config import FabricConfig, fabric_root, load_fabric_config
from .lifecycle import TransitionResult
from .lifecycle import restore as restore_envelope
from .lifecycle import transition as transition_envelope
from .models import LifecycleStatus, MemoryEnvelope, MemoryScope, SecurityClassification
from .provenance import ProvenanceResult, append_event

logger = logging.getLogger(__name__)
_SCHEMA_VERSION = 2


@dataclass(slots=True)
class StoreReadResult:
    """Disclosed result for one scope read or point lookup."""

    status: str
    records: list[MemoryEnvelope] = field(default_factory=list)
    reason: str = ""
    error: str = ""
    scope: MemoryScope | None = None

    @property
    def ok(self) -> bool:
        """Whether the read completed, including disclosed corruption recovery."""

        return self.status in {"succeeded", "recovered", "empty"}

    def to_dict(self) -> dict[str, Any]:
        """Serialize the read outcome and defensive envelope snapshots."""

        return {
            "status": self.status,
            "records": [record.to_dict() for record in self.records],
            "reason": self.reason,
            "error": self.error,
            "scope": self.scope.qualified() if self.scope is not None else None,
        }


@dataclass(slots=True)
class StoreResult:
    """Disclosed counts and identities for one canonical-store mutation."""

    status: str
    reason: str = ""
    error: str = ""
    records: list[MemoryEnvelope] = field(default_factory=list)
    requested: int = 0
    stored: int = 0
    replaced: int = 0
    deleted: int = 0
    record_ids: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)
    scope_denied_ids: list[str] = field(default_factory=list)
    evicted_ids: list[str] = field(default_factory=list)
    forced: bool = False
    storage_status: str = ""
    provenance_status: str = ""
    provenance_error: str = ""

    @property
    def ok(self) -> bool:
        """Whether the requested storage mutation completed."""

        return self.status == "succeeded" and not self.missing_ids and not self.skipped_ids

    def to_dict(self) -> dict[str, Any]:
        """Serialize operation counts without claiming audit success on failure."""

        return {
            "status": self.status,
            "reason": self.reason,
            "error": self.error,
            "records": [record.to_dict() for record in self.records],
            "requested": self.requested,
            "stored": self.stored,
            "replaced": self.replaced,
            "deleted": self.deleted,
            "record_ids": list(self.record_ids),
            "missing_ids": list(self.missing_ids),
            "skipped_ids": list(self.skipped_ids),
            "scope_denied_ids": list(self.scope_denied_ids),
            "evicted_ids": list(self.evicted_ids),
            "forced": self.forced,
            "storage_status": self.storage_status,
            "provenance_status": self.provenance_status,
            "provenance_error": self.provenance_error,
        }


@dataclass(slots=True)
class _ScopeDocument:
    scope: MemoryScope
    records: list[MemoryEnvelope]


def scope_document_path(root: Path, scope: MemoryScope) -> Path:
    """Return the collision-resistant per-scope canonical document path."""

    digest = hashlib.sha256(scope.qualified().encode("utf-8")).hexdigest()[:24]
    tenant = safe_segment(scope.tenant_id or "default")
    user = safe_segment(scope.user_id or "default")
    return Path(root) / "memory_fabric" / "scopes" / tenant / user / f"{digest}.json"


class MemoryEnvelopeStore:
    """Atomic scope-partitioned store with id/scope indexes and bounded growth."""

    def __init__(
        self,
        config: FabricConfig | Mapping[str, Any] | str | Path | None = None,
        *,
        storage_path: str | Path | None = None,
        max_records_per_scope: int = 1_000,
    ) -> None:
        if config is None:
            selected = load_fabric_config(storage_path=storage_path)
        elif isinstance(config, (str, Path)):
            if storage_path is not None:
                raise ValueError("storage_path was supplied twice")
            selected = load_fabric_config(storage_path=config)
        elif isinstance(config, Mapping):
            selected = FabricConfig.model_validate(config)
        else:
            selected = config
        if storage_path is not None and selected.storage_path is None:
            selected = selected.model_copy(update={"storage_path": str(storage_path)})
        if max_records_per_scope < 1:
            raise ValueError("max_records_per_scope must be positive")
        self.config = selected
        self.max_records_per_scope = max_records_per_scope
        self._root = fabric_root(selected.storage_path)
        self._registry_lock = threading.RLock()
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._cache: dict[str, _ScopeDocument] = {}
        self._id_index: dict[str, str] = {}
        self._duplicate_ids: set[str] = set()
        self._statuses: dict[str, str] = {}
        self._blocked: set[str] = set()

    @property
    def root(self) -> Path:
        """Resolved fabric state root."""

        return self._root

    @property
    def enabled(self) -> bool:
        """Whether public store operations are enabled."""

        return bool(self.config.enabled)

    @staticmethod
    def _scope_key(scope: MemoryScope) -> str:
        return scope.qualified()

    def _path(self, scope: MemoryScope) -> Path:
        return scope_document_path(self._root, scope)

    def _lock_for(self, scope: MemoryScope) -> threading.RLock:
        # Lock by the resolved path, not a sanitized display segment. This keeps
        # aliases serialized just as the L1 store does.
        key = str(self._path(scope))
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def _clear_scope_index(self, key: str) -> None:
        stale_ids = [record_id for record_id, scope_key in self._id_index.items() if scope_key == key]
        for record_id in stale_ids:
            self._id_index.pop(record_id, None)

    def _index_document(self, document: _ScopeDocument) -> None:
        key = self._scope_key(document.scope)
        self._clear_scope_index(key)
        for record in document.records:
            existing_scope = self._id_index.get(record.id)
            if existing_scope is not None and existing_scope != key:
                self._duplicate_ids.add(record.id)
                continue
            self._id_index[record.id] = key

    def _load_unlocked(self, scope: MemoryScope) -> tuple[_ScopeDocument, str, str]:
        key = self._scope_key(scope)
        cached = self._cache.get(key)
        if cached is not None:
            return cached, self._statuses.get(key, "succeeded"), ""
        path = self._path(scope)
        document = _ScopeDocument(scope=scope.model_copy(deep=True), records=[])
        status = "empty"
        error = ""
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, Mapping):
                    raise ValueError("scope document root must be an object")
                if int(raw.get("schema", 0)) != _SCHEMA_VERSION:
                    raise ValueError("unsupported memory fabric document schema")
                document_scope = MemoryScope.model_validate(raw.get("scope"))
                if document_scope != scope:
                    raise ValueError("document scope does not match its canonical path")
                raw_records = raw.get("records")
                if not isinstance(raw_records, list):
                    raise ValueError("records must be a list")
                records = [MemoryEnvelope.model_validate(item) for item in raw_records]
                if any(record.scope != scope for record in records):
                    raise ValueError("record scope does not match document scope")
                ids = [record.id for record in records]
                if len(ids) != len(set(ids)):
                    raise ValueError("scope document contains duplicate record ids")
                document.records = records
                status = "succeeded"
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                error = str(exc)
                backup = path.with_name(f"{path.name}.corrupt-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
                try:
                    path.replace(backup)
                except OSError as preserve_exc:
                    status = "failed"
                    self._blocked.add(key)
                    error = f"{error}; preservation failed: {preserve_exc}"
                    logger.error(
                        "Memory fabric store: corrupt document %s could not be preserved as %s (%s)",
                        path,
                        backup.name,
                        preserve_exc,
                    )
                else:
                    status = "recovered"
                    self._blocked.discard(key)
                    logger.error(
                        "Memory fabric store: corrupt document %s preserved as %s; scope starts empty",
                        path,
                        backup.name,
                    )
        self._cache[key] = document
        self._statuses[key] = status
        self._index_document(document)
        return document, status, error

    def _persist_unlocked(self, scope: MemoryScope, document: _ScopeDocument) -> None:
        payload = {
            "schema": _SCHEMA_VERSION,
            "scope": scope.model_dump(mode="json"),
            "records": [record.model_dump(mode="json") for record in document.records],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False)
        atomic_write_text(self._path(scope), text)

    def _discover_scopes(self) -> list[MemoryScope]:
        scopes: dict[str, MemoryScope] = {}
        scope_root = self._root / "memory_fabric" / "scopes"
        if not scope_root.exists():
            return []
        for path in sorted(scope_root.rglob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, Mapping):
                    scope = MemoryScope.model_validate(raw.get("scope"))
                    scopes.setdefault(self._scope_key(scope), scope)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
        return [scopes[key] for key in sorted(scopes)]

    def _hydrate_indexes_unlocked(self) -> None:
        for scope in self._discover_scopes():
            key = self._scope_key(scope)
            if key not in self._cache:
                self._load_unlocked(scope)

    def _raw_records_unlocked(self, scope: MemoryScope) -> tuple[list[MemoryEnvelope], str, str]:
        document, status, error = self._load_unlocked(scope)
        return [record.model_copy(deep=True) for record in document.records], status, error

    def list_result(
        self,
        scope: MemoryScope,
        *,
        reader_scope: MemoryScope | None = None,
        now: float | None = None,
        include_inactive: bool = True,
    ) -> StoreReadResult:
        """Read one storage scope with scope/security filtering and disclosure."""

        if not self.enabled:
            return StoreReadResult(status="skipped", reason="disabled", scope=scope)
        if not isinstance(scope, MemoryScope):
            return StoreReadResult(status="failed", reason="invalid_scope", error="expected MemoryScope")
        effective_reader = scope if reader_scope is None else reader_scope
        with self._registry_lock:
            with self._lock_for(scope):
                records, load_status, error = self._raw_records_unlocked(scope)
        if load_status == "failed":
            return StoreReadResult(
                status="failed",
                reason="corrupt_document_preservation_failed",
                error=error,
                scope=scope,
            )
        visible = [record for record in records if record.visible_to(effective_reader, now=now) and (include_inactive or record.lifecycle.status is LifecycleStatus.ACTIVE)]
        if load_status == "recovered":
            return StoreReadResult(
                status="recovered",
                records=visible,
                reason="corrupt_document_preserved",
                error=error,
                scope=scope,
            )
        if not visible:
            return StoreReadResult(status="empty", reason="no_visible_records", scope=scope)
        return StoreReadResult(status="succeeded", records=visible, scope=scope)

    def list(
        self,
        scope: MemoryScope,
        *,
        reader_scope: MemoryScope | None = None,
        now: float | None = None,
        include_inactive: bool = True,
    ) -> list[MemoryEnvelope]:
        """Return a filtered defensive snapshot of one scope."""

        return self.list_result(
            scope,
            reader_scope=reader_scope,
            now=now,
            include_inactive=include_inactive,
        ).records

    def count(
        self,
        scope: MemoryScope,
        *,
        reader_scope: MemoryScope | None = None,
        now: float | None = None,
    ) -> int:
        """Count visible records in one exact scope."""

        return len(self.list(scope, reader_scope=reader_scope, now=now))

    def ids_for_forget(self, scope: MemoryScope, *, actor_scope: MemoryScope) -> list[str]:
        """List ids an authorized actor may hard-delete, including expired rows."""

        if not self.enabled:
            return []
        with self._registry_lock:
            with self._lock_for(scope):
                records, load_status, _ = self._raw_records_unlocked(scope)
        if load_status == "failed":
            return []
        return [record.id for record in records if record.scope.permits(actor_scope) and record.security.permits(actor_scope)]

    def get_result(
        self,
        record_id: str,
        *,
        scope: MemoryScope | None = None,
        reader_scope: MemoryScope | None = None,
        now: float | None = None,
    ) -> StoreReadResult:
        """Look up by id or exact scope while enforcing reader security."""

        if not self.enabled:
            return StoreReadResult(status="skipped", reason="disabled", scope=scope)
        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            selected_scope = scope
            if selected_scope is None:
                indexed_scope = self._id_index.get(record_id)
                if indexed_scope is None:
                    return StoreReadResult(status="empty", reason="not_found")
                selected_scope = MemoryScope.from_qualified(indexed_scope)
            if record_id in self._duplicate_ids:
                return StoreReadResult(
                    status="failed",
                    reason="duplicate_id_across_scopes",
                    error=f"memory id {record_id!r} is present in multiple scopes",
                    scope=selected_scope,
                )
            with self._lock_for(selected_scope):
                records, load_status, error = self._raw_records_unlocked(selected_scope)
        if load_status == "failed":
            return StoreReadResult(
                status="failed",
                reason="corrupt_document_preservation_failed",
                error=error,
                scope=selected_scope,
            )
        record = next((candidate for candidate in records if candidate.id == record_id), None)
        if record is None:
            return StoreReadResult(status="empty", reason="not_found", scope=selected_scope)
        effective_reader = selected_scope if reader_scope is None else reader_scope
        if not record.visible_to(effective_reader, now=now):
            return StoreReadResult(status="empty", reason="scope_or_security_denied", scope=selected_scope)
        status = "recovered" if load_status == "recovered" else "succeeded"
        return StoreReadResult(status=status, records=[record], scope=selected_scope)

    def get(
        self,
        record_id: str,
        *,
        scope: MemoryScope | None = None,
        reader_scope: MemoryScope | None = None,
        now: float | None = None,
    ) -> MemoryEnvelope | None:
        """Return one visible envelope or ``None`` for missing/denied records."""

        result = self.get_result(record_id, scope=scope, reader_scope=reader_scope, now=now)
        return result.records[0] if result.records else None

    def visible_to(
        self,
        reader_scope: MemoryScope,
        *,
        now: float | None = None,
        include_inactive: bool = True,
    ) -> list[MemoryEnvelope]:
        """Search persisted scopes and return only records visible to a reader."""

        if not self.enabled:
            return []
        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            records: list[MemoryEnvelope] = []
            for scope in self._discover_scopes():
                with self._lock_for(scope):
                    scope_records, load_status, _ = self._raw_records_unlocked(scope)
                if load_status == "failed":
                    continue
                for record in scope_records:
                    status_allowed = include_inactive or record.lifecycle.status is LifecycleStatus.ACTIVE
                    if status_allowed and record.visible_to(reader_scope, now=now):
                        records.append(record)
        records.sort(key=lambda record: record.id)
        return records

    def scope_for_id(self, record_id: str) -> MemoryScope | None:
        """Return the indexed namespace for an id without returning content."""

        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            key = self._id_index.get(record_id)
            return MemoryScope.from_qualified(key) if key is not None else None

    def scope_index(self) -> dict[str, tuple[str, ...]]:
        """Return a defensive in-process index from qualified scope to ids."""

        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            index: dict[str, tuple[str, ...]] = {}
            for scope in self._discover_scopes():
                with self._lock_for(scope):
                    document = self._cache[self._scope_key(scope)]
                index[self._scope_key(scope)] = tuple(sorted(record.id for record in document.records))
            return index

    def id_index(self) -> dict[str, str]:
        """Return a defensive in-process id-to-qualified-scope index."""

        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            return dict(self._id_index)

    def read_status(self, scope: MemoryScope) -> str:
        """Return the latest load disposition for an exact scope."""

        with self._registry_lock:
            with self._lock_for(scope):
                _, status, _ = self._load_unlocked(scope)
            return status

    def _audit(
        self,
        action: str,
        record: MemoryEnvelope,
        actor_scope: MemoryScope,
        *,
        reason: str,
        status: str,
        now: float,
        from_status: LifecycleStatus | str | None = None,
        to_status: LifecycleStatus | str | None = None,
        forced: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> ProvenanceResult:
        return append_event(
            action=action,
            record_id=record.id,
            record_scope=record.scope,
            actor_scope=actor_scope,
            reason=reason,
            status=status,
            timestamp=now,
            from_status=from_status,
            to_status=to_status,
            forced=forced,
            details=details,
            config=self.config,
        )

    @staticmethod
    def _merge_audit(result: StoreResult, provenance: ProvenanceResult) -> None:
        if provenance.ok:
            if result.provenance_status != "written":
                result.provenance_status = provenance.status
            return
        if result.provenance_status == "failed":
            result.provenance_error = "; ".join(part for part in (result.provenance_error, provenance.error) if part)
        else:
            result.provenance_status = provenance.status
            result.provenance_error = provenance.error

    def _refused_create(
        self,
        record: MemoryEnvelope,
        actor_scope: MemoryScope,
        reason: str,
        now: float,
    ) -> StoreResult:
        result = StoreResult(
            status="refused",
            reason=reason,
            requested=1,
            records=[record.model_copy(deep=True)],
            record_ids=[record.id],
        )
        self._merge_audit(
            result,
            self._audit(
                "create",
                record,
                actor_scope,
                reason=reason,
                status="refused",
                now=now,
            ),
        )
        return result

    @staticmethod
    def _eviction_rank(record: MemoryEnvelope) -> tuple[bool, int, float, float, float, str]:
        status_rank = {
            LifecycleStatus.PURGED: 0,
            LifecycleStatus.ARCHIVED: 1,
            LifecycleStatus.COMPRESSED: 2,
            LifecycleStatus.ACTIVE: 3,
        }
        return (
            record.lifecycle.pinned,
            status_rank[record.lifecycle.status],
            record.quality.importance,
            record.quality.novelty,
            record.timestamps.created_at,
            record.id,
        )

    def admit(
        self,
        record: MemoryEnvelope,
        *,
        actor_scope: MemoryScope | None = None,
        reason: str = "admitted",
        now: float | None = None,
    ) -> StoreResult:
        """Validate, persist, index, audit, and bound one canonical envelope."""

        if not self.enabled:
            return StoreResult(status="skipped", reason="disabled")
        if not isinstance(record, MemoryEnvelope):
            return StoreResult(status="failed", reason="invalid_envelope", error="expected MemoryEnvelope")
        moment = time.time() if now is None else float(now)
        if not math.isfinite(moment):
            return StoreResult(status="failed", reason="invalid_timestamp", error="now must be finite")
        actor = record.scope if actor_scope is None else actor_scope
        if record.security.classification is SecurityClassification.SECRET and not self.config.allow_secret_classification:
            return self._refused_create(record, actor, "secret_classification_rejected", moment)
        if len(record.tags) > self.config.max_tags:
            return self._refused_create(record, actor, "max_tags_exceeded", moment)
        if not record.scope.permits(actor):
            return self._refused_create(record, actor, "actor_scope_denied", moment)

        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            indexed_scope = self._id_index.get(record.id)
            if record.id in self._duplicate_ids:
                return self._refused_create(record, actor, "preexisting_duplicate_id", moment)
            if indexed_scope is not None and indexed_scope != record.scope.qualified():
                return self._refused_create(record, actor, "id_scope_conflict", moment)
            with self._lock_for(record.scope):
                document, load_status, load_error = self._load_unlocked(record.scope)
                if load_status == "failed":
                    return StoreResult(
                        status="failed",
                        reason="corrupt_document_preservation_failed",
                        error=load_error,
                        records=[record.model_copy(deep=True)],
                        record_ids=[record.id],
                        storage_status=load_status,
                    )
                before = [existing.model_copy(deep=True) for existing in document.records]
                existing_index = next(
                    (index for index, existing in enumerate(document.records) if existing.id == record.id),
                    None,
                )
                candidate = record.model_copy(deep=True)
                if existing_index is None:
                    document.records.append(candidate)
                else:
                    document.records[existing_index] = candidate
                overflow = max(0, len(document.records) - self.max_records_per_scope)
                eligible = [existing for existing in document.records if existing.id != candidate.id and not existing.lifecycle.pinned]
                if len(eligible) < overflow:
                    document.records = before
                    refused = StoreResult(
                        status="refused",
                        reason="scope_capacity_has_no_eviction_candidate",
                        records=[candidate],
                        record_ids=[candidate.id],
                        storage_status=load_status,
                    )
                    self._merge_audit(
                        refused,
                        self._audit(
                            "create",
                            candidate,
                            actor,
                            reason=reason,
                            status="refused",
                            now=moment,
                            details={"storage_status": load_status, "capacity": self.max_records_per_scope},
                        ),
                    )
                    return refused
                ranked = sorted(eligible, key=self._eviction_rank)
                evicted = ranked[:overflow]
                evicted_ids = {existing.id for existing in evicted}
                document.records = [existing for existing in document.records if existing.id not in evicted_ids]
                try:
                    self._persist_unlocked(record.scope, document)
                except Exception as exc:  # noqa: BLE001 - storage errors are disclosed data
                    document.records = before
                    failed = StoreResult(
                        status="failed",
                        reason="storage_write_failed",
                        error=str(exc),
                        records=[candidate],
                        record_ids=[candidate.id],
                        storage_status=load_status,
                    )
                    self._merge_audit(
                        failed,
                        self._audit(
                            "create",
                            candidate,
                            actor,
                            reason=reason,
                            status="failed",
                            now=moment,
                            details={"storage_status": load_status, "error": str(exc)},
                        ),
                    )
                    return failed
                self._index_document(document)
                self._statuses[self._scope_key(record.scope)] = "succeeded"
                evicted_records = [item.model_copy(deep=True) for item in evicted]

        result = StoreResult(
            status="succeeded",
            records=[candidate],
            requested=1,
            stored=0 if existing_index is not None else 1,
            replaced=1 if existing_index is not None else 0,
            record_ids=[candidate.id],
            evicted_ids=[item.id for item in evicted_records],
            storage_status=load_status,
        )
        self._merge_audit(
            result,
            self._audit(
                "create",
                candidate,
                actor,
                reason=reason,
                status="replaced" if existing_index is not None else "created",
                now=moment,
                details={"storage_status": load_status},
            ),
        )
        for evicted_record in evicted_records:
            self._merge_audit(
                result,
                self._audit(
                    "forget",
                    evicted_record,
                    actor,
                    reason="deterministic scope-capacity eviction",
                    status="evicted",
                    now=moment,
                    forced=False,
                    details={"replacement_id": candidate.id, "max_records_per_scope": self.max_records_per_scope},
                ),
            )
        return result

    def put(
        self,
        record: MemoryEnvelope,
        *,
        actor_scope: MemoryScope | None = None,
        reason: str = "admitted",
        now: float | None = None,
    ) -> StoreResult:
        """Alias for :meth:`admit` used by simple persistence callers."""

        return self.admit(record, actor_scope=actor_scope, reason=reason, now=now)

    upsert = put
    add = put

    def create(
        self,
        content: str,
        *,
        scope: MemoryScope,
        types: list[str] | str,
        actor_scope: MemoryScope | None = None,
        subtype: str | None = None,
        summary: str | None = None,
        entities: list[str] | None = None,
        relations: list[dict[str, Any]] | None = None,
        source: Mapping[str, Any] | None = None,
        quality: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        security: Mapping[str, Any] | None = None,
        id: str | None = None,
        now: float | None = None,
        ttl_seconds: float | None = None,
        decay_rate: float | None = None,
        reason: str = "created",
    ) -> StoreResult:
        """Construct with config defaults and pass through disclosed admission."""

        if not self.enabled:
            return StoreResult(status="skipped", reason="disabled")
        try:
            record = MemoryEnvelope.create(
                content,
                scope=scope,
                types=types,
                subtype=subtype,
                summary=summary,
                entities=entities,
                relations=relations,
                source=source,
                quality=quality,
                tags=tags,
                security=security,
                id=id,
                now=now,
                ttl_seconds=self.config.default_ttl_seconds if ttl_seconds is None else ttl_seconds,
                decay_rate=self.config.default_decay_rate if decay_rate is None else decay_rate,
                max_tags=max(self.config.max_tags, len(tags or [])),
                allow_secret_classification=True,
            )
        except (TypeError, ValueError) as exc:
            return StoreResult(status="failed", reason="envelope_creation_failed", error=str(exc))
        return self.admit(record, actor_scope=actor_scope, reason=reason, now=now)

    def _transition_unlocked(
        self,
        record_id: str,
        scope: MemoryScope,
        actor_scope: MemoryScope,
        target: LifecycleStatus | str,
        *,
        now: float,
        reason: str,
        force: bool,
        restore: bool,
    ) -> TransitionResult:
        records, load_status, load_error = self._raw_records_unlocked(scope)
        if load_status == "failed":
            return TransitionResult(
                status="failed",
                reason="corrupt_document_preservation_failed",
                error=load_error,
                details={"storage_status": load_status},
            )
        original = next((record for record in records if record.id == record_id), None)
        if original is None:
            return TransitionResult(status="failed", reason="not_found", error=f"memory {record_id!r} not found")
        if not original.scope.permits(actor_scope):
            result = TransitionResult(
                status="refused",
                envelope=original,
                from_status=original.lifecycle.status,
                reason="actor_scope_denied",
            )
            self._merge_transition_audit(result, original, actor_scope, reason, now, force, load_status)
            return result
        if restore:
            result = restore_envelope(original, now=now, reason=reason, force=force)
        else:
            result = transition_envelope(original, target, now=now, reason=reason, force=force)
        if not result.ok or result.envelope is None:
            self._merge_transition_audit(result, original, actor_scope, reason, now, force, load_status)
            return result
        document, _, _ = self._load_unlocked(scope)
        position = next(index for index, existing in enumerate(document.records) if existing.id == record_id)
        previous = document.records[position].model_copy(deep=True)
        document.records[position] = result.envelope
        try:
            self._persist_unlocked(scope, document)
        except Exception as exc:  # noqa: BLE001 - return a disclosed failed transition
            document.records[position] = previous
            failed = TransitionResult(
                status="failed",
                envelope=original,
                from_status=original.lifecycle.status,
                reason="storage_write_failed",
                error=str(exc),
            )
            self._merge_transition_audit(failed, result.envelope, actor_scope, reason, now, force, "failed")
            return failed
        self._index_document(document)
        self._statuses[self._scope_key(scope)] = "succeeded"
        self._merge_transition_audit(result, result.envelope, actor_scope, reason, now, force, "succeeded")
        return result

    def _merge_transition_audit(
        self,
        result: TransitionResult,
        record: MemoryEnvelope,
        actor_scope: MemoryScope,
        reason: str,
        now: float,
        force: bool,
        storage_status: str,
    ) -> None:
        provenance = self._audit(
            "transition",
            record,
            actor_scope,
            reason=reason,
            status=result.status,
            now=now,
            from_status=result.from_status,
            to_status=result.to_status,
            forced=force,
            details={"storage_status": storage_status, "transition_reason": result.reason},
        )
        if provenance.ok:
            result.provenance_status = provenance.status
            return
        if result.provenance_status == "failed":
            result.provenance_error = "; ".join(part for part in (result.provenance_error, provenance.error) if part)
        else:
            result.provenance_status = provenance.status
            result.provenance_error = provenance.error

    def transition(
        self,
        record_id: str,
        target: LifecycleStatus | str,
        *,
        scope: MemoryScope,
        actor_scope: MemoryScope,
        now: float | None = None,
        reason: str = "",
        force: bool = False,
    ) -> TransitionResult:
        """Persist one ordinary adjacent lifecycle transition and audit it."""

        if not self.enabled:
            return TransitionResult(status="skipped", reason="disabled")
        moment = time.time() if now is None else float(now)
        if not math.isfinite(moment):
            return TransitionResult(status="failed", reason="invalid_timestamp", error="now must be finite")
        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            with self._lock_for(scope):
                return self._transition_unlocked(
                    record_id,
                    scope,
                    actor_scope,
                    target,
                    now=moment,
                    reason=reason,
                    force=force,
                    restore=False,
                )

    def restore(
        self,
        record_id: str,
        *,
        scope: MemoryScope,
        actor_scope: MemoryScope,
        now: float | None = None,
        reason: str = "explicit restore",
        force: bool = False,
    ) -> TransitionResult:
        """Persist the explicit cross-level restore operation and audit it."""

        if not self.enabled:
            return TransitionResult(status="skipped", reason="disabled")
        moment = time.time() if now is None else float(now)
        if not math.isfinite(moment):
            return TransitionResult(status="failed", reason="invalid_timestamp", error="now must be finite")
        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            with self._lock_for(scope):
                return self._transition_unlocked(
                    record_id,
                    scope,
                    actor_scope,
                    LifecycleStatus.ACTIVE,
                    now=moment,
                    reason=reason,
                    force=force,
                    restore=True,
                )

    def remove(
        self,
        record_ids: Iterable[str],
        *,
        scope: MemoryScope,
        actor_scope: MemoryScope,
        reason: str,
        now: float | None = None,
        force: bool = False,
    ) -> StoreResult:
        """Hard-delete selected ids atomically, disclosing every pinned refusal."""

        if not self.enabled:
            return StoreResult(status="skipped", reason="disabled")
        moment = time.time() if now is None else float(now)
        if not math.isfinite(moment):
            return StoreResult(status="failed", reason="invalid_timestamp", error="now must be finite")
        requested_ids = list(dict.fromkeys(str(record_id) for record_id in record_ids if str(record_id).strip()))
        if not requested_ids:
            return StoreResult(status="succeeded", reason="no_record_ids", requested=0)
        with self._registry_lock:
            self._hydrate_indexes_unlocked()
            with self._lock_for(scope):
                records, load_status, load_error = self._raw_records_unlocked(scope)
                if load_status == "failed":
                    return StoreResult(
                        status="failed",
                        reason="corrupt_document_preservation_failed",
                        error=load_error,
                        requested=len(requested_ids),
                        storage_status=load_status,
                    )
                by_id = {record.id: record for record in records}
                missing_ids = [record_id for record_id in requested_ids if record_id not in by_id]
                selected = [by_id[record_id] for record_id in requested_ids if record_id in by_id]
                scope_denied = [record.id for record in selected if not record.scope.permits(actor_scope)]
                authorized = [record for record in selected if record.scope.permits(actor_scope)]
                denied = [record.id for record in authorized if record.lifecycle.pinned and not force]
                deletable = [record for record in authorized if not record.lifecycle.pinned or force]
                deleted_ids = {record.id for record in deletable}
                before = [record.model_copy(deep=True) for record in records]
                document, _, _ = self._load_unlocked(scope)
                document.records = [record for record in document.records if record.id not in deleted_ids]
                if deletable:
                    try:
                        self._persist_unlocked(scope, document)
                    except Exception as exc:  # noqa: BLE001
                        document.records = before
                        return StoreResult(
                            status="failed",
                            reason="storage_write_failed",
                            error=str(exc),
                            requested=len(requested_ids),
                            missing_ids=missing_ids,
                            skipped_ids=[*scope_denied, *denied],
                            scope_denied_ids=scope_denied,
                            storage_status=load_status,
                        )
                    self._index_document(document)
                    self._statuses[self._scope_key(scope)] = "succeeded"
                deleted_records = [record.model_copy(deep=True) for record in deletable]
                result = StoreResult(
                    status="succeeded" if deleted_records else "skipped",
                    reason="" if deleted_records else "no_selected_records_deletable",
                    records=deleted_records,
                    requested=len(requested_ids),
                    deleted=len(deleted_records),
                    record_ids=[record.id for record in deleted_records],
                    missing_ids=missing_ids,
                    skipped_ids=[*scope_denied, *denied],
                    scope_denied_ids=scope_denied,
                    forced=force and any(record.lifecycle.pinned for record in deleted_records),
                    storage_status=load_status,
                )
        for record in deleted_records:
            self._merge_audit(
                result,
                self._audit(
                    "forget",
                    record,
                    actor_scope,
                    reason=reason,
                    status="deleted",
                    now=moment,
                    from_status=record.lifecycle.status,
                    to_status=LifecycleStatus.PURGED,
                    forced=record.lifecycle.pinned,
                    details={"hard_delete": True},
                ),
            )
        for record in selected:
            refusal_status = ""
            if record.id in scope_denied:
                refusal_status = "refused_scope"
            elif record.id in denied:
                refusal_status = "refused_pinned"
            if refusal_status:
                self._merge_audit(
                    result,
                    self._audit(
                        "forget",
                        record,
                        actor_scope,
                        reason=reason,
                        status=refusal_status,
                        now=moment,
                        from_status=record.lifecycle.status,
                        to_status=LifecycleStatus.PURGED,
                        forced=False,
                    ),
                )
        return result

    def delete(
        self,
        record_id: str,
        *,
        scope: MemoryScope,
        actor_scope: MemoryScope,
        reason: str = "explicit delete",
        now: float | None = None,
        force: bool = False,
    ) -> StoreResult:
        """Delete one id; the higher-level audited report lives in forget.py."""

        return self.remove(
            [record_id],
            scope=scope,
            actor_scope=actor_scope,
            reason=reason,
            now=now,
            force=force,
        )

    def reset(self) -> None:
        """Drop process-local caches, locks, and indexes without touching files."""

        with self._registry_lock:
            with self._locks_guard:
                self._locks.clear()
            self._cache.clear()
            self._id_index.clear()
            self._duplicate_ids.clear()
            self._statuses.clear()
            self._blocked.clear()


#: Short aliases for callers that use the subsystem name as the store type.
MemoryFabricStore = MemoryEnvelopeStore
FabricStore = MemoryEnvelopeStore

__all__ = [
    "FabricStore",
    "MemoryEnvelopeStore",
    "MemoryFabricStore",
    "StoreReadResult",
    "StoreResult",
    "scope_document_path",
]
