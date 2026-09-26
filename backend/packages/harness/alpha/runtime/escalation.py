"""One handoff ledger, one escalation record, one crash-resume registry.

Alpha used to keep a separate, mutually invisible record for every kind of work
transfer: the project handoff store, the bot ``TaskHandoffPackage``, the swarm
plan snapshots, the subagent batch rows and the ephemeral bot leases. A question
an operator actually asks — *who handed this to whom, why, on which attempt,
and did anyone ever look at it?* — could not be answered from any of them, and
none of them had a total order across subsystems.

This module is the single answer to that question. It owns three durable
artifacts under ``<runtime_home>/handoff-ledger/``:

``ledger.jsonl``
    Append-only, globally ordered. Every entry carries
    ``(seq, kind, domain, task_id, from_ref, to_ref, reason, reason_class,
    attempt, max_attempts, recorded_at)``. ``seq`` is assigned under a
    cross-process byte-range lock by reading the last entry on disk, so two
    processes appending at the same moment still get one total order and never
    a duplicate. Domains are additive: ``agent``/``project``, ``bot``,
    ``subagent``, ``swarm`` and ``batch`` all land in this one file.

``escalations.json``
    The durable, queryable record of work handed to a HUMAN. One open record
    per (domain, task_id, reason): re-firing the same escalation returns the
    existing record instead of spamming a new one, and every escalation is also
    a ``kind="escalation"`` ledger entry so the ordering is visible in one log.

``work-units.json``
    The crash-resume journal. A worker registers the unit it is holding, with a
    lease, a checkpoint and the payload needed to rebuild the work. After a
    crash the survivors are exactly the units whose lease expired or whose
    owning process is gone, and :func:`recover_incomplete_work` hands them to a
    fresh worker (or escalates them when their attempt ceiling is spent).

Writes are fail-soft on purpose: recording a handoff must never be the reason a
worker dies, so every entry point here returns ``None``/a best-effort result and
logs instead of raising. Reads fail closed, like
:mod:`alpha.runtime.sentinel.report_store`: a corrupt line is reported, never
silently skipped.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.bots.failure_reasons import (
    ACTION_ESCALATE,
    ACTION_REASSIGN,
    ATTEMPTS_EXHAUSTED,
    FailureDecision,
    classify_work_failure,
    decide_failure,
    failure_class,
)
from alpha.config.runtime_paths import runtime_home

if os.name == "nt":  # pragma: no cover - platform-specific import
    import msvcrt
else:  # pragma: no cover - platform-specific import
    import fcntl

logger = logging.getLogger(__name__)

#: Directory under the runtime home that holds all three artifacts.
LEDGER_DIR_NAME = "handoff-ledger"
LEDGER_FILE_NAME = "ledger.jsonl"
ESCALATION_FILE_NAME = "escalations.json"
WORK_UNIT_FILE_NAME = "work-units.json"

#: Entry kinds. ``failure`` records why a unit failed without moving it;
#: ``handoff`` records work moving between workers; ``escalation`` records the
#: move to a human; ``resume`` records a crash recovery.
KIND_FAILURE = "failure"
KIND_HANDOFF = "handoff"
KIND_ESCALATION = "escalation"
KIND_RESUME = "resume"

#: Work domains sharing the ledger.
DOMAIN_AGENT = "agent"
DOMAIN_BOT = "bot"
DOMAIN_SUBAGENT = "subagent"
DOMAIN_SWARM = "swarm"
DOMAIN_BATCH = "batch"
DOMAIN_SENTINEL = "sentinel"
KNOWN_DOMAINS = (DOMAIN_AGENT, DOMAIN_BOT, DOMAIN_SUBAGENT, DOMAIN_SWARM, DOMAIN_BATCH, DOMAIN_SENTINEL)

#: The escalation target is a human operator, not another agent.
HUMAN = "human"

_APPEND_LOCK = threading.Lock()


class LedgerError(RuntimeError):
    """The ledger could not be read honestly (never repaired, never skipped)."""


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def process_identity() -> str:
    """Stable identity of THIS process, for crash detection in the journal.

    ``host:pid:started_at`` — the start time is what makes the identity unique.
    A bare pid is not: pids are recycled, and a busy host can hand a dead
    worker's pid to a brand-new process within seconds, which would make an
    abandoned unit look alive forever. Records written before the start time
    existed still parse; they fall back to the lease.
    """
    host = socket.gethostname()
    pid = os.getpid()
    try:
        import psutil

        return f"{host}:{pid}:{psutil.Process().create_time():.3f}"
    except Exception:  # noqa: BLE001 - identity degrades to host:pid, never fails
        return f"{host}:{pid}"


def _process_alive(owner: str) -> bool:
    """True when ``owner`` still looks alive on this host.

    A unit whose owner is on another host cannot be probed, so a foreign owner
    is trusted until its LEASE expires.

    Probed with :mod:`psutil` and never with ``os.kill``: on Windows
    ``os.kill(pid, 0)`` does not mean "check" — it calls ``TerminateProcess``,
    so a liveness probe written with it would kill the very worker it was
    checking. Any probe failure is treated as "alive" (fail-closed: never
    resume work that is still running).
    """
    host, _, rest = owner.partition(":")
    pid_text, _, started_text = rest.partition(":")
    if host != socket.gethostname() or not pid_text.isdigit():
        return True
    pid = int(pid_text)
    if pid == os.getpid():
        return True
    try:
        import psutil

        process = psutil.Process(pid)
        if started_text:
            return abs(process.create_time() - float(started_text)) < 1.0
        return process.is_running() and process.status() != getattr(psutil, "STATUS_ZOMBIE", "zombie")
    except ImportError:  # pragma: no cover - psutil is a hard dependency
        logger.warning("psutil is unavailable; trusting the lease for %s", owner)
        return True
    except Exception as exc:  # noqa: BLE001 - NoSuchProcess, AccessDenied, ...
        if type(exc).__name__ == "NoSuchProcess":
            return False
        logger.debug("Could not probe liveness of %s: %s", owner, exc, exc_info=True)
        return True


@contextmanager
def _file_lock(handle: Any) -> Iterator[None]:
    """Hold an advisory inter-process byte-range lock for one append.

    Same contract as :func:`alpha.runtime.sentinel.report_store._append_lock`:
    released by closing the handle, so there is no unlock step whose failure
    could be swallowed.
    """
    if os.name == "nt":  # pragma: no cover - platform-specific
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:  # pragma: no cover - platform-specific
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    yield


@dataclass(frozen=True)
class HandoffEntry:
    """One line of the global handoff ledger."""

    seq: int
    entry_id: str
    kind: str
    domain: str
    task_id: str
    from_ref: str
    to_ref: str
    reason: str
    reason_class: str
    attempt: int
    max_attempts: int
    recorded_at: float
    recorded_at_iso: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffEntry:
        return cls(
            seq=int(data.get("seq", 0)),
            entry_id=str(data.get("entry_id", "")),
            kind=str(data.get("kind", KIND_FAILURE)),
            domain=str(data.get("domain", "")),
            task_id=str(data.get("task_id", "")),
            from_ref=str(data.get("from_ref", "")),
            to_ref=str(data.get("to_ref", "")),
            reason=str(data.get("reason", "")),
            reason_class=str(data.get("reason_class", "")),
            attempt=int(data.get("attempt", 0) or 0),
            max_attempts=int(data.get("max_attempts", 0) or 0),
            recorded_at=float(data.get("recorded_at", 0.0) or 0.0),
            recorded_at_iso=str(data.get("recorded_at_iso", "")),
            details=dict(data.get("details") or {}),
        )


class HandoffLedger:
    """Append-only JSONL ledger with one global sequence."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def path(self) -> Path:
        return self._root / LEDGER_FILE_NAME

    # -- write --------------------------------------------------------------
    def append(
        self,
        *,
        kind: str,
        domain: str,
        task_id: str,
        from_ref: str,
        to_ref: str,
        reason: str,
        attempt: int = 0,
        max_attempts: int = 0,
        details: dict[str, Any] | None = None,
        at: float | None = None,
    ) -> HandoffEntry:
        """Durably append one entry, assigning the next global ``seq``.

        The sequence is read from the tail of the file WHILE the byte-range lock
        is held, so concurrent processes cannot mint the same number. One JSON
        object per line, UTF-8, ``ensure_ascii=False`` so real non-ASCII
        objective text survives verbatim.
        """
        now = float(at if at is not None else time.time())
        payload = {
            "kind": str(kind),
            "domain": str(domain),
            "task_id": str(task_id),
            "from_ref": str(from_ref),
            "to_ref": str(to_ref),
            "reason": str(reason),
            "reason_class": failure_class(reason),
            "attempt": int(attempt or 0),
            "max_attempts": int(max_attempts or 0),
            "recorded_at": now,
            "recorded_at_iso": _iso(now),
            "details": dict(details or {}),
        }
        path = self.path
        with _APPEND_LOCK:
            os.makedirs(self._root, exist_ok=True)
            with open(path, "a+b") as handle:
                with _file_lock(handle):
                    seq = _last_seq(handle) + 1
                    entry_id = f"ho-{uuid.uuid4().hex[:12]}"
                    line_payload = {**payload, "seq": seq, "entry_id": entry_id}
                    line_body = (json.dumps(line_payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                    handle.write(line_body)
                    handle.flush()
                    os.fsync(handle.fileno())
        return HandoffEntry.from_dict(line_payload)

    # -- read ---------------------------------------------------------------
    def entries(
        self,
        *,
        domain: str | None = None,
        task_id: str | None = None,
        kind: str | None = None,
        reason: str | None = None,
        limit: int | None = None,
    ) -> list[HandoffEntry]:
        """Entries in global order, oldest first. Raises on an unreadable line."""
        raw = self.read_all()
        rows = [HandoffEntry.from_dict(item) for item in raw]
        if domain is not None:
            rows = [r for r in rows if r.domain == domain]
        if task_id is not None:
            rows = [r for r in rows if r.task_id == task_id]
        if kind is not None:
            rows = [r for r in rows if r.kind == kind]
        if reason is not None:
            rows = [r for r in rows if r.reason == reason]
        rows.sort(key=lambda r: r.seq)
        return rows[-int(limit) :] if limit is not None else rows

    def read_all(self) -> list[dict[str, Any]]:
        path = self.path
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise LedgerError(f"failed to read handoff ledger {path}: {exc}") from exc
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                decoded = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise LedgerError(f"corrupt handoff ledger {path} at line {line_number}: not valid UTF-8 ({exc})") from exc
            try:
                row = json.loads(decoded)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"corrupt handoff ledger {path} at line {line_number}: invalid JSON ({exc})") from exc
            if not isinstance(row, dict):
                raise LedgerError(f"corrupt handoff ledger {path} at line {line_number}: expected an object, got {type(row).__name__}")
            rows.append(row)
        return rows

    def latest(self, **filters: Any) -> HandoffEntry | None:
        rows = self.entries(**filters)
        return rows[-1] if rows else None


def _last_seq(handle: Any) -> int:
    """Read the ``seq`` of the last complete line, under the held lock.

    Only the tail of the file is inspected (64 KiB window), so an append stays
    O(1) on a long-lived ledger. A truncated trailing line — a process killed
    mid-write — is ignored rather than trusted: the next append simply reuses
    the last COMPLETE sequence number.
    """
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size == 0:
        return 0
    window = min(size, 64 * 1024)
    handle.seek(size - window)
    data = handle.read(window)
    for raw_line in reversed(data.split(b"\n")):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(row, dict) and isinstance(row.get("seq"), int):
            return int(row["seq"])
    return 0


@dataclass
class EscalationRecord:
    """A durable, queryable record of work handed to a human."""

    escalation_id: str
    domain: str
    task_id: str
    from_ref: str
    to_ref: str
    reason: str
    reason_class: str
    attempt: int
    max_attempts: int
    detail: str
    status: str = "open"
    created_at: float = 0.0
    created_at_iso: str = ""
    ledger_seq: int | None = None
    acknowledged_by: str | None = None
    acknowledged_at: float | None = None
    resolved_by: str | None = None
    resolved_at: float | None = None
    resolution: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class EscalationStore:
    """Atomic JSON store of human escalations."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()
        self._rows: dict[str, EscalationRecord] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._root / ESCALATION_FILE_NAME

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerError(f"failed to read escalation store {path}: {exc}") from exc
        for item in (data or {}).get("escalations", []):
            record = EscalationRecord.from_dict(item)
            self._rows[record.escalation_id] = record

    def _save(self) -> None:
        payload = {
            "version": 1,
            "escalations": [r.to_dict() for r in sorted(self._rows.values(), key=lambda r: (r.created_at, r.escalation_id))],
        }
        path = self.path
        os.makedirs(self._root, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def open_escalation(
        self,
        *,
        domain: str,
        task_id: str,
        from_ref: str,
        reason: str,
        attempt: int = 0,
        max_attempts: int = 0,
        detail: str = "",
        ledger_seq: int | None = None,
        now: float | None = None,
    ) -> EscalationRecord:
        """Open an escalation, or return the open one that already covers it.

        Idempotent per (domain, task_id, reason) while the record is open: a
        worker that fails on every attempt, or a recovery sweep that runs
        twice, must not manufacture N identical pages for one human.
        """
        stamp = float(now if now is not None else time.time())
        with self._lock:
            self._load()
            for record in self._rows.values():
                if record.status != "open":
                    continue
                if (record.domain, record.task_id, record.reason) == (domain, task_id, reason):
                    if detail and detail not in record.detail:
                        record.detail = f"{record.detail}\n{detail}".strip()
                        record.attempt = max(record.attempt, int(attempt or 0))
                        self._save()
                    return record
            record = EscalationRecord(
                escalation_id=f"esc-{uuid.uuid4().hex[:12]}",
                domain=domain,
                task_id=task_id,
                from_ref=from_ref,
                to_ref=HUMAN,
                reason=reason,
                reason_class=failure_class(reason),
                attempt=int(attempt or 0),
                max_attempts=int(max_attempts or 0),
                detail=detail,
                status="open",
                created_at=stamp,
                created_at_iso=_iso(stamp),
                ledger_seq=ledger_seq,
            )
            record.history.append({"at": stamp, "at_iso": _iso(stamp), "event": "opened", "attempt": record.attempt})
            self._rows[record.escalation_id] = record
            self._save()
            return record

    def get(self, escalation_id: str) -> EscalationRecord | None:
        with self._lock:
            self._load()
            return self._rows.get(escalation_id)

    def list(
        self,
        *,
        status: str | None = None,
        domain: str | None = None,
        task_id: str | None = None,
    ) -> list[EscalationRecord]:
        """Oldest first. ``status=None`` returns every record."""
        with self._lock:
            self._load()
            rows = list(self._rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if domain is not None:
            rows = [r for r in rows if r.domain == domain]
        if task_id is not None:
            rows = [r for r in rows if r.task_id == task_id]
        return sorted(rows, key=lambda r: (r.created_at, r.escalation_id))

    def acknowledge(self, escalation_id: str, *, by: str) -> EscalationRecord | None:
        return self._transition(escalation_id, by=by, to_status="acknowledged", note="acknowledged")

    def resolve(self, escalation_id: str, *, by: str, note: str = "") -> EscalationRecord | None:
        return self._transition(escalation_id, by=by, to_status="resolved", note=note)

    def _transition(self, escalation_id: str, *, by: str, to_status: str, note: str) -> EscalationRecord | None:
        stamp = time.time()
        with self._lock:
            self._load()
            record = self._rows.get(escalation_id)
            if record is None:
                return None
            if to_status == "acknowledged":
                record.acknowledged_by = by
                record.acknowledged_at = stamp
            else:
                record.resolved_by = by
                record.resolved_at = stamp
                record.resolution = note
            record.status = to_status
            record.history.append({"at": stamp, "at_iso": _iso(stamp), "event": to_status, "by": by, "note": note})
            self._save()
            return record


@dataclass
class WorkUnit:
    """An in-flight unit of work, durable enough to be resumed after a crash."""

    unit_id: str
    domain: str
    task_id: str
    resume_key: str
    owner: str
    payload: dict[str, Any] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1
    max_attempts: int = 3
    state: str = "in_flight"
    lease_expires_at: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkUnit:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class WorkUnitStore:
    """Atomic JSON journal of in-flight work units with leases."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()
        self._rows: dict[str, WorkUnit] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._root / WORK_UNIT_FILE_NAME

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerError(f"failed to read work-unit store {path}: {exc}") from exc
        for item in (data or {}).get("units", []):
            unit = WorkUnit.from_dict(item)
            self._rows[unit.unit_id] = unit

    def _save(self) -> None:
        payload = {
            "version": 1,
            "units": [u.to_dict() for u in sorted(self._rows.values(), key=lambda u: (u.created_at, u.unit_id))],
        }
        path = self.path
        os.makedirs(self._root, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def register(
        self,
        *,
        domain: str,
        task_id: str,
        resume_key: str,
        payload: dict[str, Any] | None = None,
        checkpoint: dict[str, Any] | None = None,
        attempt: int = 1,
        max_attempts: int = 3,
        lease_seconds: float = 60.0,
        owner: str | None = None,
        now: float | None = None,
    ) -> WorkUnit:
        stamp = float(now if now is not None else time.time())
        unit = WorkUnit(
            unit_id=f"wu-{uuid.uuid4().hex[:12]}",
            domain=domain,
            task_id=task_id,
            resume_key=resume_key,
            owner=owner or process_identity(),
            payload=dict(payload or {}),
            checkpoint=dict(checkpoint or {}),
            attempt=int(attempt),
            max_attempts=int(max_attempts),
            state="in_flight",
            lease_expires_at=stamp + float(lease_seconds),
            created_at=stamp,
            updated_at=stamp,
        )
        with self._lock:
            self._load()
            self._rows[unit.unit_id] = unit
            self._save()
        return unit

    def get(self, unit_id: str) -> WorkUnit | None:
        with self._lock:
            self._load()
            return self._rows.get(unit_id)

    def find(self, *, domain: str, task_id: str) -> WorkUnit | None:
        with self._lock:
            self._load()
            for unit in self._rows.values():
                if unit.domain == domain and unit.task_id == task_id and unit.state == "in_flight":
                    return unit
        return None

    def heartbeat(self, unit_id: str, *, lease_seconds: float = 60.0, checkpoint: dict[str, Any] | None = None, now: float | None = None) -> bool:
        stamp = float(now if now is not None else time.time())
        with self._lock:
            self._load()
            unit = self._rows.get(unit_id)
            if unit is None or unit.state != "in_flight":
                return False
            unit.lease_expires_at = stamp + float(lease_seconds)
            unit.updated_at = stamp
            if checkpoint:
                unit.checkpoint.update(checkpoint)
            self._save()
            return True

    def set_state(self, unit_id: str, state: str, *, result: dict[str, Any] | None = None) -> bool:
        with self._lock:
            self._load()
            unit = self._rows.get(unit_id)
            if unit is None:
                return False
            unit.state = state
            unit.updated_at = time.time()
            if result is not None:
                unit.result.update(result)
            self._save()
            return True

    def list(self, *, domain: str | None = None, state: str | None = None) -> list[WorkUnit]:
        with self._lock:
            self._load()
            rows = list(self._rows.values())
        if domain is not None:
            rows = [u for u in rows if u.domain == domain]
        if state is not None:
            rows = [u for u in rows if u.state == state]
        return sorted(rows, key=lambda u: (u.created_at, u.unit_id))

    def orphaned(self, *, now: float | None = None, domain: str | None = None) -> list[WorkUnit]:
        """In-flight units whose owner is gone: lease expired, or process dead."""
        stamp = float(now if now is not None else time.time())
        rows = [u for u in self.list(state="in_flight", domain=domain) if u.lease_expires_at <= stamp or not _process_alive(u.owner)]
        return sorted(rows, key=lambda u: (u.created_at, u.unit_id))


# --- Module-level singletons ------------------------------------------------

_ledger: HandoffLedger | None = None
_escalations: EscalationStore | None = None
_work_units: WorkUnitStore | None = None
_singletons_lock = threading.Lock()


def default_ledger_root() -> Path:
    """Root of the unified ledger artifacts (honours ``AGENT_WORKSPACE_HOME``)."""
    override = os.getenv("ALPHA_HANDOFF_LEDGER_HOME")
    if override:
        return Path(override).expanduser()
    return runtime_home() / LEDGER_DIR_NAME


def get_handoff_ledger() -> HandoffLedger:
    global _ledger
    with _singletons_lock:
        root = default_ledger_root()
        if _ledger is None or _ledger.root != root:
            _ledger = HandoffLedger(root)
        return _ledger


def get_escalation_store() -> EscalationStore:
    global _escalations
    with _singletons_lock:
        root = default_ledger_root()
        if _escalations is None or _escalations.path.parent != root:
            _escalations = EscalationStore(root)
        return _escalations


def get_work_unit_store() -> WorkUnitStore:
    global _work_units
    with _singletons_lock:
        root = default_ledger_root()
        if _work_units is None or _work_units.path.parent != root:
            _work_units = WorkUnitStore(root)
        return _work_units


def reset_ledger_caches() -> None:
    """Drop the cached singletons (used when the runtime home changes)."""
    global _ledger, _escalations, _work_units
    with _singletons_lock:
        _ledger = None
        _escalations = None
        _work_units = None


# --- Recording entry points (fail-soft) ------------------------------------


def record_handoff(
    domain: str,
    task_id: str,
    from_ref: str,
    to_ref: str,
    *,
    reason: str,
    attempt: int = 0,
    max_attempts: int = 0,
    details: dict[str, Any] | None = None,
) -> HandoffEntry | None:
    """Record work moving from one worker to another, in the global order."""
    try:
        return get_handoff_ledger().append(
            kind=KIND_HANDOFF,
            domain=domain,
            task_id=task_id,
            from_ref=from_ref,
            to_ref=to_ref,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            details=details,
        )
    except Exception:
        logger.warning("Failed to record %s handoff %s -> %s", domain, from_ref, to_ref, exc_info=True)
        return None


def record_resume(
    domain: str,
    task_id: str,
    *,
    from_ref: str,
    to_ref: str,
    reason: str = "worker_crash",
    attempt: int = 0,
    max_attempts: int = 0,
    checkpoint: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> HandoffEntry | None:
    """Record interrupted work being picked up again after a crash."""
    payload = dict(details or {})
    if checkpoint:
        payload["checkpoint"] = dict(checkpoint)
    try:
        return get_handoff_ledger().append(
            kind=KIND_RESUME,
            domain=domain,
            task_id=task_id,
            from_ref=from_ref,
            to_ref=to_ref,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            details=payload,
        )
    except Exception:
        logger.warning("Failed to record %s resume for %s", domain, task_id, exc_info=True)
        return None


def escalate_to_human(
    domain: str,
    task_id: str,
    from_ref: str,
    *,
    reason: str,
    attempt: int = 0,
    max_attempts: int = 0,
    detail: str = "",
    details: dict[str, Any] | None = None,
) -> EscalationRecord | None:
    """Hand a unit to a human with a durable record and a ledger entry."""
    _, record = _escalate_with_entry(
        domain,
        task_id,
        from_ref,
        reason=reason,
        attempt=attempt,
        max_attempts=max_attempts,
        detail=detail,
        details=details,
    )
    return record


def _escalate_with_entry(
    domain: str,
    task_id: str,
    from_ref: str,
    *,
    reason: str,
    attempt: int = 0,
    max_attempts: int = 0,
    detail: str = "",
    details: dict[str, Any] | None = None,
) -> tuple[HandoffEntry | None, EscalationRecord | None]:
    """Escalate and hand back the ledger entry, so callers need not re-read."""
    try:
        store = get_escalation_store()
        payload = dict(details or {})
        if detail:
            payload["detail"] = detail
        entry = get_handoff_ledger().append(
            kind=KIND_ESCALATION,
            domain=domain,
            task_id=task_id,
            from_ref=from_ref,
            to_ref=HUMAN,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            details=payload,
        )
        record = store.open_escalation(
            domain=domain,
            task_id=task_id,
            from_ref=from_ref,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            detail=detail,
            ledger_seq=entry.seq,
        )
        return entry, record
    except Exception:
        logger.warning("Failed to escalate %s %s to a human", domain, task_id, exc_info=True)
        return None, None


def list_open_escalations(*, domain: str | None = None, task_id: str | None = None) -> list[EscalationRecord]:
    """Every unresolved escalation, oldest first. The operator's work queue."""
    try:
        return get_escalation_store().list(status="open", domain=domain, task_id=task_id)
    except Exception:
        logger.warning("Failed to list escalations", exc_info=True)
        return []


@dataclass
class FailureDisposition:
    """The outcome of routing one failed attempt through the shared taxonomy."""

    decision: FailureDecision
    entry: HandoffEntry | None = None
    escalation: EscalationRecord | None = None

    @property
    def escalated(self) -> bool:
        return self.escalation is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "entry": self.entry.to_dict() if self.entry else None,
            "escalation": self.escalation.to_dict() if self.escalation else None,
        }


def record_failure(
    domain: str,
    task_id: str,
    from_ref: str,
    *,
    attempt: int,
    max_attempts: int,
    error: str | None = None,
    reason: str | None = None,
    successor: str | None = None,
    detail: str = "",
    details: dict[str, Any] | None = None,
) -> FailureDisposition:
    """Classify one failed attempt and record it — the ONE place that decides.

    Every bounded work unit (swarm task, batch item, subagent, bot turn) routes
    its failures through here, so all of them share one classification, one
    ordering, and one escalation rule instead of a per-subsystem substring
    check. Returns the decision so the caller can act on it; the ledger entry
    and any escalation are already durable when this returns.
    """
    resolved = reason if reason else classify_work_failure(error or detail)
    decision = decide_failure(resolved, attempt=attempt, max_attempts=max_attempts)
    extra = dict(details or {})
    if decision.reason != resolved:
        extra["failure_reason"] = resolved
    if error:
        extra["error"] = str(error)[:2000]

    if decision.action == ACTION_ESCALATE:
        entry, record = _escalate_with_entry(
            domain,
            task_id,
            from_ref,
            reason=decision.reason,
            attempt=attempt,
            max_attempts=max_attempts,
            detail=detail or decision.detail,
            details=extra,
        )
        return FailureDisposition(decision=decision, entry=entry, escalation=record)

    to_ref = successor if (decision.action == ACTION_REASSIGN and successor) else from_ref
    kind = KIND_HANDOFF if to_ref != from_ref else KIND_FAILURE
    entry = record_handoff(
        domain,
        task_id,
        from_ref,
        to_ref,
        reason=decision.reason,
        attempt=attempt,
        max_attempts=max_attempts,
        details={**extra, "action": decision.action, "entry_kind": kind},
    )
    return FailureDisposition(decision=decision, entry=entry, escalation=None)


# --- Crash recovery ---------------------------------------------------------

_RESUME_HANDLERS: dict[str, Callable[[WorkUnit], Any]] = {}
_RESUME_HANDLERS_LOCK = threading.Lock()


def register_resume_handler(domain: str, handler: Callable[[WorkUnit], Any]) -> None:
    """Register the callable that re-dispatches a unit after a crash.

    Handlers are in-process registrations, so a restarted process must
    re-register them (that is what :func:`recover_incomplete_work` calls before
    it sweeps). A unit whose domain has no handler is still recorded as
    escalated-or-pending rather than being silently dropped.
    """
    with _RESUME_HANDLERS_LOCK:
        _RESUME_HANDLERS[domain] = handler


def get_resume_handler(domain: str) -> Callable[[WorkUnit], Any] | None:
    with _RESUME_HANDLERS_LOCK:
        return _RESUME_HANDLERS.get(domain)


def resume_pending_work(*, domain: str | None = None, now: float | None = None) -> dict[str, Any]:
    """Re-dispatch every orphaned unit to a fresh worker, once each.

    A unit whose attempt ceiling is already spent is NOT retried: it is
    escalated with :data:`ATTEMPTS_EXHAUSTED` and marked ``escalated`` in the
    journal, because re-dispatching it would be exactly the silent infinite
    retry the ceiling exists to prevent.
    """
    store = get_work_unit_store()
    outcomes: list[dict[str, Any]] = []
    for unit in store.orphaned(now=now, domain=domain):
        if unit.attempt >= unit.max_attempts:
            record = escalate_to_human(
                unit.domain,
                unit.task_id,
                unit.owner,
                reason=ATTEMPTS_EXHAUSTED,
                attempt=unit.attempt,
                max_attempts=unit.max_attempts,
                detail=f"crash recovery found {unit.unit_id} ({unit.resume_key}) with no attempts left",
                details={"unit_id": unit.unit_id, "resume_key": unit.resume_key, "checkpoint": dict(unit.checkpoint)},
            )
            store.set_state(unit.unit_id, "escalated", result={"escalation_id": record.escalation_id if record else None})
            outcomes.append({"unit_id": unit.unit_id, "task_id": unit.task_id, "outcome": "escalated", "escalation_id": record.escalation_id if record else None})
            continue
        handler = get_resume_handler(unit.domain)
        if handler is None:
            outcomes.append({"unit_id": unit.unit_id, "task_id": unit.task_id, "outcome": "unhandled", "resume_key": unit.resume_key})
            continue
        try:
            handler(unit)
        except Exception:
            logger.warning("Resume handler for %s (%s) raised", unit.domain, unit.unit_id, exc_info=True)
            outcomes.append({"unit_id": unit.unit_id, "task_id": unit.task_id, "outcome": "handler_error", "resume_key": unit.resume_key})
            continue
        record_resume(
            unit.domain,
            unit.task_id,
            from_ref=unit.owner,
            to_ref=process_identity(),
            reason="worker_crash",
            attempt=unit.attempt,
            max_attempts=unit.max_attempts,
            checkpoint=dict(unit.checkpoint),
            details={"unit_id": unit.unit_id, "resume_key": unit.resume_key},
        )
        store.set_state(unit.unit_id, "resumed", result={"resumed_by": process_identity()})
        outcomes.append({"unit_id": unit.unit_id, "task_id": unit.task_id, "outcome": "resumed", "resume_key": unit.resume_key})
    return {"outcomes": outcomes, "count": len(outcomes), "resumed": sum(1 for o in outcomes if o["outcome"] == "resumed"), "escalated": sum(1 for o in outcomes if o["outcome"] == "escalated")}


def recover_swarm_work(*, now: float | None = None, limit: int = 50, start_runner: bool = True) -> dict[str, Any]:
    """Resume swarm work abandoned by a dead process; escalate the spent ones.

    :meth:`SwarmCoordinator._load_persisted_swarms` already parks a restored
    plan (``status="paused"``, ``terminal_reason="process_restart_requires_resume"``)
    and returns its tasks to PENDING — but nothing ever resumed it, so the plan
    sat paused until a human pressed the button. Two things were also missing
    and are the real escape hatches:

    * a task's attempt ceiling is only enforced on the FAILURE path
      (``mark_failed``), so a task whose worker kept dying was re-claimed
      forever; here a parked or lease-expired task with no attempts left is
      escalated instead,
    * nothing restarted the runner, so the resumed plan had no driver.

    Only the public coordinator surface is used, and the in-memory plan object
    is the same one the coordinator checkpoints, so a requeue is persisted by
    the coordinator's own atomic write rather than by a second writer racing it.
    """
    from alpha.swarm.coordinator import get_swarm_coordinator
    from alpha.swarm.models import TaskNodeState, is_terminal_swarm_status

    stamp = float(now if now is not None else time.time())
    summary: dict[str, Any] = {"resumed_tasks": [], "escalated_tasks": [], "resumed_plans": [], "swarms": [], "errors": [], "notes": []}
    try:
        coordinator = get_swarm_coordinator()
        plans = coordinator.list_swarms(limit=limit)
    except Exception as exc:  # noqa: BLE001 - recovery must never crash its caller
        logger.warning("Swarm recovery could not load plans: %s: %s", type(exc).__name__, exc, exc_info=True)
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
        return summary

    parked = {plan.swarm_id for plan in plans if plan.status == "paused" and plan.terminal_reason == "process_restart_requires_resume"}

    for plan in plans:
        if is_terminal_swarm_status(plan.status):
            continue
        resumed: list[str] = []
        escalated: list[str] = []
        for task in plan.tasks.values():
            if task.state in (TaskNodeState.COMPLETED, TaskNodeState.FAILED, TaskNodeState.CANCELLED):
                continue
            if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING, TaskNodeState.QUEUED):
                lease_dead = task.lease_expires_at is None or float(task.lease_expires_at) <= stamp
                if not lease_dead:
                    continue  # a live worker still holds this task
            if task.attempts >= task.max_attempts:
                # The crashed attempts were already charged by claim_task, so a
                # ceiling spent here is spent: escalate, never claim a fourth.
                disposition = record_failure(
                    DOMAIN_SWARM,
                    task.task_id,
                    f"swarm:{plan.swarm_id}:{task.lease_owner or task.assigned_worker or 'worker'}",
                    attempt=task.attempts,
                    max_attempts=task.max_attempts,
                    reason="worker_crash",
                    detail=f"swarm {plan.swarm_id}: task {task.task_id} has no attempts left after {task.attempts} crashes",
                    details={"swarm_id": plan.swarm_id, "task_id": task.task_id, "worker": task.assigned_worker},
                )
                if disposition.escalation is not None:
                    task.state = TaskNodeState.FAILED
                    task.completed_at = stamp
                    task.lease_id = None
                    task.lease_owner = None
                    task.lease_expires_at = None
                    task.error_message = f"escalated to a human: {disposition.escalation.escalation_id}"
                    escalated.append(task.task_id)
                continue
            if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING, TaskNodeState.QUEUED):
                # A resume must NOT charge a second attempt for the crash.
                task.state = TaskNodeState.PENDING
                task.lease_id = None
                task.lease_owner = None
                task.lease_expires_at = None
                task.next_attempt_at = None
                record_resume(
                    DOMAIN_SWARM,
                    task.task_id,
                    from_ref=f"swarm:{plan.swarm_id}:{task.lease_owner or task.assigned_worker or 'worker'}",
                    to_ref=process_identity(),
                    reason="worker_crash",
                    attempt=task.attempts,
                    max_attempts=task.max_attempts,
                    details={"swarm_id": plan.swarm_id, "task_id": task.task_id, "worker": task.assigned_worker},
                )
                resumed.append(task.task_id)

        unpaused = plan.swarm_id in parked
        if unpaused:
            plan.status = "running"
            plan.terminal_reason = None
            summary["resumed_plans"].append(plan.swarm_id)
            record_resume(
                DOMAIN_SWARM,
                plan.swarm_id,
                from_ref=f"swarm:{plan.swarm_id}:{plan.terminal_reason or 'previous_process'}",
                to_ref=process_identity(),
                reason="worker_crash",
                details={"swarm_id": plan.swarm_id, "scope": "plan", "goal": str(getattr(plan, "goal", ""))[:500]},
            )
        if not resumed and not escalated and not unpaused:
            continue
        try:
            coordinator.append_event(
                plan.swarm_id,
                "SWARM_RECOVERED_AFTER_CRASH",
                details={"resumed_tasks": resumed, "escalated_tasks": escalated, "resumed_plan": unpaused},
            )
            coordinator.checkpoint(plan.swarm_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Swarm %s recovery checkpoint failed: %s", plan.swarm_id, exc, exc_info=True)
            summary["errors"].append(f"{plan.swarm_id}: {exc}")
        summary["swarms"].append(plan.swarm_id)
        summary["resumed_tasks"].extend(resumed)
        summary["escalated_tasks"].extend(escalated)
        if start_runner and (resumed or unpaused):
            _start_swarm_runner(coordinator, plan.swarm_id, summary)
    return summary


def _start_swarm_runner(coordinator: Any, swarm_id: str, summary: dict[str, Any]) -> None:
    """Restart the async driver only when a loop is already running."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        summary.setdefault("notes", []).append(f"{swarm_id}: requeued but not restarted (no running event loop)")
        return
    try:
        coordinator.start_async(swarm_id)
    except Exception as exc:  # noqa: BLE001 - a restart failure must not lose the requeue
        logger.warning("Swarm %s could not be restarted after recovery: %s", swarm_id, exc, exc_info=True)
        summary.setdefault("notes", []).append(f"{swarm_id}: restart failed: {exc}")


def recover_subagent_work(*, now: float | None = None) -> dict[str, Any]:
    """Resume subagents whose lease died with their process; escalate the spent."""
    from alpha.subagents.lifecycle import get_subagent_lifecycle_manager

    stamp = float(now if now is not None else time.time())
    summary: dict[str, Any] = {"resumed": [], "escalated": []}
    try:
        manager = get_subagent_lifecycle_manager()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Subagent recovery could not load records: %s: %s", type(exc).__name__, exc, exc_info=True)
        return summary
    return manager.recover_after_restart(now=stamp)


def recover_bot_work(*, now: float | None = None, domain: str = DOMAIN_BOT) -> dict[str, Any]:
    """Resume bot work units abandoned by a dead process.

    The ephemeral specialist lease store is TTL-based, not process-based, so a
    lease that has not expired is by definition still live. The crash signal
    for bot work is the journal: a unit whose owning process is gone.
    """
    return resume_pending_work(domain=domain, now=now)


def recover_incomplete_work(*, now: float | None = None, limit: int = 50, start_swarm_runner: bool = True) -> dict[str, Any]:
    """The restart hook: one sweep across every durable work unit.

    Idempotent and fail-soft per domain — a swarm that cannot be loaded is
    reported in ``errors`` and the batch/subagent/bot recovery still runs, so
    one broken subsystem cannot block the others from resuming.
    """
    stamp = float(now if now is not None else time.time())
    report: dict[str, Any] = {"at": stamp, "domains": {}}
    for name, runner in (
        ("swarm", lambda: recover_swarm_work(now=stamp, limit=limit, start_runner=start_swarm_runner)),
        ("subagent", lambda: recover_subagent_work(now=stamp)),
        ("bot", lambda: recover_bot_work(now=stamp)),
        ("work_units", lambda: resume_pending_work(now=stamp)),
    ):
        try:
            report["domains"][name] = runner()
        except Exception as exc:  # noqa: BLE001 - a domain must not block the sweep
            logger.warning("Restart recovery for %s failed: %s: %s", name, type(exc).__name__, exc, exc_info=True)
            report["domains"][name] = {"errors": [f"{type(exc).__name__}: {exc}"]}
    try:
        report["open_escalations"] = [r.escalation_id for r in list_open_escalations()]
    except Exception as exc:  # noqa: BLE001
        report["open_escalations"] = []
        report["domains"].setdefault("escalations", {"errors": [f"{type(exc).__name__}: {exc}"]})
    return report
