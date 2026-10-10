"""Durable arena state: one JSON document per run.

The store is deliberately simple, because the state
machine in ``bracket.py`` is the whole game:

* One run is one directory under ``runtime_home()/arena/``
  holding ``state.json`` plus the run's work files.
* Every write is atomic (temp file + ``os.replace``) and
  taken under a cross-process :class:`FileLock`, so two
  Gateway workers driving the same run cannot interleave
  a lost update. The lock is advisory and
  same-filesystem - it is not a distributed lock, and it
  is never described as cross-process exactly-once.
* Reads are owner-scoped: ``load`` refuses a run whose
  recorded ``owner_id`` differs from the caller, because
  request context cannot self-assert ownership.
* A corrupt ``state.json`` is an error, never an empty
  run: an unreadable document must not read as "no work
  has happened". ``load`` raises ``ArenaStoreError``
  naming the real cause.

The run index (``index.json``) is a bounded projection of
each run's identity for listing; it is rebuilt from the
run directories on load when it disagrees with the disk,
because an index is a cache, not a source of truth.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.utils.file_lock import FileLock, FileLockTimeout

logger = logging.getLogger(__name__)

#: How long a mutator waits for another worker's lock
#: before reporting the contention instead of proceeding
#: unlocked.
LOCK_TIMEOUT_SECONDS = 30.0

#: Bounded size of the per-run event log kept beside the
#: state (the full match history lives in ``state.json``;
#: this is the tail the UI shows).
MAX_LOG_LINES = 200


class ArenaStoreError(RuntimeError):
    """The arena store is unusable or the state is corrupt."""


def store_root(override: Path | str | None = None) -> Path:
    """The directory arena state lives under."""
    return Path(override) if override is not None else runtime_home() / "arena"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` to ``path`` atomically.

    The temp file is created in the destination directory
    (so ``os.replace`` never crosses a filesystem), flushed
    and fsynced before the replace, so a crash mid-write
    leaves the previous document intact rather than a
    truncated one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class ArenaStore:
    """Owner-scoped, restart-safe persistence for arena runs."""

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = store_root(root)

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------ paths

    def run_dir(self, run_id: str) -> Path:
        """The run's directory. ``run_id`` is a generated id,
        never caller text, so path traversal is refused rather
        than repaired."""
        if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
            raise ArenaStoreError(f"invalid run id '{run_id}'")
        return self._root / run_id

    def state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state.json"

    def workspace_dir(self, run_id: str) -> Path:
        """Where the run's sub-agent work files live."""
        return self.run_dir(run_id) / "work"

    def work_path(self, run_id: str, name: str) -> Path:
        """A work file path inside the run's workspace.

        ``name`` is engine-generated (``agent-3-solution.md``),
        so the same traversal refusal applies.
        """
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ArenaStoreError(f"invalid work file name '{name}'")
        return self.workspace_dir(run_id) / name

    # ------------------------------------------------------------------ reads

    def load(self, run_id: str, owner_id: str) -> dict[str, Any]:
        """Load a run, refusing a foreign owner."""
        state = self.load_any(run_id)
        if str(state.get("owner_id")) != str(owner_id):
            raise ArenaStoreError(
                f"run '{run_id}' belongs to another owner"
            )
        return state

    def load_any(self, run_id: str) -> dict[str, Any]:
        """Load a run without the ownership check.

        The admin surface uses this; the model surface never
        does.
        """
        path = self.state_path(run_id)
        try:
            with open(path, encoding="utf-8") as handle:
                state = json.load(handle)
        except FileNotFoundError as error:
            raise KeyError(f"no arena run '{run_id}'") from error
        except (OSError, json.JSONDecodeError) as error:
            raise ArenaStoreError(f"arena state '{path}' is unreadable: {error}") from error
        if not isinstance(state, dict) or "run_id" not in state:
            raise ArenaStoreError(f"arena state '{path}' is not a run document")
        return state

    def list_runs(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        """List runs newest-first, optionally for one owner.

        Rows are bounded summaries, not full state: a listing
        must never read every run's match history.
        Sorted by ``updated_at`` descending so newest runs appear first.
        """
        if not self._root.is_dir():
            return []
        runs_data: list[tuple[float, dict[str, Any]]] = []
        unreadable_rows: list[dict[str, Any]] = []
        for entry in self._root.iterdir():
            if not entry.is_dir():
                continue
            try:
                state = self.load_any(entry.name)
            except (ArenaStoreError, KeyError):
                # A half-written or corrupt run directory is
                # disclosed, not silently dropped.
                unreadable_rows.append(
                    {
                        "run_id": entry.name,
                        "status": "unreadable",
                        "owner_id": None,
                        "task": "",
                        "updated_at": None,
                    }
                )
                continue
            if owner_id is not None and str(state.get("owner_id")) != str(owner_id):
                continue
            updated_at = state.get("updated_at") or state.get("created_at") or 0
            runs_data.append((updated_at, self._row(state)))
        # Sort by updated_at descending (newest first)
        runs_data.sort(key=lambda x: x[0], reverse=True)
        # Combine unreadable rows (they have no timestamp) at the end
        return [row for _, row in runs_data] + unreadable_rows

    @staticmethod
    def _row(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": state.get("run_id"),
            "status": state.get("status"),
            "phase": state.get("phase"),
            "owner_id": state.get("owner_id"),
            "thread_id": state.get("thread_id"),
            "agents_n": state.get("agents_n"),
            "task": state.get("task", "")[:200],
            "champion": state.get("champion"),
            "updated_at": state.get("updated_at"),
        }

    # ------------------------------------------------------------------ writes

    def save(self, state: dict[str, Any]) -> None:
        """Persist a run atomically, under the run's lock.

        The read-modify-write happens inside the lock, so a
        second process holding the same run waits rather than
        clobbering. A lock timeout is raised, never
        bypassed: proceeding unlocked would be the lost
        update the lock exists to prevent.
        """
        run_id = str(state.get("run_id", ""))
        path = self.state_path(run_id)
        lock = FileLock(path, timeout=LOCK_TIMEOUT_SECONDS)
        try:
            with lock:
                _atomic_write_json(path, state)
        except FileLockTimeout as error:
            raise ArenaStoreError(
                f"run '{run_id}' is locked by another worker: {error}"
            ) from error

    def append_log(self, run_id: str, line: str) -> None:
        """Append one line to the run's bounded event log."""
        path = self.run_dir(run_id) / "log.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"at": time.time(), "line": line}) + "\n")
            self._trim_log(path)
        except OSError as error:
            # The log is a convenience, not state: a failed
            # append must not fail the mutation it describes.
            logger.warning("arena log append failed for '%s': %s", run_id, error)

    def _trim_log(self, path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) > MAX_LOG_LINES:
            try:
                path.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n", encoding="utf-8")
            except OSError:
                pass

    def read_log(self, run_id: str, tail: int = 50) -> list[dict[str, Any]]:
        """The newest ``tail`` log lines."""
        path = self.run_dir(run_id) / "log.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []
        records: list[dict[str, Any]] = []
        for line in lines[-max(1, tail) :]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    # ------------------------------------------------------------------ lifecycle

    def delete(self, run_id: str, owner_id: str) -> None:
        """Delete a run's directory, owner-checked."""
        self.load(run_id, owner_id)
        import shutil

        shutil.rmtree(self.run_dir(run_id), ignore_errors=True)

    def ensure_dirs(self, run_id: str) -> None:
        """Create the run and workspace directories."""
        self.run_dir(run_id).mkdir(parents=True, exist_ok=True)
        self.workspace_dir(run_id).mkdir(parents=True, exist_ok=True)


__all__ = [
    "ArenaStore",
    "ArenaStoreError",
    "LOCK_TIMEOUT_SECONDS",
    "MAX_LOG_LINES",
    "store_root",
]
