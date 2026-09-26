"""Data models and state contracts for the Alpha autonomous swarm runtime."""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class SwarmMode(StrEnum):
    AUTO = "auto"
    PARALLEL = "parallel"
    MAP_REDUCE = "map_reduce"
    SCATTER_GATHER = "scatter_gather"
    HIERARCHICAL = "hierarchical"
    DEBATE = "debate"
    ENSEMBLE = "ensemble"
    CODING_WORKTREE = "coding_worktree"


class TaskNodeState(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    STRAGGLING = "straggling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# These are intentionally strings in the persisted/API contract.  Keeping the
# constants here gives every lifecycle consumer one terminal-state definition.
TERMINAL_SWARM_STATUSES = frozenset({"completed", "failed", "cancelled", "budget_exhausted", "stalled"})
NON_TERMINAL_SWARM_STATUSES = frozenset({"planning", "running", "paused", "aggregating", "verifying", "partial_success"})


def is_terminal_swarm_status(status: str) -> bool:
    """Return whether *status* may not be restarted or expanded."""

    return str(status) in TERMINAL_SWARM_STATUSES


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return _as_int(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    result = _as_float(value, default=math.nan)
    return result if math.isfinite(result) else None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if item is not None and str(item)]


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass
class SwarmBudget:
    """Hard, measured limits for one swarm run.

    ``None`` means unlimited for that dimension.  A zero limit is meaningful and
    fails closed: no tokens/tool calls are allowed.  The object stores measured
    usage only; it never invents a budget or a confidence value.
    """

    max_tokens: int | None = None
    max_tool_calls: int | None = None
    max_wall_seconds: float | None = None
    max_tasks: int = 256
    max_replans: int = 3
    max_consecutive_failures: int = 8
    used_tokens: int = 0
    used_tool_calls: int = 0
    started_at: float | None = None
    exhausted_reason: str | None = None
    consecutive_failures: int = 0

    def __post_init__(self) -> None:
        for name in ("max_tokens", "max_tool_calls"):
            value = getattr(self, name)
            if value is not None and int(value) < 0:
                raise ValueError(f"{name} must be non-negative or None")
        if self.max_wall_seconds is not None and (not math.isfinite(float(self.max_wall_seconds)) or float(self.max_wall_seconds) < 0):
            raise ValueError("max_wall_seconds must be a finite non-negative number or None")
        if int(self.max_tasks) < 1:
            raise ValueError("max_tasks must be at least 1")
        if int(self.max_replans) < 0:
            raise ValueError("max_replans must be non-negative")
        if int(self.max_consecutive_failures) < 1:
            raise ValueError("max_consecutive_failures must be at least 1")
        self.used_tokens = max(0, int(self.used_tokens))
        self.used_tool_calls = max(0, int(self.used_tool_calls))
        self.consecutive_failures = max(0, int(self.consecutive_failures))

    def start(self, now: float | None = None) -> None:
        """Start wall-clock accounting once, preserving usage across resume."""

        if self.started_at is None:
            self.started_at = float(now if now is not None else time.time())

    def can_add_tasks(self, count: int, current_task_count: int) -> bool:
        return int(current_task_count) + max(0, int(count)) <= int(self.max_tasks)

    def _exhaust(self, reason: str) -> None:
        if self.exhausted_reason is None:
            self.exhausted_reason = reason

    def record_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Record measured usage and return a truthful budget snapshot."""

        input_tokens = max(0, int(input_tokens))
        output_tokens = max(0, int(output_tokens))
        tool_calls = max(0, int(tool_calls))
        self.start(now)
        self.used_tokens += input_tokens + output_tokens
        self.used_tool_calls += tool_calls

        if self.max_tokens is not None and self.used_tokens >= self.max_tokens:
            self._exhaust("token_budget_exhausted")
        if self.max_tool_calls is not None and self.used_tool_calls >= self.max_tool_calls:
            self._exhaust("tool_call_budget_exhausted")
        if self.consecutive_failures >= self.max_consecutive_failures:
            self._exhaust("consecutive_failure_budget_exhausted")
        if self.max_wall_seconds is not None and self.started_at is not None:
            elapsed = max(0.0, float(now if now is not None else time.time()) - self.started_at)
            if elapsed >= self.max_wall_seconds:
                self._exhaust("wall_clock_budget_exhausted")
        return self.to_dict()

    def check(self, now: float | None = None) -> dict[str, Any]:
        """Check hard-limit exhaustion without inventing usage."""

        self.start(now)
        if self.max_tokens is not None and self.used_tokens >= self.max_tokens:
            self._exhaust("token_budget_exhausted")
        if self.max_tool_calls is not None and self.used_tool_calls >= self.max_tool_calls:
            self._exhaust("tool_call_budget_exhausted")
        if self.consecutive_failures >= self.max_consecutive_failures:
            self._exhaust("consecutive_failure_budget_exhausted")
        if self.max_wall_seconds is not None and self.started_at is not None:
            elapsed = max(0.0, float(now if now is not None else time.time()) - self.started_at)
            if elapsed >= self.max_wall_seconds:
                self._exhaust("wall_clock_budget_exhausted")
        return self.to_dict()

    def record_task_success(self) -> dict[str, Any]:
        """Reset the consecutive-failure circuit after a completed attempt."""

        self.consecutive_failures = 0
        return self.to_dict()

    def record_task_failure(self) -> dict[str, Any]:
        """Count one failed attempt and trip the configured failure circuit."""

        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self._exhaust("consecutive_failure_budget_exhausted")
        return self.to_dict()

    @property
    def exhausted(self) -> bool:
        return self.exhausted_reason is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "max_tool_calls": self.max_tool_calls,
            "max_wall_seconds": self.max_wall_seconds,
            "max_tasks": self.max_tasks,
            "max_replans": self.max_replans,
            "max_consecutive_failures": self.max_consecutive_failures,
            "used_tokens": self.used_tokens,
            "used_tool_calls": self.used_tool_calls,
            "consecutive_failures": self.consecutive_failures,
            "started_at": self.started_at,
            "exhausted": self.exhausted,
            "exhausted_reason": self.exhausted_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> SwarmBudget:
        data = data or {}
        return cls(
            max_tokens=_as_optional_int(data.get("max_tokens")),
            max_tool_calls=_as_optional_int(data.get("max_tool_calls")),
            max_wall_seconds=_as_optional_float(data.get("max_wall_seconds")),
            max_tasks=_as_int(data.get("max_tasks"), 256),
            max_replans=_as_int(data.get("max_replans"), 3),
            max_consecutive_failures=_as_int(data.get("max_consecutive_failures"), 8),
            used_tokens=_as_int(data.get("used_tokens"), 0),
            used_tool_calls=_as_int(data.get("used_tool_calls"), 0),
            consecutive_failures=_as_int(data.get("consecutive_failures"), 0),
            started_at=_as_optional_float(data.get("started_at")),
            exhausted_reason=str(data["exhausted_reason"]) if data.get("exhausted_reason") else None,
        )


@dataclass
class SwarmDecision:
    should_swarm: bool
    mode: SwarmMode
    reason: str
    estimated_serial_seconds: float
    estimated_parallel_seconds: float
    estimated_speedup: float
    recommended_workers: int
    estimated_overhead_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_swarm": self.should_swarm,
            "mode": self.mode.value,
            "reason": self.reason,
            "estimated_serial_seconds": self.estimated_serial_seconds,
            "estimated_parallel_seconds": self.estimated_parallel_seconds,
            "estimated_speedup": self.estimated_speedup,
            "recommended_workers": self.recommended_workers,
            "estimated_overhead_seconds": self.estimated_overhead_seconds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SwarmDecision:
        try:
            mode = SwarmMode(data.get("mode", "auto"))
        except ValueError:
            mode = SwarmMode.AUTO
        return cls(
            should_swarm=bool(data.get("should_swarm", False)),
            mode=mode,
            reason=str(data.get("reason", "")),
            estimated_serial_seconds=_as_float(data.get("estimated_serial_seconds")),
            estimated_parallel_seconds=_as_float(data.get("estimated_parallel_seconds")),
            estimated_speedup=_as_float(data.get("estimated_speedup"), 1.0),
            recommended_workers=max(1, _as_int(data.get("recommended_workers"), 1)),
            estimated_overhead_seconds=_as_float(data.get("estimated_overhead_seconds")),
        )


@dataclass
class SwarmTaskNode:
    task_id: str
    objective: str
    dependencies: list[str] = field(default_factory=list)
    assigned_worker: str | None = None
    worker_type: str = "ephemeral"  # 'permanent_bot', 'ephemeral', 'local'
    model_override: str | None = None
    worktree_path: str | None = None
    input_artifacts: list[str] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)
    state: TaskNodeState = TaskNodeState.PENDING
    result_summary: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    lease_expires_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    duration_seconds: float = 0.0
    attempts: int = 0
    max_attempts: int = 3
    backup_worker_launched: bool = False
    error_message: str | None = None
    # Swarm v2 fields.  Defaults preserve v1 serialized plans and callers.
    lease_id: str | None = None
    lease_owner: str | None = None
    next_attempt_at: float | None = None
    context_refs: list[str] = field(default_factory=list)
    capability_tags: list[str] = field(default_factory=list)
    parent_task_id: str | None = None
    priority: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)
    tool_calls: int = 0
    result_payload: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    acceptance_criteria: list[dict[str, Any]] = field(default_factory=list)
    acceptance_status: str = "not_required"
    estimated_seconds: float = 15.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "dependencies": list(self.dependencies),
            "assigned_worker": self.assigned_worker,
            "worker_type": self.worker_type,
            "model_override": self.model_override,
            "worktree_path": self.worktree_path,
            "input_artifacts": list(self.input_artifacts),
            "output_artifacts": list(self.output_artifacts),
            "state": self.state.value if isinstance(self.state, TaskNodeState) else self.state,
            "result_summary": self.result_summary,
            "evidence": [_as_mapping(item) for item in self.evidence],
            "lease_expires_at": self.lease_expires_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "backup_worker_launched": self.backup_worker_launched,
            "error_message": self.error_message,
            "lease_id": self.lease_id,
            "lease_owner": self.lease_owner,
            "next_attempt_at": self.next_attempt_at,
            "context_refs": list(self.context_refs),
            "capability_tags": list(self.capability_tags),
            "parent_task_id": self.parent_task_id,
            "priority": self.priority,
            "token_usage": dict(self.token_usage),
            "tool_calls": self.tool_calls,
            "result_payload": dict(self.result_payload),
            "verification": dict(self.verification),
            "acceptance_criteria": [dict(item) for item in self.acceptance_criteria if isinstance(item, Mapping)],
            "acceptance_status": self.acceptance_status,
            "estimated_seconds": self.estimated_seconds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SwarmTaskNode:
        state_val = data.get("state", TaskNodeState.PENDING.value)
        try:
            state = TaskNodeState(state_val)
        except (TypeError, ValueError):
            state = TaskNodeState.PENDING
        return cls(
            task_id=str(data.get("task_id", "")),
            objective=str(data.get("objective", "")),
            dependencies=_as_str_list(data.get("dependencies")),
            assigned_worker=data.get("assigned_worker"),
            worker_type=str(data.get("worker_type", "ephemeral")),
            model_override=data.get("model_override"),
            worktree_path=data.get("worktree_path"),
            input_artifacts=_as_str_list(data.get("input_artifacts")),
            output_artifacts=_as_str_list(data.get("output_artifacts")),
            state=state,
            result_summary=data.get("result_summary"),
            evidence=[_as_mapping(item) for item in data.get("evidence", []) if isinstance(item, Mapping)],
            lease_expires_at=_as_optional_float(data.get("lease_expires_at")),
            started_at=_as_optional_float(data.get("started_at")),
            completed_at=_as_optional_float(data.get("completed_at")),
            duration_seconds=max(0.0, _as_float(data.get("duration_seconds"))),
            attempts=max(0, _as_int(data.get("attempts"))),
            max_attempts=max(1, _as_int(data.get("max_attempts"), 3)),
            backup_worker_launched=bool(data.get("backup_worker_launched", False)),
            error_message=data.get("error_message"),
            lease_id=data.get("lease_id"),
            lease_owner=data.get("lease_owner"),
            next_attempt_at=_as_optional_float(data.get("next_attempt_at")),
            context_refs=_as_str_list(data.get("context_refs")),
            capability_tags=_as_str_list(data.get("capability_tags")),
            parent_task_id=data.get("parent_task_id"),
            priority=_as_int(data.get("priority")),
            token_usage={str(k): max(0, _as_int(v)) for k, v in _as_mapping(data.get("token_usage")).items()},
            tool_calls=max(0, _as_int(data.get("tool_calls"))),
            result_payload=_as_mapping(data.get("result_payload")),
            verification=_as_mapping(data.get("verification")),
            acceptance_criteria=[dict(item) for item in data.get("acceptance_criteria", []) if isinstance(item, Mapping)],
            acceptance_status=str(data.get("acceptance_status", "not_required")),
            estimated_seconds=max(0.0, _as_float(data.get("estimated_seconds"), 15.0)),
        )


@dataclass
class SwarmPlan:
    swarm_id: str
    goal: str
    mode: SwarmMode
    tasks: dict[str, SwarmTaskNode] = field(default_factory=dict)
    estimated_speedup: float = 1.0
    critical_path_seconds: float = 0.0
    max_concurrency: int = 8
    blackboard_context: dict[str, Any] = field(default_factory=dict)
    status: str = "planning"  # planning, running, paused, aggregating, verifying, completed, failed, cancelled, budget_exhausted, stalled
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    completed_at: str | None = None
    final_result: str | None = None
    quality_score: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    # Swarm v2 state and policy fields.
    schema_version: int = 2
    revision: int = 0
    owner_id: str = "default"
    budget: SwarmBudget = field(default_factory=SwarmBudget)
    replan_count: int = 0
    round: int = 0
    terminal_reason: str | None = None
    consensus: dict[str, Any] = field(default_factory=dict)
    auto_replan: bool = False
    requires_consensus: bool = False

    def progress(self) -> dict[str, int]:
        """Return a cheap, derived task-state projection for API clients."""

        return {
            "total": len(self.tasks),
            "pending": sum(1 for task in self.tasks.values() if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED)),
            "running": sum(1 for task in self.tasks.values() if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING)),
            "completed": sum(1 for task in self.tasks.values() if task.state == TaskNodeState.COMPLETED),
            "failed": sum(1 for task in self.tasks.values() if task.state == TaskNodeState.FAILED),
            "cancelled": sum(1 for task in self.tasks.values() if task.state == TaskNodeState.CANCELLED),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "swarm_id": self.swarm_id,
            "owner_id": self.owner_id,
            "goal": self.goal,
            # ``objective`` was the original public spelling.  Keep it as an
            # additive compatibility alias while new clients use ``goal``.
            "objective": self.goal,
            "mode": self.mode.value if isinstance(self.mode, SwarmMode) else self.mode,
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
            "progress": self.progress(),
            "estimated_speedup": self.estimated_speedup,
            "critical_path_seconds": self.critical_path_seconds,
            "max_concurrency": self.max_concurrency,
            "blackboard_context": dict(self.blackboard_context),
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "final_result": self.final_result,
            "quality_score": self.quality_score,
            "metrics": dict(self.metrics),
            "budget": self.budget.to_dict(),
            "replan_count": self.replan_count,
            "round": self.round,
            "terminal_reason": self.terminal_reason,
            "consensus": dict(self.consensus),
            "auto_replan": self.auto_replan,
            "requires_consensus": self.requires_consensus,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SwarmPlan:
        try:
            mode = SwarmMode(data.get("mode", SwarmMode.AUTO.value))
        except (TypeError, ValueError):
            mode = SwarmMode.AUTO
        raw_tasks = data.get("tasks", {})
        if not isinstance(raw_tasks, Mapping):
            raw_tasks = {}
        tasks = {str(k): SwarmTaskNode.from_dict(v) for k, v in raw_tasks.items() if isinstance(v, Mapping)}
        return cls(
            swarm_id=str(data.get("swarm_id", "")),
            owner_id=str(data.get("owner_id", "default")),
            goal=str(data.get("goal", "")),
            mode=mode,
            tasks=tasks,
            estimated_speedup=_as_float(data.get("estimated_speedup"), 1.0),
            critical_path_seconds=max(0.0, _as_float(data.get("critical_path_seconds"))),
            max_concurrency=max(1, _as_int(data.get("max_concurrency"), 8)),
            blackboard_context=_as_mapping(data.get("blackboard_context")),
            status=str(data.get("status", "planning")),
            created_at=str(data.get("created_at", "")),
            completed_at=data.get("completed_at"),
            final_result=data.get("final_result"),
            quality_score=_as_float(data.get("quality_score")),
            metrics=_as_mapping(data.get("metrics")),
            schema_version=_as_int(data.get("schema_version"), 1),
            revision=max(0, _as_int(data.get("revision"))),
            budget=SwarmBudget.from_dict(_as_mapping(data.get("budget"))),
            replan_count=max(0, _as_int(data.get("replan_count"))),
            round=max(0, _as_int(data.get("round"))),
            terminal_reason=data.get("terminal_reason"),
            consensus=_as_mapping(data.get("consensus")),
            auto_replan=bool(data.get("auto_replan", False)),
            requires_consensus=bool(data.get("requires_consensus", False)),
        )


@dataclass
class SwarmEvent:
    event_id: str = field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:8]}")
    swarm_id: str = ""
    event_type: str = ""
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    task_id: str | None = None
    worker: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0
    causation_id: str | None = None
    idempotency_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SwarmEvent:
        return cls(
            event_id=str(data.get("event_id", "")),
            swarm_id=str(data.get("swarm_id", "")),
            event_type=str(data.get("event_type", "")),
            timestamp=str(data.get("timestamp", "")),
            task_id=data.get("task_id"),
            worker=data.get("worker"),
            details=_as_mapping(data.get("details")),
            sequence=max(0, _as_int(data.get("sequence"))),
            causation_id=data.get("causation_id"),
            idempotency_key=data.get("idempotency_key"),
        )


@dataclass
class SwarmTaskLease:
    task_id: str
    lease_id: str
    owner: str
    expires_at: float
    task_attempt: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
