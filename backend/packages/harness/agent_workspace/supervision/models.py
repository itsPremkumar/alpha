"""Data models for the Deterministic Out-of-Band Supervisor & Watchdog."""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentHealthStatus(StrEnum):
    HEALTHY = "healthy"
    BUSY = "busy"
    IDLE = "idle"
    DEGRADED = "degraded"
    STALLED = "stalled"
    FAILED = "failed"
    RECOVERING = "recovering"


class AnomalyType(StrEnum):
    PROGRESS_FROZEN = "progress_frozen"
    TOOL_THRASHING = "tool_thrashing"
    CIRCULAR_LOOP = "circular_loop"
    HIGH_LATENCY = "high_latency"
    LEASE_EXPIRED = "lease_expired"


class AnomalySeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecoveryAction(StrEnum):
    RETRY = "retry"
    RESTART = "restart"
    HOT_REPLACE = "hot_replace"
    SUCCESSOR_HANDOFF = "successor_handoff"
    ESCALATE = "escalate"


class HeartbeatRecord(BaseModel):
    worker_id: str
    task_id: str | None = None
    timestamp: float = Field(default_factory=time.time)
    progress_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    current_action: str = ""
    lease_seconds: float = Field(default=60.0)
    artifacts_count: int = 0
    step_index: int = 0
    cpu_usage: float = 0.0
    memory_mb: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnomalyReport(BaseModel):
    anomaly_id: str = Field(default_factory=lambda: f"anom-{uuid.uuid4().hex[:8]}")
    worker_id: str
    anomaly_type: AnomalyType
    severity: AnomalySeverity = AnomalySeverity.MEDIUM
    description: str
    timestamp: float = Field(default_factory=time.time)
    suggested_action: RecoveryAction = RecoveryAction.RETRY
    details: dict[str, Any] = Field(default_factory=dict)
