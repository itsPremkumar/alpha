"""Thread-safe, bounded, per-user affect-event store."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .models import AffectEvent
from .paths import affective_root, atomic_write_text, events_path

logger = logging.getLogger(__name__)

_SCHEMA = 1


class StoreUnavailableError(RuntimeError):
    """Raised when a write could proceed without risking unreadable data."""


@dataclass(frozen=True, slots=True)
class StoreWriteResult:
    """Accepted and evicted event identifiers from one store mutation."""

    stored: int
    event_ids: tuple[str, ...]
    evicted_event_ids: tuple[str, ...]


@dataclass(slots=True)
class _UserDocument:
    events: list[AffectEvent]


class AffectiveEventStore:
    """Atomic per-user JSON store with deterministic bounded retention.

    Locks and caches are process-local. Two independent store instances aimed
    at the same file can still lose an update; cross-process coordination is
    intentionally outside this self-contained subsystem.
    """

    def __init__(self, storage_path: str | None = None) -> None:
        self._root = affective_root(storage_path)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._cache: dict[str, _UserDocument] = {}
        self._statuses: dict[str, str] = {}

    def _user_value(self, user_id: str | None) -> str:
        return user_id or ""

    def _key(self, user_id: str | None) -> str:
        # Sanitized user segments can alias. Scope locks/statuses by the actual
        # path so aliased identities serialize, while event-level filtering
        # below prevents one identity from reading the other's records.
        return str(events_path(self._root, user_id))

    def _lock_for(self, user_id: str | None) -> threading.RLock:
        key = self._key(user_id)
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def _path(self, user_id: str | None) -> Path:
        return events_path(self._root, user_id)

    def _mark_bad(self, user_id: str | None, status: str) -> None:
        self._statuses[self._key(user_id)] = status

    def _load(self, user_id: str | None) -> _UserDocument:
        key = self._key(user_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        document = _UserDocument(events=[])
        path = self._path(user_id)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
                    raise ValueError("event document must contain an events array")
                document.events = [AffectEvent.model_validate(item) for item in raw["events"]]
                self._statuses[key] = "ok"
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                backup = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
                try:
                    path.replace(backup)
                except OSError as backup_exc:
                    self._mark_bad(user_id, "corrupt_document_unpreserved")
                    logger.error(
                        "Affective store: corrupt document %s could not be preserved as %s (%s; original error: %s)",
                        path,
                        backup.name,
                        backup_exc,
                        exc,
                    )
                else:
                    self._mark_bad(user_id, "corrupt_document_preserved")
                    logger.error(
                        "Affective store: corrupt document %s preserved as %s; current scope starts empty (%s)",
                        path,
                        backup.name,
                        exc,
                    )
            except OSError as exc:
                self._mark_bad(user_id, "read_error")
                logger.error("Affective store: could not read %s (%s)", path, exc)

        self._cache[key] = document
        return document

    def _persist(self, user_id: str | None, document: _UserDocument) -> None:
        payload = {
            "schema": _SCHEMA,
            "events": [event.model_dump(mode="json") for event in document.events],
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=1)
        atomic_write_text(self._path(user_id), serialized)

    def list_events(self, user_id: str | None = None) -> list[AffectEvent]:
        """Return a deep snapshot of one user's events."""
        with self._lock_for(user_id):
            document = self._load(user_id)
            expected_user = self._user_value(user_id)
            return [event.model_copy(deep=True) for event in document.events if self._user_value(event.user_id) == expected_user]

    def get(self, event_id: str, *, user_id: str | None = None) -> AffectEvent | None:
        expected_user = self._user_value(user_id)
        with self._lock_for(user_id):
            for event in self._load(user_id).events:
                if self._user_value(event.user_id) == expected_user and event.id == event_id:
                    return event.model_copy(deep=True)
        return None

    def count(self, user_id: str | None = None) -> int:
        expected_user = self._user_value(user_id)
        with self._lock_for(user_id):
            return sum(self._user_value(event.user_id) == expected_user for event in self._load(user_id).events)

    def append(
        self,
        events: list[AffectEvent],
        *,
        user_id: str | None = None,
        max_events: int,
    ) -> StoreWriteResult:
        """Append/replace events and evict deterministically beyond ``max_events``.

        Eviction priority is lowest intensity first, then oldest creation time,
        then event id. Thus equal-intensity ties retain the newest event.
        """
        if max_events < 1:
            raise ValueError("max_events must be positive")
        if not events:
            return StoreWriteResult(stored=0, event_ids=(), evicted_event_ids=())
        expected_user = self._user_value(user_id)
        if any(self._user_value(event.user_id) != expected_user for event in events):
            raise ValueError("all events must match the store user scope")

        with self._lock_for(user_id):
            document = self._load(user_id)
            status = self.read_status(user_id)
            if status in ("read_error", "corrupt_document_unpreserved"):
                raise StoreUnavailableError(status)

            before = [event.model_copy(deep=True) for event in document.events]
            other_users = [event for event in document.events if self._user_value(event.user_id) != expected_user]
            scoped_events = [event for event in document.events if self._user_value(event.user_id) == expected_user]
            document.events = scoped_events
            by_id = {event.id: index for index, event in enumerate(document.events)}
            accepted_ids: list[str] = []
            stored = 0
            for event in events:
                copied = event.model_copy(deep=True)
                existing = by_id.get(copied.id)
                if existing is None:
                    document.events.append(copied)
                    by_id[copied.id] = len(document.events) - 1
                    stored += 1
                else:
                    document.events[existing] = copied
                accepted_ids.append(copied.id)

            overflow = max(0, len(document.events) - max_events)
            evicted: list[AffectEvent] = []
            if overflow:
                ranked = sorted(
                    document.events,
                    key=lambda event: (event.intensity, event.created_at, event.id),
                )
                eviction_order = ranked[:overflow]
                evicted_ids = {event.id for event in eviction_order}
                evicted = eviction_order
                document.events = [event for event in document.events if event.id not in evicted_ids]

            document.events = other_users + document.events
            try:
                self._persist(user_id, document)
            except Exception:
                document.events = before
                raise
            return StoreWriteResult(
                stored=stored,
                event_ids=tuple(accepted_ids),
                evicted_event_ids=tuple(event.id for event in evicted),
            )

    def delete(self, event_ids: list[str], *, user_id: str | None = None) -> int:
        """Delete selected ids from one user and return the removed count."""
        if not event_ids:
            return 0
        with self._lock_for(user_id):
            document = self._load(user_id)
            if self.read_status(user_id) in ("read_error", "corrupt_document_unpreserved"):
                raise StoreUnavailableError(self.read_status(user_id))
            wanted = set(event_ids)
            expected_user = self._user_value(user_id)
            before = sum(self._user_value(event.user_id) == expected_user for event in document.events)
            document.events = [event for event in document.events if self._user_value(event.user_id) != expected_user or event.id not in wanted]
            removed = before - sum(self._user_value(event.user_id) == expected_user for event in document.events)
            if removed:
                self._persist(user_id, document)
            return removed

    def read_status(self, user_id: str | None = None) -> str:
        """Return the latest disclosure for this user's document load."""
        key = self._key(user_id)
        if key not in self._statuses:
            with self._lock_for(user_id):
                self._load(user_id)
        return self._statuses.get(key, "ok")

    def reset(self) -> None:
        """Drop caches and locks (test/configuration lifecycle helper)."""
        with self._locks_guard:
            self._cache.clear()
            self._locks.clear()
            self._statuses.clear()

    @property
    def root(self) -> Path:
        return self._root


__all__ = ["AffectiveEventStore", "StoreUnavailableError", "StoreWriteResult"]
