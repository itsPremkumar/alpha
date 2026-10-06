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
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.mission.lifecycle import MissionEvent, MissionEventFeed

logger = logging.getLogger(__name__)

__all__ = [
    "APEX_EVENTS",
    "ApexEvent",
    "ApexSession",
    "ApexSessionState",
    "ApexStore",
    "get_apex_store",
]


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
    replans: int | None = None
    retries: int | None = None
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
    mission_id: str = ""
    thread_id: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
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
        payload["constraints"] = [SteeringConstraint.from_dict(c) for c in payload.get("constraints", []) or []]
        if not isinstance(payload.get("usage"), UsageLedger):
            payload["usage"] = UsageLedger(**(payload.get("usage") or {}))
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

    # -- events --------------------------------------------------------------

    def emit(self, session_id: str, event_type: str, **payload: Any) -> ApexEvent:
        """Journal one event, then fan it out."""
        with self._lock:
            session = self._rows.get(session_id)
            if session is not None:
                session.last_event_seq = APEX_EVENTS.next_seq(session_id)
        event = ApexEvent(
            mission_id=session_id,
            seq=APEX_EVENTS.next_seq(session_id),
            event_type=event_type,
            payload=dict(payload),
        )
        event.payload.setdefault("durable", True)
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()
        except Exception:
            event.payload["durable"] = False
            logger.error("APEX event journal append failed for %s", session_id, exc_info=True)
        APEX_EVENTS.publish(event)
        return event

    def read_events(self, session_id: str | None = None, *, after_seq: int = 0) -> list[MissionEvent]:
        """Read the journal. A corrupt tail is reported by stopping there."""
        if not self.events_path.exists():
            return []
        events: list[MissionEvent] = []
        try:
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
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
        except Exception:
            logger.error("APEX event journal read failed", exc_info=True)
        return events

    # -- sessions ------------------------------------------------------------

    def create(
        self,
        *,
        owner: str,
        objective: str,
        profile: str,
        contract_digest: str,
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
            mission_id=mission_id,
            thread_id=thread_id,
            acceptance_criteria=[str(c) for c in (acceptance_criteria or [])],
            state=ApexSessionState.IDLE,
        )
        with self._lock:
            self._rows[session.session_id] = session
            durable = self._save()
        self.emit(
            session.session_id,
            "session.created",
            owner=owner,
            objective=objective,
            profile=profile,
            contract_digest=contract_digest,
            acceptance_criteria=list(session.acceptance_criteria),
            durable_row=durable,
        )
        return session

    def get(self, session_id: str) -> ApexSession | None:
        with self._lock:
            return self._rows.get(session_id)

    def list(self, *, owner: str | None = None, state: str | None = None, limit: int = 50) -> list[ApexSession]:
        with self._lock:
            rows = list(self._rows.values())
        if owner:
            rows = [s for s in rows if s.owner == owner]
        if state:
            try:
                wanted = ApexSessionState(state)
            except ValueError:
                return []
            rows = [s for s in rows if s.state is wanted]
        return sorted(rows, key=lambda s: -s.created_at)[: max(1, int(limit))]

    def update(self, session_id: str, **changes: Any) -> ApexSession | None:
        """Apply field updates. Unknown fields are rejected, not ignored."""
        with self._lock:
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
            self._save()
            return session

    def set_state(self, session_id: str, state: ApexSessionState, *, reason: str = "") -> ApexSession | None:
        """Move a session, journaling the transition with the real reason."""
        with self._lock:
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
            self._save()
        self.emit(session_id, "session.state_changed", **{"from": previous.value, "to": state.value, "reason": reason})
        return session

    def record_constraint(self, session_id: str, instruction: str, *, source: str = "user", priority: str = "normal") -> SteeringConstraint | None:
        with self._lock:
            session = self._rows.get(session_id)
            if session is None:
                return None
            if session.is_terminal:
                self.emit(session_id, "constraint.refused", instruction=instruction, reason="session is terminal")
                return None
            constraint = session.add_constraint(instruction, source=source, priority=priority)
            self._save()
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

    def request_approval(self, session_id: str, *, note: str, requester: str = "operator") -> ApprovalRecord | None:
        """Ask an operator to decide parked work (spec §27 ``approval.required``).

        One pending approval per parked episode: a session that is
        re-blocked while an ask is still outstanding does not stack a
        second one, so an operator is never asked to clear a backlog
        of duplicates of the same block.
        """
        with self._lock:
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
            )
            session.approvals.append(record.to_dict())
            session.updated_at = time.time()
            self._save()
        # The record's own ``session_id`` is the journal key, so
        # it is not re-sent as a payload field: ``emit``'s first
        # parameter already carries it, and a same-named keyword
        # would arrive twice.
        payload = dict(record.to_dict())
        payload.pop("session_id", None)
        self.emit(session_id, "approval.requested", **payload)
        return record

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
        with self._lock:
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
                    self._save()
                    found = record
                    break
                if found is not None:
                    break
        if found is None:
            return None
        decided = dict(found.to_dict())
        decided.pop("session_id", None)
        self.emit(found.session_id, "approval.decided", **decided)
        resumed: ApexSession | None = None
        if wanted == "approved":
            resumed = self.set_state(
                found.session_id,
                ApexSessionState.ACTIVE,
                reason=f"operator approved {approval_id}",
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
        with self._lock:
            if session_id not in self._rows:
                return False
            del self._rows[session_id]
            self._save()
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
