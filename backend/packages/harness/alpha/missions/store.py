"""Durable missions: long-lived objectives above threads, below the UI.

A mission survives restarts, links the threads/runs working toward it, and
carries an artifact manifest. Lifecycle: draft -> active -> paused ->
active -> completed | cancelled.

Two properties this store now enforces, both previously absent:

* **A terminal ``completed`` requires a real acceptance pass.**  ``transition``
  used to be an unconditional status write, so a mission could reach its
  terminal state with no acceptance criterion ever evaluated.  ``acceptance``
  now carries the evaluated report and :meth:`MissionStore.complete` refuses a
  ``completed`` transition unless ``alpha.mission.acceptance`` accepts it.
* **Every mutation is journaled.**  ``events.jsonl`` beside the mission rows
  records created / attachment / acceptance / transition, so a restart can
  rebuild the history and a live subscriber can stream it.  Acceptance was
  previously BATCH and POST-HOC: nothing recorded what a mission did between
  assignment and observation.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from alpha.mission.acceptance import AcceptanceReport, assert_acceptance_passed
from alpha.mission.lifecycle import MissionEvent, MissionEventFeed

logger = logging.getLogger(__name__)

MissionStatus = Literal["draft", "active", "paused", "completed", "cancelled"]

VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "cancelled"},
    "active": {"paused", "completed", "cancelled"},
    "paused": {"active", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}

#: The status whose entry is gated on a real acceptance pass.
GATED_STATUS = "completed"

#: Process-wide live feed; the missions router's SSE route tails it.
MISSION_FEED = MissionEventFeed()


def _default_storage_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "missions" / "missions.json"
    except Exception:
        return Path.cwd() / ".alpha" / "missions" / "missions.json"


@dataclass
class Mission:
    mission_id: str
    owner: str
    objective: str
    constraints: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    status: MissionStatus = "draft"
    thread_ids: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    #: Criteria the mission must satisfy.  A terminal ``completed`` is refused
    #: unless every one of these was evaluated and held.
    acceptance_criteria: list[str] = field(default_factory=list)
    #: The most recent acceptance report (``AcceptanceReport.to_dict()``).
    acceptance: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mission:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)

    def acceptance_report(self) -> AcceptanceReport | None:
        """The stored report, or ``None`` when nothing has been evaluated."""
        return AcceptanceReport.from_dict(self.acceptance) if isinstance(self.acceptance, dict) else None


class MissionStore:
    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rows: dict[str, Mission] = {}
        self._lock = threading.Lock()
        #: Last ``completed`` refusal reason, so the router can return the real
        #: cause instead of a generic 404.
        self._refusal: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in data.get("missions", []):
                m = Mission.from_dict(item)
                self._rows[m.mission_id] = m
        except Exception:
            logger.warning("Mission load failed; starting empty", exc_info=True)
        # Replay the durable journal into the live feed so a restarted process
        # serves the same history a subscriber saw before the restart, instead
        # of starting blind at "no events".  Idempotent by sequence number: a
        # second store over the same path (a test, a re-init) must not
        # duplicate events a subscriber is already receiving.
        for event in self.read_events():
            known = {e.seq for e in MISSION_FEED.history(event.mission_id)}
            if event.seq in known:
                MISSION_FEED.adopt_seq(event.mission_id, event.seq)
                continue
            MISSION_FEED.publish(event)
            MISSION_FEED.adopt_seq(event.mission_id, event.seq)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "missions": [m.to_dict() for m in self._rows.values()]}, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Mission save failed", exc_info=True)

    @property
    def events_path(self) -> Path:
        return self.storage_path.parent / "events.jsonl"

    def _journal(self, event: MissionEvent) -> MissionEvent:
        """Append to the durable journal, then fan out to the live feed.

        Journal first: a subscriber must never observe an event that a restart
        would not replay.  The ``durable`` marker is written into the record
        itself so the persisted line and the in-memory event are byte-identical
        on the success path; a write failure is logged, flips the marker to
        ``False``, and is still fanned out so the live view degrades loudly
        rather than pretending the history is durable.
        """
        event.payload.setdefault("durable", True)
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()
        except Exception:
            event.payload["durable"] = False
            logger.warning("Mission event journal append failed for %s", event.mission_id, exc_info=True)
        return MISSION_FEED.publish(event)

    def read_events(self, mission_id: str | None = None, *, after_seq: int = 0) -> list[MissionEvent]:
        """Read the durable journal. A corrupt tail is reported, not hidden."""
        path = self.events_path
        if not path.exists():
            return []
        events: list[MissionEvent] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = MissionEvent.from_dict(json.loads(line))
                except Exception:
                    logger.warning("Mission event journal corrupt at a line; stopping there", exc_info=True)
                    break
                if mission_id and event.mission_id != mission_id:
                    continue
                if event.seq <= after_seq:
                    continue
                events.append(event)
        except Exception:
            logger.warning("Mission event journal read failed", exc_info=True)
        return events

    def _emit(self, mission_id: str, event_type: str, **payload: Any) -> MissionEvent:
        return self._journal(MissionEvent(mission_id=mission_id, seq=MISSION_FEED.next_seq(mission_id), event_type=event_type, payload=dict(payload)))

    def create(
        self,
        owner: str,
        objective: str,
        *,
        constraints: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> Mission:
        m = Mission(
            mission_id=f"msn-{uuid.uuid4().hex[:10]}",
            owner=owner,
            objective=objective,
            constraints=constraints or {},
            budget=budget or {},
            acceptance_criteria=[str(c) for c in (acceptance_criteria or [])],
        )
        with self._lock:
            self._rows[m.mission_id] = m
            self._save()
        self._emit(
            m.mission_id,
            "mission_created",
            owner=owner,
            objective=objective,
            status=m.status,
            acceptance_criteria=list(m.acceptance_criteria),
        )
        return m

    def get(self, mission_id: str) -> Mission | None:
        with self._lock:
            return self._rows.get(mission_id)

    def list(self, *, owner: str | None = None, status: str | None = None) -> list[Mission]:
        with self._lock:
            rows = list(self._rows.values())
        if owner:
            rows = [m for m in rows if m.owner == owner]
        if status:
            rows = [m for m in rows if m.status == status]
        return sorted(rows, key=lambda m: -m.created_at)

    def _gate_completion(self, m: Mission) -> str | None:
        """The real refusal reason, or ``None`` when ``completed`` is allowed.

        Refuses on all three of: no criteria recorded (nothing was promised, so
        nothing can be claimed), no report recorded yet (the criteria exist but
        nobody has measured them), and a report that is not a full pass.  This
        is the fix for "a mission reached a terminal passed state while an
        acceptance criterion was never evaluated".  Each refusal names which of
        the three it is, so the operator is never told "no criteria" about a
        mission that declared them.
        """
        if not m.acceptance_criteria:
            return "mission records no acceptance criteria; a terminal 'completed' cannot be justified"
        if m.acceptance is None:
            count = len(m.acceptance_criteria)
            return (
                f"acceptance criteria have not been evaluated yet: {count} pending; "
                f"evaluate them before completing the mission"
            )
        try:
            assert_acceptance_passed(m.acceptance_report())
        except Exception as exc:  # AcceptanceNotSatisfied and friends
            return str(exc)
        return None

    def transition(self, mission_id: str, to: MissionStatus) -> Mission | None:
        """Move a mission, refusing an unjustified terminal ``completed``.

        Returns ``None`` for both "not found" and "illegal"; the caller can call
        :meth:`completion_refusal` for the precise reason.
        """
        with self._lock:
            m = self._rows.get(mission_id)
            if not m or to not in VALID_TRANSITIONS.get(m.status, set()):
                return None
            refusal = self._gate_completion(m) if to == GATED_STATUS else None
            if refusal is not None:
                self._refusal = refusal
                return None
            previous = m.status
            m.status = to
            m.updated_at = time.time()
            self._save()
        self._emit(
            mission_id,
            "mission_transitioned",
            **{
                "from": previous,
                "to": to,
                "acceptance_report_id": (m.acceptance or {}).get("report_id"),
                "all_evaluated": bool((m.acceptance or {}).get("all_evaluated")),
            },
        )
        return m

    def completion_refusal(self, mission_id: str) -> str | None:
        """Why ``completed`` would be refused right now (``None`` if allowed)."""
        m = self.get(mission_id)
        if m is None:
            return "mission not found"
        return self._gate_completion(m)

    def record_acceptance(self, mission_id: str, report: AcceptanceReport) -> Mission | None:
        """Persist an evaluated acceptance report and journal the verdict."""
        with self._lock:
            m = self._rows.get(mission_id)
            if not m:
                return None
            m.acceptance = report.to_dict()
            m.updated_at = time.time()
            self._save()
        self._emit(
            mission_id,
            "acceptance_evaluated",
            report=m.acceptance,
            all_evaluated=report.all_evaluated,
            all_hold=report.all_hold,
            passed=report.passed,
        )
        return m

    def record_observation(self, mission_id: str, observation: str, **payload: Any) -> MissionEvent | None:
        """Journal one observed behaviour of a mission (the live view's content)."""
        m = self.get(mission_id)
        if m is None:
            return None
        return self._emit(mission_id, "observation", observation=observation, **payload)

    def attach_thread(self, mission_id: str, thread_id: str) -> Mission | None:
        with self._lock:
            m = self._rows.get(mission_id)
            if not m:
                return None
            if thread_id not in m.thread_ids:
                m.thread_ids.append(thread_id)
                m.updated_at = time.time()
                self._save()
                attached = True
            else:
                attached = False
        if attached:
            self._emit(mission_id, "thread_attached", thread_id=thread_id)
        return m

    def attach_artifact(self, mission_id: str, artifact: str) -> Mission | None:
        with self._lock:
            m = self._rows.get(mission_id)
            if not m:
                return None
            if artifact not in m.artifacts:
                m.artifacts.append(artifact)
                m.updated_at = time.time()
                self._save()
                attached = True
            else:
                attached = False
        if attached:
            self._emit(mission_id, "artifact_attached", artifact=artifact)
        return m


_store: MissionStore | None = None
_store_path: str | None = None
_store_lock = threading.Lock()


def get_mission_store() -> MissionStore:
    global _store, _store_path
    with _store_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _store is None or _store_path != live:
            _store = MissionStore()
            try:
                _store_path = str(_store.storage_path.resolve())
            except Exception:
                _store_path = live
        return _store
