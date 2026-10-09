"""APEX state: the durable session row, the event journal, and the store.

Spec §10 (Mission), §51 (event model) and §120/§121 (checkpoints). All three
exist already — ``alpha.mission.lifecycle`` has the event feed and the gated
terminal transition, ``alpha.missions.store`` has the durable rows and the
JSONL journal — so this module **adapts** them rather than repeating them.

**Why adapt instead of extend.** The existing ``MissionStore`` writes JSON and is
process-local. The APEX session needs three things it does not have: the autonomy
contract's digest (so a restart can detect policy drift, spec §162), a bounded
usage ledger (so ``/apex status`` can report consumption instead of
predicting it), and an explicit ``steering`` channel (spec §57, a mission
constraint rather than a prompt rewrite). Extending the existing row would have
added three fields to a dataclass another router already serialises.

**What this store does not own.** It does not own lifecycle. A session row
records what the contract and the executive decided; the phase machine in
:mod:`alpha.mission.lifecycle` remains the only thing that decides whether a
mission may reach a terminal state, because that gate is what makes
"completed" mean verified.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.apex.locking import cross_process_file_lock
from alpha.mission.lifecycle import MissionEvent, MissionEventFeed

logger = logging.getLogger(__name__)

__all__ = [
    "APEX_EVENTS",
    "ApexEvent",
    "ApexPersistenceError",
    "ApexSession",
    "ApexSessionState",
    "ApexStore",
    "get_apex_store",
]


class ApexPersistenceError(OSError):
    """A control change could not be committed to the session snapshot."""


class ApexSessionState(StrEnum):
    """The live session states, distinct from ``MissionPhase``.

    ``MissionPhase`` describes whether the *work* is proven; this describes
    whether the *control plane* is running. Conflating them was the specific
    mistake worth avoiding: a session can be ``PAUSED`` while its mission is
    mid-``VERIFYING``, and it can be ``COMPLETED`` only once the mission passed.
    """

    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (ApexSessionState.COMPLETED, ApexSessionState.FAILED, ApexSessionState.CANCELLED)


@dataclass
class ApexEvent(MissionEvent):
    """An APEX observation.

    Subclasses ``MissionEvent`` so it is accepted by the existing
    ``MissionEventFeed`` and its SSE replay logic unchanged — which is how the
    ``/api/apex/events`` route gets ``Last-Event-ID`` handling for free instead
    of a second cursor implementation.
    """


@dataclass
class UsageLedger:
    """Measured consumption for one session.

    ``None`` means "not measured", never ``0``. The repository states this rule
    for swarm telemetry (``tool_error_rate = None``, not a reassuring zero) and
    for host metrics; a zero here would read as "nothing was spent" when the
    truth is "nothing was counted".
    """

    tool_calls: int | None = None
    llm_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    replans: int | None = None
    retries: int | None = None
    retry_counts_by_failure_class: dict[str, int] = field(default_factory=dict)
    #: Source run ids already charged to a failure-class retry allowance.
    #: This makes a retry reservation replay-safe across a process restart.
    retry_reservations: dict[str, str] = field(default_factory=dict)
    measured_run_ids: list[str] = field(default_factory=list)
    #: Latest cumulative RunManager counters for runs without complete token
    #: usage events. Replacing snapshots makes live polling safe.
    run_snapshots: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Last durable Gateway event sequence incorporated for each run. The
    #: cursor and counters commit together so a restart cannot double count.
    event_cursors: dict[str, int] = field(default_factory=dict)
    #: Runs whose event stream has supplied measured token usage. For runs
    #: without that evidence, cumulative RunManager snapshots remain authoritative
    #: even after usage-less events advance the event cursor.
    event_usage_runs: list[str] = field(default_factory=list)
    #: Per-run event totals make it possible to switch to a complete cumulative
    #: snapshot if a later event omits usage, without double-counting earlier
    #: event deltas.
    event_usage_totals: dict[str, dict[str, int]] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    last_counted_at: float | None = None

    def measure(self, *, tool_calls: int, llm_calls: int = 0, replans: int = 0, retries: int = 0) -> None:
        """Record an observation. Passing ``None`` leaves a field unmeasured."""
        if tool_calls is not None:
            self.tool_calls = (self.tool_calls or 0) + int(tool_calls)
        if llm_calls:
            self.llm_calls = (self.llm_calls or 0) + int(llm_calls)
        if replans:
            self.replans = (self.replans or 0) + int(replans)
        if retries:
            self.retries = (self.retries or 0) + int(retries)
        self.last_counted_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SteeringConstraint:
    """One user instruction (spec §57).

    A constraint, not a prompt override. The spec is explicit that steering
    "should become a mission constraint/update, not overwrite the entire system
    prompt", and the implementation honours that: nothing here rewrites a
    system prompt, and the constraint is stored with a source so an
    operator-issued constraint is distinguishable from a model-proposed one.
    """

    constraint_id: str
    instruction: str
    source: str = "user"
    priority: str = "normal"
    scope: str = "mission"
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SteeringConstraint:
        return cls(
            constraint_id=str(data.get("constraint_id", "")),
            instruction=str(data.get("instruction", "")),
            source=str(data.get("source", "user")),
            priority=str(data.get("priority", "normal")),
            scope=str(data.get("scope", "mission")),
            recorded_at=float(data.get("recorded_at", 0.0) or 0.0),
        )


@dataclass
class ApexSession:
    """One APEX-controlled execution session.

    ``contract_digest`` is the field that makes drift detectable: a restored
    session whose digest differs from the contract it would run under has had
    its policy changed underneath it (spec §163), and the status projection
    reports that rather than running silently under new rules.
    """

    session_id: str
    owner: str
    objective: str
    state: ApexSessionState = ApexSessionState.IDLE
    profile: str = "off"
    contract_digest: str = ""
    #: Canonical, validated policy snapshot used by runtime gates. Older rows
    #: without one can be inspected but cannot authorize tool execution.
    contract_snapshot: dict[str, Any] | None = None
    #: Host-adapter projection. A run is admitted only once per generation;
    #: terminal completion remains awaiting verification.
    dispatch_generation: int = 0
    dispatch_state: str = "idle"
    run_id: str = ""
    run_status: str = ""
    dispatch_started_at: float | None = None
    mission_id: str = ""
    thread_id: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    #: Persisted verification report. Criteria alone never imply that the
    #: session has reached its verification boundary.
    acceptance: dict[str, Any] | None = None
    #: Bounded durable record of completed failed reports that triggered recovery.
    acceptance_history: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[SteeringConstraint] = field(default_factory=list)
    usage: UsageLedger = field(default_factory=UsageLedger)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_event_seq: int = 0
    #: The real reason the session cannot progress, or "".
    blocked_reason: str = ""
    cycle_count: int = 0
    #: Operator decisions on parked work, oldest first. Stored as
    #: dicts (not ApprovalRecord rows) so the row serialises with
    #: ``asdict`` unchanged and a pre-approval row loads as empty.
    approvals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["constraints"] = [c.to_dict() for c in self.constraints]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApexSession:
        payload = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        try:
            payload["state"] = ApexSessionState(payload.get("state", ApexSessionState.IDLE.value))
        except ValueError:
            payload["state"] = ApexSessionState.IDLE
        payload["acceptance_criteria"] = [str(c) for c in payload.get("acceptance_criteria", []) or []]
        if not isinstance(payload.get("acceptance"), dict):
            payload["acceptance"] = None
        payload["acceptance_history"] = [item for item in payload.get("acceptance_history", []) or [] if isinstance(item, dict)][-20:]
        if not isinstance(payload.get("contract_snapshot"), dict):
            payload["contract_snapshot"] = None
        payload["constraints"] = [SteeringConstraint.from_dict(c) for c in payload.get("constraints", []) or []]
        if not isinstance(payload.get("usage"), UsageLedger):
            raw_usage = payload.get("usage") or {}
            payload["usage"] = UsageLedger(**raw_usage)
            # Pre-marker rows treated any positive event cursor as proof that
            # event usage was authoritative. Preserve that conservative rule
            # for old snapshots; new rows persist an explicit empty list when
            # events had no usage payload.
            if isinstance(raw_usage, dict) and "event_usage_runs" not in raw_usage:
                payload["usage"].event_usage_runs = [run_id for run_id, seq in payload["usage"].event_cursors.items() if int(seq) > 0]
        # Older snapshots lacked an explicit replan counter. Retained failed
        # acceptance reports correspond one-for-one with recovery cycles; the
        # bounded history of 20 matches the largest shipped profile ceiling.
        history_count = len(payload.get("acceptance_history", []))
        measured_replans = payload["usage"].replans
        if isinstance(measured_replans, bool) or not isinstance(measured_replans, int) or measured_replans < 0:
            measured_replans = 0
        payload["usage"].replans = max(measured_replans, history_count)
        return cls(**payload)

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    def add_constraint(self, instruction: str, *, source: str = "user", priority: str = "normal") -> SteeringConstraint:
        constraint = SteeringConstraint(
            constraint_id=f"cst-{uuid.uuid4().hex[:8]}",
            instruction=str(instruction),
            source=source,
            priority=priority,
        )
        self.constraints.append(constraint)
        self.updated_at = time.time()
        return constraint


@dataclass
class ApprovalRecord:
    """One operator decision on parked work (spec §24, §27, §30).

    An approval exists because the executive **parked** a session: a
    blocked cycle creates a pending approval naming the blocker, and
    only an operator's verdict moves the session off the park. That
    is the approval gate the spec asks for — autonomy that cannot
    un-park itself. The record is stored on the session row, so it
    survives a restart with the same durability as everything else
    here.
    """

    approval_id: str
    session_id: str
    #: ``pending`` until an operator decides; the two outcomes are
    #: spelled out rather than boolean so a third state is never
    #: implied by a missing field.
    status: str = "pending"
    note: str = ""
    requester: str = ""
    operator: str = ""
    requested_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    #: Exact policy action an approval authorizes; never a session-wide grant.
    action: dict[str, str] | None = None
    consumed_at: float | None = None
    #: A late approval may release one finished run for a fresh attempt.
    requeued_at: float | None = None

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalRecord:
        return cls(
            approval_id=str(data.get("approval_id", "")),
            session_id=str(data.get("session_id", "")),
            status=str(data.get("status", "pending") or "pending"),
            note=str(data.get("note", "") or ""),
            requester=str(data.get("requester", "") or ""),
            operator=str(data.get("operator", "") or ""),
            requested_at=float(data.get("requested_at", 0.0) or 0.0),
            decided_at=data.get("decided_at"),
            action={str(k): str(v) for k, v in data["action"].items()} if isinstance(data.get("action"), dict) else None,
            consumed_at=data.get("consumed_at"),
            requeued_at=data.get("requeued_at"),
        )


def _default_storage_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "apex" / "sessions.json"
    except Exception:
        return Path.cwd() / ".alpha" / "apex" / "sessions.json"


#: Process-wide feed shared by the store and the SSE route. The same instance the
#: missions router tails is not used: an APEX subscriber must not receive mission
#: events it did not ask for, and vice versa.
APEX_EVENTS = MissionEventFeed(max_events_per_mission=2_000, subscriber_backlog=500)


class ApexStore:
    """Durable APEX session rows plus an append-only event journal.

    Write discipline follows ``alpha.missions.store``: atomic temp-then-replace
    for the row set, append-then-fan-out for events, so a live subscriber can
    never observe an event a restart would not replay. The difference is that
    the event record carries its own ``durable`` marker, so a failed journal
    append degrades loudly in the stream instead of pretending to be history.
    """

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rows: dict[str, ApexSession] = {}
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._load()

    # -- persistence ---------------------------------------------------------

    @property
    def events_path(self) -> Path:
        return self.storage_path.parent / "events.jsonl"

    @property
    def load_error(self) -> str | None:
        """The real read failure, or ``None``.

        Mirrors ``GoalStore.load_error``: a store that cannot be read must say
        so. Returning an empty list here would tell an operator there are no
        sessions when the truth is that they could not be looked up.
        """
        return self._load_error

    @property
    def is_degraded(self) -> bool:
        return self._load_error is not None

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
            rows: dict[str, ApexSession] = {}
            for item in raw.get("sessions", []):
                session = ApexSession.from_dict(item)
                rows[session.session_id] = session
            self._rows = rows
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            logger.error("APEX session load failed: %s", self._load_error, exc_info=True)
            return
        # Replay the journal into the live feed so a restarted process serves the
        # history a subscriber saw before the restart. Idempotent by sequence.
        for event in self.read_events():
            known = {e.seq for e in APEX_EVENTS.history(event.mission_id)}
            if event.seq in known:
                APEX_EVENTS.adopt_seq(event.mission_id, event.seq)
                continue
            APEX_EVENTS.publish(event)
            APEX_EVENTS.adopt_seq(event.mission_id, event.seq)

    def _refresh_rows_from_disk(self) -> None:
        """Refresh the cache while the session file's process lock is held.

        Preserve row identities so callers holding a session reference see
        updates made by another Gateway worker instead of a detached snapshot.
        """
        if not self.storage_path.exists():
            self._rows = {}
            self._load_error = None
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("sessions", []), list):
                raise ValueError("APEX session snapshot has an invalid shape")
            loaded = {session.session_id: session for session in (ApexSession.from_dict(item) for item in raw.get("sessions", []))}
            refreshed: dict[str, ApexSession] = {}
            for session_id, current in loaded.items():
                existing = self._rows.get(session_id)
                if existing is None:
                    refreshed[session_id] = current
                else:
                    # Event append updates this field after the JSON snapshot
                    # is written. Do not let that older snapshot rewind the
                    # live object during a later transaction/read.
                    current.last_event_seq = max(current.last_event_seq, existing.last_event_seq)
                    existing.__dict__.clear()
                    existing.__dict__.update(current.__dict__)
                    refreshed[session_id] = existing
            self._rows = refreshed
            self._load_error = None
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            logger.error("APEX session refresh failed: %s", self._load_error, exc_info=True)
            # Keep the last readable cache available to status/list callers.
            # Mutations still fail closed in _transaction while degraded.
            return

    def _save(self) -> bool:
        """Persist the row set. Returns whether the write actually landed."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "sessions": [s.to_dict() for s in self._rows.values()]}
            # NamedTemporaryFile + os.replace so the target is never observed
            # half-written; Path.with_suffix('.tmp') collides when two stores
            # point at one path.
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.storage_path.parent),
                prefix=f".{self.storage_path.name}.",
                suffix=".tmp",
                delete=False,
            )
            try:
                with handle:
                    json.dump(payload, handle, indent=1, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.storage_path)
            except Exception:
                Path(handle.name).unlink(missing_ok=True)
                raise
            return True
        except Exception:
            logger.error("APEX session save failed", exc_info=True)
            return False

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Commit a control change or restore every live row on failure.

        Callers receive the actual session objects, so replacing the mapping
        alone would leave existing readers holding an uncommitted change.
        Restore those objects too, including nested usage and approval state.
        The per-instance lock coordinates threads; the sidecar file lock and
        refresh coordinate Gateway workers sharing this local runtime directory.
        """
        with self._lock, cross_process_file_lock(self.storage_path):
            self._refresh_rows_from_disk()
            if self.is_degraded:
                raise ApexPersistenceError("APEX session store is unreadable; refusing to overwrite it")
            previous = dict(self._rows)
            snapshots = {key: deepcopy(row.__dict__) for key, row in previous.items()}
            try:
                yield
                if self._rows.keys() == previous.keys() and all(row.__dict__ == snapshots[key] for key, row in self._rows.items()):
                    return
                if not self._save():
                    raise ApexPersistenceError("Could not persist APEX session changes")
            except BaseException:
                for key, row in previous.items():
                    row.__dict__.clear()
                    row.__dict__.update(snapshots[key])
                self._rows = previous
                raise

    # -- events --------------------------------------------------------------

    def emit(self, session_id: str, event_type: str, **payload: Any) -> ApexEvent:
        """Journal one cross-worker sequenced event, then fan it out locally."""
        with cross_process_file_lock(self.events_path):
            last_seq = 0
            if self.events_path.exists():
                try:
                    with self.events_path.open("r", encoding="utf-8") as journal:
                        for line in journal:
                            try:
                                item = json.loads(line)
                                if item.get("mission_id") == session_id:
                                    last_seq = max(last_seq, int(item.get("seq", 0) or 0))
                            except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
                                continue
                except OSError:
                    last_seq = max((item.seq for item in APEX_EVENTS.history(session_id)), default=0)
                    logger.error("APEX event journal could not be read before append", exc_info=True)
            event = ApexEvent(
                mission_id=session_id,
                seq=last_seq + 1,
                event_type=event_type,
                payload=dict(payload),
            )
            event.payload.setdefault("durable", True)
            try:
                with self.events_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                event.payload["durable"] = False
                logger.error("APEX event journal append failed for %s", session_id, exc_info=True)
        with self._lock:
            session = self._rows.get(session_id)
            if session is not None:
                session.last_event_seq = event.seq
        APEX_EVENTS.adopt_seq(session_id, event.seq)
        APEX_EVENTS.publish(event)
        return event

    def read_events(self, session_id: str | None = None, *, after_seq: int = 0) -> list[MissionEvent]:
        """Read the journal. A corrupt tail is reported by stopping there."""
        try:
            with cross_process_file_lock(self.events_path):
                if not self.events_path.exists():
                    return []
                journal_text = self.events_path.read_text(encoding="utf-8")
        except Exception:
            logger.error("APEX event journal read failed", exc_info=True)
            return []
        events: list[MissionEvent] = []
        for line in journal_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = MissionEvent.from_dict(json.loads(line))
            except Exception:
                logger.error("APEX event journal corrupt at a line; stopping there", exc_info=True)
                break
            if session_id and event.mission_id != session_id:
                continue
            if event.seq <= after_seq:
                continue
            events.append(event)
        return events

    # -- sessions ------------------------------------------------------------

    def create(
        self,
        *,
        owner: str,
        objective: str,
        profile: str,
        contract_digest: str,
        contract_snapshot: dict[str, Any] | None = None,
        mission_id: str = "",
        thread_id: str = "",
        acceptance_criteria: list[str] | None = None,
    ) -> ApexSession:
        session = ApexSession(
            session_id=f"apx-{uuid.uuid4().hex[:10]}",
            owner=owner,
            objective=objective,
            profile=profile,
            contract_digest=contract_digest,
            contract_snapshot=dict(contract_snapshot) if isinstance(contract_snapshot, dict) else None,
            mission_id=mission_id,
            thread_id=thread_id,
            acceptance_criteria=[str(c) for c in (acceptance_criteria or [])],
            state=ApexSessionState.IDLE,
        )
        with self._transaction():
            self._rows[session.session_id] = session
        self.emit(
            session.session_id,
            "session.created",
            owner=owner,
            objective=objective,
            profile=profile,
            contract_digest=contract_digest,
            acceptance_criteria=list(session.acceptance_criteria),
            durable_row=True,
        )
        return session

    def get(self, session_id: str) -> ApexSession | None:
        with self._lock, cross_process_file_lock(self.storage_path):
            self._refresh_rows_from_disk()
            return self._rows.get(session_id)

    def list(
        self,
        *,
        owner: str | None = None,
        state: str | None = None,
        limit: int = 50,
        after: tuple[float, str] | None = None,
    ) -> list[ApexSession]:
        with self._lock, cross_process_file_lock(self.storage_path):
            self._refresh_rows_from_disk()
            rows = list(self._rows.values())
        if owner:
            rows = [s for s in rows if s.owner == owner]
        if state:
            try:
                wanted = ApexSessionState(state)
            except ValueError:
                return []
            rows = [s for s in rows if s.state is wanted]
        rows.sort(key=lambda s: (-s.created_at, s.session_id))
        if after is not None:
            after_key = (-float(after[0]), str(after[1]))
            rows = [session for session in rows if (-session.created_at, session.session_id) > after_key]
        return rows[: max(1, int(limit))]

    def iter_pages(
        self,
        *,
        owner: str | None = None,
        state: str | None = None,
        page_size: int = 200,
    ) -> Iterator[list[ApexSession]]:
        """Yield stable keyset pages so old sessions cannot fall off a fixed limit.

        The cursor is based on immutable session creation time and id. Rows
        created while a scan is in progress cannot shift offsets and cause an
        older session to be skipped on every subsequent tick.
        """
        page_size = max(1, int(page_size))
        cursor: tuple[float, str] | None = None
        while True:
            page = self.list(owner=owner, state=state, limit=page_size, after=cursor)
            if not page:
                return
            yield page
            if len(page) < page_size:
                return
            last = page[-1]
            cursor = (last.created_at, last.session_id)

    def update(self, session_id: str, **changes: Any) -> ApexSession | None:
        """Apply field updates. Unknown fields are rejected, not ignored."""
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None:
                return None
            unknown = sorted(set(changes) - set(ApexSession.__dataclass_fields__))
            if unknown:
                raise ValueError(f"unknown APEX session fields: {unknown}")
            for key, value in changes.items():
                if key == "state" and not isinstance(value, ApexSessionState):
                    value = ApexSessionState(value)
                setattr(session, key, value)
            session.updated_at = time.time()
            return session

    def consume_tool_call(self, session_id: str, *, limit: int | None) -> tuple[bool, int | None]:
        """Atomically account for one admitted APEX tool call against its budget.

        The reservation is persisted before the tool can execute. A crash may
        therefore over-count a call that never started, but cannot let a retry
        spend the same budget twice. This is process-local with this JSON store;
        multi-process deployments need a shared transactional backend.
        """
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None:
                return False, None
            used = session.usage.tool_calls or 0
            if limit is not None and used >= max(0, int(limit)):
                return False, used
            updated_usage = UsageLedger(**session.usage.to_dict())
            updated_usage.measure(tool_calls=1)
            session.usage = updated_usage
            session.updated_at = time.time()
            return True, used + 1

    def reserve_failure_retry(
        self,
        session_id: str,
        *,
        failure_class: str,
        limit: int | None,
        source_run_id: str,
        generation: int,
    ) -> tuple[bool, int]:
        """Durably reserve one recovery retry, idempotent by failed run id.

        Returns ``(allowed, used_for_class)``. The reservation is committed
        before a recovered run is admitted, so a crash cannot mint an
        unaccounted retry. Re-observing the same source run is free.
        """
        failure_class = str(failure_class).strip() or "unknown"
        source_run_id = str(source_run_id)
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.is_terminal or session.state is not ApexSessionState.ACTIVE or session.dispatch_generation != generation or session.run_id != source_run_id:
                return False, 0
            prior_class = session.usage.retry_reservations.get(source_run_id)
            counts = dict(session.usage.retry_counts_by_failure_class)
            used = max(0, int(counts.get(failure_class, 0)))
            if prior_class is not None:
                return prior_class == failure_class, used
            if limit is not None and used >= max(0, int(limit)):
                return False, used
            usage = UsageLedger(**session.usage.to_dict())
            usage.retry_reservations[source_run_id] = failure_class
            usage.retry_counts_by_failure_class[failure_class] = used + 1
            usage.retries = max(0, int(usage.retries or 0)) + 1
            usage.last_counted_at = time.time()
            session.usage = usage
            session.updated_at = time.time()
        self.emit(
            session_id,
            "run.retry_reserved",
            source_run_id=source_run_id,
            failure_class=failure_class,
            used=used + 1,
            limit=limit,
            generation=generation,
        )
        return True, used + 1

    def claim_dispatch(self, session_id: str) -> int | None:
        """Durably reserve one host dispatch; retries reuse its generation."""
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.is_terminal or session.state is not ApexSessionState.ACTIVE:
                return None
            if session.dispatch_state in {"running", "awaiting_verification", "failed"}:
                return None
            if session.dispatch_state == "idle":
                session.dispatch_generation += 1
                session.dispatch_started_at = time.time()
            session.dispatch_state = "starting"
            session.updated_at = time.time()
            return session.dispatch_generation

    def record_dispatch_run(self, session_id: str, *, generation: int, run_id: str, status: str) -> bool:
        """Link the idempotently admitted Gateway run to this APEX session."""
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.dispatch_generation != generation:
                return False
            # The supervisor and the explicit dispatch route can observe the
            # same idempotent RunManager admission concurrently. Once the first
            # observer commits the link, the second must report success for
            # that exact run instead of turning a successful dispatch into a
            # spurious HTTP error.
            if session.run_id == str(run_id):
                return True
            if session.dispatch_state != "starting" or session.run_id:
                return False
            session.run_id = str(run_id)
            session.run_status = str(status)
            session.dispatch_state = "running"
            session.updated_at = time.time()
        self.emit(session_id, "run.dispatched", run_id=str(run_id), generation=generation, status=str(status))
        return True

    def record_recovered_run(
        self,
        session_id: str,
        *,
        generation: int,
        source_run_id: str,
        run_id: str,
        status: str,
    ) -> bool:
        """Move the session projection to a safely resumed RunManager run.

        RunManager remains the lifecycle owner. This compare-and-set only
        changes the APEX projection when the source run and dispatch generation
        still match; a stale recovery cannot replace newer session work.
        """
        normalized = str(status).lower()
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.is_terminal or session.dispatch_generation != generation or session.state is not ApexSessionState.ACTIVE:
                return False
            # Recovery can race across Gateway workers. RunStore's idempotency
            # key deliberately returns the same continuation to both, so a
            # second observer must treat an already-linked target as success
            # without regressing its newer observed status.
            if session.run_id == str(run_id):
                return True
            if session.run_id != source_run_id:
                return False
            session.run_id = str(run_id)
            session.run_status = normalized
            if normalized in {"completed", "success"}:
                session.dispatch_state = "awaiting_verification"
            elif normalized in {"error", "failed", "interrupted", "cancelled"}:
                session.dispatch_state = "failed"
            else:
                session.dispatch_state = "running"
            session.updated_at = time.time()
        self.emit(
            session_id,
            "run.recovery_linked",
            generation=generation,
            source_run_id=source_run_id,
            run_id=str(run_id),
            status=normalized,
        )
        return True

    def record_dispatch_failure(self, session_id: str, *, generation: int, reason: str) -> bool:
        """Park an unadmitted dispatch failure so it cannot loop or look active."""
        message = str(reason).strip()[:1000] or "host dispatch failed"
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.dispatch_generation != generation or session.dispatch_state != "starting" or session.run_id:
                return False
            session.dispatch_state = "failed"
            session.run_status = "dispatch_error"
            session.blocked_reason = message
            session.updated_at = time.time()
        self.emit(session_id, "run.dispatch_failed", generation=generation, reason=message)
        return True

    def record_run_status(self, session_id: str, *, run_id: str, status: str) -> bool:
        """Persist observed RunManager status without claiming verification."""
        normalized = str(status).lower()
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.run_id != run_id:
                return False
            if session.run_status == normalized and session.dispatch_state in {"running", "awaiting_verification", "failed"}:
                return True
            session.run_status = normalized
            if normalized in {"completed", "success"}:
                session.dispatch_state = "awaiting_verification"
            elif normalized in {"error", "failed", "interrupted", "cancelled"}:
                session.dispatch_state = "failed"
            else:
                session.dispatch_state = "running"
            session.updated_at = time.time()
        self.emit(session_id, "run.status_observed", run_id=run_id, status=normalized, dispatch_state=session.dispatch_state)
        return True

    def requeue_approved_tool_action(self, session_id: str, *, run_id: str) -> bool:
        """Release a finished run when an approved tool action was never consumed.

        An operator can approve after the model run that requested approval has
        already ended. In that case the exact approval is durable, but the
        terminal RunManager link would otherwise keep the dispatcher parked in
        ``awaiting_verification`` forever. Only an active session with a
        successful linked run and an unconsumed ``apex.tool_policy`` approval
        can be requeued; live runs, rejected approvals, and consumed actions are
        left alone.
        """
        approval_ids: list[str] = []
        with self._transaction():
            session = self._rows.get(session_id)
            if (
                session is None
                or session.is_terminal
                or session.state is not ApexSessionState.ACTIVE
                or session.run_id != run_id
                or session.dispatch_state != "awaiting_verification"
                or session.run_status not in {"completed", "success"}
                or session.acceptance is not None
            ):
                return False
            for index, item in enumerate(session.approvals):
                record = ApprovalRecord.from_dict(item)
                if record.status == "approved" and record.requester == "apex.tool_policy" and record.consumed_at is None and record.requeued_at is None and isinstance(record.action, dict):
                    record.requeued_at = time.time()
                    session.approvals[index] = record.to_dict()
                    approval_ids.append(record.approval_id)
            if not approval_ids:
                return False
            previous_generation = session.dispatch_generation
            session.run_id = ""
            session.run_status = ""
            session.dispatch_state = "idle"
            session.updated_at = time.time()
        self.emit(
            session_id,
            "run.requeued_after_approval",
            run_id=run_id,
            generation=previous_generation,
            approval_ids=approval_ids,
        )
        return True

    def recover_after_acceptance_failure(self, session_id: str, *, reason: str) -> ApexSession | None:
        """Reopen a measured-but-failed objective for one new host dispatch.

        The failed report is copied into the durable event journal before the
        current report and terminal RunManager link are cleared. ``RunManager``
        still owns that run's lifecycle; this only makes the APEX adapter ready
        to request a new generation after the executive has selected recovery.
        """
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.is_terminal:
                return None
            previous_state = session.state
            previous_run_id = session.run_id
            failed_report = session.acceptance
            if isinstance(failed_report, dict):
                session.acceptance_history = [*session.acceptance_history, failed_report][-20:]
            session.state = ApexSessionState.ACTIVE
            session.blocked_reason = ""
            session.acceptance = None
            session.run_id = ""
            session.run_status = ""
            session.dispatch_state = "idle"
            session.usage.replans = max(0, int(session.usage.replans or 0)) + 1
            session.updated_at = time.time()
        self.emit(
            session_id,
            "acceptance.recovery_started",
            reason=str(reason)[:1000],
            previous_state=previous_state.value,
            previous_run_id=previous_run_id,
            failed_report=failed_report,
            replan_count=session.usage.replans,
        )
        return session

    def replan_failed_run(
        self,
        session_id: str,
        *,
        run_id: str,
        run_status: str,
        reason: str,
        max_replans: int | None,
    ) -> ApexSession | None:
        """Prepare an explicitly operator-approved retry of a terminal failed run.

        This only clears the APEX projection after the caller independently
        verifies the RunManager record is terminal. It never resumes a
        checkpoint or dispatches a run; the supervisor owns the next dispatch.
        The compare-and-set prevents a stale operator action from replacing a
        newer run or bypassing a pending approval.
        """
        normalized_status = str(run_status).strip().lower()
        normalized_reason = str(reason).strip()[:1000]
        if normalized_status not in {"error", "failed", "interrupted", "cancelled"}:
            return None
        if not normalized_reason:
            return None
        with self._transaction():
            session = self._rows.get(session_id)
            if (
                session is None
                or session.is_terminal
                or session.state is not ApexSessionState.ACTIVE
                or session.run_id != str(run_id)
                or session.dispatch_state != "failed"
                or session.run_status != normalized_status
                or session.acceptance is not None
                # SafeRunRecoveryService reserves before admitting a checkpoint
                # continuation. Do not detach a source run while that reservation
                # is live; otherwise a competing recovery pass could race the
                # operator-requested new generation.
                or str(run_id) in session.usage.retry_reservations
                or any(ApprovalRecord.from_dict(item).status == "pending" for item in session.approvals)
            ):
                return None
            replans_used = max(0, int(session.usage.replans or 0))
            if max_replans is not None and replans_used >= max_replans:
                return None
            previous_run_id = session.run_id
            session.add_constraint(
                f"Operator replan after failed run {previous_run_id} ({normalized_status}): {normalized_reason}",
                source="operator",
                priority="high",
            )
            session.usage.replans = replans_used + 1
            session.run_id = ""
            session.run_status = ""
            session.dispatch_state = "idle"
            session.dispatch_started_at = None
            session.blocked_reason = ""
            session.updated_at = time.time()
        self.emit(
            session_id,
            "run.operator_replan_requested",
            previous_run_id=previous_run_id,
            run_status=normalized_status,
            reason=normalized_reason,
            replan_count=session.usage.replans,
        )
        return session

    def record_run_usage(self, session_id: str, *, run_id: str, input_tokens: int, output_tokens: int, llm_calls: int) -> bool:
        """Upsert cumulative RunManager usage without double-counting polls."""
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.run_id != run_id:
                return False
            # A usage-less event advances event_cursors for replay safety, but
            # does not prove that the event stream measured token usage. Only
            # a run with an actual usage-bearing event suppresses snapshots.
            if run_id in session.usage.event_usage_runs:
                return True
            usage = UsageLedger(**session.usage.to_dict())
            current = {
                "input_tokens": max(0, int(input_tokens)),
                "output_tokens": max(0, int(output_tokens)),
                "llm_calls": max(0, int(llm_calls)),
            }
            previous = usage.run_snapshots.get(run_id, {})
            if current == previous:
                return True
            for key, value in current.items():
                delta = value - int(previous.get(key, 0))
                setattr(usage, key, max(0, int(getattr(usage, key) or 0) + delta))
            usage.total_tokens = max(0, int(usage.total_tokens or 0) + current["input_tokens"] + current["output_tokens"] - int(previous.get("input_tokens", 0)) - int(previous.get("output_tokens", 0)))
            usage.run_snapshots[run_id] = current
            if run_id not in usage.measured_run_ids:
                usage.measured_run_ids.append(run_id)
            usage.last_counted_at = time.time()
            session.usage = usage
            session.updated_at = time.time()
        self.emit(session_id, "run.usage_recorded", run_id=run_id, input_tokens=max(0, int(input_tokens)), output_tokens=max(0, int(output_tokens)), llm_calls=max(0, int(llm_calls)))
        return True

    def record_run_usage_event(
        self,
        session_id: str,
        *,
        run_id: str,
        seq: int,
        input_tokens: int | None,
        output_tokens: int | None,
        llm_call: bool,
    ) -> bool:
        """Add one durable run event once, using its sequence as the cursor."""
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.run_id != run_id:
                return False
            usage = UsageLedger(**session.usage.to_dict())
            cursor = int(usage.event_cursors.get(run_id, 0))
            if int(seq) <= cursor:
                return True
            token_usage_measured = input_tokens is not None and output_tokens is not None
            # If cumulative snapshots have already become this run's source,
            # keep them: they cover usage-less calls too. Switching sources on
            # the first later event could discard earlier calls omitted from
            # the event payload.
            snapshot_is_source = run_id in usage.run_snapshots
            if not snapshot_is_source:
                if token_usage_measured:
                    in_count = max(0, int(input_tokens))
                    out_count = max(0, int(output_tokens))
                    totals = usage.event_usage_totals.setdefault(run_id, {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0})
                    totals["input_tokens"] += in_count
                    totals["output_tokens"] += out_count
                    if llm_call:
                        totals["llm_calls"] += 1
                    usage.input_tokens = (usage.input_tokens or 0) + in_count
                    usage.output_tokens = (usage.output_tokens or 0) + out_count
                    usage.total_tokens = (usage.total_tokens or 0) + in_count + out_count
                    if run_id not in usage.event_usage_runs:
                        usage.event_usage_runs.append(run_id)
                    if llm_call:
                        usage.llm_calls = (usage.llm_calls or 0) + 1
                elif run_id in usage.event_usage_runs:
                    # An incomplete row means event accounting cannot represent
                    # the whole run. Retract this run's known event contribution;
                    # the host tick immediately replaces it with the cumulative
                    # RunManager snapshot. Legacy rows without per-run totals
                    # keep their prior conservative event source.
                    totals = usage.event_usage_totals.pop(run_id, None)
                    if totals is not None:
                        usage.input_tokens = max(0, int(usage.input_tokens or 0) - totals["input_tokens"])
                        usage.output_tokens = max(0, int(usage.output_tokens or 0) - totals["output_tokens"])
                        usage.total_tokens = max(0, int(usage.total_tokens or 0) - totals["input_tokens"] - totals["output_tokens"])
                        usage.llm_calls = max(0, int(usage.llm_calls or 0) - totals["llm_calls"])
                        usage.event_usage_runs.remove(run_id)
            usage.event_cursors[run_id] = int(seq)
            usage.last_counted_at = time.time()
            session.usage = usage
            session.updated_at = time.time()
        self.emit(session_id, "run.usage_event_recorded", run_id=run_id, seq=int(seq))
        return True

    def set_state(self, session_id: str, state: ApexSessionState, *, reason: str = "") -> ApexSession | None:
        """Move a session, journaling the transition with the real reason."""
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None:
                return None
            previous = session.state
            if previous is state:
                return session
            if previous.is_terminal:
                # A terminal session does not move. Refusing here is what stops a
                # late cycle from reopening a completed mission.
                self.emit(session_id, "session.transition_refused", **{"from": previous.value, "to": state.value, "reason": reason or "session is terminal"})
                return None
            session.state = state
            session.blocked_reason = reason if state is ApexSessionState.BLOCKED else ""
            session.updated_at = time.time()
        self.emit(session_id, "session.state_changed", **{"from": previous.value, "to": state.value, "reason": reason})
        return session

    def record_constraint(self, session_id: str, instruction: str, *, source: str = "user", priority: str = "normal") -> SteeringConstraint | None:
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None:
                return None
            if session.is_terminal:
                self.emit(session_id, "constraint.refused", instruction=instruction, reason="session is terminal")
                return None
            constraint = session.add_constraint(instruction, source=source, priority=priority)
        self.emit(session_id, "constraint.recorded", **constraint.to_dict())
        return constraint

    # -- scope join ----------------------------------------------------------

    def active_for_scope(self, scope_key: str) -> ApexSession | None:
        """The newest non-terminal session bound to a conversation scope.

        The mode store keys on the conversation (thread or session id);
        an ``ApexSession`` carries the same identifier in ``thread_id``
        or ``mission_id``. A control command names a *scope*, not a
        session id, so this is the join between the two — and the
        reason ``/apex pause`` can act without the caller knowing a
        session id. A scope with no live session is ``None``, never a
        fabricated row.
        """
        if not scope_key:
            return None
        with self._lock:
            rows = [s for s in self._rows.values() if not s.is_terminal and scope_key in (s.thread_id, s.mission_id)]
        return max(rows, key=lambda s: s.created_at) if rows else None

    def latest_for_scope(self, scope_key: str) -> ApexSession | None:
        """The newest session bound to a scope, terminal or not.

        The complement of :meth:`active_for_scope`: a control
        command that finds no live session falls back to this
        so a *completed* mission is answerable by name —
        "session X is terminal" — rather than as an absence
        that is indistinguishable from a conversation that
        never had a session at all.
        """
        if not scope_key:
            return None
        with self._lock:
            rows = [s for s in self._rows.values() if scope_key in (s.thread_id, s.mission_id)]
        return max(rows, key=lambda s: s.created_at) if rows else None

    # -- approvals -----------------------------------------------------------

    def request_approval(
        self,
        session_id: str,
        *,
        note: str,
        requester: str = "operator",
        action: dict[str, str] | None = None,
    ) -> ApprovalRecord | None:
        """Ask an operator to decide parked work (spec §27 ``approval.required``).

        One pending approval per parked episode: a session that is
        re-blocked while an ask is still outstanding does not stack a
        second one, so an operator is never asked to clear a backlog
        of duplicates of the same block.
        """
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None:
                return None
            if session.is_terminal:
                self.emit(session_id, "approval.refused", note=note, reason="session is terminal")
                return None
            if self.pending_approval(session_id) is not None:
                return None
            record = ApprovalRecord(
                approval_id=f"app-{uuid.uuid4().hex[:8]}",
                session_id=session_id,
                note=str(note),
                requester=str(requester),
                action={str(k): str(v) for k, v in action.items()} if isinstance(action, dict) else None,
            )
            session.approvals.append(record.to_dict())
            session.updated_at = time.time()
        # The record's own ``session_id`` is the journal key, so
        # it is not re-sent as a payload field: ``emit``'s first
        # parameter already carries it, and a same-named keyword
        # would arrive twice.
        payload = dict(record.to_dict())
        payload.pop("session_id", None)
        self.emit(session_id, "approval.requested", **payload)
        return record

    def consume_approved_action(self, session_id: str, *, action: dict[str, str]) -> bool:
        """Consume one prior approval for this exact action, atomically once."""
        with self._transaction():
            session = self._rows.get(session_id)
            if session is None or session.state is not ApexSessionState.ACTIVE:
                return False
            for index in range(len(session.approvals) - 1, -1, -1):
                record = ApprovalRecord.from_dict(session.approvals[index])
                if record.status != "approved" or record.consumed_at is not None or record.action != action:
                    continue
                record.consumed_at = time.time()
                session.approvals[index] = record.to_dict()
                session.updated_at = time.time()
                self.emit(session_id, "approval.action_consumed", approval_id=record.approval_id, action=action)
                return True
        return False

    def pending_approval(self, session_id: str) -> ApprovalRecord | None:
        """The session's outstanding ask, newest first, or ``None``."""
        with self._lock:
            session = self._rows.get(session_id)
            if session is None:
                return None
            for item in reversed(session.approvals):
                record = ApprovalRecord.from_dict(item)
                if record.is_pending:
                    return record
        return None

    def decide_approval(self, approval_id: str, *, verdict: str, operator: str, note: str = "") -> tuple[ApprovalRecord, ApexSession | None] | None:
        """Record an operator's verdict and apply it.

        ``approved`` is the only thing that un-parks a blocked
        session — the executive cannot do it itself, which is the
        property the approval gate exists to guarantee. ``rejected``
        records the decision and leaves the park in place. A
        second decision on the same id is refused (``None``), so a
        stale UI cannot overwrite a fresh verdict.
        """
        wanted = str(verdict).lower()
        if wanted not in ("approved", "rejected"):
            raise ValueError(f"unknown verdict {verdict!r}; expected 'approved' or 'rejected'")
        found: ApprovalRecord | None = None
        resumed: ApexSession | None = None
        previous: ApexSessionState | None = None
        with self._transaction():
            for session in self._rows.values():
                for index, item in enumerate(session.approvals):
                    record = ApprovalRecord.from_dict(item)
                    if record.approval_id != approval_id:
                        continue
                    if not record.is_pending:
                        return None
                    record.status = wanted
                    record.operator = str(operator)
                    if note:
                        record.note = str(note)
                    record.decided_at = time.time()
                    session.approvals[index] = record.to_dict()
                    session.updated_at = time.time()
                    # Consuming the approval and un-parking its session are one
                    # durable transition. Two saves strand a BLOCKED session
                    # with no pending approval if the process stops between them.
                    if wanted == "approved" and not session.is_terminal:
                        previous = session.state
                        session.state = ApexSessionState.ACTIVE
                        session.blocked_reason = ""
                        resumed = session
                    found = record
                    break
                if found is not None:
                    break
        if found is None:
            return None
        decided = dict(found.to_dict())
        decided.pop("session_id", None)
        self.emit(found.session_id, "approval.decided", **decided)
        if resumed is not None and previous is not ApexSessionState.ACTIVE:
            self.emit(
                found.session_id,
                "session.state_changed",
                **{"from": previous.value, "to": ApexSessionState.ACTIVE.value, "reason": f"operator approved {approval_id}"},
            )
        return found, resumed

    def approvals(self, *, owner: str | None = None) -> list[dict[str, Any]]:
        """Every approval, newest session first, flattened and owner-scoped."""
        with self._lock:
            rows = list(self._rows.values())
        if owner:
            rows = [s for s in rows if s.owner == owner]
        flat: list[dict[str, Any]] = []
        for session in sorted(rows, key=lambda s: -s.created_at):
            for item in session.approvals:
                record = ApprovalRecord.from_dict(item)
                flat.append({**record.to_dict(), "owner": session.owner, "objective": session.objective})
        return flat

    def delete(self, session_id: str) -> bool:
        with self._transaction():
            if session_id not in self._rows:
                return False
            del self._rows[session_id]
        self.emit(session_id, "session.deleted")
        return True


_store: ApexStore | None = None
_store_lock = threading.Lock()


def get_apex_store() -> ApexStore:
    """The process-wide store, rebuilt if the resolved path changes."""
    global _store
    with _store_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _store is None or str(_store.storage_path) != live:
            _store = ApexStore()
        return _store
