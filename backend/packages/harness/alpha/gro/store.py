"""GRO persistence: one atomic JSON document per run, honestly degraded.

Storage rules (matching the house pattern in ``groups/claims.py`` and the
durability contract in the harness guide):

- **Absent is not corrupt.** A missing file returns ``None`` and leaves
  ``is_degraded`` false — a first run must not report a fault.
- **Corrupt is disclosed.** An unreadable or unparseable document sets
  ``is_degraded`` / ``load_error`` and returns ``None``; it is never read as
  "no runs", because "my work could not be read" and "there is no work" lead
  to opposite decisions.
- **A write either landed or the caller is told it did not.** ``save`` returns
  ``False`` and stamps ``run.save_error`` rather than raising, so the runner
  can report an unpersisted run instead of claiming durability it lacks.
- **Single-process JSON, never cross-process exactly-once.** The ``FileLock``
  is advisory and filesystem-local: atomic and restart-recoverable for one
  Gateway, not a shared multi-worker repository.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from alpha.gro.models import GroRun

logger = logging.getLogger(__name__)

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_LISTED_RUNS = 50


def is_valid_run_id(run_id: str) -> bool:
    """Run ids address a file path, so they are validated, never repaired."""

    return bool(_RUN_ID_RE.match(run_id or ""))


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("_", (value or "").strip())[:64]
    return cleaned.strip("._-") or fallback


class GroStore:
    """Owner-scoped durable store for GRO runs under ``<root>/<owner>/<run_id>.json``."""

    def __init__(self, root: Path | str | None = None) -> None:
        if root is None:
            from alpha.config.runtime_paths import runtime_home

            root = runtime_home() / "gro"
        self.root = Path(root)
        self.is_degraded = False
        self.load_error: str | None = None

    # -- paths -----------------------------------------------------------
    def _owner_dir(self, owner_id: str) -> Path:
        return self.root / _safe_segment(owner_id, "_local")

    def _path(self, owner_id: str, run_id: str) -> Path:
        if not is_valid_run_id(run_id):
            raise ValueError(f"invalid GRO run id {run_id!r}: expected 1-64 chars of [A-Za-z0-9_.-] starting alphanumeric")
        return self._owner_dir(owner_id) / f"{run_id}.json"

    # -- write -----------------------------------------------------------
    def save(self, run: GroRun) -> bool:
        """Atomically persist ``run`` under a cross-process lock.

        Returns whether the write landed. On failure the store records the
        reason (``run.save_error``) and returns ``False`` — the caller must
        not claim durability it did not achieve.
        """

        path = self._path(run.owner_id, run.run_id)
        lock_path = path.with_suffix(".json.lock")
        try:
            from alpha.utils.file_lock import FileLock

            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(run.to_dict(), ensure_ascii=False, indent=2, default=str)
            with FileLock(lock_path, timeout=5.0):
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(path)
            run.save_error = None
            return True
        except Exception as exc:  # noqa: BLE001 - durability failure is data, not an escape
            run.save_error = f"{type(exc).__name__}: {exc}"
            logger.warning("GRO run %s was not persisted: %s", run.run_id, run.save_error, exc_info=True)
            return False

    # -- read ------------------------------------------------------------
    def load(self, run_id: str, *, owner_id: str) -> GroRun | None:
        """Load one run. ``None`` = absent (healthy) or unreadable (degraded)."""

        try:
            path = self._path(owner_id, run_id)
        except ValueError as exc:
            self.is_degraded = True
            self.load_error = str(exc)
            return None
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"expected a JSON object, got {type(data).__name__}")
            return GroRun.from_dict(data)
        except Exception as exc:  # noqa: BLE001 - a corrupt document is disclosed, never read as empty
            self.is_degraded = True
            self.load_error = f"{run_id}: {type(exc).__name__}: {exc}"
            logger.warning("GRO run %s could not be read: %s", run_id, self.load_error, exc_info=True)
            return None

    def list_runs(self, *, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Bounded owner-scoped summaries, newest first.

        An unreadable document still appears (flagged ``unreadable``) so a
        corrupt run cannot silently shorten the list.
        """

        bounded = max(1, min(int(limit), _MAX_LISTED_RUNS))
        owner_dir = self._owner_dir(owner_id)
        if not owner_dir.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            paths = sorted(owner_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError as exc:
            self.is_degraded = True
            self.load_error = f"listing {owner_dir}: {type(exc).__name__}: {exc}"
            return []
        for path in paths[:bounded]:
            summary: dict[str, Any] = {"run_id": path.stem}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError(f"expected a JSON object, got {type(data).__name__}")
                run = GroRun.from_dict(data)
                selection = run.selection.to_dict() if run.selection else None
                summary.update(
                    {
                        "state": run.state.value,
                        "mode": run.mode.value,
                        "task_type": run.contract.task_type.value,
                        "risk_tier": run.contract.risk_tier.value,
                        "candidate_count": len(run.candidate_specs),
                        "eligible_count": sum(1 for evaluation in run.evaluations.values() if evaluation.eligible),
                        "outcome": selection.get("outcome") if selection else None,
                        "selected_candidate_id": selection.get("selected_candidate_id") if selection else None,
                        "repair_round": run.repair_round,
                        "created_at": run.created_at,
                        "updated_at": run.updated_at,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - flag it, do not drop it
                summary["unreadable"] = True
                summary["error"] = f"{type(exc).__name__}: {exc}"
                logger.warning("GRO run file %s could not be read: %s", path.name, summary["error"], exc_info=True)
            entries.append(summary)
        return entries

    def append_event(self, *, owner_id: str, run_id: str, event: dict[str, Any]) -> bool:
        """Append one bounded JSONL event beside the run document.

        Best-effort observability: failure sets ``is_degraded`` and returns
        ``False`` rather than raising into the run's completion path.
        """

        try:
            path = self._path(owner_id, run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            events_path = path.with_suffix(".events.jsonl")
            line = json.dumps({"at": time.time(), **event}, ensure_ascii=False, default=str)
            with open(events_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return True
        except Exception as exc:  # noqa: BLE001
            self.is_degraded = True
            self.load_error = f"event append {run_id}: {type(exc).__name__}: {exc}"
            logger.warning("GRO event for run %s was not appended: %s", run_id, self.load_error, exc_info=True)
            return False

    def read_events(self, *, owner_id: str, run_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Tail of the run's event journal (newest last), for progress reads.

        Absent journal → ``[]`` while healthy. An unreadable journal sets
        ``is_degraded``/``load_error`` and returns ``[]`` — the caller must
        read that as "could not read", never "no events". A torn final line
        (an append in flight) is skipped: the journal is best-effort
        observability, never a decision input.
        """

        bounded = max(1, min(int(limit), _MAX_LISTED_RUNS))
        try:
            path = self._path(owner_id, run_id).with_suffix(".events.jsonl")
        except ValueError as exc:
            self.is_degraded = True
            self.load_error = str(exc)
            return []
        if not path.exists():
            return []
        try:
            entries: list[dict[str, Any]] = []
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        entry = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict):
                        entries.append(entry)
            return entries[-bounded:]
        except OSError as exc:
            self.is_degraded = True
            self.load_error = f"events {run_id}: {type(exc).__name__}: {exc}"
            logger.warning("GRO event journal for run %s could not be read: %s", run_id, self.load_error, exc_info=True)
            return []
