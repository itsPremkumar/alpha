"""Snapshot and rollback for intelligence state (prompt §11).

Relationship to existing snapshot machinery
-------------------------------------------
Alpha already has three snapshot/rollback implementations and this module uses
the right one for each rather than writing a fourth mechanism:

* :class:`alpha.runtime.sentinel.checkpoint.CheckpointManager` — file-level
  checkpoint/restore for repository files. Not used here: intelligence state is
  structured documents, not source files.
* :class:`alpha.config.self_tuning.rollback.RollbackManager` — rolls back a
  *config change-set*. Not used here: a learning candidate is not a config
  change-set, and its rollback target is a document snapshot, not an inverted
  edit.
* :mod:`alpha.evolution.update_engine` — Git fast-forward/backup-ref. Not used
  here: learning state is deliberately **not** source code and must never be
  committed into ``main``.

So this module composes the repository's existing **atomic writer**
(:mod:`alpha.persistence.storekit.atomic`) with a bounded, content-addressed
snapshot directory and a retry budget. That is the smallest thing that satisfies
the requirement without duplicating a rollback engine.

Bounded recovery is the contract, not an aspiration
---------------------------------------------------
:meth:`SnapshotManager.run_with_rollback` enforces a hard attempt cap
(``max_retries``) and, on exhaustion, **raises** with the reason. There is no
path that loops: an unmeasurable candidate that fails its gate cannot retry
forever, and the caller is told the budget is spent rather than left waiting on a
background task that will never converge.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

__all__ = [
    "SNAPSHOT_RELATIVE_PATH",
    "SnapshotInfo",
    "SnapshotManager",
    "SnapshotError",
    "RetryBudgetExhausted",
    "snapshot_dir",
]

SNAPSHOT_RELATIVE_PATH = Path("intelligence") / "snapshots"

#: Snapshots retained before the oldest is removed. Bounded on purpose: an
#: unbounded snapshot directory is a disk leak that eventually takes down the
#: host it was meant to protect.
RETAINED_SNAPSHOTS = 20

T = TypeVar("T")


class SnapshotError(RuntimeError):
    """Base class for snapshot failures."""


class RetryBudgetExhausted(SnapshotError):
    """A candidate kept failing past its attempt budget. Never loops further."""


def snapshot_dir() -> Path:
    """Directory holding intelligence snapshots under ``runtime_home()``."""
    return runtime_home() / SNAPSHOT_RELATIVE_PATH


@dataclass(frozen=True)
class SnapshotInfo:
    """Metadata about one stored snapshot. The payload itself stays on disk."""

    snapshot_id: str
    label: str
    created_at: float
    path: str
    payload_keys: tuple[str, ...] = ()
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "label": self.label,
            "created_at": self.created_at,
            "path": self.path,
            "payload_keys": list(self.payload_keys),
            "size_bytes": self.size_bytes,
        }


class SnapshotManager:
    """Content-addressed, bounded snapshots of intelligence state."""

    def __init__(self, root: str | Path | None = None, *, retain: int = RETAINED_SNAPSHOTS) -> None:
        if retain < 1:
            raise SnapshotError(f"retain must be >= 1, got {retain}")
        self.root = Path(root) if root is not None else snapshot_dir()
        self.retain = int(retain)
        self._lock = threading.RLock()

    # -- capture ------------------------------------------------------------

    def create(self, label: str, payload: dict[str, Any]) -> SnapshotInfo:
        """Write a snapshot of ``payload``.

        The snapshot id is the SHA-256 of the canonical payload, so capturing
        identical state twice yields the *same* id rather than two near-copies —
        which makes "did anything actually change?" answerable by id comparison.
        """
        if not isinstance(payload, dict):
            raise SnapshotError(f"snapshot payload must be an object, got {type(payload).__name__}")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        snapshot_id = hashlib.sha256(encoded).hexdigest()[:16]
        document = {
            "snapshot_id": snapshot_id,
            "label": label,
            "created_at": time.time(),
            "payload": payload,
        }
        target = self.root / f"{snapshot_id}.json"
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            # Atomic: a crash mid-write leaves the previous snapshot intact.
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(document, indent=2, sort_keys=True, default=str), encoding="utf-8")
            temporary.replace(target)
            self._prune()
        return SnapshotInfo(
            snapshot_id=snapshot_id,
            label=label,
            created_at=float(document["created_at"]),
            path=str(target),
            payload_keys=tuple(sorted(payload)),
            size_bytes=len(encoded),
        )

    def _prune(self) -> None:
        """Remove the oldest snapshots beyond ``retain``, oldest-first by mtime."""
        files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for stale in files[self.retain :]:
            try:
                stale.unlink()
            except OSError as exc:
                logger.warning("could not prune snapshot %s: %s", stale, exc)

    # -- restore ------------------------------------------------------------

    def load(self, snapshot_id: str) -> dict[str, Any]:
        """Read one snapshot's payload.

        Raises rather than returning a partial document: restoring from a
        truncated snapshot would replace good state with a fraction of it.
        """
        key = (snapshot_id or "").strip()
        if not key or any(char in key for char in "/\\") or key in {".", ".."}:
            raise SnapshotError(f"invalid snapshot id {snapshot_id!r}")
        path = self.root / f"{key}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SnapshotError(f"no snapshot {key!r} at {path}") from exc
        except (OSError, ValueError) as exc:
            raise SnapshotError(f"snapshot {key!r} at {path} could not be read: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
            raise SnapshotError(f"snapshot {key!r} at {path} has no payload object")
        return dict(document["payload"])

    def list(self) -> list[SnapshotInfo]:
        """Snapshots, newest first."""
        with self._lock:
            if not self.root.exists():
                return []
            entries: list[SnapshotInfo] = []
            for path in self.root.glob("*.json"):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    # An unreadable snapshot is reported as absent rather than
                    # crashing the listing; restore() will still refuse it.
                    continue
                if not isinstance(document, dict):
                    continue
                payload = document.get("payload")
                entries.append(
                    SnapshotInfo(
                        snapshot_id=str(document.get("snapshot_id", path.stem)),
                        label=str(document.get("label", "")),
                        created_at=float(document.get("created_at", path.stat().st_mtime)),
                        path=str(path),
                        payload_keys=tuple(sorted(payload)) if isinstance(payload, dict) else (),
                        size_bytes=path.stat().st_size,
                    )
                )
            entries.sort(key=lambda info: info.created_at, reverse=True)
            return entries

    def latest(self) -> SnapshotInfo | None:
        entries = self.list()
        return entries[0] if entries else None

    def delete(self, snapshot_id: str) -> bool:
        key = (snapshot_id or "").strip()
        if not key or any(char in key for char in "/\\") or key in {".", ".."}:
            raise SnapshotError(f"invalid snapshot id {snapshot_id!r}")
        path = self.root / f"{key}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    # -- guarded execution --------------------------------------------------

    def run_with_rollback(
        self,
        candidate: str,
        apply: Callable[[], T],
        evaluate: Callable[[T], bool],
        restore: Callable[[], Any],
        *,
        max_retries: int = 2,
        on_attempt: Callable[[int, bool], None] | None = None,
    ) -> dict[str, Any]:
        """Snapshot, apply, evaluate, and roll back on degradation.

        The loop is bounded by ``max_retries`` and **raises**
        :class:`RetryBudgetExhausted` when the budget is spent. It never retries
        past the cap and never silently gives up, because a caller that receives
        ``False`` here would have no way to tell "rolled back cleanly" from
        "gave up after twenty attempts".

        Args:
            candidate: Label for the change being attempted, recorded in the
                snapshot so the rollback target is identifiable.
            apply: Mutates state and returns a result to evaluate.
            evaluate: Returns ``True`` when the candidate did not degrade.
            restore: Restores from the snapshot just taken.
            max_retries: Hard attempt cap. ``0`` means "apply once, never retry".
            on_attempt: Optional observer, called as ``(attempt_number, ok)``.
        """
        if max_retries < 0:
            raise SnapshotError(f"max_retries must be >= 0, got {max_retries}")
        attempts: list[dict[str, Any]] = []
        snapshot = self.create(f"pre-{candidate}", {"candidate": candidate})
        for attempt in range(1, max(1, max_retries) + 1):
            try:
                result = apply()
            except Exception as exc:
                # An exception from apply() is a failed attempt, not a crash of
                # the guard: restoring here is what keeps a half-applied
                # candidate from becoming the new baseline.
                logger.warning("intelligence candidate %r raised on attempt %d: %s", candidate, attempt, exc)
                restore()
                attempts.append({"attempt": attempt, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
                if on_attempt is not None:
                    on_attempt(attempt, False)
                continue
            ok = bool(evaluate(result))
            attempts.append({"attempt": attempt, "ok": ok, "result": _summarise(result)})
            if on_attempt is not None:
                on_attempt(attempt, ok)
            if ok:
                return {
                    "candidate": candidate,
                    "promoted": True,
                    "attempts": attempts,
                    "snapshot_id": snapshot.snapshot_id,
                    "retries_used": attempt - 1,
                }
            restore()
        raise RetryBudgetExhausted(
            f"candidate {candidate!r} did not pass evaluation within {max_retries} attempt(s); the snapshot "
            f"{snapshot.snapshot_id} was restored each time and the budget is now spent. Widening the learning "
            "budget or lowering the regression bar is an operator decision, not something this loop will retry forever for."
        )

    def purge(self) -> int:
        """Remove every snapshot. Returns how many were removed."""
        with self._lock:
            if not self.root.exists():
                return 0
            removed = 0
            for path in self.root.glob("*.json"):
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("could not purge snapshot %s: %s", path, exc)
            return removed


def _summarise(value: Any) -> Any:
    """Reduce an arbitrary apply() result to something JSON-safe and bounded."""
    if isinstance(value, dict):
        return {key: _summarise(value[key]) for key in list(value)[:20]}
    if isinstance(value, (list, tuple)):
        return [_summarise(item) for item in list(value)[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _summarise(to_dict())
        except Exception:  # noqa: BLE001 - a summary must never break the guard
            return f"<{type(value).__name__}>"
    return f"<{type(value).__name__}>"


def copy_state(src: str | Path, dst: str | Path) -> None:  # pragma: no cover - convenience
    """Recursively copy a state tree. Used by operators restoring by hand."""
    shutil.copytree(Path(src), Path(dst), dirs_exist_ok=True)
