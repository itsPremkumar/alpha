"""Durable, scoped memory journal for scheduled (cron) job runs.

Hermes-style design: every scheduled job owns an append-only JSONL journal on
disk (``<root>/job-<id>.jsonl``). Each launch appends one ``dispatched``
entry *before* the run is created; each terminal observation appends one
``outcome`` entry. Before the next launch the service reads the prior journal
and injects it into the run prompt inside a ``<memory>`` block.

Honesty contract:

* Reads fail closed. A corrupt, unreadable, or schema-violating journal line
  raises a typed error naming the file and the 1-based line number. Bytes are
  never repaired, skipped, or rewritten, and a launch never proceeds with
  silently missing prior context.
* Writes fail closed before launch: the ``dispatched`` entry is journaled
  before ``_launch_run``, so a write failure aborts the occurrence while no
  run exists yet. A write failure after a run is already live or terminal
  cannot fail closed; it is logged at ERROR and disclosed as a note in the
  next run's prompt. No fake outcome entry is ever written in its place.
* Retention / TTL pruning happens at read time only and is disclosed
  verbatim in every rendered context block; the journal file itself is never
  rewritten, so no history is destroyed silently.
* ``dispatched`` entries with no observed terminal outcome render as
  ``NO OUTCOME RECORDED`` — a run whose outcome was never observed (crash,
  restart, write failure) is never presented as having finished.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home

if os.name == "nt":  # pragma: no cover - platform-specific import
    import msvcrt
else:  # pragma: no cover - platform-specific import
    import fcntl


class JobMemoryError(RuntimeError):
    """Base class for scheduled-job memory journal failures."""


class JobMemoryReadError(JobMemoryError):
    """The journal exists but could not be read completely and honestly."""


class JobMemoryCorruptError(JobMemoryReadError):
    """A journal line is not valid, schema-conforming JSON. Never repaired."""


class JobMemoryWriteError(JobMemoryError):
    """A journal entry could not be durably appended."""


_UNSAFE_JOB_ID = re.compile(r"[^A-Za-z0-9_-]+")


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@contextmanager
def _append_lock(handle: Any) -> Iterator[None]:
    """Hold an advisory inter-process byte-range lock for one append.

    The lock is released implicitly when the caller closes the handle, so
    there is no unlock step whose failure could be silently swallowed. On
    contention the platform blocking call raises ``OSError`` after its retry
    window; the caller converts that to :class:`JobMemoryWriteError`.
    """
    if os.name == "nt":  # pragma: no cover - platform-specific
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:  # pragma: no cover - platform-specific
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    yield


class JobMemoryStore:
    """Append-only JSONL journal, one file per scheduled job."""

    def __init__(
        self,
        root: Path,
        *,
        retention_runs: int | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        if retention_runs is not None and retention_runs < 1:
            raise ValueError("retention_runs must be >= 1 or None")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0 or None")
        self._root = Path(root)
        self._retention_runs = retention_runs
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not job_id:
            raise ValueError(f"job id must be a non-empty string, got {job_id!r}")
        # Generated task ids (UUIDs) pass through unchanged. Anything else is
        # flattened to a safe file-name stem; two hostile ids could collide
        # only if they differ solely in replaced characters.
        safe = _UNSAFE_JOB_ID.sub("-", job_id).strip("-")
        if not safe:
            raise ValueError(f"job id {job_id!r} has no safe file-name form")
        return self._root / f"job-{safe}.jsonl"

    def append(self, job_id: str, entry: dict[str, Any]) -> None:
        """Durably append one JSON object as a single line. Raises on failure."""
        try:
            path = self._path_for(job_id)
        except ValueError as exc:
            raise JobMemoryWriteError(str(exc)) from exc
        if not isinstance(entry, dict):
            raise JobMemoryWriteError(f"journal entry must be a dict, got {type(entry).__name__}")
        if not isinstance(entry.get("event"), str) or not entry["event"]:
            raise JobMemoryWriteError("journal entry requires a non-empty string 'event' field")
        if not isinstance(entry.get("recorded_at"), str) or not entry["recorded_at"]:
            raise JobMemoryWriteError("journal entry requires a non-empty string 'recorded_at' field")
        try:
            line = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise JobMemoryWriteError(f"journal entry for job {job_id!r} is not JSON-serializable: {exc}") from exc
        try:
            with self._lock:
                self._root.mkdir(parents=True, exist_ok=True)
                with open(path, "ab") as handle:
                    with _append_lock(handle):
                        handle.write(line)
                        handle.flush()
                        os.fsync(handle.fileno())
        except OSError as exc:
            raise JobMemoryWriteError(f"failed to durably append journal entry to {path}: {exc}") from exc

    def read(self, job_id: str) -> list[dict[str, Any]]:
        """Read every entry, in order. Raises typed errors; never repairs."""
        try:
            path = self._path_for(job_id)
        except ValueError as exc:
            raise JobMemoryReadError(str(exc)) from exc
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise JobMemoryReadError(f"failed to read journal {path}: {exc}") from exc
        entries: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                decoded = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise JobMemoryCorruptError(f"corrupt journal {path} at line {line_number}: not valid UTF-8 ({exc})") from exc
            try:
                entry = json.loads(decoded)
            except json.JSONDecodeError as exc:
                raise JobMemoryCorruptError(f"corrupt journal {path} at line {line_number}: invalid JSON ({exc})") from exc
            if not isinstance(entry, dict):
                raise JobMemoryCorruptError(
                    f"corrupt journal {path} at line {line_number}: expected a JSON object, got {type(entry).__name__}"
                )
            if not isinstance(entry.get("event"), str) or not entry["event"]:
                raise JobMemoryCorruptError(f"corrupt journal {path} at line {line_number}: missing or invalid 'event' field")
            if not isinstance(entry.get("recorded_at"), str) or not entry["recorded_at"]:
                raise JobMemoryCorruptError(f"corrupt journal {path} at line {line_number}: missing or invalid 'recorded_at' field")
            entries.append(entry)
        return entries

    def retention_disclosure(self) -> list[str]:
        """Verbatim disclosure of what this store prunes and how."""
        if self._retention_runs is None:
            retention_line = "retention: not configured; every recorded run is eligible to be shown"
        else:
            retention_line = (
                f"retention: only the most recent {self._retention_runs} run(s) are shown; "
                "older runs are pruned at read time and the journal file is never rewritten"
            )
        if self._ttl_seconds is None:
            ttl_line = "ttl: not configured; no run ages out"
        else:
            ttl_line = (
                f"ttl: runs journaled more than {self._ttl_seconds:g} second(s) ago are pruned at read time "
                "and the journal file is never rewritten"
            )
        return [retention_line, ttl_line]

    def prior_context_block(self, job_id: str) -> str:
        """Render the prior-run chronology for injection into a run prompt.

        Raises :class:`JobMemoryReadError` (fail closed) on any read problem;
        the caller must not launch with silently missing prior context.
        """
        entries = self.read(job_id)
        try:
            path = self._path_for(job_id)
        except ValueError as exc:  # read() already validated the id; unreachable in practice
            raise JobMemoryReadError(str(exc)) from exc
        groups = self._group_runs(entries)
        shown = self._apply_retention(groups, now=datetime.now(UTC))
        header = f"Scheduled job memory for job {job_id} (source: {path})"
        disclosures = self.retention_disclosure()
        if not entries:
            body = ["No prior runs recorded: journal absent or empty (first observed run for this job)."]
        elif not shown:
            body = [
                f"Journal holds {len(entries)} recorded entry/entries but none survive the retention policy "
                "above; no prior run context is shown."
            ]
        else:
            body = [f"{len(shown)} prior run(s) shown, oldest first:"]
            for index, group in enumerate(shown, start=1):
                body.extend(self._render_run(index, group))
        return "\n".join([header, *disclosures, *body])

    @staticmethod
    def _group_runs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for entry in entries:
            if entry["event"] == "dispatched":
                current = {"dispatched": entry, "events": []}
                groups.append(current)
            else:
                if current is None:
                    current = {"dispatched": None, "events": []}
                    groups.append(current)
                current["events"].append(entry)
        return groups

    def _apply_retention(self, groups: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
        kept = groups
        if self._ttl_seconds is not None:
            cutoff = now - timedelta(seconds=self._ttl_seconds)
            kept = [group for group in kept if self._ttl_keeps(group, cutoff=cutoff)]
        if self._retention_runs is not None and len(kept) > self._retention_runs:
            kept = kept[-self._retention_runs :]
        return kept

    @staticmethod
    def _ttl_keeps(group: dict[str, Any], *, cutoff: datetime) -> bool:
        anchor = group["dispatched"] or (group["events"][0] if group["events"] else None)
        if anchor is None:
            return True
        timestamp = _parse_timestamp(anchor.get("recorded_at"))
        if timestamp is None:
            # Unparseable history is never silently dropped by age.
            return True
        return timestamp >= cutoff

    @staticmethod
    def _render_run(index: int, group: dict[str, Any]) -> list[str]:
        dispatched = group["dispatched"]
        if dispatched is None:
            head = f"[{index}] dispatch record missing (outcome entries recorded without a preceding dispatch entry)"
        else:
            head = (
                f"[{index}] dispatched: journaled_at={dispatched.get('recorded_at')}, "
                f"trigger={dispatched.get('trigger')!r}, run_id={dispatched.get('run_id')!r}, "
                f"occurrence={dispatched.get('task_run_id')!r}"
            )
        lines = [head]
        events = group["events"]
        terminal_seen = False
        for event in events:
            if event["event"] == "outcome":
                terminal_seen = True
                lines.append(JobMemoryStore._render_outcome(event))
            else:
                lines.append(f"    event: {event['event']} at {event.get('recorded_at')}")
        if not terminal_seen:
            lines.append("    outcome: NO OUTCOME RECORDED (no terminal outcome observed for this run)")
        return lines

    @staticmethod
    def _render_outcome(event: dict[str, Any]) -> str:
        status = event.get("status")
        if not isinstance(status, str) or not status:
            status = "unknown-status (entry lacks a valid 'status' field)"
        duration = event.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            duration_text = "unknown (run start time was not observed by this process)"
        else:
            duration_text = f"{float(duration):.3f}s"
        finished = event.get("finished_at")
        line = (
            f"    outcome: {status}; finished_at={finished if finished is not None else 'unknown'}; "
            f"duration={duration_text}"
        )
        run_id = event.get("run_id")
        if run_id:
            line += f"; run_id={run_id}"
        error = event.get("error")
        if error:
            line += f"; error={error}"
        note = event.get("duration_note")
        if note:
            line += f"; note={note}"
        return line


def default_job_memory_store() -> JobMemoryStore:
    """Store rooted in the writable Alpha state directory (per host)."""
    return JobMemoryStore(runtime_home() / "scheduled-job-memory")
