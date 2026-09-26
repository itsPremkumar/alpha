"""Thread-safe, bounded per-scope JSON storage for utility records.

The store owns only utility metadata.  It never opens, mutates, or deletes a
host memory record.  Writes are serialized per scope and atomically replaced;
unreadable documents are moved to ``.corrupt-*`` siblings before the scope
starts empty.  Bound eviction is deterministic: lowest score first, then oldest
``last_seen``, then lexical record id, with an explicit notice for every id.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .config import UtilityConfig
from .models import EvictionNotice, UtilityRecord
from .paths import atomic_write_text, document_path, preserve_corrupt, utility_root

logger = logging.getLogger(__name__)

_SCHEMA = 1


class _ScopeState:
    __slots__ = ("records", "evictions", "corruption_events", "disk_signature")

    def __init__(self) -> None:
        self.records: dict[str, UtilityRecord] = {}
        self.evictions: list[EvictionNotice] = []
        self.corruption_events: list[str] = []
        self.disk_signature: tuple[int, int] | None = None


class UtilityStore:
    """Bounded utility metadata store with per-scope locks."""

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        max_observations_per_scope: int | None = None,
        config: UtilityConfig | Mapping[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> None:
        active_config = self._coerce_config(config)
        selected_path = storage_path
        if selected_path is None and active_config is not None:
            selected_path = active_config.storage_path
        if max_observations_per_scope is None and active_config is not None:
            max_observations_per_scope = active_config.max_observations_per_scope
        if max_observations_per_scope is None:
            max_observations_per_scope = 1000
        if isinstance(max_observations_per_scope, bool) or int(max_observations_per_scope) < 1:
            raise ValueError("max_observations_per_scope must be a positive integer")
        self._root = utility_root(selected_path)
        self._max_observations = int(max_observations_per_scope)
        self._enabled = active_config.enabled if enabled is None and active_config is not None else bool(enabled if enabled is not None else True)
        self._states: dict[tuple[str, str], _ScopeState] = {}
        self._state_lock = threading.Lock()

    @staticmethod
    def _coerce_config(config: UtilityConfig | Mapping[str, Any] | None) -> UtilityConfig | None:
        if config is None:
            return None
        if isinstance(config, UtilityConfig):
            return config
        return UtilityConfig.from_mapping(config)

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        key = str(path)
        with cls._locks_guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._locks[key] = lock
            return lock

    @staticmethod
    def _key(user_id: str | None, agent_name: str | None) -> tuple[str, str]:
        return (str(user_id or "default"), str(agent_name or "default"))

    def _path(self, user_id: str | None = None, agent_name: str | None = None) -> Path:
        return document_path(self._root, user_id, agent_name)

    @property
    def storage_path(self) -> Path:
        return self._root

    @property
    def root_path(self) -> Path:
        return self._root

    @property
    def max_observations_per_scope(self) -> int:
        return self._max_observations

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def _disk_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _load(self, user_id: str | None, agent_name: str | None) -> _ScopeState:
        key = self._key(user_id, agent_name)
        path = self._path(user_id, agent_name)
        signature = self._disk_signature(path)
        with self._state_lock:
            cached = self._states.get(key)
            if cached is not None and cached.disk_signature == signature:
                return cached
        state = _ScopeState()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, Mapping) or not isinstance(raw.get("records"), list):
                    raise ValueError("utility document must contain a records list")
                state.records = {record.record_id: record for record in (UtilityRecord.model_validate(item) for item in raw["records"])}
                state.evictions = [EvictionNotice.model_validate(item) for item in raw.get("evictions", [])]
                raw_events = raw.get("corruption_events", [])
                if not isinstance(raw_events, list) or not all(isinstance(item, str) for item in raw_events):
                    raise ValueError("corruption_events must be a string list")
                state.corruption_events = list(raw_events)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                backup = preserve_corrupt(path)
                if backup is None:
                    state.corruption_events.append(f"corrupt document could not be preserved: {path}")
                else:
                    state.corruption_events.append(f"corrupt document preserved: {backup}")
                logger.error("Utility store: corrupt document %s preserved=%s (%s)", path, backup, exc)
        state.disk_signature = self._disk_signature(path)
        with self._state_lock:
            self._states[key] = state
        return state

    def _persist(self, user_id: str | None, agent_name: str | None, state: _ScopeState) -> None:
        payload = {
            "schema": _SCHEMA,
            "records": [state.records[key].model_dump(mode="json") for key in sorted(state.records)],
            "evictions": [item.model_dump(mode="json") for item in state.evictions],
            "corruption_events": list(state.corruption_events),
        }
        path = self._path(user_id, agent_name)
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        )
        state.disk_signature = self._disk_signature(path)

    def _bound(self, state: _ScopeState) -> list[UtilityRecord]:
        excess = len(state.records) - self._max_observations
        if excess <= 0:
            return []
        ordered = sorted(
            state.records.values(),
            key=lambda item: (item.score, item.last_seen, item.record_id),
        )
        removed: list[UtilityRecord] = []
        for record in ordered[:excess]:
            del state.records[record.record_id]
            state.evictions.append(
                EvictionNotice(
                    record_id=record.record_id,
                    reason="bounded_retention:lowest_utility_oldest_tie_id",
                    utility=record.score,
                    last_seen=record.last_seen,
                )
            )
            removed.append(record)
        return removed

    def put(
        self,
        record: UtilityRecord | Mapping[str, Any],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> int:
        """Insert/replace one utility record; return 1 when accepted."""

        if not self._enabled:
            return 0
        parsed = record if isinstance(record, UtilityRecord) else UtilityRecord.model_validate(dict(record))
        path = self._path(user_id, agent_name)
        with self._lock_for(path):
            state = self._load(user_id, agent_name)
            state.records[parsed.record_id] = parsed.model_copy(deep=True)
            self._bound(state)
            self._persist(user_id, agent_name, state)
        return 1

    def upsert(
        self,
        record: UtilityRecord | Mapping[str, Any],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> UtilityRecord | None:
        if not self._enabled:
            return None
        self.put(record, user_id=user_id, agent_name=agent_name)
        return self.get(parsed_record_id(record), user_id=user_id, agent_name=agent_name)

    def put_records(
        self,
        records: Iterable[UtilityRecord | Mapping[str, Any]],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> int:
        """Insert/replace a batch and return the number of input records."""

        if not self._enabled:
            return 0
        parsed = [item if isinstance(item, UtilityRecord) else UtilityRecord.model_validate(dict(item)) for item in records]
        if not parsed:
            return 0
        path = self._path(user_id, agent_name)
        with self._lock_for(path):
            state = self._load(user_id, agent_name)
            for record in parsed:
                state.records[record.record_id] = record.model_copy(deep=True)
            self._bound(state)
            self._persist(user_id, agent_name, state)
        return len(parsed)

    def apply(
        self,
        records: Iterable[UtilityRecord | Mapping[str, Any]],
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> int:
        return self.put_records(records, user_id=user_id, agent_name=agent_name)

    def get(
        self,
        record_id: str,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> UtilityRecord | None:
        if not self._enabled:
            return None
        with self._lock_for(self._path(user_id, agent_name)):
            record = self._load(user_id, agent_name).records.get(str(record_id))
            return record.model_copy(deep=True) if record else None

    def list_records(
        self,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[UtilityRecord]:
        if not self._enabled:
            return []
        with self._lock_for(self._path(user_id, agent_name)):
            state = self._load(user_id, agent_name)
            return [state.records[key].model_copy(deep=True) for key in sorted(state.records)]

    def records(
        self,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[UtilityRecord]:
        return self.list_records(user_id, agent_name)

    def all_records(
        self,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[UtilityRecord]:
        return self.list_records(user_id, agent_name)

    def count(
        self,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> int:
        if not self._enabled:
            return 0
        with self._lock_for(self._path(user_id, agent_name)):
            return len(self._load(user_id, agent_name).records)

    def evictions(
        self,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[EvictionNotice]:
        if not self._enabled:
            return []
        with self._lock_for(self._path(user_id, agent_name)):
            return [item.model_copy(deep=True) for item in self._load(user_id, agent_name).evictions]

    def corruption_events(
        self,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[str]:
        if not self._enabled:
            return []
        with self._lock_for(self._path(user_id, agent_name)):
            return list(self._load(user_id, agent_name).corruption_events)

    def eviction_log(
        self,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[EvictionNotice]:
        return self.evictions(user_id, agent_name)

    def reset_scope(
        self,
        *,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> bool:
        """Reset only this utility scope; no host-memory path is touched."""

        if not self._enabled:
            return False
        path = self._path(user_id, agent_name)
        with self._lock_for(path):
            previous = self._load(user_id, agent_name)
            state = _ScopeState()
            state.corruption_events = list(previous.corruption_events)
            self._persist(user_id, agent_name, state)
            with self._state_lock:
                self._states[self._key(user_id, agent_name)] = state
        return True

    def snapshot(
        self,
        user_id: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        if not self._enabled:
            return {
                "enabled": False,
                "records": [],
                "evictions": [],
                "corruption_events": [],
                "max_observations_per_scope": self._max_observations,
            }
        with self._lock_for(self._path(user_id, agent_name)):
            state = self._load(user_id, agent_name)
            return {
                "enabled": True,
                "records": [state.records[key].model_dump(mode="json") for key in sorted(state.records)],
                "evictions": [item.model_dump(mode="json") for item in state.evictions],
                "corruption_events": list(state.corruption_events),
                "max_observations_per_scope": self._max_observations,
            }


def parsed_record_id(record: UtilityRecord | Mapping[str, Any]) -> str:
    if isinstance(record, UtilityRecord):
        return record.record_id
    return str(record.get("record_id", ""))


# Compatibility names for hosts that call the metadata object a memory store.
MemoryUtilityStore = UtilityStore
UtilityRecordStore = UtilityStore

__all__ = ["MemoryUtilityStore", "UtilityRecordStore", "UtilityStore", "parsed_record_id"]
