"""Data models and enums for the Decoupled External Job Engine.

Decouples heavy OS tasks (builds, tests, data processing, benchmarks) from
the LLM token reasoning loop, providing durable job tracking, resource budgeting,
and cancellation tokens.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class JobPriority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class ResourceLimits(BaseModel):
    max_cpu_percent: float = Field(default=80.0, description="CPU threshold limit before throttling")
    max_memory_mb: int = Field(default=2048, description="Memory ceiling in Megabytes")
    timeout_seconds: float = Field(default=300.0, description="Hard timeout in seconds")


class JobSpec(BaseModel):
    job_id: str = Field(default_factory=lambda: f"job-{uuid.uuid4().hex[:8]}")
    owner_id: str | None = Field(default=None, description="Server-assigned authenticated owner")
    title: str = Field(default="Background Task", description="Human-readable job label")
    command: list[str] | str = Field(..., description="Executable command and args or shell string")
    working_dir: str | None = Field(default=None, description="Working directory path")
    env: dict[str, str] = Field(default_factory=dict, description="Custom environment variables")
    priority: JobPriority = Field(default=JobPriority.NORMAL, description="Scheduling priority")
    resources: ResourceLimits = Field(default_factory=ResourceLimits, description="Resource limits")
    tags: list[str] = Field(default_factory=list, description="Categorization tags (e.g. ['build', 'test'])")
    parent_agent_id: str | None = Field(default=None, description="Requesting agent ID")
    lease_seconds: float = Field(default=60.0, description="Worker heartbeat lease period")
    created_at: float = Field(default_factory=time.time)


class JobResult(BaseModel):
    job_id: str
    status: JobStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    execution_seconds: float = 0.0
    artifacts: list[str] = Field(default_factory=list, description="Paths to generated artifacts")
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
