"""Sub-Agent Lifecycle Engine: Asynchronous Non-Blocking Workers, Leases & Heartbeats.

Manages first-class task-scoped subagents with:
- Asynchronous non-blocking lifecycle states (CREATED -> RUNNING -> COMPLETED)
- Active heartbeats, progress emission, and time-bounded task leases
- Automatic stall and hang detection (expired lease / inactive loop)
- Recursive parent-child tree tracking with strict depth & child count limits
- Propagation of pause, resume, and cancellation signals down child subtrees
- Durable progress checkpoints, a bounded attempt ceiling, and a durable
  escalation to a human when that ceiling is spent (see
  :mod:`alpha.runtime.escalation` for the shared ledger)
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

#: Attempts a subagent gets before its failure is escalated to a human. A
#: crashed attempt counts: a worker that died mid-flight cannot be resumed by
#: the same process, so the next worker is a new attempt.
DEFAULT_MAX_ATTEMPTS = 3


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
    #: Attempt ceiling for this subagent. When it is reached the subagent stops
    #: being retried and the failure is escalated to a human with a durable
    #: record instead of looping forever.
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

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
    #: How many times this unit has actually been dispatched. Charged by
    #: :meth:`SubagentLifecycleManager.start_subagent`, so a resumed unit pays
    #: the next attempt and a crashed one has already paid its own.
    attempt: int = 0
    #: Durable progress checkpoint: the last known good resume point, so a
    #: replacement worker restarts from work that is already done instead of
    #: from the beginning.
    progress: dict[str, Any] = field(default_factory=dict)
    #: Typed reason code of the most recent failure (``""`` when never failed).
    failure_reason: str = ""
    #: Id of the escalation record opened for this subagent, if any.
    escalation_id: str | None = None

    @property
    def max_attempts(self) -> int:
        return max(1, int(self.contract.max_attempts or DEFAULT_MAX_ATTEMPTS))

    @property
    def attempts_exhausted(self) -> bool:
        return self.attempt >= self.max_attempts

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
            "attempt": self.attempt,
            "progress": dict(self.progress),
            "failure_reason": self.failure_reason,
            "escalation_id": self.escalation_id,
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
            attempt=int(data.get("attempt", 0) or 0),
            progress=dict(data.get("progress") or {}),
            failure_reason=str(data.get("failure_reason", "") or ""),
            escalation_id=data.get("escalation_id"),
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
        """Dispatch a READY subagent, charging one attempt against its ceiling.

        A READY unit whose ceiling is already spent is NOT dispatched: it is
        escalated to a human and the call returns False, so a restart sweep
        that requeues work can never restart a unit that has given up.
        """
        rec = self._records.get(subagent_id)
        if not rec or rec.status != SubagentStatusEnum.READY:
            return False
        if rec.attempts_exhausted:
            self._escalate(rec, detail=f"{subagent_id} reached its attempt ceiling ({rec.attempt}/{rec.max_attempts}) before dispatch")
            return False
        rec.attempt += 1
        rec.status = SubagentStatusEnum.RUNNING
        rec.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.renew_lease(subagent_id)
        self.checkpoint_to_disk(subagent_id)
        return True

    def record_progress(
        self,
        subagent_id: str,
        *,
        next_action: str = "",
        step_index: int | None = None,
        progress_percent: float | None = None,
        artifacts: list[str] | None = None,
        **extra: Any,
    ) -> bool:
        """Persist a resume point for this subagent.

        The checkpoint is written to the same record file as the rest of the
        lifecycle state, so a replacement worker (or a restarted process) can
        pick the work up from the last step that actually completed instead of
        repeating it.
        """
        rec = self._records.get(subagent_id)
        if not rec or rec.status in (SubagentStatusEnum.COMPLETED, SubagentStatusEnum.FAILED, SubagentStatusEnum.CANCELLED):
            return False
        checkpoint: dict[str, Any] = {
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "attempt": rec.attempt,
        }
        if next_action:
            checkpoint["next_action"] = next_action
        if step_index is not None:
            checkpoint["step_index"] = int(step_index)
        if progress_percent is not None:
            checkpoint["progress_percent"] = min(100.0, max(0.0, float(progress_percent)))
        if artifacts:
            checkpoint["artifacts"] = [str(a) for a in artifacts]
        checkpoint.update(extra)
        rec.progress.update(checkpoint)
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
        """Scans active subagents for expired leases and marks stalled workers.

        An expired lease is a typed ``worker_crash`` — the unit is still owed
        work, and a replacement worker may resume it — so each stall is also
        appended to the global handoff ledger instead of only being logged.
        """
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
                    try:
                        from alpha.runtime.escalation import DOMAIN_SUBAGENT, record_failure

                        record_failure(
                            DOMAIN_SUBAGENT,
                            sid,
                            f"subagent:{sid}",
                            attempt=rec.attempt,
                            max_attempts=rec.max_attempts,
                            reason="worker_crash",
                            detail=f"lease expired for subagent {sid}",
                        )
                    except Exception:  # noqa: BLE001 - bookkeeping must not mask the stall
                        logger.warning("Could not record lease-expiry failure for %s", sid, exc_info=True)
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

    def fail_subagent(self, subagent_id: str, error_message: str, *, reason: str | None = None, escalate: bool = True) -> bool:
        """Fail a subagent, classify the failure, and escalate at the ceiling.

        The status transition is unchanged (FAILED) so existing callers and
        recovery engines keep working; what is new is that the failure is
        classified against the SHARED taxonomy, appended to the global handoff
        ledger, and — once the attempt ceiling is spent — escalated to a human
        as a durable, queryable record instead of being retried forever.
        """
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
        if escalate:
            disposition = self._record_failure(rec, error_message, reason=reason)
            if disposition is not None and disposition.escalation is not None:
                rec.escalation_id = disposition.escalation.escalation_id
        self.checkpoint_to_disk(subagent_id)
        return True

    def _record_failure(self, rec: SubagentRecord, error_message: str, *, reason: str | None = None) -> Any:
        """Route one failed attempt through the shared failure taxonomy."""
        try:
            from alpha.runtime.escalation import DOMAIN_SUBAGENT, record_failure

            disposition = record_failure(
                DOMAIN_SUBAGENT,
                rec.subagent_id,
                f"subagent:{rec.subagent_id}",
                attempt=rec.attempt,
                max_attempts=rec.max_attempts,
                error=error_message,
                reason=reason,
                detail=f"{rec.parent_agent_id} -> {rec.subagent_id}: {error_message}"[:2000],
                details={"objective": rec.contract.objective[:500], "parent_agent_id": rec.parent_agent_id},
            )
        except Exception:  # noqa: BLE001 - bookkeeping must not mask the failure
            logger.warning("Could not record subagent failure for %s", rec.subagent_id, exc_info=True)
            return None
        rec.failure_reason = disposition.decision.reason
        return disposition

    def _escalate(self, rec: SubagentRecord, *, detail: str) -> str | None:
        """Open a durable escalation for a subagent that is out of attempts."""
        try:
            from alpha.runtime.escalation import DOMAIN_SUBAGENT, escalate_to_human

            record = escalate_to_human(
                DOMAIN_SUBAGENT,
                rec.subagent_id,
                f"subagent:{rec.subagent_id}",
                reason="attempts_exhausted",
                attempt=rec.attempt,
                max_attempts=rec.max_attempts,
                detail=detail,
                details={"objective": rec.contract.objective[:500], "parent_agent_id": rec.parent_agent_id},
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not escalate subagent %s", rec.subagent_id, exc_info=True)
            return None
        rec.escalation_id = record.escalation_id if record else None
        rec.failure_reason = "attempts_exhausted"
        return rec.escalation_id

    def recover_after_restart(self, *, now: float | None = None) -> dict[str, Any]:
        """Resume subagents abandoned by a dead process; escalate the spent ones.

        This is a RESTART-time sweep, not a heartbeat: it assumes the records it
        loads from disk belong to processes that are gone, so it must not run
        beside live workers for the same storage directory. A subagent whose
        lease is still valid is left alone (a live process owns it); one with a
        dead lease goes back to READY so a fresh worker can continue from its
        checkpoint, WITHOUT charging a new attempt — the crashed attempt was
        already charged when it was dispatched. If it has no attempts left, it
        is escalated to a human instead of being resumed forever.
        """
        stamp = float(now if now is not None else time.time())
        summary: dict[str, Any] = {"resumed": [], "escalated": [], "skipped": [], "owner_pid": os.getpid()}

        for sid, rec in list(self._records.items()):
            if rec.status in (SubagentStatusEnum.COMPLETED, SubagentStatusEnum.FAILED, SubagentStatusEnum.CANCELLED, SubagentStatusEnum.EXPIRED):
                continue
            if rec.status not in (SubagentStatusEnum.RUNNING, SubagentStatusEnum.READY, SubagentStatusEnum.STALLED, SubagentStatusEnum.RECOVERING):
                continue
            if rec.lease.expires_at > stamp:
                summary["skipped"].append(sid)
                continue
            if rec.attempts_exhausted:
                escalation_id = self._escalate(rec, detail=f"subagent {sid} has no attempts left after a crash (attempt {rec.attempt}/{rec.max_attempts})")
                rec.status = SubagentStatusEnum.FAILED
                rec.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))
                self.checkpoint_to_disk(sid)
                summary["escalated"].append({"subagent_id": sid, "escalation_id": escalation_id})
                continue
            previous_owner = f"subagent:{sid}:attempt-{rec.attempt}"
            rec.status = SubagentStatusEnum.READY
            rec.started_at = None
            rec.is_orphaned = False
            self.renew_lease(sid)
            self.checkpoint_to_disk(sid)
            try:
                from alpha.runtime.escalation import DOMAIN_SUBAGENT, record_resume

                record_resume(
                    DOMAIN_SUBAGENT,
                    sid,
                    from_ref=previous_owner,
                    to_ref=f"subagent:{sid}:attempt-{rec.attempt + 1}",
                    reason="worker_crash",
                    attempt=rec.attempt,
                    max_attempts=rec.max_attempts,
                    checkpoint=dict(rec.progress),
                    details={"parent_agent_id": rec.parent_agent_id},
                )
            except Exception:  # noqa: BLE001
                logger.warning("Could not record resume for subagent %s", sid, exc_info=True)
            summary["resumed"].append(sid)
        return summary

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
