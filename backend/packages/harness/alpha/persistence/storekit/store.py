"""Reusable per-scope document store built from the StoreKit primitives.

``ScopedStore`` is intentionally a small, honest store rather than a second
memory domain.  It owns one versioned JSON document per logical scope, keeps
records in the document's ``records`` array, and rebuilds its index by reading
documents.  There is no persisted side index that can drift from the records
it describes.

Every public read and every read/modify/write sequence is coordinated by two
layers: a per-path re-entrant thread lock for callers inside one interpreter
and a sibling OS file lock for independent processes.  The document is loaded
only while the file lock is held, so two workers cannot both read an old
snapshot and then overwrite one another.  A lock failure is returned as
``unavailable``/``timeout`` rather than being treated as a successful write.

The result objects intentionally resemble the result objects already used by
Alpha's memory stores (``status``, ``reason``, ``error``, counts, ids, and an
``ok`` property).  A store author can therefore adopt the lock/write/migration
seams without changing its public caller-facing vocabulary.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .atomic import atomic_write_json
from .config import StoreKitConfig
from .documents import (
    DocumentEnvelope,
    DocumentLoadResult,
    load_document,
    make_document,
    quarantine_document,
    validate_document,
)
from .locking import FileLock, LockMode, file_lock_path
from .migrations import MigrationRegistry, MigrationResult, migrate_document
from .retention import RetentionBudget, RetentionCandidate, RetentionPolicy, RetentionReport, plan_retention

__all__ = [
    "ScopedStore",
    "StoreIndex",
    "StoreReadResult",
    "StoreResult",
    "StoreUnavailableError",
    "StoreWriteResult",
]

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


class StoreUnavailableError(RuntimeError):
    """Raised by the strict convenience wrappers when a store cannot write."""


@dataclass(slots=True)
class StoreReadResult:
    """Disclosed result of one scope read or point lookup."""

    status: str
    records: list[Any] = field(default_factory=list)
    payload: Any = None
    reason: str = ""
    error: str = ""
    scope: Any = None
    lock_status: str = ""
    lock_reason: str = ""
    preserved_path: Path | None = None
    format_version: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "recovered", "empty", "migration_required"}

    @property
    def protected(self) -> bool:
        return self.lock_status == "acquired"

    @property
    def items(self) -> list[Any]:
        """Alias used by stores whose payload calls records ``items``."""

        return self.records

    @property
    def recovered(self) -> bool:
        return self.status == "recovered"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "records": list(self.records),
            "payload": self.payload,
            "reason": self.reason,
            "error": self.error,
            "scope": str(self.scope) if self.scope is not None else None,
            "lock_status": self.lock_status,
            "lock_reason": self.lock_reason,
            "preserved_path": str(self.preserved_path) if self.preserved_path is not None else None,
            "format_version": self.format_version,
        }


@dataclass(slots=True)
class StoreWriteResult:
    """Disclosed counts and identities from one store mutation."""

    status: str
    reason: str = ""
    error: str = ""
    scope: Any = None
    stored: int = 0
    updated: int = 0
    deleted: int = 0
    record_ids: list[str] = field(default_factory=list)
    evicted_ids: list[str] = field(default_factory=list)
    lock_status: str = ""
    lock_reason: str = ""
    preserved_path: Path | None = None
    format_version: int | None = None
    changed: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "skipped"} and self.reason not in {"lock_unavailable", "lock_timeout"}

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(self.record_ids)

    @property
    def evicted_event_ids(self) -> tuple[str, ...]:
        return tuple(self.evicted_ids)

    @property
    def storage_status(self) -> str:
        return self.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "error": self.error,
            "scope": str(self.scope) if self.scope is not None else None,
            "stored": self.stored,
            "updated": self.updated,
            "deleted": self.deleted,
            "record_ids": list(self.record_ids),
            "evicted_ids": list(self.evicted_ids),
            "lock_status": self.lock_status,
            "lock_reason": self.lock_reason,
            "preserved_path": str(self.preserved_path) if self.preserved_path is not None else None,
            "format_version": self.format_version,
            "changed": self.changed,
        }


#: Existing stores use both names; expose both without a second type.
StoreResult = StoreWriteResult


@dataclass(frozen=True, slots=True)
class StoreIndex:
    """An index rebuilt from the documents present at read time."""

    id_to_scope: Mapping[str, str]
    scope_to_ids: Mapping[str, tuple[str, ...]]
    skipped: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id_to_scope": dict(self.id_to_scope),
            "scope_to_ids": {key: list(value) for key, value in self.scope_to_ids.items()},
            "skipped": list(self.skipped),
        }


@dataclass(slots=True)
class _LoadState:
    document: DocumentEnvelope | None
    load_status: str
    reason: str = ""
    error: str = ""
    preserved_path: Path | None = None
    lock_status: str = ""

    @property
    def usable(self) -> bool:
        return self.load_status in {"empty", "ok", "migrated"}


def _scope_text(scope: Any) -> str:
    if scope is None:
        return "default"
    if isinstance(scope, Mapping):
        value = scope.get("qualified") or scope.get("key") or scope.get("scope_id") or scope.get("id") or scope.get("scope")
    elif isinstance(scope, (tuple, list)) and len(scope) == 2:
        value = f"{scope[0]}:{scope[1]}"
    elif hasattr(scope, "qualified"):
        value = scope.qualified()
    else:
        value = getattr(scope, "id", None) or scope
    text = str(value or "default").strip()
    return text or "default"


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("_", value).strip("._") or "default"
    return cleaned[:120]


def _scope_segment(value: str) -> str:
    """Sanitize a scope without making two different scopes collide."""

    cleaned = _safe_segment(value)
    if cleaned == value and len(value) <= 120:
        return cleaned
    import hashlib

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{cleaned}-{digest}"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number not in {float("inf"), float("-inf")} else default


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


class ScopedStore:
    """Bounded, versioned, thread- and process-safe per-scope store.

    Args:
        root: Directory that contains this store's documents.
        storage_path: Alias for ``root`` for hosts that use the existing
            memory-store constructor vocabulary.
        store_id: Stable directory/name prefix for the store.
        schema_id: Envelope schema id.  Defaults to ``storekit.<store_id>``.
        format_version: Version written for new documents.
        config: StoreKit settings.  The package default is disabled; pass
            ``enabled=True`` or a config with ``enabled=True`` to opt in.
        scope_path: Optional callback returning a document path for a scope.
        lock_path: Optional callback returning a lock path for a scope.
        migration_registry: Optional format migration chain.
        max_documents: Per-scope record bound; overrides the config default.
        max_bytes: Optional per-scope byte bound; overrides the config default.
        record_key: Payload array key used by the record helpers.  ``None``
            enables the generic ``mutate_payload`` surface for documents such
            as entity graphs or social state that are not one record array.
        legacy_loader: Optional adapter from a pre-envelope JSON mapping to a
            checksum-sealed document.  It is a shim seam, not an implicit
            conversion: returning ``None`` leaves normal corruption handling
            in charge.
        serializer: Optional record -> JSON mapping hook for model objects.
        deserializer: Optional JSON mapping -> record hook.
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        storage_path: Path | str | None = None,
        store_id: str = "storekit",
        schema_id: str | None = None,
        format_version: int | None = None,
        target_version: int | None = None,
        config: StoreKitConfig | Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        clock: Callable[[], float] | None = None,
        scope_path: Callable[[Any], Path] | None = None,
        lock_path: Callable[[Any], Path] | None = None,
        migration_registry: MigrationRegistry | None = None,
        max_documents: int | None = None,
        max_bytes: int | None = None,
        record_key: str | None = "records",
        legacy_loader: Callable[[Mapping[str, Any]], DocumentEnvelope | None] | None = None,
        retention_policy: RetentionPolicy | None = None,
        serializer: Callable[[Any], Mapping[str, Any]] | None = None,
        deserializer: Callable[[Mapping[str, Any]], Any] | None = None,
        auto_migrate: bool = True,
    ) -> None:
        if isinstance(config, Mapping):
            config = StoreKitConfig.model_validate(config)
        selected = config or StoreKitConfig()
        if enabled is not None:
            selected = selected.model_copy(update={"enabled": bool(enabled)})
        if max_documents is not None and int(max_documents) < 1:
            raise ValueError("max_documents must be positive")
        if max_bytes is not None and int(max_bytes) < 1:
            raise ValueError("max_bytes must be positive")
        selected_root = root if root is not None else storage_path
        if selected_root is None:
            raise ValueError("ScopedStore requires root or storage_path")
        self.root = Path(selected_root).expanduser().resolve(strict=False)
        self.store_id = _safe_segment(str(store_id or "storekit"))
        self.config = selected
        self.schema_id = str(schema_id or f"storekit.{self.store_id}")
        self.format_version = int(format_version or selected.default_target_format_version)
        self.target_version = int(target_version or self.format_version)
        self.migration_registry = migration_registry
        self.auto_migrate = bool(auto_migrate)
        self._clock = clock or time.time
        self._scope_path = scope_path
        self._lock_path = lock_path
        self._serializer = serializer
        self._deserializer = deserializer
        self.record_key = None if record_key is None else str(record_key)
        if self.record_key == "":
            raise ValueError("record_key must be a non-empty string or None")
        self.legacy_loader = legacy_loader
        self.max_documents = int(max_documents or selected.default_max_documents_per_scope)
        self.max_bytes = None if max_bytes is None else int(max_bytes)
        if self.max_bytes is None:
            self.max_bytes = selected.default_max_bytes_per_scope
        self._retention_policy = retention_policy or RetentionPolicy(
            budget=RetentionBudget(max_bytes=self.max_bytes, max_count=self.max_documents),
            demote_age_seconds=selected.retention_demote_age_seconds,
        )
        self._thread_locks: dict[str, threading.RLock] = {}
        self._thread_locks_guard = threading.Lock()

    # -- identity and paths ------------------------------------------------
    @staticmethod
    def scope_key(scope: Any) -> str:
        return _scope_text(scope)

    def scope_path(self, scope: Any) -> Path:
        """Return the stable document path for a scope."""

        key = self.scope_key(scope)
        if self._scope_path is not None:
            candidate = Path(self._scope_path(scope))
        else:
            candidate = self.root / self.store_id / f"{_scope_segment(key)}.json"
        candidate = candidate.expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("scope document path must remain below the store root") from exc
        return resolved

    def lock_path(self, scope: Any) -> Path:
        """Return the sibling lock path for a scope."""

        if self._lock_path is not None:
            candidate = Path(self._lock_path(scope))
            if not candidate.is_absolute():
                candidate = self.root / candidate
            return candidate.resolve(strict=False)
        return file_lock_path(self.scope_path(scope))

    # -- serialization ----------------------------------------------------
    def _serialize_record(self, record: Any) -> dict[str, Any]:
        if self._serializer is not None:
            value = self._serializer(record)
        elif isinstance(record, Mapping):
            value = dict(record)
        elif hasattr(record, "model_dump"):
            value = record.model_dump(mode="json")
        else:
            raise TypeError("record must be a mapping or expose model_dump(); pass serializer= for other objects")
        if not isinstance(value, Mapping):
            raise TypeError("record serializer must return a mapping")
        return copy.deepcopy(dict(value))

    def _deserialize_record(self, record: Mapping[str, Any]) -> Any:
        if self._deserializer is not None:
            return self._deserializer(record)
        return copy.deepcopy(dict(record))

    @staticmethod
    def _record_id(record: Mapping[str, Any], explicit: str | None = None) -> str:
        value = explicit if explicit is not None else record.get("id")
        if value is None or not str(value):
            raise ValueError("each stored record needs a non-empty id")
        return str(value)

    def _records_from_document(self, document: DocumentEnvelope) -> list[dict[str, Any]]:
        if self.record_key is None:
            return []
        payload = document.payload
        if not isinstance(payload, Mapping):
            raise ValueError("store document payload must be an object")
        raw_records = payload.get(self.record_key, [])
        if not isinstance(raw_records, list):
            raise ValueError(f"store document payload.{self.record_key} must be an array")
        records: list[dict[str, Any]] = []
        for raw in raw_records:
            if not isinstance(raw, Mapping):
                raise ValueError("each stored record must be an object")
            records.append(copy.deepcopy(dict(raw)))
        return records

    # -- locking ----------------------------------------------------------
    def _thread_lock(self, path: Path) -> threading.RLock:
        key = str(path)
        with self._thread_locks_guard:
            lock = self._thread_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._thread_locks[key] = lock
            return lock

    @contextmanager
    def _locked(self, scope: Any, *, exclusive: bool):
        path = self.scope_path(scope)
        thread_lock = self._thread_lock(path)
        with thread_lock:
            mode = LockMode.EXCLUSIVE if exclusive or not self.config.allow_shared_reads else LockMode.READ
            lock = FileLock(
                self.lock_path(scope),
                mode=mode,
                timeout=self.config.lock_timeout_seconds,
                stale_after_seconds=self.config.stale_lock_after_seconds,
                poll_interval_seconds=self.config.lock_poll_interval_seconds,
                enabled=self.config.locking_enabled,
            )
            result = lock.acquire()
            try:
                yield result
            finally:
                if result.acquired:
                    lock.release()

    def _disabled_read(self, scope: Any) -> StoreReadResult:
        return StoreReadResult(status="skipped", reason="disabled", scope=scope, lock_status="not_attempted")

    def _disabled_write(self, scope: Any) -> StoreWriteResult:
        return StoreWriteResult(status="skipped", reason="disabled", scope=scope, lock_status="not_attempted")

    # -- loading and migration --------------------------------------------
    def _load_unlocked(
        self,
        scope: Any,
        *,
        allow_migration: bool = False,
        max_format_version: int | None = None,
    ) -> _LoadState:
        path = self.scope_path(scope)
        maximum = max(self.target_version, self.format_version) if max_format_version is None else int(max_format_version)
        if self.legacy_loader is not None and path.exists():
            try:
                legacy_raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, TypeError, ValueError):
                legacy_raw = None
            if isinstance(legacy_raw, Mapping) and "format_version" not in legacy_raw:
                try:
                    legacy_document = self.legacy_loader(copy.deepcopy(dict(legacy_raw)))
                except Exception as exc:  # noqa: BLE001 - quarantine a failed adapter
                    preserved, error = quarantine_document(path, clock=self._clock)
                    return _LoadState(
                        None,
                        "corrupt_preservation_failed" if error else "corrupt_preserved",
                        reason="legacy_adapter_failed",
                        error=error or str(exc),
                        preserved_path=preserved,
                    )
                if legacy_document is not None:
                    legacy_validation = validate_document(
                        legacy_document.to_dict(),
                        expected_schema_id=self.schema_id,
                        max_format_version=maximum,
                        verify_checksum=True,
                    )
                    if legacy_validation.ok and legacy_validation.document is not None:
                        result = DocumentLoadResult(status="ok", path=path, document=legacy_validation.document, raw=legacy_raw)
                    else:
                        return _LoadState(
                            None,
                            legacy_validation.reason,
                            reason=legacy_validation.reason,
                            error=legacy_validation.error,
                        )
                else:
                    result = load_document(
                        path,
                        expected_schema_id=self.schema_id,
                        max_format_version=maximum,
                        verify_checksum=True,
                        clock=self._clock,
                        quarantine=self.config.preserve_corrupt,
                    )
            else:
                result = load_document(
                    path,
                    expected_schema_id=self.schema_id,
                    max_format_version=maximum,
                    verify_checksum=True,
                    clock=self._clock,
                    quarantine=self.config.preserve_corrupt,
                )
        else:
            result = load_document(
                path,
                expected_schema_id=self.schema_id,
                max_format_version=maximum,
                verify_checksum=True,
                clock=self._clock,
                quarantine=self.config.preserve_corrupt,
            )
        if result.status == "empty":
            return _LoadState(None, "empty", reason="document_absent")
        if result.status in {"unsupported_future_version", "schema_mismatch", "read_error", "corrupt_preservation_failed", "corrupt_preserved", "corrupt_json"}:
            return _LoadState(
                None,
                result.status,
                reason=result.reason,
                error=result.error,
                preserved_path=result.preserved_path,
            )
        document = result.document
        if document is None:
            return _LoadState(None, "corrupt_document", reason=result.reason, error=result.error, preserved_path=result.preserved_path)
        try:
            self._records_from_document(document)
        except (TypeError, ValueError) as exc:
            preserved, error = quarantine_document(path, clock=self._clock)
            return _LoadState(
                None,
                "corrupt_preservation_failed" if error else "corrupt_preserved",
                reason="malformed_payload",
                error=error or str(exc),
                preserved_path=preserved,
            )
        if document.format_version < self.target_version:
            if not allow_migration or self.migration_registry is None:
                return _LoadState(document, "migration_required", reason="format_version_behind_target")
            migrated = migrate_document(
                document,
                self.migration_registry,
                target_version=self.target_version,
                dry_run=False,
                clock=self._clock,
            )
            if not migrated.ok or migrated.document is None:
                return _LoadState(
                    document,
                    "migration_failed",
                    reason=migrated.reason or "migration_failed",
                    error=migrated.error,
                )
            try:
                self._write_document_unlocked(path, migrated.document)
            except Exception as exc:  # noqa: BLE001 - disclose write failure and preserve source
                return _LoadState(document, "migration_write_failed", reason="migration_write_failed", error=str(exc))
            return _LoadState(migrated.document, "migrated", reason="format_migrated")
        if document.format_version > self.target_version:
            return _LoadState(document, "unsupported_future_version", reason="document_newer_than_target")
        return _LoadState(document, "ok")

    def _write_document_unlocked(self, path: Path, document: DocumentEnvelope) -> None:
        sealed = document.sealed(clock=self._clock)
        atomic_write_json(
            path,
            sealed.to_dict(),
            indent=self.config.json_indent,
            fsync=self.config.fsync_policy,
        )

    # -- read surface -----------------------------------------------------
    def read_result(self, scope: Any = "default") -> StoreReadResult:
        """Read one scope with lock, corruption, and migration disclosure."""

        if not self.config.enabled:
            return self._disabled_read(scope)
        try:
            with self._locked(scope, exclusive=self.migration_registry is not None) as lock_result:
                if not lock_result.acquired:
                    return StoreReadResult(
                        status="unavailable",
                        reason="lock_unavailable",
                        error=lock_result.reason or lock_result.status,
                        scope=scope,
                        lock_status=lock_result.status,
                        lock_reason=lock_result.reason,
                    )
                state = self._load_unlocked(scope, allow_migration=self.auto_migrate and self.migration_registry is not None)
                if state.load_status == "empty":
                    return StoreReadResult(status="empty", reason="scope_empty", scope=scope, lock_status=lock_result.status, lock_reason=lock_result.reason)
                if state.load_status in {"corrupt_preserved", "corrupt_preservation_failed", "corrupt_document", "corrupt_json"}:
                    return StoreReadResult(
                        status="recovered" if state.load_status == "corrupt_preserved" else "failed",
                        reason="corrupt_document_preserved" if state.preserved_path else "corrupt_document",
                        error=state.error,
                        scope=scope,
                        lock_status=lock_result.status,
                        lock_reason=lock_result.reason,
                        preserved_path=state.preserved_path,
                    )
                if state.load_status in {"unsupported_future_version", "schema_mismatch", "migration_failed", "migration_write_failed", "read_error"}:
                    return StoreReadResult(
                        status=state.load_status,
                        reason=state.reason,
                        error=state.error,
                        scope=scope,
                        lock_status=lock_result.status,
                        lock_reason=lock_result.reason,
                        preserved_path=state.preserved_path,
                        format_version=state.document.format_version if state.document is not None else None,
                    )
                assert state.document is not None
                records = [self._deserialize_record(record) for record in self._records_from_document(state.document)]
                has_content = bool(records) if self.record_key is not None else bool(state.document.payload)
                status = "succeeded" if has_content else "empty"
                reason = state.reason if state.load_status == "migration_required" else ("scope_empty" if not has_content else "")
                return StoreReadResult(
                    status=status,
                    records=records,
                    payload=copy.deepcopy(state.document.payload),
                    reason=reason,
                    scope=scope,
                    lock_status=lock_result.status,
                    lock_reason=lock_result.reason,
                    preserved_path=state.preserved_path,
                    format_version=state.document.format_version,
                )
        except (OSError, TypeError, ValueError) as exc:
            return StoreReadResult(status="failed", reason="read_failed", error=str(exc), scope=scope)

    def read(self, scope: Any = "default") -> list[Any]:
        return self.read_result(scope).records

    list = read

    def list_result(self, scope: Any = "default") -> StoreReadResult:
        return self.read_result(scope)

    def get_result(self, record_id: str, *, scope: Any | None = None) -> StoreReadResult:
        """Look up one id, scanning documents when no scope is supplied."""

        if not self.config.enabled:
            return self._disabled_read(scope)
        scopes = [scope] if scope is not None else self.iter_scopes()
        for candidate in scopes:
            result = self.read_result(candidate)
            for record in result.records:
                raw_id = record.get("id") if isinstance(record, Mapping) else getattr(record, "id", None)
                if str(raw_id) == str(record_id):
                    return StoreReadResult(
                        status=result.status if result.status != "empty" else "succeeded",
                        records=[record],
                        reason=result.reason,
                        error=result.error,
                        scope=candidate,
                        lock_status=result.lock_status,
                        lock_reason=result.lock_reason,
                        preserved_path=result.preserved_path,
                        format_version=result.format_version,
                    )
        if scope is not None:
            return StoreReadResult(status="empty", reason="not_found", scope=scope, lock_status="acquired")
        return StoreReadResult(status="empty", reason="not_found", lock_status="acquired")

    def get(self, record_id: str, *, scope: Any | None = None) -> Any | None:
        result = self.get_result(record_id, scope=scope)
        return result.records[0] if result.records else None

    def count(self, scope: Any = "default") -> int:
        return len(self.read(scope))

    def read_payload_result(self, scope: Any = "default") -> StoreReadResult:
        """Read a document payload, including non-record-array stores."""

        return self.read_result(scope)

    def read_payload(self, scope: Any = "default") -> Any:
        return self.read_result(scope).payload

    # -- mutation ---------------------------------------------------------
    def _candidate(self, record: Mapping[str, Any], now: float) -> RetentionCandidate:
        created = _finite(record.get("created_at", record.get("updated_at", now)), now)
        return RetentionCandidate(
            key=str(record.get("id", "")),
            size_bytes=_json_size(record),
            age_seconds=max(0.0, now - created),
            importance=_finite(record.get("importance", 0.0)),
            pinned=bool(record.get("pinned", False)),
            tier=str(record.get("tier", "active")),
        )

    def _bounded_records(
        self,
        records: list[dict[str, Any]],
        *,
        now: float,
        max_documents: int | None = None,
        max_bytes: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], RetentionReport]:
        budget = RetentionBudget(
            max_bytes=max_bytes,
            max_count=max_documents if max_documents is not None else self.max_documents,
        )
        report = plan_retention(
            [self._candidate(record, now) for record in records],
            budget,
            policy=self._retention_policy,
        )
        evicted = set(report.keys_to_evict)
        kept = [record for record in records if str(record.get("id", "")) not in evicted]
        if report.over_budget:
            raise ValueError(f"retention_budget_exceeded: {report.reason}")
        return kept, sorted(evicted), report

    def mutate_payload(
        self,
        scope: Any,
        mutator: Callable[[dict[str, Any]], Any],
    ) -> StoreWriteResult:
        """Read/modify/write an arbitrary JSON payload under one lock.

        This is the direct seam for entity graphs, social state, and other
        documents whose invariants span several arrays.  The mutator receives
        a detached mapping and may return a replacement mapping or ``None`` to
        keep its in-place changes.  No generic record retention is applied;
        the owning store remains responsible for its domain-specific bounds.
        """

        if not self.config.enabled:
            return self._disabled_write(scope)
        try:
            with self._locked(scope, exclusive=True) as lock_result:
                if not lock_result.acquired:
                    status = "timeout" if lock_result.status == "timeout" else "unavailable"
                    return StoreWriteResult(
                        status=status,
                        reason="lock_unavailable" if status == "unavailable" else "lock_timeout",
                        error=lock_result.reason,
                        scope=scope,
                        lock_status=lock_result.status,
                        lock_reason=lock_result.reason,
                    )
                state = self._load_unlocked(scope, allow_migration=self.auto_migrate and self.migration_registry is not None)
                if state.load_status == "empty":
                    document = make_document({}, format_version=self.format_version, schema_id=self.schema_id, clock=self._clock, document_id=self.scope_key(scope))
                    before: Any = {}
                elif state.document is not None and state.load_status in {"ok", "migrated", "migration_required"}:
                    if state.load_status == "migration_required" and self.migration_registry is None:
                        return StoreWriteResult(status="failed", reason="migration_required", error="no migration registry is registered", scope=scope, lock_status=lock_result.status, lock_reason=lock_result.reason)
                    document = state.document
                    before = copy.deepcopy(document.payload)
                else:
                    return StoreWriteResult(status="failed", reason=state.reason or state.load_status, error=state.error, scope=scope, lock_status=lock_result.status, lock_reason=lock_result.reason, preserved_path=state.preserved_path)
                working = copy.deepcopy(document.payload) if isinstance(document.payload, Mapping) else {}
                if not isinstance(working, dict):
                    return StoreWriteResult(status="failed", reason="invalid_payload", error="payload must be a JSON object", scope=scope, lock_status=lock_result.status, lock_reason=lock_result.reason)
                outcome = mutator(working)
                selected = working if outcome is None else outcome
                if not isinstance(selected, Mapping):
                    return StoreWriteResult(status="failed", reason="invalid_payload", error="payload mutator must return an object or None", scope=scope, lock_status=lock_result.status, lock_reason=lock_result.reason)
                updated = document.with_payload(dict(selected), clock=self._clock)
                self._write_document_unlocked(self.scope_path(scope), updated)
                return StoreWriteResult(status="succeeded", scope=scope, changed=selected != before, lock_status=lock_result.status, lock_reason=lock_result.reason, format_version=updated.format_version)
        except Exception as exc:  # noqa: BLE001 - storage errors are disclosed data
            return StoreWriteResult(status="failed", reason="storage_write_failed", error=str(exc), scope=scope)

    update_payload = mutate_payload

    def _mutate_records(
        self,
        scope: Any,
        mutator: Callable[[list[dict[str, Any]]], Any],
        *,
        max_documents: int | None = None,
        max_bytes: int | None = None,
    ) -> StoreWriteResult:
        if not self.config.enabled:
            return self._disabled_write(scope)
        if self.record_key is None:
            return StoreWriteResult(status="failed", reason="record_helpers_disabled", error="construct the store with record_key or use mutate_payload", scope=scope)
        try:
            with self._locked(scope, exclusive=True) as lock_result:
                if not lock_result.acquired:
                    status = "timeout" if lock_result.status == "timeout" else "unavailable"
                    return StoreWriteResult(
                        status=status,
                        reason="lock_unavailable" if status == "unavailable" else "lock_timeout",
                        error=lock_result.reason,
                        scope=scope,
                        lock_status=lock_result.status,
                        lock_reason=lock_result.reason,
                    )
                state = self._load_unlocked(scope, allow_migration=self.auto_migrate and self.migration_registry is not None)
                if state.load_status == "empty":
                    previous: list[dict[str, Any]] = []
                    document = make_document({"records": []}, format_version=self.format_version, schema_id=self.schema_id, clock=self._clock, document_id=self.scope_key(scope))
                elif state.document is not None and state.load_status in {"ok", "migrated", "migration_required"}:
                    if state.load_status == "migration_required" and self.migration_registry is None:
                        return StoreWriteResult(
                            status="failed",
                            reason="migration_required",
                            error="no migration registry is registered for this store",
                            scope=scope,
                            lock_status=lock_result.status,
                            lock_reason=lock_result.reason,
                        )
                    previous = self._records_from_document(state.document)
                    document = state.document
                else:
                    return StoreWriteResult(
                        status="failed",
                        reason=state.reason or state.load_status,
                        error=state.error,
                        scope=scope,
                        lock_status=lock_result.status,
                        lock_reason=lock_result.reason,
                        preserved_path=state.preserved_path,
                    )
                before_ids = [str(record.get("id", "")) for record in previous]
                working = copy.deepcopy(previous)
                outcome = mutator(working)
                metadata: Mapping[str, Any] = {}
                if isinstance(outcome, tuple) and len(outcome) == 2 and isinstance(outcome[1], Mapping):
                    working, metadata = list(outcome[0]), outcome[1]
                elif outcome is not None:
                    working = list(outcome)
                if not isinstance(working, list) or any(not isinstance(item, Mapping) for item in working):
                    raise ValueError("store mutator must produce a list of record mappings")
                normalized = [copy.deepcopy(dict(item)) for item in working]
                now = _finite(self._clock(), time.time())
                kept, evicted, _report = self._bounded_records(normalized, now=now, max_documents=max_documents, max_bytes=max_bytes)
                payload_base = dict(document.payload) if isinstance(document.payload, Mapping) else {}
                payload_base[self.record_key] = kept
                updated = document.with_payload(payload_base, clock=self._clock)
                self._write_document_unlocked(self.scope_path(scope), updated)
                after_ids = [str(record.get("id", "")) for record in kept]
                stored = len([item for item in after_ids if item not in before_ids])
                deleted = max(0, len(before_ids) - len(after_ids) - len(evicted))
                return StoreWriteResult(
                    status="succeeded",
                    reason=str(metadata.get("reason", "")),
                    scope=scope,
                    stored=stored,
                    updated=len(after_ids) - stored,
                    deleted=int(metadata.get("deleted", deleted)),
                    record_ids=after_ids,
                    evicted_ids=evicted,
                    lock_status=lock_result.status,
                    lock_reason=lock_result.reason,
                    format_version=updated.format_version,
                    changed=normalized != previous or after_ids != before_ids,
                )
        except Exception as exc:  # noqa: BLE001 - storage errors are disclosed data
            return StoreWriteResult(status="failed", reason="storage_write_failed", error=str(exc), scope=scope)

    def mutate(
        self,
        scope: Any,
        mutator: Callable[[list[dict[str, Any]]], Any],
        *,
        max_documents: int | None = None,
        max_bytes: int | None = None,
    ) -> StoreWriteResult:
        """Read/modify/write under one exclusive cross-process lock."""

        return self._mutate_records(scope, mutator, max_documents=max_documents, max_bytes=max_bytes)

    def put(
        self,
        record: Any,
        scope: Any = "default",
        *,
        record_id: str | None = None,
        max_documents: int | None = None,
        max_bytes: int | None = None,
    ) -> StoreWriteResult:
        """Add or replace one record by id."""

        try:
            serialized = self._serialize_record(record)
            identifier = self._record_id(serialized, record_id)
            serialized["id"] = identifier
        except (TypeError, ValueError) as exc:
            return StoreWriteResult(status="failed", reason="invalid_record", error=str(exc), scope=scope)

        def apply(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            replaced = False
            for index, existing in enumerate(records):
                if str(existing.get("id", "")) == identifier:
                    records[index] = copy.deepcopy(serialized)
                    replaced = True
                    break
            if not replaced:
                records.append(copy.deepcopy(serialized))
            return records, {"reason": "replaced" if replaced else "created"}

        result = self._mutate_records(scope, apply, max_documents=max_documents, max_bytes=max_bytes)
        if result.status == "succeeded":
            result.updated = 1 if result.reason == "replaced" else 0
            result.stored = 0 if result.reason == "replaced" else 1
            result.record_ids = [identifier]
        return result

    upsert = put
    add = put
    write = put
    save = put

    def put_records(
        self,
        records: Iterable[Any],
        scope: Any = "default",
        *,
        max_documents: int | None = None,
        max_bytes: int | None = None,
    ) -> StoreWriteResult:
        """Add or replace several records in one locked transaction."""

        try:
            serialized = [self._serialize_record(record) for record in records]
            for record in serialized:
                record["id"] = self._record_id(record)
        except (TypeError, ValueError) as exc:
            return StoreWriteResult(status="failed", reason="invalid_record", error=str(exc), scope=scope)

        def apply(existing: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            positions = {str(item.get("id", "")): index for index, item in enumerate(existing)}
            for record in serialized:
                identifier = str(record["id"])
                if identifier in positions:
                    existing[positions[identifier]] = copy.deepcopy(record)
                else:
                    positions[identifier] = len(existing)
                    existing.append(copy.deepcopy(record))
            return existing, {"reason": "upserted"}

        return self._mutate_records(scope, apply, max_documents=max_documents, max_bytes=max_bytes)

    put_many = put_records
    upsert_records = put_records
    append = put_records

    def delete(self, record_id: str, scope: Any = "default") -> StoreWriteResult:
        return self.delete_records([record_id], scope)

    def remove(self, record_ids: Iterable[str], scope: Any = "default") -> StoreWriteResult:
        return self.delete_records(record_ids, scope)

    def delete_records(self, record_ids: Iterable[str], scope: Any = "default") -> StoreWriteResult:
        wanted = {str(identifier) for identifier in record_ids if str(identifier)}
        if not wanted:
            return StoreWriteResult(status="succeeded", reason="no_record_ids", scope=scope)

        def apply(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            kept = [record for record in records if str(record.get("id", "")) not in wanted]
            removed = len(records) - len(kept)
            return kept, {"reason": "deleted", "deleted": removed}

        result = self._mutate_records(scope, apply)
        if result.status == "succeeded":
            result.record_ids = sorted(wanted)
        return result

    remove_records = delete_records

    # -- index and footprint ---------------------------------------------
    def iter_scope_paths(self) -> list[Path]:
        directory = self.root / self.store_id
        if not directory.exists():
            return []
        return sorted(path for path in directory.rglob("*.json") if path.is_file() and ".corrupt-" not in path.name and not path.name.endswith(".lock"))

    def iter_scopes(self) -> list[str]:
        return [path.stem for path in self.iter_scope_paths()]

    def index(self) -> StoreIndex:
        """Rebuild an id/scope index from the documents currently on disk."""

        id_to_scope: dict[str, str] = {}
        scope_to_ids: dict[str, tuple[str, ...]] = {}
        skipped: list[str] = []
        for path in self.iter_scope_paths():
            scope = path.stem
            with self._locked(scope, exclusive=self.migration_registry is not None) as lock_result:
                if not lock_result.acquired:
                    skipped.append(f"{path}:{lock_result.status}")
                    continue
                state = self._load_unlocked(scope, allow_migration=False)
                if not state.usable or state.document is None:
                    skipped.append(f"{path}:{state.load_status}")
                    continue
                try:
                    ids = tuple(str(record.get("id", "")) for record in self._records_from_document(state.document) if record.get("id") is not None)
                except (TypeError, ValueError) as exc:
                    skipped.append(f"{path}:{exc}")
                    continue
                scope_to_ids[scope] = ids
                for identifier in ids:
                    id_to_scope[identifier] = scope
        return StoreIndex(id_to_scope=id_to_scope, scope_to_ids=scope_to_ids, skipped=tuple(skipped))

    def scope_index(self) -> dict[str, tuple[str, ...]]:
        return dict(self.index().scope_to_ids)

    def id_index(self) -> dict[str, str]:
        return dict(self.index().id_to_scope)

    def footprint(self) -> dict[str, Any]:
        """Return the store's own file/byte footprint without caching it."""

        files = 0
        total = 0
        for path in self.iter_scope_paths():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            files += 1
            total += size
        return {"files": files, "bytes": total, "root": str(self.root / self.store_id)}

    # -- retention --------------------------------------------------------
    def retention_report(self, scope: Any = "default", *, max_documents: int | None = None, max_bytes: int | None = None) -> RetentionReport:
        """Return a proposal without deleting anything."""

        result = self.read_result(scope)
        if result.status in {"failed", "unavailable", "skipped"}:
            return plan_retention([], RetentionBudget(max_bytes=max_bytes, max_count=max_documents))
        now = _finite(self._clock(), time.time())
        records = [record if isinstance(record, Mapping) else {"id": getattr(record, "id", ""), "content": record} for record in result.records]
        return plan_retention(
            [self._candidate(dict(record), now) for record in records],
            RetentionBudget(max_bytes=max_bytes, max_count=max_documents if max_documents is not None else self.max_documents),
            policy=self._retention_policy,
        )

    def apply_retention(self, scope: Any = "default", *, max_documents: int | None = None, max_bytes: int | None = None) -> StoreWriteResult:
        """Apply the deterministic proposal inside the store's transaction."""

        def apply(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return records

        result = self._mutate_records(scope, apply, max_documents=max_documents, max_bytes=max_bytes)
        return result

    # -- explicit migration ----------------------------------------------
    def migrate(self, scope: Any = "default", *, target_version: int | None = None, dry_run: bool | None = None) -> MigrationResult:
        """Migrate one scope; the default is the configured dry-run policy."""

        if not self.config.enabled:
            original = None
            return MigrationResult(status="skipped", document=None, original=original, target_version=target_version or self.target_version, reason="disabled", dry_run=bool(dry_run if dry_run is not None else self.config.dry_run))
        if self.migration_registry is None:
            return MigrationResult(status="refused", document=None, original=None, target_version=target_version or self.target_version, reason="no_migration_registry", dry_run=bool(dry_run if dry_run is not None else self.config.dry_run))
        target = int(target_version or self.target_version)
        use_dry_run = self.config.dry_run if dry_run is None else bool(dry_run)
        try:
            with self._locked(scope, exclusive=True) as lock_result:
                if not lock_result.acquired:
                    return MigrationResult(status="refused", document=None, original=None, target_version=target, reason="lock_unavailable", error=lock_result.reason, dry_run=use_dry_run)
                state = self._load_unlocked(
                    scope,
                    allow_migration=False,
                    max_format_version=self.migration_registry.latest_version,
                )
                if state.load_status == "empty":
                    return MigrationResult(status="skipped", document=None, original=None, target_version=target, reason="document_absent", dry_run=use_dry_run)
                if state.document is None:
                    return MigrationResult(status="refused", document=None, original=None, target_version=target, reason=state.reason, error=state.error, dry_run=use_dry_run)
                result = migrate_document(state.document, self.migration_registry, target_version=target, dry_run=use_dry_run, clock=self._clock)
                if result.ok and not use_dry_run and result.document is not None:
                    self._write_document_unlocked(self.scope_path(scope), result.document)
                return result
        except Exception as exc:  # noqa: BLE001 - migration persistence is disclosed
            return MigrationResult(status="failed", document=None, original=None, target_version=target, reason="migration_storage_failed", error=str(exc), dry_run=use_dry_run)

    def migrate_all(self, *, target_version: int | None = None, dry_run: bool | None = None) -> tuple[MigrationResult, ...]:
        return tuple(self.migrate(scope, target_version=target_version, dry_run=dry_run) for scope in self.iter_scopes())

    # -- lifecycle --------------------------------------------------------
    def reset(self) -> None:
        """Drop process-local thread locks and cached state; files stay put."""

        with self._thread_locks_guard:
            self._thread_locks.clear()

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)
