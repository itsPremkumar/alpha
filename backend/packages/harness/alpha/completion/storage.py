"""Locked JSON persistence for the completion store.

Single-process durability only: a JSON document guarded by
:class:`alpha.utils.file_lock.FileLock`. The lock is *advisory* and gives no
cross-process exactly-once guarantee — a completion store shared across hosts
without a working advisory lock is read-modify-write racing, and this package
never claims otherwise.

Two rules the rest of the engine depends on:

* **A missing file is a fresh store; a malformed file is a fault.** The loader
  distinguishes them, because "there are no contracts" and "I could not read
  the contracts" lead to opposite decisions.
* **A mutation that cannot be persisted is discarded, not half-applied.** The
  document is written back only after ``fn`` returns; a write failure raises
  and the caller's in-memory change is not authoritative.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from alpha.utils.file_lock import FileLock, FileLockTimeout

from .errors import StoreCorrupt, StoreUnreadable, StoreWriteFailed

logger = logging.getLogger(__name__)

_STORE_VERSION = 1

T = TypeVar("T")

#: Sections of the document. Each maps id -> record dict. The scope is the
#: aggregate root (it embeds its tasks, gates, evidence, dispatches and
#: ownership claims), so there is deliberately no top-level `tasks`/`gates`
#: table to drift out of sync with it.
_SECTIONS = (
    "scopes",
    "approvals",
)

#: Monotonic id counters. Never derived from list length (ids are reused).
_COUNTERS = ("contract_seq", "task_seq", "gate_seq", "evidence_seq", "dispatch_seq", "event_seq")


def empty_document() -> dict[str, Any]:
    """A brand-new store document. Every read path starts here."""
    doc: dict[str, Any] = {section: {} for section in _SECTIONS}
    doc["version"] = _STORE_VERSION
    doc["events"] = []
    doc["counters"] = {name: 0 for name in _COUNTERS}
    return doc


class CompletionStore:
    """A JSON-backed document store with an advisory file lock.

    Every mutation goes through :meth:`mutate`, which takes the lock, loads
    the document, applies ``fn`` and atomically replaces the file (temp file +
    ``os.replace``). Readers that need a consistent snapshot use :meth:`read`.
    """

    def __init__(self, path: Path, *, lock_timeout: float = 5.0) -> None:
        self.path = Path(path)
        self._lock_timeout = float(lock_timeout)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def read(self, fn: Callable[[dict[str, Any]], T]) -> T:
        """Run ``fn(doc)`` under the lock without persisting.

        Use for every read that must not interleave with a mutation.
        """
        return self._with_lock(lambda: fn(self._load()))

    def mutate(self, fn: Callable[[dict[str, Any]], T]) -> T:
        """Run ``fn(doc)`` under the lock and persist the result atomically.

        ``fn`` receives the loaded document, mutates it in place, and returns
        a value. The document is written back only if ``fn`` returns without
        raising — a refused mutation leaves the file untouched.
        """

        def _run() -> T:
            doc = self._load()
            result = fn(doc)
            self._write(doc)
            return result

        return self._with_lock(_run)

    def _with_lock(self, fn: Callable[[], T]) -> T:
        # FileLock appends ".lock" itself and derives it from the data path,
        # so the sidecar never collides with the atomically-replaced document.
        try:
            with FileLock(self.path, timeout=self._lock_timeout):
                return fn()
        except FileLockTimeout as exc:
            raise StoreUnreadable(f"completion store is locked by another process: {exc}") from exc

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_document()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StoreUnreadable(f"cannot read completion store: {exc}") from exc
        if not raw.strip():
            # An empty file is a torn write from a pre-atomic writer, not a
            # fresh store: the file existing means someone intended state.
            raise StoreCorrupt(f"completion store {self.path} is empty")
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StoreCorrupt(f"completion store is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise StoreCorrupt("completion store root must be an object")
        base = empty_document()
        for key, value in base.items():
            doc.setdefault(key, value)
        return doc

    def _write(self, doc: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False, default=str)
            fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=".completion-", suffix=".tmp")
        except OSError as exc:
            raise StoreWriteFailed(f"cannot stage completion store write: {exc}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except OSError as exc:
            raise StoreWriteFailed(f"cannot write completion store: {exc}") from exc
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:  # pragma: no cover - best-effort cleanup
                    logger.debug("leftover temp file %s could not be removed", tmp_name, exc_info=True)
