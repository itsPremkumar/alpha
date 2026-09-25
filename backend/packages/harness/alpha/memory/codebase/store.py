"""Thread-safe bounded JSON snapshot store for codebase structure memory."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from .config import CodebaseConfig
from .models import CodebaseSnapshot
from .paths import atomic_write_text, codebase_root, repo_dir, snapshot_path

logger = logging.getLogger(__name__)
_SCHEMA = 1
_locks_guard = threading.Lock()
_path_locks: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _locks_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _path_locks[key] = lock
        return lock


class CodebaseStore:
    """Store snapshots per repository with atomic, bounded history.

    One JSON document contains a schema marker and a version-sorted snapshot
    list.  Every save is a locked read/modify/write followed by an atomic
    replace, so readers see either the old complete document or the new one.
    If the document cannot be parsed/validated it is moved to a sibling
    ``.corrupt-*`` file before an empty history is used; the unreadable bytes
    are never silently overwritten.
    """

    def __init__(
        self,
        storage_path: str | os.PathLike[str] | None = None,
        *,
        repo_id: str = "default",
        max_history: int | None = None,
        config: CodebaseConfig | None = None,
    ) -> None:
        if isinstance(storage_path, CodebaseConfig) and config is None:
            config = storage_path
            storage_path = None
        self.config = config
        selected_path = storage_path if storage_path is not None else (config.storage_path if config is not None else None)
        self.root = codebase_root(selected_path)
        self.repo_id = str(repo_id or "default")
        selected_history = max_history if max_history is not None else (config.max_snapshot_history if config is not None else 10)
        if selected_history < 1:
            raise ValueError("max_history must be >= 1")
        self.max_history = int(selected_history)
        self._path = snapshot_path(self.root, self.repo_id)
        self._lock = _lock_for(self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def snapshot_path(self) -> Path:
        return self._path

    @property
    def history_path(self) -> Path:
        return self._path

    @property
    def repo_path(self) -> Path:
        return repo_dir(self.root, self.repo_id)

    def path_for(self, repo_id: str | None = None) -> Path:
        """Return the document path for a repository without changing state."""

        return snapshot_path(self.root, str(repo_id or self.repo_id or "default"))

    def _lock_for_repo(self, repo_id: str) -> threading.RLock:
        return _lock_for(self.path_for(repo_id))

    def _preserve_corrupt(self, path: Path) -> None:
        backup = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
        try:
            os.replace(path, backup)
            logger.error("Codebase store: corrupt document preserved as %s", backup)
        except OSError:
            logger.exception("Codebase store: could not preserve corrupt document %s", path)

    def _read_document(self, repo_id: str) -> dict[str, object]:
        path = snapshot_path(self.root, repo_id)
        if not path.exists():
            return {"schema": _SCHEMA, "repo_id": repo_id, "current_version": 0, "snapshots": []}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA or not isinstance(raw.get("snapshots"), list):
                raise ValueError("invalid codebase snapshot document")
            snapshots = [CodebaseSnapshot.model_validate(item) for item in raw["snapshots"]]
            snapshots.sort(key=lambda item: (item.version, item.generated_at, item.repo_id))
            return {
                "schema": _SCHEMA,
                "repo_id": str(raw.get("repo_id") or repo_id),
                "current_version": max((item.version for item in snapshots), default=0),
                "snapshots": snapshots,
            }
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.error("Codebase store: invalid document %s (%s)", path, exc)
            self._preserve_corrupt(path)
            return {"schema": _SCHEMA, "repo_id": repo_id, "current_version": 0, "snapshots": []}

    def _write_document(self, repo_id: str, snapshots: Iterable[CodebaseSnapshot], current_version: int) -> None:
        path = snapshot_path(self.root, repo_id)
        ordered = sorted(list(snapshots), key=lambda item: (item.version, item.generated_at, item.repo_id))
        payload = {
            "schema": _SCHEMA,
            "repo_id": repo_id,
            "current_version": current_version,
            "snapshots": [item.model_dump(mode="json") for item in ordered],
        }
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def save(self, snapshot: CodebaseSnapshot | str, repo_id: str | CodebaseSnapshot | None = None) -> CodebaseSnapshot:
        """Atomically append a versioned snapshot and return the stored copy.

        History eviction is deterministic: versions are sorted ascending and
        the oldest versions are removed until ``max_history`` remains.  The
        newly written snapshot carries the evicted version numbers in its
        ``evicted_versions`` field and a disclosure.  The store's explicit
        ``repo_id`` owns the destination; pass ``repo_id=...`` to write a
        different repository bucket.
        """

        if isinstance(snapshot, str):
            if not isinstance(repo_id, CodebaseSnapshot):
                raise TypeError("save(repo_id, snapshot) requires a CodebaseSnapshot")
            snapshot, repo_id = repo_id, snapshot
        if not isinstance(snapshot, CodebaseSnapshot):
            raise TypeError("snapshot must be a CodebaseSnapshot")
        target_repo = str(repo_id or self.repo_id or "default")
        with self._lock_for_repo(target_repo):
            document = self._read_document(target_repo)
            existing = list(document["snapshots"])  # type: ignore[arg-type]
            next_version = max((item.version for item in existing), default=0) + 1
            incoming = snapshot.model_copy(deep=True, update={"repo_id": target_repo, "version": next_version})
            if incoming.module_count != len(incoming.modules):
                incoming = incoming.model_copy(update={"module_count": len(incoming.modules), "edge_count": len(incoming.edges)})
            all_snapshots = [*existing, incoming]
            all_snapshots.sort(key=lambda item: (item.version, item.generated_at, item.repo_id))
            evicted = all_snapshots[: max(0, len(all_snapshots) - self.max_history)]
            retained = all_snapshots[len(evicted) :]
            evicted_versions = [item.version for item in evicted]
            if evicted_versions:
                disclosure = f"history_evicted: {evicted_versions}"
                incoming = incoming.model_copy(
                    update={
                        "evicted_versions": sorted(set([*incoming.evicted_versions, *evicted_versions])),
                        "disclosures": sorted({*incoming.disclosures, disclosure}),
                    }
                )
                retained = [incoming if item.version == next_version else item for item in retained]
            self._write_document(target_repo, retained, next_version)
            return incoming.model_copy(deep=True)

    put_snapshot = save
    write_snapshot = save
    append_snapshot = save
    save_snapshot = save

    def load(self, repo_id: str | None = None) -> CodebaseSnapshot | None:
        """Load the newest valid snapshot, or ``None`` when none exists."""

        target_repo = str(repo_id or self.repo_id or "default")
        with self._lock_for_repo(target_repo):
            document = self._read_document(target_repo)
            snapshots = list(document["snapshots"])  # type: ignore[arg-type]
            return snapshots[-1].model_copy(deep=True) if snapshots else None

    latest = load
    get_snapshot = load
    load_snapshot = load

    def get_version(self, version: int, repo_id: str | None = None) -> CodebaseSnapshot | None:
        target_repo = str(repo_id or self.repo_id or "default")
        if version < 1:
            raise ValueError("version must be >= 1")
        with self._lock_for_repo(target_repo):
            snapshots = list(self._read_document(target_repo)["snapshots"])  # type: ignore[arg-type]
        for snapshot in snapshots:
            if snapshot.version == version:
                return snapshot.model_copy(deep=True)
        return None

    def history(self, repo_id: str | None = None, *, newest_first: bool = False) -> list[CodebaseSnapshot]:
        """Return valid snapshots in version order (oldest first by default)."""

        target_repo = str(repo_id or self.repo_id or "default")
        with self._lock_for_repo(target_repo):
            snapshots = [item.model_copy(deep=True) for item in self._read_document(target_repo)["snapshots"]]  # type: ignore[arg-type]
        snapshots.sort(key=lambda item: (item.version, item.generated_at, item.repo_id), reverse=newest_first)
        return snapshots

    list_history = history

    def clear(self, repo_id: str | None = None) -> None:
        """Remove a repository document while preserving any corrupt backups."""

        target_repo = str(repo_id or self.repo_id or "default")
        with self._lock_for_repo(target_repo):
            self.path_for(target_repo).unlink(missing_ok=True)


__all__ = ["CodebaseStore"]
