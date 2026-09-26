"""Durable state, history, and cross-process locking for source updates.

The existing Phase-1 ``update_state.json`` remains the public state contract
used by ``GET /api/evolution/update-state`` and runtime identity.  This module
adds the richer transaction fields and an append-only audit trail without
silently discarding a corrupt or unreadable state file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution import release_check

logger = logging.getLogger(__name__)

HISTORY_FILE = "update_history.jsonl"
LOCK_DIRECTORY = "update.lock"
LOCK_OWNER_FILE = "owner.json"
MAINTENANCE_FILE = "update_maintenance.json"
RUNTIME_STATUS_FILE = "update_runtime_status.json"
DEFAULT_LOCK_STALE_SECONDS = 6 * 60 * 60
MAX_HISTORY_BYTES = 5 * 1024 * 1024
MAX_HISTORY_RECORDS = 2_000
MAX_STATE_STRING = 8_000


class UpdateBusyError(RuntimeError):
    """Another updater process currently owns the transaction lock."""


class UpdateStateError(RuntimeError):
    """Persisted update state is not safe to use."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _redact_text(value: str) -> str:
    """Remove common credential forms before state/history reaches disk."""
    text = value.replace("\x00", "")
    text = re.sub(
        r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b((?:github[_-]?token|gh[_-]?token|access[_-]?token|api[_-]?key|password|secret)\s*[:=]\s*)[^\s,;&]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    return text[:MAX_STATE_STRING]


def redact_update_text(value: object) -> str:
    """Public bounded diagnostic redactor for updater logs and state."""
    return _redact_text(str(value or ""))


def safe_state_snapshot(value: Any) -> Any:
    """Return a bounded, credential-redacted copy of an update payload."""
    return _safe_state_value(value)


def _safe_state_value(value: Any) -> Any:
    """Recursively bound and redact values persisted in update state."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(key)[:200]: _safe_state_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_state_value(item) for item in value[:500]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(str(value))


def _state_dir() -> Path:
    return runtime_home()


def _history_path() -> Path:
    return _state_dir() / HISTORY_FILE


def _lock_path() -> Path:
    return _state_dir() / LOCK_DIRECTORY


def _maintenance_path() -> Path:
    return _state_dir() / MAINTENANCE_FILE


def _runtime_status_path() -> Path:
    return _state_dir() / RUNTIME_STATUS_FILE


def maintenance_record() -> dict[str, Any] | None:
    """Return the active update-maintenance marker, if one exists.

    A malformed marker is treated as active (fail closed): a crash or a
    partial write must never make a new run look safe while an updater may
    still be between checkout switch and restart.
    """
    path = _maintenance_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        return {"active": True, "reason": "update maintenance marker is unreadable"}
    if not isinstance(raw, dict) or raw.get("active") is not True:
        return None
    return _safe_state_value(raw)  # type: ignore[return-value]


def maintenance_active() -> bool:
    """Whether run admission must remain closed for an update transaction."""
    return maintenance_record() is not None


def write_maintenance(transaction_id: str, *, phase: str) -> Path:
    """Publish a cross-process admission barrier before checkout mutation."""
    existing = maintenance_record()
    if existing is not None and existing.get("transactionId") not in {None, transaction_id}:
        raise UpdateBusyError("another source update already owns the maintenance barrier")
    path = _maintenance_path()
    payload = {
        "active": True,
        "transactionId": transaction_id,
        "phase": phase,
        "pid": os.getpid(),
        "startedAt": _now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)
    return path


def clear_maintenance(transaction_id: str | None = None) -> None:
    """Clear only this transaction's marker; never remove a peer's marker."""
    path = _maintenance_path()
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError, TypeError):
        # A malformed marker is safer to remove during explicit recovery than
        # to strand every future run behind it forever.
        with suppress(OSError):
            path.unlink(missing_ok=True)
        return
    if transaction_id is not None and current.get("transactionId") not in {None, transaction_id}:
        return
    with suppress(OSError):
        path.unlink(missing_ok=True)


def write_runtime_status(*, active: bool, source: str = "gateway") -> None:
    """Publish a tiny best-effort activity snapshot for detached helpers."""
    path = _runtime_status_path()
    payload = {
        "active": bool(active),
        "source": source[:80],
        "pid": os.getpid(),
        "updatedAt": _now_iso(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        logger.debug("Could not publish update runtime status: %s", exc)


def runtime_has_active_work() -> bool:
    """Read the Gateway's last activity snapshot; missing/stale means idle."""
    try:
        raw = json.loads(_runtime_status_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return False
    if not isinstance(raw, dict):
        return False
    try:
        age = time.time() - _runtime_status_path().stat().st_mtime
    except OSError:
        return False
    # A stale snapshot must not deadlock updates after a Gateway crash.  The
    # maintenance marker and the final pre-switch check remain authoritative.
    if age > 15.0:
        return False
    return raw.get("active") is True


def _transition_fields(phase: str, **values: Any) -> dict[str, Any]:
    state = dict(release_check.load_update_state())
    state["state"] = phase
    state["updatedAt"] = _now_iso()
    # ``None`` is meaningful here: it explicitly clears a previous candidate,
    # target, or error when a transition reaches UP_TO_DATE/HEALTHY. Fields
    # not supplied remain unchanged because they are absent from ``values``.
    state.update(values)
    return state


def save_state(phase: str, **values: Any) -> dict[str, Any]:
    """Persist one state-machine phase and return the exact stored payload."""
    state = _safe_state_value(_transition_fields(phase, **values))
    if state.get("stateCorrupt") is True:
        raise UpdateStateError("Cannot overwrite an unreadable update state; recover or repair it explicitly")
    # Keep the Phase-1 spelling as the canonical short version field while
    # exposing the richer candidate fields used by the Phase-2 engine.
    if "availableVersion" in values:
        state["latestTag"] = values["availableVersion"]
    if not release_check._save_update_state(state):
        raise UpdateStateError("Could not persist update state")
    return state


def load_state() -> dict[str, Any]:
    """Load the public state, converting a corrupt file into a hard error."""
    state = release_check.load_update_state()
    if state.get("state") == release_check.CHECK_FAILED and state.get("error"):
        # ``load_update_state`` intentionally returns a CHECK_FAILED disclosure
        # for a corrupt file.  Callers that need to mutate state must not treat
        # that disclosure as a valid empty transaction.  The explicit marker
        # avoids confusing an upstream error containing the word "unreadable"
        # with local state corruption.
        if state.get("stateCorrupt") is True:
            raise UpdateStateError(str(state["error"]))
    return state


def _rotate_history_if_needed(path: Path) -> None:
    """Keep the audit trail bounded without retaining unbounded raw output."""
    try:
        if path.stat().st_size <= MAX_HISTORY_BYTES:
            return
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, ValueError):
        return
    retained = lines[-MAX_HISTORY_RECORDS:]
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        logger.warning("Could not rotate update history %s: %s", path, exc)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def append_history(event: str, **fields: Any) -> None:
    """Append one bounded, secret-free update event.

    History is best effort for observability, but malformed existing lines are
    reported rather than silently treated as valid records.
    """
    path = _history_path()
    record: dict[str, Any] = {
        "event": event,
        "at": _now_iso(),
        "pid": os.getpid(),
    }
    for key, value in fields.items():
        safe_value = _safe_state_value(value)
        record[str(key)[:100]] = safe_value if isinstance(safe_value, (str, int, float, bool, type(None))) else str(safe_value)[:1_000]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_history_if_needed(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Could not persist update history to %s: %s", path, exc)


def history(limit: int = 50) -> list[dict[str, Any]]:
    """Return newest-last history records, skipping corrupt lines honestly."""
    limit = max(1, min(int(limit), 500))
    path = _history_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Could not read update history %s: %s", path, exc)
        return []
    records: list[dict[str, Any]] = []
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            skipped += 1
    if skipped:
        logger.warning("Skipped %d corrupt update-history line(s) in %s", skipped, path)
    return [_safe_state_value(record) for record in records[-limit:]]


def _lock_is_stale(path: Path, stale_seconds: float) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True
    if age < stale_seconds:
        return False
    owner = path / LOCK_OWNER_FILE
    try:
        payload = json.loads(owner.read_text(encoding="utf-8"))
        pid = int(payload.get("pid", 0))
    except (OSError, ValueError, TypeError):
        return True
    if pid <= 0:
        return True
    # A process from another host cannot be checked with os.kill(pid, 0).
    # Never steal a recent lock; age is the conservative fallback.
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return False


class FileUpdateLock:
    """Atomic directory lock shared by CLI, Gateway, and scheduled helpers."""

    def __init__(self, *, stale_seconds: float = DEFAULT_LOCK_STALE_SECONDS) -> None:
        self.path = _lock_path()
        self.stale_seconds = max(60.0, float(stale_seconds))
        self._owned = False
        self._depth = 0

    def __enter__(self) -> FileUpdateLock:
        if self._owned:
            self._depth += 1
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.mkdir()
            self._owned = True
        except FileExistsError as exc:
            if _lock_is_stale(self.path, self.stale_seconds):
                # Rename the stale directory out of the way atomically.  A
                # second process can then only observe either the old stale
                # directory or the new owner; neither can delete a live lock.
                quarantine = self.path.with_name(f"{self.path.name}.reclaim.{uuid.uuid4().hex[:8]}")
                try:
                    os.replace(self.path, quarantine)
                    shutil.rmtree(quarantine)
                    self.path.mkdir()
                    self._owned = True
                except FileExistsError:
                    raise UpdateBusyError(f"Another update transaction owns {self.path}") from exc
                except OSError as stale_exc:
                    raise UpdateBusyError(f"Update lock is stale but could not be reclaimed: {self.path}") from stale_exc
            else:
                raise UpdateBusyError(f"Another update transaction owns {self.path}") from exc
        self._depth = 1
        if self._owned:
            owner = {
                "pid": os.getpid(),
                "started_at": _now_iso(),
                "hostname": os.environ.get("COMPUTERNAME", ""),
            }
            owner_path = self.path / LOCK_OWNER_FILE
            temporary = owner_path.with_name(f"{owner_path.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                temporary.write_text(json.dumps(owner), encoding="utf-8")
                os.replace(temporary, owner_path)
            except OSError as exc:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
                self.__exit__(None, None, None)
                raise UpdateBusyError(f"Could not write update lock owner: {self.path}") from exc
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if not self._owned:
            return
        self._depth -= 1
        if self._depth > 0:
            return
        try:
            shutil.rmtree(self.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove update lock %s: %s", self.path, exc)
        self._owned = False
        self._depth = 0


@contextmanager
def update_lock(*, stale_seconds: float = DEFAULT_LOCK_STALE_SECONDS) -> Iterator[FileUpdateLock]:
    """Context-manager wrapper retained for readable transaction code."""
    with FileUpdateLock(stale_seconds=stale_seconds) as lock:
        yield lock


class StateWriter:
    """Serialize state writes within one process and append phase history."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def transition(self, phase: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            state = save_state(phase, **values)
            append_history(
                "phase",
                phase=phase,
                transaction_id=state.get("transactionId"),
                reason=state.get("reason"),
                from_commit=state.get("previousCommit"),
                to_commit=state.get("targetCommit"),
            )
            return state

    def fail(self, reason: str, **values: Any) -> dict[str, Any]:
        current = load_state()
        attempts = current.get("failedAttempts")
        if not isinstance(attempts, int) or attempts < 0:
            attempts = 0
        values.setdefault("failedAttempts", attempts + 1)
        return self.transition(release_check.CHECK_FAILED, error=reason, reason=reason, **values)


__all__ = [
    "FileUpdateLock",
    "StateWriter",
    "UpdateBusyError",
    "UpdateStateError",
    "append_history",
    "clear_maintenance",
    "history",
    "load_state",
    "maintenance_active",
    "maintenance_record",
    "redact_update_text",
    "runtime_has_active_work",
    "safe_state_snapshot",
    "save_state",
    "update_lock",
    "write_maintenance",
    "write_runtime_status",
]
