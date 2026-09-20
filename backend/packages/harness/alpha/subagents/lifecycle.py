"""Sub-Agent Lifecycle Engine: Asynchronous Non-Blocking Workers, Leases & Heartbeats.

Manages first-class task-scoped subagents with:
- Asynchronous non-blocking lifecycle states (CREATED -> RUNNING -> COMPLETED)
- Active heartbeats, progress emission, and time-bounded task leases
- Automatic stall and hang detection (expired lease / inactive loop)
- Recursive parent-child tree tracking with strict depth & child count limits
- Propagation of pause, resume, and cancellation signals down child subtrees
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GLOBAL_LIFECYCLE_MANAGER: SubagentLifecycleManager | None = None


class SubagentStatusEnum(StrEnum):
    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    STALLED = "stalled"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ARCHIVED = "archived"


@dataclass
class SubagentContract:
    objective: str
    role: str = "specialist"
    instructions: str = ""
    model_override: str | None = None
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    workspace_mode: str = "isolated"  # isolated, shared, hybrid
    workspace_path: str | None = None
    permissions: list[str] = field(default_factory=list)
    timeout_seconds: int = 600
    lease_duration_seconds: int = 60
    token_budget: int = 50000
    context_mode: str = "selective"  # none, minimal, selective, full
    injected_context: dict[str, Any] = field(default_factory=dict)
    survival_policy: str = "transfer_on_parent_failure"  # cancel_on_parent_failure, continue_on_parent_failure, transfer_on_parent_failure

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubagentContract:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SubagentHeartbeat:
    timestamp: str
    current_action: str
    progress_percent: float = 0.0  # 0.0 - 100.0
    last_tool: str | None = None
    tokens_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SubagentLease:
    lease_id: str
    subagent_id: str
    expires_at: float  # epoch timestamp in seconds
    renew_count: int = 0

    def is_valid(self) -> bool:
        return time.time() < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SubagentDeliverable:
    status: str
    summary: str
    findings: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence_score: float = 1.0
    errors: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SubagentRecord:
    subagent_id: str
    parent_agent_id: str
    parent_task_id: str | None
    depth: int
    contract: SubagentContract
    status: SubagentStatusEnum
    created_at: str
    lease: SubagentLease
    started_at: str | None = None
    completed_at: str | None = None
    last_heartbeat: SubagentHeartbeat | None = None
    result: SubagentDeliverable | None = None
    stall_count: int = 0
    children_ids: list[str] = field(default_factory=list)
    is_orphaned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "subagent_id": self.subagent_id,
            "parent_agent_id": self.parent_agent_id,
            "parent_task_id": self.parent_task_id,
            "depth": self.depth,
            "contract": self.contract.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "last_heartbeat": self.last_heartbeat.to_dict() if self.last_heartbeat else None,
            "lease": self.lease.to_dict(),
            "result": self.result.to_dict() if self.result else None,
            "stall_count": self.stall_count,
            "children_ids": list(self.children_ids),
            "is_orphaned": self.is_orphaned,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubagentRecord:
        contract = SubagentContract.from_dict(data["contract"])
        lease_data = data["lease"]
        lease = SubagentLease(
            lease_id=lease_data["lease_id"],
            subagent_id=lease_data["subagent_id"],
            expires_at=lease_data["expires_at"],
            renew_count=lease_data.get("renew_count", 0),
        )
        hb_data = data.get("last_heartbeat")
        hb = SubagentHeartbeat(**hb_data) if hb_data else None
        res_data = data.get("result")
        res = SubagentDeliverable(**res_data) if res_data else None

        return cls(
            subagent_id=data["subagent_id"],
            parent_agent_id=data["parent_agent_id"],
            parent_task_id=data.get("parent_task_id"),
            depth=data.get("depth", 1),
            contract=contract,
            status=SubagentStatusEnum(data["status"]),
            created_at=data["created_at"],
            lease=lease,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            last_heartbeat=hb,
            result=res,
            stall_count=data.get("stall_count", 0),
            children_ids=data.get("children_ids", []),
            is_orphaned=data.get("is_orphaned", False),
        )


def get_subagent_lifecycle_manager(storage_dir: Path | str | None = None) -> SubagentLifecycleManager:
    """Returns singleton instance of SubagentLifecycleManager."""
    global _GLOBAL_LIFECYCLE_MANAGER
    if _GLOBAL_LIFECYCLE_MANAGER is None:
        _GLOBAL_LIFECYCLE_MANAGER = SubagentLifecycleManager(storage_dir=storage_dir)
    return _GLOBAL_LIFECYCLE_MANAGER


class SubagentLifecycleManager:
    """Master controller managing asynchronous subagent lifecycle, heartbeats, and leases."""

    DEFAULT_MAX_DEPTH = 3
    DEFAULT_MAX_CHILDREN_PER_PARENT = 10

    def __init__(self, storage_dir: Path | str | None = None):
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            base = os.environ.get("AGENT_WORKSPACE_HOME", "~/.agent-workspace")
            self.storage_dir = Path(os.path.expanduser(base)) / "subagents"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, SubagentRecord] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self.storage_dir.exists():
            return
        for f in self.storage_dir.glob("*.json"):
            try:
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                    rec = SubagentRecord.from_dict(data)
                    self._records[rec.subagent_id] = rec
            except Exception as exc:
                logger.warning(f"Failed to load subagent checkpoint {f}: {exc}")

    def spawn_subagent(
        self,
        parent_agent_id: str,
        contract: SubagentContract,
        parent_task_id: str | None = None,
        depth: int = 1,
    ) -> SubagentRecord:
        """Asynchronously provisions a new task-scoped subagent with an active lease."""
        # 1. Enforce max depth
        if depth > self.DEFAULT_MAX_DEPTH:
            raise ValueError(f"Spawn rejected: maximum subagent recursion depth ({self.DEFAULT_MAX_DEPTH}) exceeded.")

        # 2. Enforce max active children per parent
        active_children = sum(1 for r in self._records.values() if r.parent_agent_id == parent_agent_id and r.status in (SubagentStatusEnum.RUNNING, SubagentStatusEnum.READY, SubagentStatusEnum.INITIALIZING))
        if active_children >= self.DEFAULT_MAX_CHILDREN_PER_PARENT:
            raise ValueError(f"Spawn rejected: parent agent '{parent_agent_id}' already has {active_children} active subagents (limit: {self.DEFAULT_MAX_CHILDREN_PER_PARENT}).")

        subagent_id = f"sub-{uuid.uuid4().hex[:8]}"
        now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        lease_duration = contract.lease_duration_seconds or 60

        lease = SubagentLease(
            lease_id=f"lease-{uuid.uuid4().hex[:6]}",
            subagent_id=subagent_id,
            expires_at=time.time() + lease_duration,
            renew_count=0,
        )

        record = SubagentRecord(
            subagent_id=subagent_id,
            parent_agent_id=parent_agent_id,
            parent_task_id=parent_task_id,
            depth=depth,
            contract=contract,
            status=SubagentStatusEnum.READY,
            created_at=now_ts,
            lease=lease,
        )

        self._records[subagent_id] = record

        # Register child to parent record if parent is also a subagent
        if parent_agent_id in self._records:
            self._records[parent_agent_id].children_ids.append(subagent_id)
            self.checkpoint_to_disk(parent_agent_id)

        self.checkpoint_to_disk(subagent_id)
        logger.info(f"Spawned subagent '{subagent_id}' for parent '{parent_agent_id}' (depth={depth})")
        return record

    def start_subagent(self, subagent_id: str) -> bool:
        rec = self._records.get(subagent_id)
        if not rec or rec.status != SubagentStatusEnum.READY:
            return False
        rec.status = SubagentStatusEnum.RUNNING
        rec.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.renew_lease(subagent_id)
        self.checkpoint_to_disk(subagent_id)
        return True

    def record_heartbeat(
        self,
        subagent_id: str,
        current_action: str,
        progress_percent: float = 0.0,
        last_tool: str | None = None,
        tokens_used: int = 0,
    ) -> bool:
        """Records liveness heartbeat and extends the subagent lease."""
        rec = self._records.get(subagent_id)
        if not rec or rec.status not in (SubagentStatusEnum.RUNNING, SubagentStatusEnum.READY, SubagentStatusEnum.RECOVERING):
            return False

        now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec.last_heartbeat = SubagentHeartbeat(
            timestamp=now_ts,
            current_action=current_action,
            progress_percent=min(100.0, max(0.0, progress_percent)),
            last_tool=last_tool,
            tokens_used=tokens_used,
        )

        # Extend lease
        self.renew_lease(subagent_id)
        self.checkpoint_to_disk(subagent_id)
        return True

    def renew_lease(self, subagent_id: str) -> bool:
        rec = self._records.get(subagent_id)
        if not rec:
            return False
        duration = rec.contract.lease_duration_seconds or 60
        rec.lease.expires_at = time.time() + duration
        rec.lease.renew_count += 1
        return True

    def check_liveness_and_stalls(self) -> dict[str, Any]:
        """Scans active subagents for expired leases and marks stalled workers."""
        stalled: list[str] = []
        running: list[str] = []

        for sid, rec in self._records.items():
            if rec.status in (SubagentStatusEnum.RUNNING, SubagentStatusEnum.READY):
                if not rec.lease.is_valid():
                    rec.status = SubagentStatusEnum.STALLED
                    rec.stall_count += 1
                    stalled.append(sid)
                    self.checkpoint_to_disk(sid)
                    logger.warning(f"Subagent '{sid}' lease expired; transitioned to STALLED (stall_count={rec.stall_count}).")
                else:
                    running.append(sid)

        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stalled_count": len(stalled),
            "stalled_ids": stalled,
            "running_count": len(running),
            "running_ids": running,
        }

    def complete_subagent(self, subagent_id: str, deliverable: SubagentDeliverable) -> bool:
        rec = self._records.get(subagent_id)
        if not rec or rec.status in (SubagentStatusEnum.COMPLETED, SubagentStatusEnum.FAILED, SubagentStatusEnum.CANCELLED):
            return False

        rec.status = SubagentStatusEnum.COMPLETED
        rec.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec.result = deliverable
        self.checkpoint_to_disk(subagent_id)
        return True

    def fail_subagent(self, subagent_id: str, error_message: str) -> bool:
        rec = self._records.get(subagent_id)
        if not rec or rec.status in (SubagentStatusEnum.COMPLETED, SubagentStatusEnum.FAILED, SubagentStatusEnum.CANCELLED):
            return False

        rec.status = SubagentStatusEnum.FAILED
        rec.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec.result = SubagentDeliverable(
            status="failed",
            summary=f"Task failed: {error_message}",
            errors=[error_message],
            confidence_score=0.0,
        )
        self.checkpoint_to_disk(subagent_id)
        return True

    def cancel_subagent(self, subagent_id: str, reason: str = "") -> bool:
        """Cancels a subagent and propagates cancellation downward to all descendants."""
        rec = self._records.get(subagent_id)
        if not rec or rec.status in (SubagentStatusEnum.COMPLETED, SubagentStatusEnum.CANCELLED):
            return False

        rec.status = SubagentStatusEnum.CANCELLED
        rec.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec.result = SubagentDeliverable(
            status="cancelled",
            summary=f"Cancelled: {reason or 'User/Parent requested cancellation'}",
            errors=[reason] if reason else [],
        )
        self.checkpoint_to_disk(subagent_id)

        # Propagate to children
        for cid in rec.children_ids:
            self.cancel_subagent(cid, reason=f"Parent '{subagent_id}' cancelled")

        return True

    def pause_subagent(self, subagent_id: str) -> bool:
        rec = self._records.get(subagent_id)
        if not rec or rec.status != SubagentStatusEnum.RUNNING:
            return False
        rec.status = SubagentStatusEnum.WAITING
        self.checkpoint_to_disk(subagent_id)
        for cid in rec.children_ids:
            self.pause_subagent(cid)
        return True

    def resume_subagent(self, subagent_id: str) -> bool:
        rec = self._records.get(subagent_id)
        if not rec or rec.status != SubagentStatusEnum.WAITING:
            return False
        rec.status = SubagentStatusEnum.RUNNING
        self.renew_lease(subagent_id)
        self.checkpoint_to_disk(subagent_id)
        for cid in rec.children_ids:
            self.resume_subagent(cid)
        return True

    def get_subagent(self, subagent_id: str) -> SubagentRecord | None:
        return self._records.get(subagent_id)

    def list_subagents(
        self,
        parent_id: str | None = None,
        status: SubagentStatusEnum | None = None,
        limit: int = 50,
    ) -> list[SubagentRecord]:
        recs = list(self._records.values())
        if parent_id:
            recs = [r for r in recs if r.parent_agent_id == parent_id]
        if status:
            recs = [r for r in recs if r.status == status]
        recs.sort(key=lambda r: r.created_at, reverse=True)
        return recs[:limit]

    def checkpoint_to_disk(self, subagent_id: str) -> None:
        rec = self._records.get(subagent_id)
        if not rec:
            return
        target = self.storage_dir / f"{subagent_id}.json"
        try:
            with open(target, "w", encoding="utf-8") as fp:
                json.dump(rec.to_dict(), fp, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to persist subagent record {subagent_id}: {exc}")
