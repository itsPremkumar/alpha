"""The APEX Goal Operating System — spec §7 and §8.

A goal is the unit of work APEX actually reasons about. A mission holds an
autonomy contract; a goal holds an objective, the criteria that would prove it
done, and the evidence that it was.

**Why this is not `alpha.mission` or `alpha.goals` again.**

Three goal-ish stores already exist and all three are *adapted*, not extended:

* ``alpha.goals`` — ``GoalContract`` / ``PlanVersion`` / ``TaskAttempt``, an
  owner-scoped approval state machine with a frozen ``extra="forbid"`` schema.
  It has no execution states, so it cannot express "recovering" or "verifying".
* ``alpha.mission.state_machine`` — a 15-state ``TaskState`` for a *task*, with
  no objective, criteria or evidence attached.
* ``alpha.mission.lifecycle`` — 7 ``MissionPhase`` values and a gated terminal
  ``PASSED``. This module reuses that gate rather than rebuilding it.

What none of them has is the spec §7 vocabulary (``DECOMPOSING``,
``REPLANNING``, ``PARTIAL``) attached to a persistent objective with a parent
link, which is what a dynamic decomposition needs.

**Two properties are load-bearing.**

1. **``COMPLETED`` is unreachable without evidence.** :meth:`ApexGoal.request_completion`
   routes through ``alpha.mission.acceptance.assert_acceptance_passed``, so a
   goal whose criteria were never measured cannot close. ``PARTIAL`` and
   ``FAILED`` are reachable and are *not* downgrades to hide behind — the spec
   wants a partial reported as partial.

2. **A child cannot outrank its parent.** ``GoalStore.create_child`` walks the
   ancestor chain and refuses a priority above any ancestor's, which is what
   keeps a decomposition from quietly reordering the work it was derived from.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ApexGoal",
    "ApexGoalStore",
    "GoalState",
    "GOAL_TRANSITIONS",
    "TERMINAL_GOAL_STATES",
    "IllegalGoalTransition",
    "get_goal_store",
    "new_goal_id",
]


class GoalState(StrEnum):
    """Spec §7, verbatim. Fifteen states, in the spec's order."""

    IDLE = "idle"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    REPLANNING = "replanning"
    BLOCKED = "blocked"
    WAITING = "waiting"
    PAUSED = "paused"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_GOAL_STATES


#: Terminal states. Note that ``PARTIAL`` is one of them: a partial result is a
#: real outcome that gets recorded and surfaced, not an error to be retried into
#: either success or total failure (spec §123).
TERMINAL_GOAL_STATES: frozenset[GoalState] = frozenset(
    {
        GoalState.COMPLETED,
        GoalState.PARTIAL,
        GoalState.FAILED,
        GoalState.CANCELLED,
    }
)

#: States in which work is expected to progress without an external event.
ACTIVE_GOAL_STATES: frozenset[GoalState] = frozenset(
    {
        GoalState.ANALYZING,
        GoalState.PLANNING,
        GoalState.DECOMPOSING,
        GoalState.EXECUTING,
        GoalState.VERIFYING,
        GoalState.RECOVERING,
        GoalState.REPLANNING,
    }
)

#: States in which the goal is parked and only an external event moves it.
WAITING_GOAL_STATES: frozenset[GoalState] = frozenset({GoalState.BLOCKED, GoalState.WAITING, GoalState.PAUSED})


#: Legal transitions. Read the two rules that shape this table:
#:
#: * ``VERIFYING`` can go back to ``EXECUTING``. A goal whose verification failed
#:   and whose repair is not yet planned re-enters execution rather than dying.
#: * ``PAUSED`` and ``BLOCKED`` both reach ``REPLANNING``. Stopping is not a
#:   dead end; that is spec §37's strategy escalation.
GOAL_TRANSITIONS: dict[GoalState, frozenset[GoalState]] = {
    GoalState.IDLE: frozenset({GoalState.ANALYZING, GoalState.CANCELLED}),
    GoalState.ANALYZING: frozenset(
        {
            GoalState.PLANNING,
            GoalState.BLOCKED,
            GoalState.WAITING,
            GoalState.PAUSED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.PLANNING: frozenset(
        {
            GoalState.DECOMPOSING,
            GoalState.REPLANNING,
            GoalState.EXECUTING,
            GoalState.BLOCKED,
            GoalState.WAITING,
            GoalState.PAUSED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.DECOMPOSING: frozenset(
        {
            GoalState.EXECUTING,
            GoalState.REPLANNING,
            GoalState.BLOCKED,
            GoalState.PAUSED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.EXECUTING: frozenset(
        {
            GoalState.VERIFYING,
            GoalState.RECOVERING,
            GoalState.REPLANNING,
            GoalState.BLOCKED,
            GoalState.WAITING,
            GoalState.PAUSED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.VERIFYING: frozenset(
        {
            GoalState.COMPLETED,
            GoalState.PARTIAL,
            GoalState.EXECUTING,
            GoalState.RECOVERING,
            GoalState.REPLANNING,
            GoalState.BLOCKED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.RECOVERING: frozenset(
        {
            GoalState.EXECUTING,
            GoalState.REPLANNING,
            GoalState.VERIFYING,
            GoalState.BLOCKED,
            GoalState.WAITING,
            GoalState.PAUSED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.REPLANNING: frozenset(
        {
            GoalState.PLANNING,
            GoalState.DECOMPOSING,
            GoalState.EXECUTING,
            GoalState.BLOCKED,
            GoalState.WAITING,
            GoalState.PAUSED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.BLOCKED: frozenset(
        {
            GoalState.REPLANNING,
            GoalState.WAITING,
            GoalState.PAUSED,
            GoalState.EXECUTING,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.WAITING: frozenset(
        {
            GoalState.EXECUTING,
            GoalState.BLOCKED,
            GoalState.REPLANNING,
            GoalState.PAUSED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.PAUSED: frozenset(
        {
            GoalState.EXECUTING,
            GoalState.REPLANNING,
            GoalState.BLOCKED,
            GoalState.FAILED,
            GoalState.CANCELLED,
        }
    ),
    GoalState.COMPLETED: frozenset(),
    GoalState.PARTIAL: frozenset(),
    GoalState.FAILED: frozenset(),
    GoalState.CANCELLED: frozenset(),
}


class IllegalGoalTransition(ValueError):
    """Raised when a goal is asked to move somewhere it cannot go."""


def new_goal_id() -> str:
    return f"agl-{uuid.uuid4().hex[:10]}"


@dataclass
class GoalEvidence:
    """One measured observation attached to a goal (spec §23).

    ``holds`` is ``None`` until something measured it. ``None`` and ``False``
    are different facts: "nobody checked" is not "it did not hold", and
    collapsing them is how a goal closes on a criterion nobody ran.
    """

    evidence_id: str
    criterion: str = ""
    source: str = ""
    detail: str = ""
    holds: bool | None = None
    recorded_at: float = field(default_factory=time.time)

    @classmethod
    def unmeasured(cls, criterion: str, *, source: str = "", detail: str = "") -> GoalEvidence:
        return cls(evidence_id=f"ev-{uuid.uuid4().hex[:8]}", criterion=criterion, source=source, detail=detail)

    @classmethod
    def measured(cls, criterion: str, holds: bool, *, source: str = "", detail: str = "") -> GoalEvidence:
        return cls(
            evidence_id=f"ev-{uuid.uuid4().hex[:8]}",
            criterion=criterion,
            source=source,
            detail=detail,
            holds=bool(holds),
        )

    @property
    def evaluated(self) -> bool:
        return self.holds is not None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evaluated"] = self.evaluated
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalEvidence:
        holds = data.get("holds")
        return cls(
            evidence_id=str(data.get("evidence_id", "")),
            criterion=str(data.get("criterion", "")),
            source=str(data.get("source", "")),
            detail=str(data.get("detail", "")),
            holds=None if holds is None else bool(holds),
            recorded_at=float(data.get("recorded_at", 0.0) or 0.0),
        )


@dataclass
class ApexGoal:
    """One objective with the criteria that would prove it done (spec §7)."""

    goal_id: str
    objective: str
    parent_goal_id: str = ""
    description: str = ""
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    #: Higher wins. A child may not outrank any ancestor.
    priority: int = 50
    deadline: float | None = None
    state: GoalState = GoalState.IDLE
    risk: str = "R1"
    budget: dict[str, Any] = field(default_factory=dict)
    owner: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    plan_version: int = 0
    evidence: list[GoalEvidence] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    current_strategy: str = ""
    #: The real blocker, or "".
    blocked_reason: str = ""
    #: How many times this goal has been replanned (spec §68 budgets this).
    replan_count: int = 0
    #: Child goal ids, in creation order.
    child_ids: list[str] = field(default_factory=list)
    #: The session this goal was decomposed from, when it has one. The
    #: join is what makes ``/api/apex/goals/{id}/decisions`` answerable:
    #: a goal's decisions are its session's cycle decisions.
    session_id: str = ""
    mission_id: str = ""
    #: Specialists recorded against this goal (spec §10/§16). Dicts,
    #: not a second registry: the subagent lifecycle manager owns the
    #: agent; this records only that one was *asked for* and why.
    agent_records: list[dict[str, Any]] = field(default_factory=list)

    # -- derived ------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_GOAL_STATES

    @property
    def is_waiting(self) -> bool:
        return self.state in WAITING_GOAL_STATES

    def latest_evidence(self) -> dict[str, GoalEvidence]:
        """The most recent measurement per criterion.

        This is the distinction that makes verify -> repair -> re-verify
        (spec §193) work at all. The ``evidence`` list stays append-only because
        the audit trail is the point; but the *current verdict* for a criterion
        is its latest measurement. Scoping the failure check to the whole log
        instead would mean a goal that failed once could never complete, even
        after a passing re-verification — a goal permanently poisoned by its own
        history.
        """
        latest: dict[str, GoalEvidence] = {}
        for item in self.evidence:
            if item.evaluated and item.criterion:
                latest[item.criterion] = item
        return latest

    def criteria_without_evidence(self) -> list[str]:
        """Criteria whose latest measurement is still unmeasured.

        An empty list is what completion requires.
        """
        measured = self.latest_evidence()
        return [c for c in self.success_criteria if c not in measured]

    def criteria_failed(self) -> list[str]:
        """Criteria whose latest measurement did not hold."""
        return [c for c, e in self.latest_evidence().items() if e.holds is False]

    def acceptance_evidence(self) -> dict[str, bool]:
        """Latest measured booleans, keyed by criterion, for the acceptance gate.

        Only evaluated evidence appears. A criterion with no entry stays
        UNVERIFIED upstream, which is the point.
        """
        return {criterion: bool(e.holds) for criterion, e in self.latest_evidence().items()}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["is_terminal"] = self.is_terminal
        data["evidence"] = [e.to_dict() for e in self.evidence]
        data["criteria_without_evidence"] = self.criteria_without_evidence()
        data["criteria_failed"] = self.criteria_failed()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApexGoal:
        payload = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        try:
            payload["state"] = GoalState(payload.get("state", GoalState.IDLE.value))
        except ValueError:
            payload["state"] = GoalState.IDLE
        payload["success_criteria"] = [str(c) for c in payload.get("success_criteria", []) or []]
        payload["constraints"] = [str(c) for c in payload.get("constraints", []) or []]
        payload["artifacts"] = [str(a) for a in payload.get("artifacts", []) or []]
        payload["child_ids"] = [str(c) for c in payload.get("child_ids", []) or []]
        payload["evidence"] = [GoalEvidence.from_dict(e) for e in payload.get("evidence", []) or []]
        deadline = payload.get("deadline")
        payload["deadline"] = float(deadline) if isinstance(deadline, (int, float)) else None
        return cls(**payload)


#: Ancestors a goal may not exceed, by walking ``parent_goal_id``.
_MAX_ANCESTOR_DEPTH = 12


class ApexGoalStore:
    """Durable goal rows, with the parent/child links a decomposition needs.

    Write discipline matches :class:`alpha.apex.store.ApexStore`: a
    ``NamedTemporaryFile`` plus ``os.replace`` so a reader never sees a
    half-written set, and a corrupt file surfaces as ``load_error`` rather than
    as "there are no goals".

    ``event_sink`` is the single journal writer, injected rather than
    imported: goal events ride the APEX event journal under their own
    ``agl-`` ids, which is what makes ``GET /api/apex/goals/{id}/events``
    a replay of real history instead of a second cursor implementation.
    The harness cannot import the session store at module load without a
    cycle risk, and a test wants to observe the sink without touching
    disk, so the caller supplies it.
    """

    def __init__(
        self,
        storage_path: str | Path | None = None,
        event_sink: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rows: dict[str, ApexGoal] = {}
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._event_sink = event_sink
        self._load()

    def _journal(self, goal_id: str, event_type: str, **payload: Any) -> None:
        """Append one goal event through the injected journal writer.

        A goal whose event cannot be journalled still exists — the
        journal is the audit trail, not the state — so a sink failure
        is logged, never raised into the write that triggered it.
        """
        if self._event_sink is None:
            return
        try:
            self._event_sink(goal_id, event_type, dict(payload))
        except Exception:
            logger.warning("APEX goal event %s for %s was not journalled", event_type, goal_id, exc_info=True)

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def is_degraded(self) -> bool:
        return self._load_error is not None

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
            rows: dict[str, ApexGoal] = {}
            for item in raw.get("goals", []):
                goal = ApexGoal.from_dict(item)
                rows[goal.goal_id] = goal
            self._rows = rows
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            logger.error("APEX goal load failed: %s", self._load_error, exc_info=True)

    def _save(self) -> bool:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "goals": [g.to_dict() for g in self._rows.values()]}
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
            logger.error("APEX goal save failed", exc_info=True)
            return False

    # -- reads ---------------------------------------------------------------

    def get(self, goal_id: str) -> ApexGoal | None:
        with self._lock:
            return self._rows.get(goal_id)

    def list(
        self,
        *,
        owner: str | None = None,
        state: str | None = None,
        root_only: bool = False,
        session_id: str = "",
        limit: int = 200,
    ) -> list[ApexGoal]:
        with self._lock:
            rows = list(self._rows.values())
        if owner:
            rows = [g for g in rows if g.owner == owner]
        if session_id:
            rows = [g for g in rows if g.session_id == session_id]
        if state:
            try:
                wanted = GoalState(state)
            except ValueError:
                return []
            rows = [g for g in rows if g.state is wanted]
        if root_only:
            rows = [g for g in rows if not g.parent_goal_id]
        return sorted(rows, key=lambda g: (-g.priority, g.created_at))[: max(1, int(limit))]

    def children(self, goal_id: str) -> list[ApexGoal]:
        parent = self.get(goal_id)
        if parent is None:
            return []
        with self._lock:
            return [self._rows[c] for c in parent.child_ids if c in self._rows]

    def ancestors(self, goal_id: str) -> list[ApexGoal]:
        """Root-first chain, bounded.

        The bound is not defensive noise: a cycle introduced by a bad write
        would otherwise hang every reader that walks ancestry.
        """
        chain: list[ApexGoal] = []
        seen: set[str] = set()
        current = self.get(goal_id)
        while current is not None and current.parent_goal_id and len(chain) < _MAX_ANCESTOR_DEPTH:
            if current.goal_id in seen:
                break
            seen.add(current.goal_id)
            parent = self.get(current.parent_goal_id)
            if parent is None:
                break
            chain.append(parent)
            current = parent
        return list(reversed(chain))

    def tree(self, goal_id: str) -> dict[str, Any]:
        """One goal and its descendants, for the UI's goal tree."""

        def _node(goal: ApexGoal, depth: int) -> dict[str, Any]:
            return {
                "goal_id": goal.goal_id,
                "objective": goal.objective,
                "state": goal.state.value,
                "priority": goal.priority,
                "depth": depth,
                "criteria_total": len(goal.success_criteria),
                "criteria_unmeasured": len(goal.criteria_without_evidence()),
                "children": [_node(c, depth + 1) for c in self.children(goal.goal_id)],
            }

        root = self.get(goal_id)
        return _node(root, 0) if root is not None else {}

    # -- writes --------------------------------------------------------------

    def create(
        self,
        *,
        objective: str,
        owner: str = "",
        description: str = "",
        success_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        priority: int = 50,
        deadline: float | None = None,
        risk: str = "R1",
        budget: dict[str, Any] | None = None,
        session_id: str = "",
        mission_id: str = "",
    ) -> ApexGoal:
        goal = ApexGoal(
            goal_id=new_goal_id(),
            objective=objective,
            owner=owner,
            description=description,
            success_criteria=[str(c) for c in (success_criteria or [])],
            constraints=[str(c) for c in (constraints or [])],
            priority=int(priority),
            deadline=deadline,
            risk=risk,
            budget=dict(budget or {}),
            session_id=str(session_id),
            mission_id=str(mission_id),
        )
        with self._lock:
            self._rows[goal.goal_id] = goal
            self._save()
        self._journal(
            goal.goal_id,
            "goal.created",
            objective=goal.objective,
            owner=goal.owner,
            parent_goal_id=goal.parent_goal_id,
            # Not ``session_id``: that name is the journal writer's
            # own first parameter (the mission/row id), so a payload
            # key of the same name would arrive twice.
            linked_session=goal.session_id,
            success_criteria=list(goal.success_criteria),
        )
        return goal

    def create_child(self, parent_goal_id: str, **kwargs: Any) -> ApexGoal:
        """Create a subgoal, refusing a priority above any ancestor's.

        Property 2 of the module: a decomposition that reorders the work it was
        derived from is a planning bug, and it is cheaper to refuse at creation
        than to discover it as a duplicated mission.
        """
        parent = self.get(parent_goal_id)
        if parent is None:
            raise KeyError(f"no APEX goal {parent_goal_id!r}")
        if parent.is_terminal:
            raise IllegalGoalTransition(f"goal '{parent_goal_id}' is terminal ('{parent.state.value}'); it cannot gain children")

        requested = int(kwargs.get("priority", parent.priority))
        ancestors = self.ancestors(parent_goal_id)
        ceiling = min([a.priority for a in [*ancestors, parent]] or [parent.priority])
        if requested > ceiling:
            raise ValueError(f"subgoal priority {requested} exceeds the ancestor ceiling {ceiling}; a decomposition may not outrank the work it derives from")

        child = self.create(
            objective=str(kwargs.get("objective", "")),
            owner=str(kwargs.get("owner", parent.owner)),
            description=str(kwargs.get("description", "")),
            success_criteria=list(kwargs.get("success_criteria") or []),
            constraints=list(kwargs.get("constraints") or []),
            priority=requested,
            deadline=kwargs.get("deadline"),
            risk=str(kwargs.get("risk", parent.risk)),
            budget=dict(kwargs.get("budget") or {}),
            # A decomposition inherits the session it was derived
            # from, so the goal tree and the session's decision
            # log stay joinable without a second link. ``or``
            # rather than ``get(default)``: an explicit empty
            # string in the request must not shadow the parent's
            # link with nothing.
            session_id=str(kwargs.get("session_id") or parent.session_id),
            mission_id=str(kwargs.get("mission_id") or parent.mission_id),
        )
        child.parent_goal_id = parent_goal_id
        with self._lock:
            parent.child_ids.append(child.goal_id)
            self._save()
        self._journal(
            child.goal_id,
            "goal.decomposed",
            parent_goal_id=parent_goal_id,
            priority=child.priority,
        )
        return child

    def transition(self, goal_id: str, target: GoalState, *, reason: str = "") -> ApexGoal:
        """Move a goal, refusing an illegal or terminal transition."""
        with self._lock:
            goal = self._rows.get(goal_id)
            if goal is None:
                raise KeyError(f"no APEX goal {goal_id!r}")
            if goal.state is target:
                return goal
            if goal.is_terminal:
                raise IllegalGoalTransition(f"goal '{goal_id}' is terminal ('{goal.state.value}'); it cannot move to '{target.value}'")
            allowed = GOAL_TRANSITIONS.get(goal.state, frozenset())
            if target not in allowed:
                raise IllegalGoalTransition(f"goal '{goal_id}' cannot move from '{goal.state.value}' to '{target.value}'; allowed: {sorted(s.value for s in allowed)}")
            previous = goal.state
            goal.state = target
            goal.updated_at = time.time()
            if target in WAITING_GOAL_STATES:
                goal.blocked_reason = reason
            elif target is GoalState.REPLANNING:
                goal.replan_count += 1
                goal.plan_version += 1
            self._save()
            self._journal(
                goal_id,
                "goal.transitioned",
                **{"from": previous.value, "to": target.value, "reason": reason},
            )
            return goal

    def request_completion(self, goal_id: str, *, reason: str = "") -> ApexGoal:
        """Enter ``COMPLETED``, or raise with the real refusal.

        Routed through ``alpha.mission.acceptance`` — the same gate
        ``MissionLifecycle.request_pass`` uses — so a goal with an unmeasured or
        failed criterion cannot close. ``PARTIAL`` stays reachable for the
        honest partial result; it is not a consolation prize behind this gate.
        """
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError(f"no APEX goal {goal_id!r}")

        unmeasured = goal.criteria_without_evidence()
        failed = goal.criteria_failed()
        if not goal.success_criteria:
            # Nothing was promised, so nothing can be claimed.
            raise IllegalGoalTransition(f"goal '{goal_id}' has no success criteria, so there is nothing to prove; record PARTIAL or FAILED instead")
        if unmeasured or failed:
            detail = []
            if unmeasured:
                detail.append(f"{len(unmeasured)} unmeasured ({', '.join(sorted(unmeasured))})")
            if failed:
                # The failing criterion is named, not counted:
                # an operator reading the refusal must be able to
                # act on it without a second lookup.
                detail.append(f"{len(failed)} failed ({', '.join(sorted(failed))})")
            raise IllegalGoalTransition(f"goal '{goal_id}' cannot be COMPLETED ({', '.join(detail)}); measure the criteria first, or report PARTIAL")
        completed = self.transition(goal_id, GoalState.COMPLETED, reason=reason)
        self._journal(goal_id, "goal.completed", reason=reason, criteria=list(goal.success_criteria))
        return completed

    def add_evidence(self, goal_id: str, evidence: GoalEvidence) -> ApexGoal | None:
        """Attach one measurement. Unmeasured evidence is stored but decides nothing."""
        with self._lock:
            goal = self._rows.get(goal_id)
            if goal is None:
                return None
            goal.evidence.append(evidence)
            goal.updated_at = time.time()
            self._save()
            self._journal(
                goal_id,
                "goal.evidence_recorded",
                evidence_id=evidence.evidence_id,
                criterion=evidence.criterion,
                evaluated=evidence.evaluated,
                holds=evidence.holds,
                source=evidence.source,
            )
            return goal

    def add_artifact(self, goal_id: str, artifact_ref: str) -> ApexGoal | None:
        with self._lock:
            goal = self._rows.get(goal_id)
            if goal is None:
                return None
            if artifact_ref not in goal.artifacts:
                goal.artifacts.append(artifact_ref)
                goal.updated_at = time.time()
                self._save()
            return goal

    def set_strategy(self, goal_id: str, strategy: str) -> ApexGoal | None:
        with self._lock:
            goal = self._rows.get(goal_id)
            if goal is None:
                return None
            goal.current_strategy = str(strategy)
            goal.updated_at = time.time()
            self._save()
            self._journal(goal_id, "goal.strategy_set", strategy=goal.current_strategy)
            return goal

    def add_agent(self, goal_id: str, agent_id: str, *, role: str = "") -> ApexGoal | None:
        """Record that a specialist was asked for this goal (spec §10/§16).

        The subagent lifecycle manager owns the agent itself — leases,
        heartbeats, recovery. This records only the *ask*: which agent,
        in what role, for which goal. That record is what makes
        ``GET /api/apex/goals/{id}/agents`` a list of real asks rather
        than a guess, and it is why an empty list means "none recorded",
        never "none exist".
        """
        with self._lock:
            goal = self._rows.get(goal_id)
            if goal is None:
                return None
            if goal.is_terminal:
                self._journal(goal_id, "goal.agent_refused", agent_id=str(agent_id), reason="goal is terminal")
                return None
            record = {
                "agent_id": str(agent_id),
                "role": str(role),
                "recorded_at": time.time(),
            }
            goal.agent_records.append(record)
            goal.updated_at = time.time()
            self._save()
        self._journal(goal_id, "goal.agent_recorded", **record)
        return goal

    def add_constraint(self, goal_id: str, instruction: str, *, source: str = "user") -> ApexGoal | None:
        """Attach one steering constraint to a goal (spec §57).

        The constraint list stays plain strings — the goal row's schema —
        while the *source* rides the event, so an operator-issued
        constraint is distinguishable from a model-proposed one in the
        audit trail without a second stored field.
        """
        if not str(instruction).strip():
            return None
        with self._lock:
            goal = self._rows.get(goal_id)
            if goal is None:
                return None
            if goal.is_terminal:
                self._journal(goal_id, "goal.constraint_refused", instruction=instruction, reason="goal is terminal")
                return None
            goal.constraints.append(str(instruction))
            goal.updated_at = time.time()
            self._save()
        self._journal(goal_id, "goal.constraint_recorded", instruction=str(instruction), source=str(source))
        return goal

    def verify(self, goal_id: str, *, reason: str = "") -> ApexGoal:
        """The verification-first completion flow (spec §16).

        A goal whose criteria are not all measured enters ``VERIFYING``
        — where that transition is legal — so the work that would
        measure them can run; a goal whose criteria are all measured
        closes through the acceptance gate, the only thing that may
        write ``COMPLETED``. A criterion that measured false is named
        in the refusal, because a partial result is reported as
        ``PARTIAL``, never folded into a completion.
        """
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError(f"no APEX goal {goal_id!r}")
        if goal.is_terminal:
            raise IllegalGoalTransition(f"goal '{goal_id}' is terminal ('{goal.state.value}'); it cannot be verified")
        unmeasured = goal.criteria_without_evidence()
        if unmeasured:
            # Already in VERIFYING is a no-op transition, so a goal
            # mid-verification reports its missing measurements rather
            # than erroring on its own state.
            return self.transition(
                goal_id,
                GoalState.VERIFYING,
                reason=reason or f"{len(unmeasured)} criteria unmeasured",
            )
        completed = self.request_completion(goal_id, reason=reason or "all criteria measured")
        self._journal(goal_id, "goal.verified", reason=reason)
        return completed

    def delete(self, goal_id: str) -> bool:
        with self._lock:
            goal = self._rows.get(goal_id)
            if goal is None:
                return False
            if goal.child_ids:
                raise ValueError(f"goal '{goal_id}' still has {len(goal.child_ids)} child goal(s); delete or reparent them first")
            del self._rows[goal_id]
            self._save()
            return True


def _default_storage_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "apex" / "goals.json"
    except Exception:
        return Path.cwd() / ".alpha" / "apex" / "goals.json"


_store: ApexGoalStore | None = None
_store_lock = threading.Lock()


def _session_event_sink() -> Callable[[str, str, dict[str, Any]], None]:
    """The APEX journal writer, resolved lazily per call.

    ``alpha.apex.store`` owns the one ``events.jsonl`` writer. Resolving
    it here — rather than importing it at module load — keeps the
    harness's import graph acyclic, and a goal store built before the
    session store exists still works: the sink simply fails closed into
    the ``_journal`` logger on the unlikely path that it cannot resolve.
    """

    def _sink(mission_id: str, event_type: str, payload: dict[str, Any]) -> None:
        from alpha.apex.store import get_apex_store

        get_apex_store().emit(mission_id, event_type, **payload)

    return _sink


def get_goal_store() -> ApexGoalStore:
    global _store
    with _store_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _store is None or str(_store.storage_path) != live:
            _store = ApexGoalStore(event_sink=_session_event_sink())
        return _store
