"""MCP Tasks (July 2026 Specification) Protocol Adapter.

Extends Model Context Protocol with asynchronous, stateful, long-running
Task lifecycle operations (submit, poll, cancel, retrieve artifacts).
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MCPTaskState(StrEnum):
    PENDING = "pending"

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MCPTaskSpec(BaseModel):
    task_id: str = Field(default_factory=lambda: f"mcp-task-{uuid.uuid4().hex[:8]}")
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=300.0)
    idempotency_key: str | None = None
    created_at: float = Field(default_factory=time.time)


class MCPTaskStatus(BaseModel):
    task_id: str
    state: MCPTaskState = MCPTaskState.PENDING
    progress_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    result: Any = None
    error: str | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class MCPTaskManager:
    """Manages asynchronous lifecycle of long-running MCP Tasks."""

    def __init__(self):
        # task_id -> MCPTaskStatus
        self._tasks: dict[str, MCPTaskStatus] = {}
        # idempotency_key -> task_id
        self._idempotency_map: dict[str, str] = {}

    def submit_task(self, spec: MCPTaskSpec) -> MCPTaskStatus:
        """Submit a new asynchronous MCP Task or return existing idempotent task."""
        if spec.idempotency_key and spec.idempotency_key in self._idempotency_map:
            existing_id = self._idempotency_map[spec.idempotency_key]
            return self._tasks[existing_id]

        status = MCPTaskStatus(
            task_id=spec.task_id,
            state=MCPTaskState.RUNNING,
            progress_percent=10.0,
            created_at=time.time(),
            updated_at=time.time(),
        )
        self._tasks[spec.task_id] = status
        if spec.idempotency_key:
            self._idempotency_map[spec.idempotency_key] = spec.task_id

        return status

    def update_progress(
        self,
        task_id: str,
        progress: float,
        state: MCPTaskState = MCPTaskState.RUNNING,
        result: Any = None,
        error: str | None = None,
    ) -> MCPTaskStatus:
        status = self._tasks.get(task_id)
        if not status:
            raise KeyError(f"MCP Task '{task_id}' not found.")

        status.progress_percent = progress
        status.state = state
        if result is not None:
            status.result = result
        if error is not None:
            status.error = error
        status.updated_at = time.time()
        return status

    def cancel_task(self, task_id: str, reason: str = "User cancelled") -> bool:
        status = self._tasks.get(task_id)
        if not status or status.state in (MCPTaskState.COMPLETED, MCPTaskState.CANCELLED):
            return False

        status.state = MCPTaskState.CANCELLED
        status.error = reason
        status.updated_at = time.time()
        return True

    def get_status(self, task_id: str) -> MCPTaskStatus | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[MCPTaskStatus]:
        return list(self._tasks.values())
