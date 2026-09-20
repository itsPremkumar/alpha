"""Data models for Never-Ending Autonomous Operation and Perpetual Daemon."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class DaemonState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    STAGNATION_RECOVERY = "stagnation_recovery"
    CONSOLIDATING_MEMORY = "consolidating_memory"
    DISCOVERING_TASKS = "discovering_tasks"


@dataclass
class PerpetualGoal:
    """Long-term continuous objective maintained by the perpetual daemon."""

    goal_id: str = field(default_factory=lambda: f"goal_{uuid.uuid4().hex[:8]}")
    title: str = "Autonomous Continuous Codebase & Architecture Evolution"
    description: str = "Continuously discover, optimize, verify, and maintain system excellence."
    priority: int = 1
    status: str = "active"  # active, in_progress, completed, archived
    progress_percent: float = 0.0
    subtasks: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    updated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerpetualGoal:
        return cls(**data)


@dataclass
class AutonomousTask:
    """Proactively discovered task queued for execution without human prompting."""

    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    goal_id: str = ""
    title: str = ""
    source: str = "test_gap_discovery"  # test_gap_discovery, security_audit, code_smell_refactor, epistemic_falsification, performance_profiling, doc_drift
    status: str = "discovered"  # discovered, scheduled, executing, completed, failed
    priority: int = 1
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutonomousTask:
        return cls(**data)


@dataclass
class StagnationIncident:
    """Record of a detected execution loop or deadlock and its recovery intervention."""

    incident_id: str = field(default_factory=lambda: f"stag_{uuid.uuid4().hex[:8]}")
    detected_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    signature: str = ""
    repeated_action: str = ""
    consecutive_failures: int = 0
    recovery_action_taken: str = ""  # backtrack_checkpoint, inject_negative_constraint, escalate_model_tier, reset_working_memory
    resolved: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryConsolidationReport:
    """Summary of background episodic memory consolidation into semantic facts."""

    report_id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    traces_analyzed: int = 0
    facts_extracted: int = 0
    skills_indexed: int = 0
    pruned_tokens: int = 0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PerpetualDaemonTelemetry:
    """Real-time telemetry and health vitals for the Perpetual Autonomous Daemon."""

    state: DaemonState = DaemonState.RUNNING
    heartbeat_count: int = 0
    uptime_seconds: float = 0.0
    active_goals_count: int = 0
    total_tasks_discovered: int = 0
    tasks_completed_count: int = 0
    stagnation_incidents_recovered: int = 0
    consolidation_cycles_completed: int = 0
    last_heartbeat_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d
