"""Durable JSONL history for Sentinel run reports.

One append-only journal (``<runtime_home>/sentinel-reports/reports.jsonl``)
holds every Sentinel pass — supervisor-driven ticks and manual API-triggered
passes alike — so the frontend can show what the engine actually did instead
of a truncated in-memory summary string.

Honesty contract (mirrors ``app/scheduler/job_memory.py``):

* Reads fail closed. A corrupt, unreadable, or schema-violating journal line
  raises a typed error naming the file and the 1-based line number. Bytes are
  never repaired, skipped, or rewritten, and a caller never receives a
  silently-missing prefix of the history.
* Writes fail closed before the bytes land: the entry is validated first, then
  appended with ``flush`` + ``os.fsync`` under an advisory byte-range lock, so
  a returned success means the record is durable. A write failure raises a
  typed error naming the file and the OS reason.
* The journal file is never rewritten or pruned; a capped read discloses the
  cap, the total on disk, and the source path in :class:`ReportHistory`.
* ``encoding="utf-8"`` on every text path; the JSON is written with
  ``ensure_ascii=False`` so real non-ASCII output survives verbatim.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from alpha.config.runtime_paths import runtime_home

if os.name == "nt":  # pragma: no cover - platform-specific import
    import msvcrt
else:  # pragma: no cover - platform-specific import
    import fcntl

#: Journal file name inside the store directory.
JOURNAL_NAME = "reports.jsonl"

# Every instance shares one journal file, so appends serialise on a
# process-wide lock before taking the inter-process byte-range lock.
_APPEND_LOCK = threading.Lock()


class ReportStoreError(RuntimeError):
    """Base class for Sentinel report-history failures."""


class ReportStoreReadError(ReportStoreError):
    """The journal exists but could not be read completely and honestly."""


class ReportStoreCorruptError(ReportStoreReadError):
    """A journal line is not valid, schema-conforming JSON. Never repaired."""


class ReportStoreWriteError(ReportStoreError):
    """A journal entry could not be durably appended."""


@dataclass(frozen=True)
class ReportHistory:
    """One capped, honest view of the journal for a reader."""

    #: Entries in journal order (oldest first), at most ``cap`` newest ones.
    entries: tuple[dict[str, Any], ...]
    #: Total entries recorded on disk, regardless of the cap.
    total: int
    #: The cap that was applied (None = no cap requested).
    cap: int | None
    #: Absolute path of the journal file that was read.
    source: str
    #: True when the journal does not exist yet (first observed pass).
    absent: bool

    def disclosures(self) -> list[str]:
        """Verbatim statements about what this reading did and did not show."""
        if self.absent:
            source_line = f"source: {self.source} (absent — no pass has been recorded yet)"
        else:
            source_line = f"source: {self.source}"
        if self.total == 0:
            shown_line = "history: no passes recorded yet (journal present but empty)"
        elif self.cap is None:
            shown_line = f"history: all {self.total} recorded pass(es) shown, oldest first"
        else:
            shown_line = (
                f"history: newest {len(self.entries)} of {self.total} recorded pass(es) shown, oldest first; "
                "the journal file is append-only and is never rewritten"
            )
        return [
            source_line,
            shown_line,
            "integrity: journal lines are read as-is; an unreadable line fails the whole read "
            "with the file name and line number instead of being skipped or repaired",
        ]


class SentinelReportStore:
    """Append-only JSONL journal for Sentinel run reports."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = os.fspath(root)

    @property
    def root(self) -> str:
        return self._root

    @property
    def path(self) -> str:
        return os.path.join(self._root, JOURNAL_NAME)

    # -- write ---------------------------------------------------------------
    def append(self, entry: dict[str, Any]) -> None:
        """Durably append one journal entry as a single JSON line. Raises on failure.

        An entry must be a JSON object carrying a non-empty ISO ``recorded_at``
        string and a JSON-object ``report`` (the ``RunReport.to_dict()`` payload).
        Validation happens before any byte is written, so a rejected entry never
        leaves a partial line behind.
        """
        if not isinstance(entry, dict):
            raise ReportStoreWriteError(f"journal entry must be a dict, got {type(entry).__name__}")
        recorded_at = entry.get("recorded_at")
        if not isinstance(recorded_at, str) or not recorded_at:
            raise ReportStoreWriteError("journal entry requires a non-empty string 'recorded_at' field")
        report = entry.get("report")
        if not isinstance(report, dict):
            raise ReportStoreWriteError(f"journal entry requires a dict 'report' field, got {type(report).__name__}")
        trigger = entry.get("trigger")
        if trigger is not None and (not isinstance(trigger, str) or not trigger):
            raise ReportStoreWriteError("'trigger' must be a non-empty string when present")
        auto_heal = entry.get("auto_heal")
        if auto_heal is not None and not isinstance(auto_heal, bool):
            raise ReportStoreWriteError("'auto_heal' must be a bool when present")
        try:
            line = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ReportStoreWriteError(f"journal entry is not JSON-serializable: {exc}") from exc
        path = self.path
        try:
            with _APPEND_LOCK:
                os.makedirs(self._root, exist_ok=True)
                with open(path, "ab") as handle:
                    with _append_lock(handle):
                        handle.write(line)
                        handle.flush()
                        os.fsync(handle.fileno())
        except OSError as exc:
            raise ReportStoreWriteError(f"failed to durably append sentinel report to {path}: {exc}") from exc

    # -- read ----------------------------------------------------------------
    def read_all(self) -> list[dict[str, Any]]:
        """Read every entry, in journal order. Raises typed errors; never repairs."""
        path = self.path
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ReportStoreReadError(f"failed to read sentinel report journal {path}: {exc}") from exc
        entries: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                decoded = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ReportStoreCorruptError(
                    f"corrupt sentinel report journal {path} at line {line_number}: not valid UTF-8 ({exc})"
                ) from exc
            try:
                entry = json.loads(decoded)
            except json.JSONDecodeError as exc:
                raise ReportStoreCorruptError(
                    f"corrupt sentinel report journal {path} at line {line_number}: invalid JSON ({exc})"
                ) from exc
            if not isinstance(entry, dict):
                raise ReportStoreCorruptError(
                    f"corrupt sentinel report journal {path} at line {line_number}: expected a JSON object, got {type(entry).__name__}"
                )
            if not isinstance(entry.get("recorded_at"), str) or not entry["recorded_at"]:
                raise ReportStoreCorruptError(
                    f"corrupt sentinel report journal {path} at line {line_number}: missing or invalid 'recorded_at' field"
                )
            if not isinstance(entry.get("report"), dict):
                raise ReportStoreCorruptError(
                    f"corrupt sentinel report journal {path} at line {line_number}: missing or invalid 'report' field"
                )
            entries.append(entry)
        return entries

    def history(self, *, limit: int | None = None) -> ReportHistory:
        """Capped read: the newest ``limit`` entries, oldest first, plus disclosures."""
        if limit is not None and limit < 1:
            raise ReportStoreReadError(f"limit must be >= 1 or None, got {limit!r}")
        path = self.path
        entries = self.read_all()
        total = len(entries)
        kept = entries if limit is None else entries[-limit:]
        return ReportHistory(
            entries=tuple(kept),
            total=total,
            cap=limit,
            source=path,
            absent=total == 0 and not os.path.exists(path),
        )


@contextmanager
def _append_lock(handle: Any) -> Iterator[None]:
    """Hold an advisory inter-process byte-range lock for one append.

    Released implicitly when the caller closes the handle, so there is no
    unlock step whose failure could be silently swallowed. On contention the
    platform blocking call raises ``OSError``; the caller converts that to
    :class:`ReportStoreWriteError`.
    """
    if os.name == "nt":  # pragma: no cover - platform-specific
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:  # pragma: no cover - platform-specific
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    yield


def default_sentinel_report_store() -> SentinelReportStore:
    """Store rooted in the writable Alpha state directory (per host)."""
    return SentinelReportStore(runtime_home() / "sentinel-reports")
