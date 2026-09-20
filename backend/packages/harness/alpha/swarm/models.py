"""Data models and state contracts for Autonomous Agent Swarm subsystem."""

from __future__ import annotations

import time
import uuid
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
    def from_dict(cls, data: dict[str, Any]) -> SwarmDecision:
        return cls(
            should_swarm=bool(data.get("should_swarm", False)),
            mode=SwarmMode(data.get("mode", "auto")),
            reason=str(data.get("reason", "")),
            estimated_serial_seconds=float(data.get("estimated_serial_seconds", 0.0)),
            estimated_parallel_seconds=float(data.get("estimated_parallel_seconds", 0.0)),
            estimated_speedup=float(data.get("estimated_speedup", 1.0)),
            recommended_workers=int(data.get("recommended_workers", 1)),
            estimated_overhead_seconds=float(data.get("estimated_overhead_seconds", 0.0)),
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
            "evidence": list(self.evidence),
            "lease_expires_at": self.lease_expires_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "backup_worker_launched": self.backup_worker_launched,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SwarmTaskNode:
        state_val = data.get("state", TaskNodeState.PENDING.value)
        try:
            state = TaskNodeState(state_val)
        except ValueError:
            state = TaskNodeState.PENDING
        return cls(
            task_id=str(data.get("task_id", "")),
            objective=str(data.get("objective", "")),
            dependencies=list(data.get("dependencies", [])),
            assigned_worker=data.get("assigned_worker"),
            worker_type=str(data.get("worker_type", "ephemeral")),
            model_override=data.get("model_override"),
            worktree_path=data.get("worktree_path"),
            input_artifacts=list(data.get("input_artifacts", [])),
            output_artifacts=list(data.get("output_artifacts", [])),
            state=state,
            result_summary=data.get("result_summary"),
            evidence=list(data.get("evidence", [])),
            lease_expires_at=data.get("lease_expires_at"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 3)),
            backup_worker_launched=bool(data.get("backup_worker_launched", False)),
            error_message=data.get("error_message"),
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
    status: str = "planning"  # planning, running, paused, aggregating, verifying, completed, failed, cancelled
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    completed_at: str | None = None
    final_result: str | None = None
    quality_score: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "goal": self.goal,
            "mode": self.mode.value if isinstance(self.mode, SwarmMode) else self.mode,
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SwarmPlan:
        mode_val = data.get("mode", SwarmMode.AUTO.value)
        try:
            mode = SwarmMode(mode_val)
        except ValueError:
            mode = SwarmMode.AUTO

        raw_tasks = data.get("tasks", {})
        tasks = {k: SwarmTaskNode.from_dict(v) for k, v in raw_tasks.items()}

        return cls(
            swarm_id=str(data.get("swarm_id", "")),
            goal=str(data.get("goal", "")),
            mode=mode,
            tasks=tasks,
            estimated_speedup=float(data.get("estimated_speedup", 1.0)),
            critical_path_seconds=float(data.get("critical_path_seconds", 0.0)),
            max_concurrency=int(data.get("max_concurrency", 8)),
            blackboard_context=dict(data.get("blackboard_context", {})),
            status=str(data.get("status", "planning")),
            created_at=str(data.get("created_at", "")),
            completed_at=data.get("completed_at"),
            final_result=data.get("final_result"),
            quality_score=float(data.get("quality_score", 0.0)),
            metrics=dict(data.get("metrics", {})),
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SwarmEvent:
        return cls(
            event_id=str(data.get("event_id", "")),
            swarm_id=str(data.get("swarm_id", "")),
            event_type=str(data.get("event_type", "")),
            timestamp=str(data.get("timestamp", "")),
            task_id=data.get("task_id"),
            worker=data.get("worker"),
            details=dict(data.get("details", {})),
        )
