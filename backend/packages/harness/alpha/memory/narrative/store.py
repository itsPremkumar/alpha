"""Thread-safe, atomic, bounded per-scope narrative event/story storage.

One scope is one logical ``(scope type, scope id)`` namespace.  Events and the
synthesized story use separate JSON documents so a failed story write cannot
damage the source timeline.  Locks are per scope and process-local; atomic
replacement prevents torn documents, while independent processes remain outside
this subsystem's transaction boundary.

The events document carries a ``schema`` marker and is gated: a document that
parses but declares a format this build does not implement is refused, kept
byte-for-byte, and never written over.  The synthesized story document carries
no marker of its own -- it is a derived view of the events, regenerated on
demand -- so it keeps its existing shape-only validation.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.memory._store_format import (
    STORE_FORMAT_UNSUPPORTED,
    StoreFormatVerdict,
    classify_store_format,
    format_disclosure,
)

from .config import NarrativeConfig
from .models import NarrativeEvent, StoryDocument
from .paths import (
    atomic_write_text,
    events_path,
    narrative_root,
    scope_key,
    story_path,
)

logger = logging.getLogger(__name__)

_SCHEMA = 1
_STORE_ID = "narrative.events"


class StoreUnavailableError(RuntimeError):
    """Raised when a write could risk replacing an unreadable document."""


@dataclass(frozen=True, slots=True)
class StoreWriteResult:
    """Identifiers and counts from one event mutation."""

    stored: int = 0
    updated: int = 0
    event_ids: tuple[str, ...] = ()
    evicted_event_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class _ScopeState:
    events: list[NarrativeEvent]
    story: StoryDocument | None
    event_status: str = "ok"
    story_status: str = "ok"
    events_loaded: bool = False
    story_loaded: bool = False


class NarrativeStore:
    """Bounded source-event store plus one generated story per scope."""

    def __init__(
        self,
        storage_path: str | NarrativeConfig | None = None,
        *,
        config: NarrativeConfig | None = None,
    ) -> None:
        if isinstance(storage_path, NarrativeConfig):
            if config is not None:
                raise ValueError("pass NarrativeConfig as storage_path or config, not both")
            config = storage_path
            storage_path = None
        if config is None:
            config = NarrativeConfig(storage_path=storage_path if isinstance(storage_path, str) else None)
        self.config = config
        self._root = narrative_root(storage_path if storage_path is not None else config.storage_path)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._states: dict[str, _ScopeState] = {}
        self._blocked: set[str] = set()
        self._format_refusals: dict[str, StoreFormatVerdict] = {}

    @property
    def root(self) -> Path:
        return self._root

    def _lock_for(self, scope: Any, scope_id: str | None = None) -> threading.RLock:
        key = scope_key(scope, scope_id)
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    @contextmanager
    def scope_lock(self, scope: Any, scope_id: str | None = None):
        """Hold the same per-scope lock across a compound regeneration."""

        lock = self._lock_for(scope, scope_id)
        with lock:
            yield

    def _state(self, scope: Any, scope_id: str | None = None) -> tuple[str, _ScopeState]:
        key = scope_key(scope, scope_id)
        state = self._states.get(key)
        if state is None:
            state = _ScopeState(events=[], story=None)
            self._states[key] = state
        return key, state

    def _event_path(self, scope: Any, scope_id: str | None = None) -> Path:
        return events_path(self._root, scope, scope_id)

    def _story_path(self, scope: Any, scope_id: str | None = None) -> Path:
        return story_path(self._root, scope, scope_id)

    @staticmethod
    def _preserve_corrupt(path: Path, state: _ScopeState, kind: str, exc: Exception) -> bool:
        backup = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
        try:
            path.replace(backup)
        except OSError as backup_exc:
            state.event_status = "corrupt_document_unpreserved" if kind == "events" else state.event_status
            state.story_status = "corrupt_document_unpreserved" if kind == "story" else state.story_status
            logger.error(
                "Narrative store: corrupt %s document %s could not be preserved as %s (%s; original error: %s)",
                kind,
                path,
                backup.name,
                backup_exc,
                exc,
            )
            return False
        state.event_status = "corrupt_document_preserved" if kind == "events" else state.event_status
        state.story_status = "corrupt_document_preserved" if kind == "story" else state.story_status
        logger.error(
            "Narrative store: corrupt %s document %s preserved as %s; scope starts empty (%s)",
            kind,
            path,
            backup.name,
            exc,
        )
        return True

    def _load_events(self, scope: Any, scope_id: str | None = None) -> _ScopeState:
        key, state = self._state(scope, scope_id)
        if state.events_loaded:
            return state
        state.events_loaded = True
        path = self._event_path(scope, scope_id)
        if not path.exists():
            state.event_status = "ok"
            return state
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            state.events = []
            if isinstance(exc, OSError):
                state.event_status = "read_error"
                self._blocked.add(key)
                logger.error("Narrative store: could not read event document %s (%s)", path, exc)
            elif not self._preserve_corrupt(path, state, "events", exc):
                self._blocked.add(key)
            return state

        # The bytes parsed. A marker this build does not implement means another
        # build owns the file; quarantining it would destroy that data.
        verdict = classify_store_format(store=_STORE_ID, path=path, raw=raw, supported_version=_SCHEMA)
        if verdict.refusal:
            self._format_refusals[key] = verdict
            state.events = []
            state.event_status = STORE_FORMAT_UNSUPPORTED
            self._blocked.add(key)
            logger.error(
                "Narrative store: refusing %s (%s); document left in place and writes blocked",
                verdict.path,
                format_disclosure(verdict),
            )
            return state
        try:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("events"), list):
                raise ValueError("event document must contain an events array")
            state.events = [NarrativeEvent.model_validate(item) for item in raw["events"]]
            state.event_status = "ok"
        except (ValueError, TypeError) as exc:
            state.events = []
            if not self._preserve_corrupt(path, state, "events", exc):
                self._blocked.add(key)
        return state

    def format_refusal(self, scope: Any = "user", scope_id: str | None = None) -> StoreFormatVerdict | None:
        """Return the version refusal held for a scope, or ``None``."""

        return self._format_refusals.get(scope_key(scope, scope_id))

    def _load_story(self, scope: Any, scope_id: str | None = None) -> _ScopeState:
        key, state = self._state(scope, scope_id)
        if state.story_loaded:
            return state
        state.story_loaded = True
        path = self._story_path(scope, scope_id)
        if not path.exists():
            state.story_status = "ok"
            return state
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or not isinstance(raw.get("chapters"), list):
                raise ValueError("story document must contain a chapters array")
            state.story = StoryDocument.model_validate(raw)
            state.story_status = "ok"
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            state.story = None
            if not self._preserve_corrupt(path, state, "story", exc):
                self._blocked.add(key)
        except OSError as exc:
            state.story = None
            state.story_status = "read_error"
            self._blocked.add(key)
            logger.error("Narrative store: could not read story document %s (%s)", path, exc)
        return state

    def _require_writable(self, key: str, state: _ScopeState, kind: str) -> None:
        status = state.event_status if kind == "events" else state.story_status
        if key in self._blocked or status in {"read_error", "corrupt_document_unperserved"}:
            raise StoreUnavailableError(status)

    def _persist_events(self, scope: Any, scope_id: str | None, state: _ScopeState) -> None:
        payload = {
            "schema": _SCHEMA,
            "events": [event.model_dump(mode="json") for event in state.events],
        }
        atomic_write_text(
            self._event_path(scope, scope_id),
            json.dumps(payload, ensure_ascii=False, indent=1),
        )

    def _persist_story(self, scope: Any, scope_id: str | None, state: _ScopeState) -> None:
        if state.story is None:
            raise ValueError("cannot persist a missing story")
        payload = state.story.model_dump(mode="json")
        atomic_write_text(
            self._story_path(scope, scope_id),
            json.dumps(payload, ensure_ascii=False, indent=1),
        )

    def read_status(self, scope: Any = "user", scope_id: str | None = None) -> str:
        """Return the latest event-document disclosure for a scope."""

        with self._lock_for(scope, scope_id):
            return self._load_events(scope, scope_id).event_status

    def story_status(self, scope: Any = "user", scope_id: str | None = None) -> str:
        """Return the latest story-document disclosure for a scope."""

        with self._lock_for(scope, scope_id):
            return self._load_story(scope, scope_id).story_status

    def list_events(
        self,
        scope: Any = "user",
        start: float | None = None,
        end: float | None = None,
        *,
        scope_id: str | None = None,
    ) -> list[NarrativeEvent]:
        """Return a deep chronological snapshot, optionally filtered by overlap."""

        with self._lock_for(scope, scope_id):
            events = [event.model_copy(deep=True) for event in self._load_events(scope, scope_id).events]
        if start is not None and end is not None and start > end:
            return []
        if start is not None:
            events = [event for event in events if event.period_end >= start]
        if end is not None:
            events = [event for event in events if event.period_start <= end]
        return sorted(events, key=lambda event: (event.period_start, event.period_end, event.id))

    def events(self, scope: Any = "user", **kwargs: Any) -> list[NarrativeEvent]:
        """Alias for :meth:`list_events`."""

        return self.list_events(scope, **kwargs)

    def count(self, scope: Any = "user", *, scope_id: str | None = None) -> int:
        with self._lock_for(scope, scope_id):
            return len(self._load_events(scope, scope_id).events)

    def get_event(
        self,
        event_id: str,
        scope: Any = "user",
        *,
        scope_id: str | None = None,
    ) -> NarrativeEvent | None:
        with self._lock_for(scope, scope_id):
            for event in self._load_events(scope, scope_id).events:
                if event.id == event_id:
                    return event.model_copy(deep=True)
        return None

    def append_events(
        self,
        events: Iterable[NarrativeEvent | Mapping[str, Any]],
        scope: Any = "user",
        *,
        scope_id: str | None = None,
        max_events: int | None = None,
        min_importance: float | None = None,
    ) -> StoreWriteResult:
        """Add/replace events and deterministically evict beyond the cap."""

        if isinstance(events, (NarrativeEvent, Mapping)):
            event_input = [events]
        else:
            event_input = list(events)
        materialized = [event if isinstance(event, NarrativeEvent) else NarrativeEvent.model_validate(event) for event in event_input]
        if not materialized:
            return StoreWriteResult()
        target_key = scope_key(scope, scope_id)
        target_kind, target_id = target_key.split(":", 1) if ":" in target_key else (target_key, "default")
        for event in materialized:
            if event.scope != target_kind:
                raise ValueError("event scope does not match destination scope")
            if event.scope_id == "default" and target_id != "default":
                event = event.model_copy(update={"scope_id": target_id}, deep=True)
            elif event.scope_id != target_id:
                raise ValueError("event scope_id does not match destination scope")
        cap = self.config.max_events if max_events is None else int(max_events)
        if cap < 1:
            raise ValueError("max_events must be positive")
        floor = self.config.min_importance if min_importance is None else float(min_importance)
        key, state = self._state(scope, scope_id)
        with self._lock_for(scope, scope_id):
            self._load_events(scope, scope_id)
            self._require_writable(key, state, "events")
            before = [event.model_copy(deep=True) for event in state.events]
            positions = {event.id: index for index, event in enumerate(state.events)}
            stored = 0
            updated = 0
            accepted: list[str] = []
            for original in materialized:
                event = original
                if event.scope_id == "default" and target_id != "default":
                    event = event.model_copy(update={"scope_id": target_id}, deep=True)
                position = positions.get(event.id)
                if position is None:
                    state.events.append(event.model_copy(deep=True))
                    positions[event.id] = len(state.events) - 1
                    stored += 1
                else:
                    state.events[position] = event.model_copy(deep=True)
                    updated += 1
                accepted.append(event.id)
            overflow = max(0, len(state.events) - cap)
            evicted: list[NarrativeEvent] = []
            if overflow:
                ranked = sorted(
                    state.events,
                    key=lambda event: (
                        0 if event.importance < floor else 1,
                        event.importance,
                        event.created_at,
                        event.period_start,
                        event.id,
                    ),
                )
                evicted = ranked[:overflow]
                evicted_ids = {event.id for event in evicted}
                state.events = [event for event in state.events if event.id not in evicted_ids]
            state.events.sort(key=lambda event: (event.period_start, event.period_end, event.id))
            try:
                self._persist_events(scope, scope_id, state)
            except Exception:
                state.events = before
                raise
            state.event_status = "ok"
            return StoreWriteResult(
                stored=stored,
                updated=updated,
                event_ids=tuple(accepted),
                evicted_event_ids=tuple(event.id for event in evicted),
            )

    def put_events(
        self,
        events: Iterable[NarrativeEvent | Mapping[str, Any]],
        scope: Any = "user",
        **kwargs: Any,
    ) -> StoreWriteResult:
        """Alias for :meth:`append_events`."""

        return self.append_events(events, scope, **kwargs)

    def ingest(
        self,
        events: Iterable[NarrativeEvent | Mapping[str, Any]],
        scope: Any = "user",
        **kwargs: Any,
    ) -> StoreWriteResult:
        """Alias for :meth:`append_events` used by capture adapters."""

        return self.append_events(events, scope, **kwargs)

    def get_events(self, scope: Any = "user", **kwargs: Any) -> list[NarrativeEvent]:
        """Alias for :meth:`list_events`."""

        return self.list_events(scope, **kwargs)

    def ingest_records(
        self,
        events: Iterable[NarrativeEvent | Mapping[str, Any]],
        scope: Any = "user",
        **kwargs: Any,
    ) -> StoreWriteResult:
        """Source-seam alias for :meth:`append_events`."""

        return self.append_events(events, scope, **kwargs)

    def delete_events(
        self,
        event_ids: Iterable[str],
        scope: Any = "user",
        *,
        scope_id: str | None = None,
    ) -> int:
        wanted = {str(event_id) for event_id in event_ids}
        if not wanted:
            return 0
        with self._lock_for(scope, scope_id):
            key, state = self._state(scope, scope_id)
            self._load_events(scope, scope_id)
            self._require_writable(key, state, "events")
            before = [event.model_copy(deep=True) for event in state.events]
            state.events = [event for event in state.events if event.id not in wanted]
            removed = len(before) - len(state.events)
            if removed:
                try:
                    self._persist_events(scope, scope_id, state)
                except Exception:
                    state.events = before
                    raise
            return removed

    def get_story(self, scope: Any = "user", *, scope_id: str | None = None) -> StoryDocument | None:
        """Return the persisted story, or ``None`` when absent/unreadable."""

        with self._lock_for(scope, scope_id):
            story = self._load_story(scope, scope_id).story
            return story.model_copy(deep=True) if story is not None else None

    def load_story(self, scope: Any = "user", *, scope_id: str | None = None) -> StoryDocument | None:
        """Alias for :meth:`get_story`."""

        return self.get_story(scope, scope_id=scope_id)

    def story(self, scope: Any = "user", *, scope_id: str | None = None) -> StoryDocument | None:
        """Alias for :meth:`get_story`."""

        return self.get_story(scope, scope_id=scope_id)

    def save_story(
        self,
        document: StoryDocument,
        scope: Any = "user",
        *,
        scope_id: str | None = None,
    ) -> StoryDocument:
        """Atomically persist a story document for one scope."""

        if not isinstance(document, StoryDocument):
            document = StoryDocument.model_validate(document)
        target_kind, target_id = scope_key(scope, scope_id).split(":", 1) if ":" in scope_key(scope, scope_id) else (scope_key(scope, scope_id), "default")
        if document.scope != target_kind or (document.scope_id != target_id and document.scope_id != "default"):
            raise ValueError("story scope does not match destination scope")
        with self._lock_for(scope, scope_id):
            key, state = self._state(scope, scope_id)
            self._load_story(scope, scope_id)
            self._require_writable(key, state, "story")
            previous = state.story.model_copy(deep=True) if state.story is not None else None
            state.story = document.model_copy(deep=True)
            try:
                self._persist_story(scope, scope_id, state)
            except Exception:
                state.story = previous
                raise
            state.story_status = "ok"
            return state.story.model_copy(deep=True)

    def write_story(
        self,
        document: StoryDocument,
        scope: Any = "user",
        *,
        scope_id: str | None = None,
    ) -> StoryDocument:
        """Alias for :meth:`save_story`."""

        return self.save_story(document, scope, scope_id=scope_id)

    def save(self, document: StoryDocument, scope: Any = "user", *, scope_id: str | None = None) -> StoryDocument:
        """Short alias for :meth:`save_story`."""

        return self.save_story(document, scope, scope_id=scope_id)

    def load(self, scope: Any = "user", *, scope_id: str | None = None) -> StoryDocument | None:
        """Short alias for :meth:`get_story`."""

        return self.get_story(scope, scope_id=scope_id)

    def story_file(self, scope: Any = "user", *, scope_id: str | None = None) -> Path:
        """Return the path where this scope's story would be stored."""

        return self._story_path(scope, scope_id)

    def event_file(self, scope: Any = "user", *, scope_id: str | None = None) -> Path:
        """Return the path where this scope's event document would be stored."""

        return self._event_path(scope, scope_id)

    def reset(self) -> None:
        """Drop process-local caches and locks (test/config lifecycle helper)."""

        with self._locks_guard:
            self._states.clear()
            self._locks.clear()
            self._blocked.clear()


# A descriptive alias for callers that use the longer subsystem name.
NarrativeMemoryStore = NarrativeStore
NarrativeEventStore = NarrativeStore
StoryStore = NarrativeStore

__all__ = [
    "NarrativeEventStore",
    "NarrativeMemoryStore",
    "NarrativeStore",
    "StoryStore",
    "StoreUnavailableError",
    "StoreWriteResult",
]
