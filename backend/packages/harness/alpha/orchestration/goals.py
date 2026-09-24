"""Goal-Oriented Task Decomposer (master plan section 3).

Hierarchical decomposition: Goal -> Milestones -> Tasks -> Subtasks, with a
typed task state machine enforced in deterministic Python — LLMs propose
transitions and decompositions, this runtime decides and applies them.

State machine (spec 3.1, minus BLOCKED which is out of Module A scope):

    PENDING -> READY -> IN_PROGRESS -> COMPLETED        (verified)
                          |
                          +-> COMPENSATING              (failure rollback recorded)

Enforcement rules, all deterministic:

* only edges declared in ``ALLOWED_TRANSITIONS`` may fire;
* ``READY`` additionally requires every dependency task to be ``COMPLETED``
  (a task with zero dependencies satisfies this vacuously);
* ``COMPLETED`` requires non-empty evidence (spec 3.1: "verified with
  non-empty ... evidence");
* ``COMPENSATING`` requires a non-empty failure reason;
* dependency edges always point at already-created tasks, so the dependency
  graph is acyclic by construction — no runtime cycle search is needed.

Honesty notes:

* ``COMPENSATING`` means a failure triggered rollback of this task's
  side-effects. This module records and enforces state only; it never claims
  a rollback actually ran — executing compensation belongs to the runtime
  executor layer that consumes these records.
* Goal ``status``/``completion_percent`` are recomputed deterministically
  from task states on every save; they are never asserted by an LLM.

Durable sync (spec 3.2): ``runtime_home()/projects/{project_id}/`` holds
``goals.json`` (goals + milestones + completion percentages), ``tasks.json``
(tasks/subtasks with assignee + evidence) and ``decisions.json`` (rationale
log). All writes go through ``alpha.evolution.identity.atomic_write_json``
(tmp file + ``os.replace``, UTF-8). Those paths are distinct from the
``alpha.projects`` stores (``tasks/goal-*.json`` and
``decisions/decisions.json``), so the two layers never share a file.

This module is a synchronous library: callers on the asyncio event loop must
offload mutations to an executor (e.g. ``alpha.utils.file_io.run_file_io`` /
``asyncio.to_thread``). Locks are per-instance only; separate
instances/processes targeting the same files can lose updates.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json

SCHEMA_VERSION = 1

GOALS_FILE = "goals.json"
TASKS_FILE = "tasks.json"
DECISIONS_FILE = "decisions.json"

# project_id becomes a path segment: refuse separators and traversal outright.
_PROJECT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class TaskState(StrEnum):
    """Typed task lifecycle states (spec 3.1, BLOCKED out of Module A scope)."""

    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    COMPENSATING = "compensating"


# The single authoritative transition table. Anything not listed is rejected.
ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.READY}),
    TaskState.READY: frozenset({TaskState.IN_PROGRESS}),
    TaskState.IN_PROGRESS: frozenset({TaskState.COMPLETED, TaskState.COMPENSATING}),
    TaskState.COMPLETED: frozenset(),
    TaskState.COMPENSATING: frozenset(),
}


class GoalDecompositionError(ValueError):
    """Base class for every deterministic rejection this engine raises."""


class DecompositionError(GoalDecompositionError):
    """Structural violation: unknown id, bad parent, empty title, traversal."""


class InvalidTaskTransitionError(GoalDecompositionError):
    """Requested edge is not present in ALLOWED_TRANSITIONS."""


class UnmetDependencyError(GoalDecompositionError):
    """READY requested while at least one dependency task is not COMPLETED."""


class EvidenceRequiredError(GoalDecompositionError):
    """COMPLETED requested without non-empty evidence."""


class CorruptGoalStateError(GoalDecompositionError):
    """Persisted state is unreadable or fails cross-reference validation; fail closed."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class GoalRecord:
    """High-level goal: objective, success criteria, milestone hierarchy."""

    id: str
    objective: str
    success_criteria: list[str] = field(default_factory=list)
    milestone_ids: list[str] = field(default_factory=list)
    status: str = "active"  # "active" | "complete" (derived, never asserted)
    completion_percent: float = 0.0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objective": self.objective,
            "success_criteria": list(self.success_criteria),
            "milestone_ids": list(self.milestone_ids),
            "status": self.status,
            "completion_percent": self.completion_percent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalRecord:
        return cls(
            id=str(data["id"]),
            objective=str(data["objective"]),
            success_criteria=[str(item) for item in data.get("success_criteria", [])],
            milestone_ids=[str(item) for item in data.get("milestone_ids", [])],
            status=str(data.get("status", "active")),
            completion_percent=float(data.get("completion_percent", 0.0)),
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
        )


@dataclass
class Milestone:
    """Intermediate grouping between a goal and its tasks."""

    id: str
    goal_id: str
    title: str
    task_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "title": self.title,
            "task_ids": list(self.task_ids),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Milestone:
        return cls(
            id=str(data["id"]),
            goal_id=str(data["goal_id"]),
            title=str(data["title"]),
            task_ids=[str(item) for item in data.get("task_ids", [])],
            created_at=str(data.get("created_at", _now())),
        )


@dataclass
class TaskRecord:
    """A task (parent_id None) or subtask (parent_id set) in the hierarchy."""

    id: str
    goal_id: str
    milestone_id: str
    title: str
    parent_id: str | None = None
    state: TaskState = TaskState.PENDING
    depends_on: list[str] = field(default_factory=list)
    assignee: str | None = None
    attempts: int = 0
    evidence: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "milestone_id": self.milestone_id,
            "parent_id": self.parent_id,
            "title": self.title,
            "state": self.state.value,
            "depends_on": list(self.depends_on),
            "assignee": self.assignee,
            "attempts": self.attempts,
            "evidence": list(self.evidence),
            "failure_reason": self.failure_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        return cls(
            id=str(data["id"]),
            goal_id=str(data["goal_id"]),
            milestone_id=str(data["milestone_id"]),
            parent_id=None if data.get("parent_id") is None else str(data["parent_id"]),
            title=str(data["title"]),
            state=TaskState(str(data["state"])),
            depends_on=[str(item) for item in data.get("depends_on", [])],
            assignee=None if data.get("assignee") is None else str(data["assignee"]),
            attempts=int(data.get("attempts", 0)),
            evidence=[str(item) for item in data.get("evidence", [])],
            failure_reason=None if data.get("failure_reason") is None else str(data["failure_reason"]),
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
        )


@dataclass
class DecisionRecord:
    """Architectural trade-off / rationale logged during execution (spec 3.2)."""

    id: str
    topic: str
    decision: str
    rationale: str = ""
    goal_id: str | None = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "decision": self.decision,
            "rationale": self.rationale,
            "goal_id": self.goal_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        return cls(
            id=str(data["id"]),
            topic=str(data["topic"]),
            decision=str(data["decision"]),
            rationale=str(data.get("rationale", "")),
            goal_id=None if data.get("goal_id") is None else str(data["goal_id"]),
            created_at=str(data.get("created_at", _now())),
        )


class GoalDecomposer:
    """Durable Milestones -> Tasks -> Subtasks decomposer with enforced state.

    Construction loads any persisted state for ``project_id`` and fails closed
    (``CorruptGoalStateError``) on unreadable or cross-reference-invalid files —
    a corrupt store is never silently replaced with an empty one.
    """

    def __init__(self, project_id: str, *, home: Path | None = None) -> None:
        if not isinstance(project_id, str) or not _PROJECT_ID_RE.fullmatch(project_id):
            raise DecompositionError(f"invalid project_id {project_id!r}: expected 1-128 chars of [A-Za-z0-9._-] starting alphanumeric (path-safe)")
        self.project_id = project_id
        base = Path(home) if home is not None else runtime_home()
        self.root = base / "projects" / project_id
        self.goals: dict[str, GoalRecord] = {}
        self.milestones: dict[str, Milestone] = {}
        self.tasks: dict[str, TaskRecord] = {}
        self.decisions: list[DecisionRecord] = []
        self._load()

    # ------------------------------------------------------------------
    # Durable sync (invariant 3: atomic writes; invariant 4: UTF-8)
    # ------------------------------------------------------------------
    @property
    def goals_path(self) -> Path:
        return self.root / GOALS_FILE

    @property
    def tasks_path(self) -> Path:
        return self.root / TASKS_FILE

    @property
    def decisions_path(self) -> Path:
        return self.root / DECISIONS_FILE

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CorruptGoalStateError(f"cannot read {path}: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise CorruptGoalStateError(f"corrupt JSON in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise CorruptGoalStateError(f"corrupt state in {path}: expected a JSON object, got {type(data).__name__}")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise CorruptGoalStateError(f"unsupported schema_version {version!r} in {path} (expected {SCHEMA_VERSION})")
        return data

    def _load(self) -> None:
        goals_payload = self._read_json(self.goals_path)
        if goals_payload is not None:
            try:
                for item in goals_payload.get("goals", []):
                    record = GoalRecord.from_dict(item)
                    if record.id in self.goals:
                        raise ValueError(f"duplicate goal id {record.id!r}")
                    self.goals[record.id] = record
                for item in goals_payload.get("milestones", []):
                    milestone = Milestone.from_dict(item)
                    if milestone.id in self.milestones:
                        raise ValueError(f"duplicate milestone id {milestone.id!r}")
                    if milestone.goal_id not in self.goals:
                        raise ValueError(f"milestone {milestone.id!r} references unknown goal {milestone.goal_id!r}")
                    self.milestones[milestone.id] = milestone
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptGoalStateError(f"invalid goal state in {self.goals_path}: {exc}") from exc

        tasks_payload = self._read_json(self.tasks_path)
        if tasks_payload is not None:
            try:
                for item in tasks_payload.get("tasks", []):
                    record = TaskRecord.from_dict(item)
                    if record.id in self.tasks:
                        raise ValueError(f"duplicate task id {record.id!r}")
                    self.tasks[record.id] = record
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptGoalStateError(f"invalid task state in {self.tasks_path}: {exc}") from exc
            # Cross-reference validation after the full index exists.
            try:
                for record in self.tasks.values():
                    if record.goal_id not in self.goals:
                        raise ValueError(f"task {record.id!r} references unknown goal {record.goal_id!r}")
                    if record.milestone_id not in self.milestones:
                        raise ValueError(f"task {record.id!r} references unknown milestone {record.milestone_id!r}")
                    if record.parent_id is not None and record.parent_id not in self.tasks:
                        raise ValueError(f"task {record.id!r} references unknown parent {record.parent_id!r}")
                    for dep in record.depends_on:
                        if dep not in self.tasks:
                            raise ValueError(f"task {record.id!r} references unknown dependency {dep!r}")
                        if self.tasks[dep].goal_id != record.goal_id:
                            raise ValueError(f"task {record.id!r} depends on cross-goal task {dep!r}")
            except ValueError as exc:
                raise CorruptGoalStateError(f"invalid task cross-references in {self.tasks_path}: {exc}") from exc

        decisions_payload = self._read_json(self.decisions_path)
        if decisions_payload is not None:
            try:
                for item in decisions_payload.get("decisions", []):
                    record = DecisionRecord.from_dict(item)
                    if record.goal_id is not None and record.goal_id not in self.goals:
                        raise ValueError(f"decision {record.id!r} references unknown goal {record.goal_id!r}")
                    self.decisions.append(record)
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptGoalStateError(f"invalid decision state in {self.decisions_path}: {exc}") from exc

    def save(self) -> None:
        """Persist all three stores atomically (also called after each mutation)."""
        self._persist_goals()
        self._persist_tasks()
        self._persist_decisions()

    def _persist_goals(self) -> None:
        self._recompute_goal_progress()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "project_id": self.project_id,
            "goals": [goal.to_dict() for goal in self.goals.values()],
            "milestones": [milestone.to_dict() for milestone in self.milestones.values()],
        }
        atomic_write_json(self.goals_path, payload)

    def _persist_tasks(self) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "project_id": self.project_id,
            "tasks": [task.to_dict() for task in self.tasks.values()],
        }
        atomic_write_json(self.tasks_path, payload)

    def _persist_decisions(self) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "project_id": self.project_id,
            "decisions": [record.to_dict() for record in self.decisions],
        }
        atomic_write_json(self.decisions_path, payload)

    # ------------------------------------------------------------------
    # Deterministic derived state
    # ------------------------------------------------------------------
    def _tasks_of_goal(self, goal_id: str) -> list[TaskRecord]:
        return [task for task in self.tasks.values() if task.goal_id == goal_id]

    def _recompute_goal_progress(self) -> None:
        """Status and completion percent are derived from task states only.

        A pure read (no change) never mutates ``updated_at``.
        """
        for goal in self.goals.values():
            tasks = self._tasks_of_goal(goal.id)
            if tasks:
                completed = sum(1 for task in tasks if task.state is TaskState.COMPLETED)
                new_percent = round(100.0 * completed / len(tasks), 2)
                new_status = "complete" if completed == len(tasks) else "active"
            else:
                # A goal with no tasks cannot be evidenced complete; stay active.
                new_percent = 0.0
                new_status = "active"
            if new_percent != goal.completion_percent or new_status != goal.status:
                goal.completion_percent = new_percent
                goal.status = new_status
                goal.updated_at = _now()

    def goal_progress(self, goal_id: str) -> dict[str, Any]:
        goal = self._goal(goal_id)
        tasks = self._tasks_of_goal(goal.id)
        counts = {state.value: 0 for state in TaskState}
        for task in tasks:
            counts[task.state.value] += 1
        self._recompute_goal_progress()
        return {
            "goal_id": goal.id,
            "objective": goal.objective,
            "status": goal.status,
            "completion_percent": goal.completion_percent,
            "total_tasks": len(tasks),
            "state_counts": counts,
        }

    def _refresh_ready(self) -> list[str]:
        """Promote every PENDING task whose dependencies are all COMPLETED.

        A single pass suffices: promotion never completes a task, so no new
        dependency becomes satisfied while the pass runs.
        """
        promoted: list[str] = []
        for task in self.tasks.values():
            if task.state is not TaskState.PENDING:
                continue
            if all(self.tasks[dep].state is TaskState.COMPLETED for dep in task.depends_on):
                task.state = TaskState.READY
                task.updated_at = _now()
                promoted.append(task.id)
        return promoted

    # ------------------------------------------------------------------
    # Lookups (deterministic rejections)
    # ------------------------------------------------------------------
    def _goal(self, goal_id: str) -> GoalRecord:
        try:
            return self.goals[goal_id]
        except KeyError:
            raise DecompositionError(f"unknown goal {goal_id!r}") from None

    def _milestone(self, milestone_id: str) -> Milestone:
        try:
            return self.milestones[milestone_id]
        except KeyError:
            raise DecompositionError(f"unknown milestone {milestone_id!r}") from None

    def _task(self, task_id: str) -> TaskRecord:
        try:
            return self.tasks[task_id]
        except KeyError:
            raise DecompositionError(f"unknown task {task_id!r}") from None

    @staticmethod
    def _require_text(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DecompositionError(f"{label} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _require_evidence(evidence: str | Sequence[str] | None) -> list[str]:
        if isinstance(evidence, str):
            items = [evidence.strip()] if evidence.strip() else []
        elif evidence is None:
            items = []
        else:
            items = [str(item).strip() for item in evidence if str(item).strip()]
        if not items:
            raise EvidenceRequiredError("COMPLETED requires non-empty evidence (spec 3.1): pass an evidence string or a non-empty sequence of them")
        return items

    def _validate_dependencies(self, task_goal_id: str, depends_on: Sequence[str]) -> list[str]:
        resolved: list[str] = []
        for dep in depends_on:
            dep_task = self._task(dep)  # raises DecompositionError when unknown
            if dep_task.goal_id != task_goal_id:
                raise DecompositionError(f"dependency {dep!r} belongs to a different goal")
            if dep not in resolved:
                resolved.append(dep)
        # Edges only ever reference already-created tasks, so the graph is
        # acyclic by construction (no cycle search needed).
        return resolved

    # ------------------------------------------------------------------
    # Decomposition API
    # ------------------------------------------------------------------
    def create_goal(self, objective: str, *, success_criteria: Sequence[str] = (), milestones: Sequence[str] = ()) -> GoalRecord:
        objective = self._require_text(objective, "objective")
        criteria = [self._require_text(item, "success criterion") for item in success_criteria]
        goal = GoalRecord(id=_new_id("goal"), objective=objective, success_criteria=criteria)
        self.goals[goal.id] = goal
        for title in milestones:
            self.add_milestone(goal.id, title, _persist=False)
        self._persist_goals()
        return goal

    def add_milestone(self, goal_id: str, title: str, *, _persist: bool = True) -> Milestone:
        self._goal(goal_id)
        title = self._require_text(title, "milestone title")
        milestone = Milestone(id=_new_id("ms"), goal_id=goal_id, title=title)
        self.milestones[milestone.id] = milestone
        self.goals[goal_id].milestone_ids.append(milestone.id)
        self.goals[goal_id].updated_at = _now()
        if _persist:
            self._persist_goals()
        return milestone

    def add_task(self, milestone_id: str, title: str, *, depends_on: Sequence[str] = (), assignee: str | None = None) -> TaskRecord:
        milestone = self._milestone(milestone_id)
        return self._create_task(milestone, title, parent_id=None, depends_on=depends_on, assignee=assignee)

    def add_subtask(self, parent_task_id: str, title: str, *, depends_on: Sequence[str] = (), assignee: str | None = None) -> TaskRecord:
        parent = self._task(parent_task_id)
        if parent.parent_id is not None:
            raise DecompositionError(f"task {parent.id!r} is already a subtask; hierarchy depth is Milestone -> Task -> Subtask")
        milestone = self._milestone(parent.milestone_id)
        return self._create_task(milestone, title, parent_id=parent.id, depends_on=depends_on, assignee=assignee)

    def _create_task(self, milestone: Milestone, title: str, *, parent_id: str | None, depends_on: Sequence[str], assignee: str | None) -> TaskRecord:
        title = self._require_text(title, "task title")
        goal_id = milestone.goal_id
        deps = self._validate_dependencies(goal_id, depends_on)
        task = TaskRecord(
            id=_new_id("task"),
            goal_id=goal_id,
            milestone_id=milestone.id,
            parent_id=parent_id,
            title=title,
            depends_on=deps,
            assignee=None if assignee is None else str(assignee),
        )
        self.tasks[task.id] = task
        milestone.task_ids.append(task.id)
        # Deterministic immediate promotion: zero/satisfied deps => READY now.
        self._refresh_ready()
        self._persist_tasks()
        self._persist_goals()
        return task

    # ------------------------------------------------------------------
    # State machine (the single enforcement point)
    # ------------------------------------------------------------------
    def transition(self, task_id: str, to_state: TaskState | str, *, evidence: str | Sequence[str] | None = None, reason: str = "") -> TaskRecord:
        task = self._task(task_id)
        try:
            target = TaskState(to_state)
        except ValueError:
            raise InvalidTaskTransitionError(f"task {task_id}: unknown state {to_state!r}") from None
        allowed = ALLOWED_TRANSITIONS[task.state]
        if target not in allowed:
            allowed_list = ", ".join(sorted(state.value for state in allowed)) or "none (terminal)"
            raise InvalidTaskTransitionError(f"task {task_id}: illegal transition {task.state.value} -> {target.value} (allowed from {task.state.value}: {allowed_list})")

        evidence_items: list[str] = []
        if target is TaskState.READY:
            unmet = [dep for dep in task.depends_on if self.tasks[dep].state is not TaskState.COMPLETED]
            if unmet:
                raise UnmetDependencyError(f"task {task_id}: cannot enter READY with unmet dependencies {unmet}")
        elif target is TaskState.COMPLETED:
            evidence_items = self._require_evidence(evidence)
        elif target is TaskState.COMPENSATING:
            if not isinstance(reason, str) or not reason.strip():
                raise DecompositionError(f"task {task_id}: COMPENSATING requires a non-empty failure reason")

        task.state = target
        task.updated_at = _now()
        if target is TaskState.IN_PROGRESS:
            task.attempts += 1
        elif target is TaskState.COMPLETED:
            for item in evidence_items:
                if item not in task.evidence:
                    task.evidence.append(item)
            # Deterministic cascades: promote satisfied dependents and
            # recompute derived goal status/percentage.
            self._refresh_ready()
        elif target is TaskState.COMPENSATING:
            task.failure_reason = reason.strip()

        self._persist_tasks()
        self._persist_goals()
        return task

    def start(self, task_id: str) -> TaskRecord:
        """READY -> IN_PROGRESS."""
        return self.transition(task_id, TaskState.IN_PROGRESS)

    def complete(self, task_id: str, evidence: str | Sequence[str]) -> TaskRecord:
        """IN_PROGRESS -> COMPLETED; evidence is mandatory (spec 3.1)."""
        return self.transition(task_id, TaskState.COMPLETED, evidence=evidence)

    def fail(self, task_id: str, error: str) -> TaskRecord:
        """IN_PROGRESS -> COMPENSATING on an injected/real failure.

        Records that rollback was triggered; this engine does not itself
        execute compensation (see module docstring).
        """
        return self.transition(task_id, TaskState.COMPENSATING, reason=error)

    def refresh_ready(self) -> list[str]:
        """Promote every dependency-satisfied PENDING task; returns promoted ids."""
        promoted = self._refresh_ready()
        if promoted:
            self._persist_tasks()
            self._persist_goals()
        return promoted

    # ------------------------------------------------------------------
    # Decisions ledger (spec 3.2)
    # ------------------------------------------------------------------
    def record_decision(self, topic: str, decision: str, *, rationale: str = "", goal_id: str | None = None) -> DecisionRecord:
        topic = self._require_text(topic, "topic")
        decision = self._require_text(decision, "decision")
        if goal_id is not None:
            self._goal(goal_id)
        record = DecisionRecord(
            id=_new_id("dec"),
            topic=topic,
            decision=decision,
            rationale=str(rationale),
            goal_id=goal_id,
        )
        self.decisions.append(record)
        self._persist_decisions()
        return record
