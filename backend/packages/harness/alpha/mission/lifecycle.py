"""Mission lifecycle: phases, the live event feed, and the pass gate.

Two gaps this closes, both confirmed against the shipped code:

1. ``MissionStore.transition(mission_id, "completed")`` was an unconditional
   status write.  A mission could reach its terminal state with zero acceptance
   criteria ever evaluated, so "completed" carried no information about whether
   the work met its own bar.  :func:`MissionLifecycle.require_terminal` is the
   gate: ``passed``/``completed`` is refused unless
   :func:`alpha.mission.acceptance.assert_acceptance_passed` accepts a real
   report.

2. Acceptance was BATCH and POST-HOC.  Nothing recorded what the mission did
   between assignment and observation, so there was no live view at all.  This
   module is the event seam: every phase change, thread/artifact attachment,
   acceptance evaluation and run observation appends a
   :class:`MissionEvent` to a bounded in-process journal that a subscriber
   (the SSE route) can tail.  Events are also handed to a durable sink so a
   restart can replay the history instead of starting blind.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.mission.acceptance import (
    AcceptanceNotSatisfied,
    AcceptanceReport,
    assert_acceptance_passed,
    evaluate_acceptance,
    unevaluated_report,
)


class MissionPhase(StrEnum):
    """Phases a mission moves through, from assignment to a terminal outcome."""

    DRAFT = "draft"
    ASSIGNED = "assigned"
    RUNNING = "running"
    VERIFYING = "verifying"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IllegalMissionTransition(ValueError):
    """Raised when a mission is asked to move somewhere it cannot go."""


#: Legal phase moves.  ``failed``/``cancelled`` are terminal; ``passed`` is
#: terminal AND gated on a real acceptance pass.
MISSION_TRANSITIONS: dict[MissionPhase, set[MissionPhase]] = {
    MissionPhase.DRAFT: {MissionPhase.ASSIGNED, MissionPhase.CANCELLED},
    MissionPhase.ASSIGNED: {MissionPhase.RUNNING, MissionPhase.CANCELLED, MissionPhase.FAILED},
    MissionPhase.RUNNING: {MissionPhase.VERIFYING, MissionPhase.FAILED, MissionPhase.CANCELLED},
    MissionPhase.VERIFYING: {MissionPhase.PASSED, MissionPhase.FAILED, MissionPhase.CANCELLED},
    MissionPhase.PASSED: set(),
    MissionPhase.FAILED: set(),
    MissionPhase.CANCELLED: set(),
}

TERMINAL_MISSION_PHASES = frozenset({MissionPhase.PASSED, MissionPhase.FAILED, MissionPhase.CANCELLED})

#: The phase that may only be entered with a passing acceptance report.
GATED_TERMINAL_PHASE = MissionPhase.PASSED


def can_transition(current: MissionPhase, target: MissionPhase) -> bool:
    return target in MISSION_TRANSITIONS.get(current, set())


@dataclass
class MissionEvent:
    """One observation about a mission, in the order it happened.

    ``seq`` is a per-mission monotonic counter and ``timestamp`` is a wall
    clock, so a consumer can order events and detect a gap after a restart
    without trusting arrival order.
    """

    mission_id: str
    seq: int
    event_type: str
    timestamp: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "seq": self.seq,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionEvent:
        return cls(
            mission_id=str(data.get("mission_id", "")),
            seq=int(data.get("seq", 0) or 0),
            event_type=str(data.get("event_type", "unknown")),
            timestamp=float(data.get("timestamp", 0.0) or 0.0),
            payload=dict(data.get("payload", {}) or {}),
        )


class MissionEventFeed:
    """Bounded in-process journal plus fan-out to live subscribers.

    One instance per process serves every mission.  Subscribers get a queue per
    mission id; a slow subscriber is never allowed to block a mission, so each
    subscriber's own backlog is capped and the overflow is disclosed on the
    next event it receives rather than silently dropped.
    """

    def __init__(self, *, max_events_per_mission: int = 2_000, subscriber_backlog: int = 500) -> None:
        self._events: dict[str, list[MissionEvent]] = {}
        self._seq: dict[str, itertools.count] = {}
        self._subscribers: dict[str, set[Callable[[MissionEvent], None]]] = {}
        self._lock = threading.RLock()
        self._max_events = max(10, int(max_events_per_mission))
        self._subscriber_backlog = max(1, int(subscriber_backlog))
        self._overflowed: dict[int, int] = {}

    def next_seq(self, mission_id: str) -> int:
        with self._lock:
            counter = self._seq.get(mission_id)
            if counter is None:
                counter = itertools.count(1)
                self._seq[mission_id] = counter
            return next(counter)

    def adopt_seq(self, mission_id: str, last_seq: int) -> None:
        """Continue numbering after a restart so replayed history has no gap."""
        with self._lock:
            counter = self._seq.get(mission_id)
            if counter is None:
                self._seq[mission_id] = itertools.count(int(last_seq) + 1)
            elif last_seq > 0:
                self._seq[mission_id] = itertools.count(int(last_seq) + 1)

    def publish(self, event: MissionEvent) -> MissionEvent:
        with self._lock:
            history = self._events.setdefault(event.mission_id, [])
            history.append(event)
            if len(history) > self._max_events:
                del history[: len(history) - self._max_events]
            subscribers = list(self._subscribers.get(event.mission_id, ()))
        for subscriber in subscribers:
            self._deliver(subscriber, event)
        return event

    def _deliver(self, subscriber: Callable[[MissionEvent], None], event: MissionEvent) -> None:
        try:
            subscriber(event)
        except Exception:  # noqa: BLE001 - one broken subscriber must not stop the rest
            return

    def record(self, mission_id: str, event_type: str, **payload: Any) -> MissionEvent:
        """Append and fan out one event, assigning its sequence number."""
        event = MissionEvent(
            mission_id=mission_id,
            seq=self.next_seq(mission_id),
            event_type=event_type,
            payload=dict(payload),
        )
        return self.publish(event)

    def history(self, mission_id: str, *, after_seq: int = 0) -> list[MissionEvent]:
        with self._lock:
            events = list(self._events.get(mission_id, ()))
        return [e for e in events if e.seq > after_seq]

    def mission_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._events)

    def subscribe(self, mission_id: str, subscriber: Callable[[MissionEvent], None]) -> None:
        with self._lock:
            self._subscribers.setdefault(mission_id, set()).add(subscriber)

    def unsubscribe(self, mission_id: str, subscriber: Callable[[MissionEvent], None]) -> None:
        with self._lock:
            handlers = self._subscribers.get(mission_id)
            if handlers is not None:
                handlers.discard(subscriber)
                if not handlers:
                    self._subscribers.pop(mission_id, None)

    def subscriber_count(self, mission_id: str) -> int:
        with self._lock:
            return len(self._subscribers.get(mission_id, ()))


#: Process-wide lifecycle registry.  Kept at module scope (not on the class)
#: so every instance shares ONE table, and keyed by mission id so a concurrent
#: caller on the same mission observes the same phase.
_LIFECYCLES: dict[str, MissionLifecycle] = {}
_REGISTRY_GUARD = threading.Lock()


@dataclass
class MissionLifecycle:
    """One mission's phase, its acceptance criteria, and its live event feed.

    This is the object that makes "terminal passed" mean something: the only
    way into :data:`GATED_TERMINAL_PHASE` is through :meth:`request_pass`,
    which refuses unless every acceptance criterion was actually evaluated and
    held.
    """

    mission_id: str
    feed: MissionEventFeed
    owner: str = ""
    phase: MissionPhase = MissionPhase.DRAFT
    acceptance_criteria: list[str] = field(default_factory=list)
    acceptance: AcceptanceReport | None = None
    history: list[MissionEvent] = field(default_factory=list)

    # ------------------------------------------------------------- registry

    @classmethod
    def create(
        cls,
        mission_id: str,
        *,
        feed: MissionEventFeed,
        owner: str = "",
        acceptance_criteria: list[str] | None = None,
    ) -> MissionLifecycle:
        """Create a lifecycle AND journal its creation event.

        The creation event carries the owner and the acceptance criteria, which
        is what makes a lifecycle restartable from its own feed: without it a
        restored mission knows its phase but not what it was supposed to prove.
        """
        lifecycle = cls(
            mission_id=mission_id,
            feed=feed,
            owner=owner,
            acceptance_criteria=[str(c) for c in (acceptance_criteria or [])],
        )
        lifecycle._emit(
            "mission_created",
            owner=owner,
            acceptance_criteria=list(lifecycle.acceptance_criteria),
        )
        return cls.register(lifecycle)

    @classmethod
    def register(cls, lifecycle: MissionLifecycle) -> MissionLifecycle:
        with _REGISTRY_GUARD:
            _LIFECYCLES[lifecycle.mission_id] = lifecycle
        return lifecycle

    @classmethod
    def get(cls, mission_id: str) -> MissionLifecycle | None:
        with _REGISTRY_GUARD:
            return _LIFECYCLES.get(mission_id)

    @classmethod
    def drop(cls, mission_id: str) -> None:
        with _REGISTRY_GUARD:
            _LIFECYCLES.pop(mission_id, None)

    @classmethod
    def all(cls) -> list[MissionLifecycle]:
        with _REGISTRY_GUARD:
            return list(_LIFECYCLES.values())

    # --------------------------------------------------------------- events

    def _emit(self, event_type: str, **payload: Any) -> MissionEvent:
        event = self.feed.record(self.mission_id, event_type, **payload)
        self.history.append(event)
        return event

    def restore(self, events: Iterable[MissionEvent | dict[str, Any]]) -> None:
        """Re-seed phase/criteria from a durable log after a restart.

        Accepts either :class:`MissionEvent` objects or the raw dicts a journal
        file holds, so a caller can hand over exactly what it read from disk.
        """
        for raw_event in events:
            event = raw_event if isinstance(raw_event, MissionEvent) else MissionEvent.from_dict(dict(raw_event))
            self.history.append(event)
            if event.event_type == "mission_created":
                self.owner = str(event.payload.get("owner", self.owner))
                self.acceptance_criteria = [str(c) for c in event.payload.get("acceptance_criteria", []) or []]
            elif event.event_type == "phase_changed":
                raw = str(event.payload.get("to", ""))
                try:
                    self.phase = MissionPhase(raw)
                except ValueError:
                    continue
            elif event.event_type == "acceptance_evaluated":
                payload = event.payload.get("report")
                if isinstance(payload, dict):
                    self.acceptance = AcceptanceReport.from_dict(payload)
        if self.history:
            self.feed.adopt_seq(self.mission_id, max(e.seq for e in self.history))

    # -------------------------------------------------------------- phases

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_MISSION_PHASES

    def can_transition_to(self, target: MissionPhase) -> bool:
        return can_transition(self.phase, target)

    def transition(self, target: MissionPhase, *, reason: str = "", actor: str = "orchestrator") -> MissionPhase:
        """Move to ``target`` or raise with the real reason.

        Entering :data:`GATED_TERMINAL_PHASE` here always refuses: use
        :meth:`request_pass`, which checks the acceptance report first.
        """
        if target is GATED_TERMINAL_PHASE:
            raise IllegalMissionTransition(
                f"mission '{self.mission_id}' cannot enter '{GATED_TERMINAL_PHASE.value}' directly; "
                f"the acceptance gate must be satisfied first (use request_pass)"
            )
        if self.is_terminal:
            raise IllegalMissionTransition(
                f"mission '{self.mission_id}' is terminal ('{self.phase.value}'); it cannot move to '{target.value}'"
            )
        if not self.can_transition_to(target):
            allowed = sorted(s.value for s in MISSION_TRANSITIONS.get(self.phase, set()))
            raise IllegalMissionTransition(
                f"mission '{self.mission_id}' cannot move from '{self.phase.value}' to '{target.value}'; "
                f"allowed next phases: {allowed}"
            )
        previous = self.phase
        self.phase = target
        self._emit(
            "phase_changed",
            **{"from": previous.value, "to": target.value, "reason": reason, "actor": actor},
        )
        return self.phase

    def fail(self, reason: str, *, actor: str = "orchestrator") -> MissionPhase:
        if not self.can_transition_to(MissionPhase.FAILED):
            raise IllegalMissionTransition(
                f"mission '{self.mission_id}' in phase '{self.phase.value}' cannot be marked failed"
            )
        return self.transition(MissionPhase.FAILED, reason=reason, actor=actor)

    # ----------------------------------------------------------- acceptance

    def evaluate(
        self,
        evidence: dict[str, bool] | None = None,
        *,
        evaluator: str = "recorded_evidence",
        criteria: list[str] | None = None,
    ) -> AcceptanceReport:
        """Evaluate the criteria and journal the verdict.

        With no evidence the report is produced with every criterion
        UNVERIFIED — the mission then cannot be passed, which is the honest
        outcome of "nobody checked".
        """
        target = list(criteria) if criteria is not None else list(self.acceptance_criteria)
        if evidence:
            report = evaluate_acceptance(target, evidence, evaluator=evaluator)
        else:
            report = unevaluated_report(
                target,
                evaluator=evaluator,
                notes=["no measured evidence was supplied; every criterion is UNVERIFIED"],
            )
        self.acceptance = report
        if report.evaluator.startswith("recorded_evidence") and self.can_transition_to(MissionPhase.VERIFYING):
            self.transition(MissionPhase.VERIFYING, reason="acceptance criteria evaluated", actor=evaluator)
        self._emit(
            "acceptance_evaluated",
            report=report.to_dict(),
            all_evaluated=report.all_evaluated,
            all_hold=report.all_hold,
            passed=report.passed,
        )
        return report

    def request_pass(self, *, reason: str = "", actor: str = "orchestrator") -> MissionPhase:
        """Enter the terminal ``passed`` phase, or raise the real refusal.

        Refusal is the point: with no acceptance report, with an un-evaluated
        criterion, or with a criterion that was measured and did not hold, this
        raises :class:`AcceptanceNotSatisfied` carrying the real reason and the
        run stays where it was.
        """
        assert_acceptance_passed(self.acceptance)
        if self.is_terminal:
            raise IllegalMissionTransition(
                f"mission '{self.mission_id}' is already terminal ('{self.phase.value}')"
            )
        if not self.can_transition_to(MissionPhase.PASSED):
            allowed = sorted(s.value for s in MISSION_TRANSITIONS.get(self.phase, set()))
            raise IllegalMissionTransition(
                f"mission '{self.mission_id}' in phase '{self.phase.value}' cannot reach "
                f"'{MissionPhase.PASSED.value}'; allowed next phases: {allowed}"
            )
        previous = self.phase
        self.phase = MissionPhase.PASSED
        self._emit(
            "phase_changed",
            **{
                "from": previous.value,
                "to": MissionPhase.PASSED.value,
                "reason": reason,
                "actor": actor,
                "acceptance_report_id": self.acceptance.report_id if self.acceptance else None,
            },
        )
        return self.phase

    # -------------------------------------------------------- observations

    def observe(self, observation: str, **payload: Any) -> MissionEvent:
        """Record one observed behaviour of the mission (the live view's content)."""
        return self._emit("observation", observation=observation, **payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "owner": self.owner,
            "phase": self.phase.value,
            "is_terminal": self.is_terminal,
            "acceptance_criteria": list(self.acceptance_criteria),
            "acceptance": self.acceptance.to_dict() if self.acceptance else None,
            "event_count": len(self.history),
            "last_seq": self.history[-1].seq if self.history else 0,
        }


def new_mission_id() -> str:
    return f"msn-{uuid.uuid4().hex[:10]}"


__all__ = [
    "GATED_TERMINAL_PHASE",
    "MISSION_TRANSITIONS",
    "AcceptanceNotSatisfied",
    "IllegalMissionTransition",
    "MissionEvent",
    "MissionEventFeed",
    "MissionLifecycle",
    "MissionPhase",
    "TERMINAL_MISSION_PHASES",
    "can_transition",
    "new_mission_id",
]
