"""Master Swarm Coordinator: Manages swarm lifecycle, state, and persistence."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.cnp_auction import LeaderCandidate, LeaderElection, elect_leader
from alpha.swarm.communication import SwarmMessage, SwarmMessageBus
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.estimator import SwarmBenefitEstimator
from alpha.swarm.models import (
    SwarmBudget,
    SwarmDecision,
    SwarmEvent,
    SwarmMode,
    SwarmPlan,
    SwarmTaskLease,
    SwarmTaskNode,
    TaskNodeState,
    is_terminal_swarm_status,
)
from alpha.swarm.scheduler import SwarmPlanValidationError, SwarmScheduler

logger = logging.getLogger(__name__)

_GLOBAL_COORDINATOR: SwarmCoordinator | None = None
_GLOBAL_COORDINATOR_LOCK = threading.Lock()


def _admission_fingerprint(
    *,
    goal: str,
    mode: SwarmMode,
    items: list[str],
    max_concurrency: int,
    budget: SwarmBudget,
    auto_replan: bool,
    requires_consensus: bool,
) -> str:
    """Build a deterministic, non-secret request fingerprint for idempotency."""

    return json.dumps(
        {
            "goal": goal,
            "mode": mode.value if isinstance(mode, SwarmMode) else str(mode),
            "items": items,
            "max_concurrency": int(max_concurrency),
            "budget": {
                "max_tokens": budget.max_tokens,
                "max_tool_calls": budget.max_tool_calls,
                "max_wall_seconds": budget.max_wall_seconds,
                "max_tasks": budget.max_tasks,
                "max_replans": budget.max_replans,
                "max_consecutive_failures": budget.max_consecutive_failures,
            },
            "auto_replan": bool(auto_replan),
            "requires_consensus": bool(requires_consensus),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def get_swarm_coordinator() -> SwarmCoordinator:
    """Return the process-local singleton without racing first-use callers."""
    global _GLOBAL_COORDINATOR
    if _GLOBAL_COORDINATOR is None:
        with _GLOBAL_COORDINATOR_LOCK:
            if _GLOBAL_COORDINATOR is None:
                _GLOBAL_COORDINATOR = SwarmCoordinator()
    return _GLOBAL_COORDINATOR


class SwarmCoordinator:
    """Coordinates lifecycle, state, scheduling, and aggregation of autonomous agent swarms."""

    def __init__(self, storage_dir: Path | str | None = None):
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            base = os.environ.get("AGENT_WORKSPACE_HOME", "~/.agent-workspace")
            self.storage_dir = Path(os.path.expanduser(base)) / "swarms"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # A coordinator is process-local, but a single Gateway can handle
        # HTTP, tool, and background-runner mutations concurrently.  The lock
        # protects the in-memory plan and the event sequence; atomic file
        # replacement protects readers from observing half-written JSON.
        self._lock = threading.RLock()
        self._swarms: dict[str, SwarmPlan] = {}
        self._events: dict[str, list[SwarmEvent]] = {}
        self._message_buses: dict[str, SwarmMessageBus] = {}
        self._admission_keys: dict[tuple[str, str], str] = {}
        self._load_persisted_swarms()

    @property
    def state_lock(self) -> threading.RLock:
        """Expose the short-lived state lock to the async runner.

        Worker model calls run outside this lock.  Only scheduler/state and
        checkpoint mutations are serialized here.
        """

        return self._lock

    def _event_path(self, swarm_id: str) -> Path:
        return self.storage_dir / f"{swarm_id}.events.jsonl"

    def _load_event_journal(self, swarm_id: str) -> list[SwarmEvent]:
        path = self._event_path(swarm_id)
        if not path.exists():
            return []
        events: list[SwarmEvent] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                        if isinstance(raw, Mapping):
                            events.append(SwarmEvent.from_dict(raw))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        logger.warning("Skipping corrupt swarm event in %s", path)
        except OSError as exc:
            logger.warning("Failed to load swarm event journal %s: %s", path, exc)
        for index, event in enumerate(events, 1):
            if event.sequence <= 0:
                event.sequence = index
        return events

    def _write_event(self, event: SwarmEvent) -> None:
        path = self._event_path(event.swarm_id)
        try:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, TypeError, ValueError) as exc:
            # The in-memory event remains available, but persistence failure is
            # logged rather than silently pretending the audit trail is durable.
            logger.warning("Failed to persist swarm event %s: %s", event.event_id, exc)

    def _atomic_write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_persisted_swarms(self) -> None:
        if not self.storage_dir.exists():
            return
        for file in self.storage_dir.glob("*.json"):
            try:
                with file.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if not isinstance(data, Mapping):
                    raise ValueError("checkpoint root must be an object")
                plan = SwarmPlan.from_dict(data)
                self._swarms[plan.swarm_id] = plan
                self._events[plan.swarm_id] = self._load_event_journal(plan.swarm_id)
                self._message_buses[plan.swarm_id] = SwarmMessageBus.from_dict(
                    plan.blackboard_context.get("messages_state"),
                    swarm_id=plan.swarm_id,
                )
                admission_key = plan.metrics.get("admission_idempotency_key")
                if admission_key:
                    self._admission_keys[(plan.owner_id, str(admission_key))] = plan.swarm_id
            except Exception as exc:
                logger.warning("Failed to load swarm checkpoint %s: %s", file, exc)

        # A process restart cannot safely assume that an old in-memory worker
        # still owns a lease.  Park recoverable plans for an explicit resume;
        # the next runner will claim pending work using fresh lease ids.
        for plan in self._swarms.values():
            if plan.status not in {"running", "aggregating", "verifying"}:
                continue
            for task in plan.tasks.values():
                if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                    task.state = TaskNodeState.PENDING
                    task.lease_id = None
                    task.lease_owner = None
                    task.lease_expires_at = None
                    task.error_message = "recovered after process restart; awaiting a fresh lease"
            plan.status = "paused"
            plan.terminal_reason = "process_restart_requires_resume"
            self.append_event(
                plan.swarm_id,
                "SWARM_RECOVERED_AFTER_RESTART",
                details={"reason": plan.terminal_reason},
            )
            self.checkpoint(plan.swarm_id)

    def evaluate_intent(self, goal: str, items: Sequence[str] | None = None) -> SwarmDecision:
        """Evaluates whether a goal warrants a swarm."""
        return SwarmBenefitEstimator.estimate(goal, items=items)

    def create_swarm(
        self,
        goal: str,
        mode: SwarmMode = SwarmMode.AUTO,
        items: Sequence[str] | None = None,
        max_concurrency: int = 8,
        *,
        owner_id: str = "default",
        budget: SwarmBudget | Mapping[str, Any] | None = None,
        auto_replan: bool = False,
        requires_consensus: bool = False,
        idempotency_key: str | None = None,
    ) -> SwarmPlan:
        """Construct, validate, and register a new bounded swarm plan."""

        goal = str(goal or "").strip()
        if not goal:
            raise ValueError("goal must be non-empty")
        if len(goal) > 32_000:
            raise ValueError("goal is limited to 32000 characters")
        normalized_items = [str(item).strip() for item in (items or ()) if str(item).strip()]
        if len(normalized_items) > 512:
            raise ValueError("items is limited to 512 entries")
        if any(len(item) > 8_000 for item in normalized_items):
            raise ValueError("each swarm item is limited to 8000 characters")
        normalized_owner = str(owner_id or "default").strip() or "default"
        if len(normalized_owner) > 256:
            raise ValueError("owner_id is limited to 256 characters")
        normalized_key = str(idempotency_key).strip() if idempotency_key else ""
        if normalized_key and len(normalized_key) > 256:
            raise ValueError("idempotency_key is too long")
        effective_budget = budget if isinstance(budget, SwarmBudget) else SwarmBudget.from_dict(budget)
        effective_concurrency = min(max(1, int(max_concurrency)), 64)
        fingerprint = _admission_fingerprint(
            goal=goal,
            mode=mode,
            items=normalized_items,
            max_concurrency=effective_concurrency,
            budget=effective_budget,
            auto_replan=bool(auto_replan),
            requires_consensus=bool(requires_consensus),
        )
        if normalized_key:
            existing_id = self._admission_keys.get((normalized_owner, normalized_key))
            if existing_id:
                existing = self._swarms.get(existing_id)
                if existing is not None:
                    existing_fingerprint = existing.metrics.get("admission_fingerprint")
                    if existing_fingerprint and existing_fingerprint != fingerprint:
                        raise ValueError("idempotency_key was already used with a different swarm request")
                    return existing
        plan = SwarmTaskDecomposer.decompose(
            goal=goal,
            mode=mode,
            items=normalized_items or None,
            max_concurrency=effective_concurrency,
        )
        plan.owner_id = normalized_owner
        plan.budget = effective_budget
        if normalized_key:
            plan.metrics["admission_idempotency_key"] = normalized_key
            plan.metrics["admission_fingerprint"] = fingerprint
        plan.budget.start()
        plan.auto_replan = bool(auto_replan)
        plan.requires_consensus = bool(requires_consensus)
        leader_candidates = [
            LeaderCandidate(
                agent_id=task.assigned_worker,
                capabilities=frozenset(task.capability_tags or [task.worker_type]),
                current_load=0.5,
                reputation=0.5,
                evidence_count=0,
            )
            for task in plan.tasks.values()
            if task.assigned_worker
        ]
        if leader_candidates:
            plan.metrics["leader_election"] = elect_leader(leader_candidates).to_dict()
        else:
            plan.metrics["leader_election"] = LeaderElection(
                leader=None,
                score=0.0,
                method="capability-load-reputation-v1",
                reason="no permanently assigned leader candidate",
            ).to_dict()
        plan.blackboard_context.setdefault("schema_version", 2)
        plan.blackboard_context.setdefault("messages_state", SwarmMessageBus(plan.swarm_id).to_dict())
        if not effective_budget.can_add_tasks(len(plan.tasks), 0):
            raise SwarmPlanValidationError("decomposed task count exceeds max_tasks budget")
        SwarmScheduler(plan).validate_graph()
        with self._lock:
            if normalized_key:
                existing_id = self._admission_keys.get((normalized_owner, normalized_key))
                if existing_id and existing_id in self._swarms:
                    existing = self._swarms[existing_id]
                    existing_fingerprint = existing.metrics.get("admission_fingerprint")
                    if existing_fingerprint and existing_fingerprint != fingerprint:
                        raise ValueError("idempotency_key was already used with a different swarm request")
                    return existing
            plan.status = "running"
            self._swarms[plan.swarm_id] = plan
            self._events[plan.swarm_id] = []
            self._message_buses[plan.swarm_id] = SwarmMessageBus.from_dict(plan.blackboard_context.get("messages_state"), swarm_id=plan.swarm_id)
            if normalized_key:
                self._admission_keys[(normalized_owner, normalized_key)] = plan.swarm_id
            self.append_event(
                plan.swarm_id,
                "SWARM_CREATED",
                details={
                    "goal": goal,
                    "mode": plan.mode.value,
                    "tasks_count": len(plan.tasks),
                    "estimated_speedup": plan.estimated_speedup,
                    "critical_path_seconds": plan.critical_path_seconds,
                    "schema_version": plan.schema_version,
                    "owner_id": plan.owner_id,
                },
            )
            self.checkpoint(plan.swarm_id)
        return plan

    def get_swarm(self, swarm_id: str, *, owner_id: str | None = None) -> SwarmPlan | None:
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if plan is None or owner_id is not None and plan.owner_id != owner_id:
                return None
            return plan

    def list_swarms(self, limit: int = 20, *, owner_id: str | None = None) -> list[SwarmPlan]:
        with self._lock:
            plans = [plan for plan in self._swarms.values() if owner_id is None or plan.owner_id == owner_id]
            plans.sort(key=lambda p: p.created_at, reverse=True)
            return plans[: max(1, int(limit))]

    def pause_swarm(self, swarm_id: str) -> bool:
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or is_terminal_swarm_status(plan.status):
                return False
            if plan.status == "paused":
                return True
            plan.status = "paused"
            plan.terminal_reason = None
            self.append_event(swarm_id, "SWARM_PAUSED")
            self.checkpoint(swarm_id)
            return True

    def resume_swarm(self, swarm_id: str) -> bool:
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or plan.status != "paused":
                return False
            plan.status = "running"
            plan.terminal_reason = None
            self.append_event(swarm_id, "SWARM_RESUMED", details={"reason": "fresh scheduler claims will fence old work"})
            self.checkpoint(swarm_id)
            return True

    def cancel_swarm(self, swarm_id: str, reason: str = "") -> bool:
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan:
                return False
            if plan.status == "cancelled":
                return True
            if is_terminal_swarm_status(plan.status):
                return False
            plan.status = "cancelled"
            plan.terminal_reason = str(reason or "operator_cancelled")
            for task in plan.tasks.values():
                if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED, TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                    task.state = TaskNodeState.CANCELLED
                    task.completed_at = time.time()
                    task.lease_id = None
                    task.lease_owner = None
                    task.lease_expires_at = None
            self.append_event(swarm_id, "SWARM_CANCELLED", details={"reason": str(reason or "")})
            self.checkpoint(swarm_id)
            return True

    def step(self, swarm_id: str) -> dict[str, Any]:
        """Advance one durable scheduler tick (used by tools and integrations)."""
        from alpha.swarm.watchdog import SwarmWatchdog

        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or plan.status != "running":
                return {"status": plan.status if plan else "not_found", "dispatched": []}
            budget = plan.budget.check()
            if budget.get("exhausted"):
                plan.status = "budget_exhausted"
                plan.terminal_reason = plan.budget.exhausted_reason
                for task in plan.tasks.values():
                    if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED, TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                        task.state = TaskNodeState.CANCELLED
                        task.error_message = f"budget_exhausted: {plan.budget.exhausted_reason}"
                        task.completed_at = time.time()
                self.append_event(swarm_id, "SWARM_BUDGET_EXHAUSTED", details=budget)
                self.checkpoint(swarm_id)
                return {"status": plan.status, "dispatched": [], "budget": budget}

            scheduler = SwarmScheduler(plan)
            try:
                scheduler.validate_graph()
            except SwarmPlanValidationError as exc:
                plan.status = "failed"
                plan.terminal_reason = f"invalid_swarm_plan: {exc}"
                plan.final_result = f"Swarm plan validation failed: {exc}"
                plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self.append_event(swarm_id, "SWARM_PLAN_INVALID", details={"error": str(exc)})
                self.checkpoint(swarm_id)
                return {"status": plan.status, "dispatched": [], "error": str(exc)}
            watchdog_report = SwarmWatchdog.check_and_reconcile(plan)
            for tid in watchdog_report.get("speculative_backups_spawned", []):
                self.append_event(swarm_id, "SPECULATIVE_BACKUP_LAUNCHED", task_id=tid)
            for tid in watchdog_report.get("retried_tasks", []):
                self.append_event(swarm_id, "TASK_RETRY_SCHEDULED", task_id=tid, details={"reason": "lease_expired"})
            for tid in watchdog_report.get("failed_tasks", []):
                self.append_event(swarm_id, "TASK_FAILED", task_id=tid, details={"reason": "lease_expired_attempts_exhausted"})
            for _tid in [*watchdog_report.get("retried_tasks", []), *watchdog_report.get("failed_tasks", [])]:
                plan.budget.record_task_failure()
            if plan.budget.exhausted:
                plan.status = "budget_exhausted"
                plan.terminal_reason = plan.budget.exhausted_reason
                for task in plan.tasks.values():
                    if task.state in (TaskNodeState.PENDING, TaskNodeState.QUEUED, TaskNodeState.RUNNING, TaskNodeState.STRAGGLING):
                        task.state = TaskNodeState.CANCELLED
                        task.error_message = f"budget_exhausted: {plan.terminal_reason}"
                        task.completed_at = time.time()
                self.append_event(swarm_id, "SWARM_BUDGET_EXHAUSTED", details=plan.budget.to_dict())
                self.checkpoint(swarm_id)
                return {"status": plan.status, "dispatched": [], "budget": plan.budget.to_dict()}

            replanned: list[str] = []
            if plan.auto_replan:
                for task_node in list(plan.tasks.values()):
                    if task_node.state != TaskNodeState.FAILED:
                        continue
                    repair_task_id = self.auto_replan_failed_task(swarm_id, task_node.task_id)
                    if repair_task_id:
                        replanned.append(repair_task_id)
            if replanned:
                return {
                    "status": plan.status,
                    "dispatched": [],
                    "replanned": replanned,
                    "replans": plan.replan_count,
                }

            ready_now = scheduler.get_ready_tasks()
            running_now = sum(1 for task in plan.tasks.values() if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING))
            if not ready_now and running_now == 0 and not scheduler.is_swarm_finished():
                stranded = scheduler.fail_unrunnable_tasks()
                if stranded:
                    self.append_event(
                        swarm_id,
                        "SWARM_STRANDED_TASKS_FAILED",
                        details={"task_ids": [task.task_id for task in stranded]},
                    )

            if scheduler.is_swarm_finished():
                plan.status = "aggregating"
                self.append_event(swarm_id, "SWARM_AGGREGATING")
                try:
                    agg_result = SwarmAggregator.aggregate(plan)
                except Exception as exc:
                    logger.exception("Failed to aggregate swarm %s", swarm_id)
                    plan.status = "failed"
                    plan.terminal_reason = f"aggregation_failed: {exc}"
                    plan.final_result = f"Swarm aggregation failed: {exc}"
                    plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self.append_event(swarm_id, "SWARM_AGGREGATION_FAILED", details={"error": str(exc)})
                    self.checkpoint(swarm_id)
                    return {"status": plan.status, "aggregated": False, "error": str(exc)}
                plan.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                final_event = "SWARM_COMPLETED" if plan.status == "completed" else "SWARM_FAILED" if plan.status == "failed" else "SWARM_PARTIAL_SUCCESS"
                self.append_event(swarm_id, final_event, details=agg_result)
                self.checkpoint(swarm_id)
                return {"status": plan.status, "aggregated": True, "result": agg_result}

            dispatched = scheduler.dispatch_ready_tasks(lease_owner=f"step:{swarm_id}")
            for task in dispatched:
                self.append_event(
                    swarm_id,
                    "TASK_DISPATCHED",
                    task_id=task.task_id,
                    worker=task.assigned_worker or "ephemeral-worker",
                    details={"objective": task.objective, "lease_id": task.lease_id, "attempt": task.attempts},
                )
            self.checkpoint(swarm_id)
            return {
                "status": plan.status,
                "dispatched": [task.task_id for task in dispatched],
                "running_count": sum(1 for task in plan.tasks.values() if task.state in (TaskNodeState.RUNNING, TaskNodeState.STRAGGLING)),
                "budget": plan.budget.to_dict(),
            }

    def append_event(
        self,
        swarm_id: str,
        event_type: str,
        task_id: str | None = None,
        worker: str | None = None,
        details: dict[str, Any] | None = None,
        *,
        causation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SwarmEvent:
        """Append an in-memory and durable ordered event.

        Event writes are separate from plan checkpoints.  Callers can batch a
        checkpoint after a state transition without losing the audit record.
        """

        with self._lock:
            events = self._events.setdefault(swarm_id, [])
            if idempotency_key:
                for existing in reversed(events):
                    if existing.idempotency_key == idempotency_key:
                        return existing
            sequence = (events[-1].sequence + 1) if events else 1
            evt = SwarmEvent(
                swarm_id=swarm_id,
                event_type=event_type,
                task_id=task_id,
                worker=worker,
                details=dict(details or {}),
                sequence=sequence,
                causation_id=causation_id,
                idempotency_key=idempotency_key,
            )
            events.append(evt)
            self._write_event(evt)
            plan = self._swarms.get(swarm_id)
            if plan is not None:
                plan.revision += 1
            return evt

    def get_events(self, swarm_id: str, limit: int = 50) -> list[SwarmEvent]:
        with self._lock:
            events = self._events.get(swarm_id, [])
            if limit <= 0:
                return []
            return list(events[-int(limit) :])

    def checkpoint_if_revision(self, swarm_id: str, expected_revision: int) -> bool:
        """Compare-and-checkpoint a plan revision inside the process lock."""

        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or int(plan.revision) != int(expected_revision):
                return False
            self.checkpoint(swarm_id)
            return True

    def checkpoint(self, swarm_id: str) -> None:
        """Atomically persist a plan snapshot without exposing a partial JSON file."""

        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan:
                return
            plan.revision += 1
            target = self.storage_dir / f"{swarm_id}.json"
            try:
                self._atomic_write_json(target, plan.to_dict())
            except (OSError, TypeError, ValueError) as exc:
                logger.warning("Failed to checkpoint swarm %s: %s", swarm_id, exc)

    def _message_bus(self, plan: SwarmPlan) -> SwarmMessageBus:
        bus = self._message_buses.get(plan.swarm_id)
        if bus is None:
            bus = SwarmMessageBus.from_dict(plan.blackboard_context.get("messages_state"), swarm_id=plan.swarm_id)
            self._message_buses[plan.swarm_id] = bus
        return bus

    def publish_message(
        self,
        swarm_id: str,
        *,
        topic: str,
        sender: str,
        kind: str = "observation",
        content: str = "",
        data: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        trust: str = "untrusted",
        idempotency_key: str | None = None,
    ) -> SwarmMessage:
        """Publish a bounded message to the swarm blackboard."""

        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or is_terminal_swarm_status(plan.status):
                raise ValueError(f"swarm {swarm_id!r} is not accepting messages")
            bus = self._message_bus(plan)
            message = bus.publish(
                topic=topic,
                sender=sender,
                kind=kind,
                content=content,
                data=data,
                task_id=task_id,
                trust=trust,
                idempotency_key=idempotency_key,
            )
            plan.blackboard_context["messages_state"] = bus.to_dict()
            self.append_event(
                swarm_id,
                "SWARM_MESSAGE",
                task_id=task_id,
                worker=sender,
                details={
                    "message_id": message.message_id,
                    "sequence": message.sequence,
                    "topic": message.topic,
                    "kind": message.kind,
                    "trust": message.trust,
                },
                idempotency_key=idempotency_key,
            )
            self.checkpoint(swarm_id)
            return message

    def get_messages(
        self,
        swarm_id: str,
        *,
        topic: str | None = None,
        task_id: str | None = None,
        since_sequence: int = 0,
        limit: int = 50,
    ) -> list[SwarmMessage]:
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan:
                return []
            return self._message_bus(plan).read(
                topic=topic,
                task_id=task_id,
                since_sequence=since_sequence,
                limit=limit,
            )

    def task_context(self, swarm_id: str, task: SwarmTaskNode, *, limit: int = 12) -> list[dict[str, Any]]:
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan:
                return []
            return self._message_bus(plan).context_for(task.task_id, task.context_refs, limit=limit)

    def claim_task(
        self,
        swarm_id: str,
        task_id: str,
        *,
        owner: str,
        lease_seconds: float = 60.0,
        expected_revision: int | None = None,
    ) -> SwarmTaskLease | None:
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or plan.status != "running":
                return None
            if expected_revision is not None and int(plan.revision) != int(expected_revision):
                return None
            scheduler = SwarmScheduler(plan)
            lease = scheduler.claim_task(task_id, lease_owner=owner, lease_seconds=lease_seconds)
            if lease:
                self.append_event(
                    swarm_id,
                    "TASK_LEASED",
                    task_id=task_id,
                    worker=owner,
                    details={"lease_id": lease.lease_id, "expires_at": lease.expires_at, "attempt": lease.task_attempt},
                )
                self.checkpoint(swarm_id)
            return lease

    def complete_task(
        self,
        swarm_id: str,
        task_id: str,
        *,
        result_summary: str,
        evidence: Sequence[Mapping[str, object]] | None = None,
        output_artifacts: Sequence[str] | None = None,
        lease_id: str | None = None,
        expected_revision: int | None = None,
    ) -> SwarmTaskNode | None:
        """Complete a task and fence stale attempts at the coordinator boundary.

        ``None`` means either an unknown task/revision or a result that lost its
        lease race.  Callers that need to distinguish those cases should check
        the task first; the Gateway maps the latter to a conflict response.
        """

        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or task_id not in plan.tasks:
                return None
            if expected_revision is not None and int(plan.revision) != int(expected_revision):
                return None
            previous_state = plan.tasks[task_id].state
            updated = SwarmScheduler(plan).mark_completed(
                task_id,
                result_summary,
                evidence=evidence,
                output_artifacts=output_artifacts,
                lease_id=lease_id,
            )
            if updated is not None and previous_state != TaskNodeState.COMPLETED and updated.state == TaskNodeState.COMPLETED:
                # Acceptance is an overlay on execution.  Apply it only after
                # the lease-fenced state transition succeeds so a stale caller
                # cannot mutate verification metadata on a live attempt.
                if updated.acceptance_criteria:
                    SwarmAggregator.apply_acceptance_verification(
                        updated,
                        [dict(item) for item in (evidence or []) if isinstance(item, Mapping)],
                    )
                plan.budget.record_task_success()
                self.append_event(
                    swarm_id,
                    "TASK_COMPLETED",
                    task_id=task_id,
                    details={
                        "summary": result_summary,
                        "acceptance_status": updated.acceptance_status,
                        "verification": updated.verification,
                    },
                    idempotency_key=f"external-task-complete:{swarm_id}:{task_id}:{lease_id or 'legacy'}",
                )
                self.checkpoint(swarm_id)
            return updated

    def metrics(self, swarm_id: str) -> dict[str, Any]:
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan:
                return {"status": "not_found"}
            scheduler = SwarmScheduler(plan)
            bus = self._message_bus(plan)
            progress = scheduler.progress()
            return {
                "swarm_id": swarm_id,
                "status": plan.status,
                "schema_version": plan.schema_version,
                "revision": plan.revision,
                "progress": progress,
                "acceptance": {
                    "passed": sum(1 for task in plan.tasks.values() if task.acceptance_status == "passed"),
                    "failed": sum(1 for task in plan.tasks.values() if task.acceptance_status == "failed"),
                    "unverified": sum(1 for task in plan.tasks.values() if task.acceptance_criteria and task.acceptance_status not in {"passed", "failed"}),
                },
                "budget": plan.budget.check(),
                "messages": {"total": bus.count(), "topics": sorted({message.topic for message in bus.read(limit=bus.max_messages)})},
                "events": len(self._events.get(swarm_id, [])),
                "consensus": dict(plan.consensus),
                "terminal_reason": plan.terminal_reason,
                "rounds": plan.round,
                "replans": plan.replan_count,
            }

    def dynamic_expand(
        self,
        swarm_id: str,
        new_tasks: list[dict[str, Any]],
        parent_task_id: str | None = None,
        expected_revision: int | None = None,
    ) -> list[str]:
        """Validate and inject tasks into an active DAG as one state transition."""

        if not isinstance(new_tasks, list) or not new_tasks:
            raise SwarmPlanValidationError("new_tasks must be a non-empty JSON array")
        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or is_terminal_swarm_status(plan.status):
                return []
            if expected_revision is not None and int(plan.revision) != int(expected_revision):
                raise SwarmPlanValidationError("swarm revision changed; reload before expanding")
            if plan.replan_count >= plan.budget.max_replans:
                raise SwarmPlanValidationError("replan budget exhausted")
            if not plan.budget.can_add_tasks(len(new_tasks), len(plan.tasks)):
                raise SwarmPlanValidationError("dynamic expansion exceeds max_tasks budget")
            if parent_task_id and parent_task_id not in plan.tasks and not any(isinstance(raw, Mapping) and raw.get("task_id") == parent_task_id for raw in new_tasks):
                raise SwarmPlanValidationError(f"unknown parent task: {parent_task_id!r}")

            additions: dict[str, SwarmTaskNode] = {}
            for raw in new_tasks:
                if not isinstance(raw, Mapping):
                    raise SwarmPlanValidationError("each new task must be an object")
                tid = str(raw.get("task_id") or f"task-dyn-{uuid.uuid4().hex[:8]}")
                if tid in plan.tasks or tid in additions:
                    raise SwarmPlanValidationError(f"duplicate task id: {tid!r}")
                objective = str(raw.get("objective") or "").strip()
                if not objective:
                    raise SwarmPlanValidationError(f"task {tid!r} has an empty objective")
                deps = [str(dep) for dep in raw.get("dependencies", []) if str(dep)]
                if parent_task_id and parent_task_id not in deps:
                    deps.append(parent_task_id)
                worktree_path = raw.get("worktree_path")
                if worktree_path:
                    worktree = Path(str(worktree_path))
                    if worktree.is_absolute() or ".." in worktree.parts:
                        raise SwarmPlanValidationError(f"task {tid!r} worktree_path must be a relative path without '..'")
                additions[tid] = SwarmTaskNode(
                    task_id=tid,
                    objective=objective,
                    dependencies=deps,
                    assigned_worker=raw.get("assigned_worker"),
                    worker_type=str(raw.get("worker_type", "ephemeral")),
                    model_override=raw.get("model_override"),
                    worktree_path=raw.get("worktree_path"),
                    input_artifacts=[str(item) for item in raw.get("input_artifacts", []) if item],
                    output_artifacts=[str(item) for item in raw.get("output_artifacts", []) if item],
                    context_refs=[str(item) for item in raw.get("context_refs", []) if item],
                    capability_tags=[str(item) for item in raw.get("capability_tags", []) if item],
                    parent_task_id=parent_task_id,
                    priority=int(raw.get("priority", 0) or 0),
                    max_attempts=max(1, int(raw.get("max_attempts", 3) or 3)),
                    acceptance_criteria=[dict(item) for item in raw.get("acceptance_criteria", []) if isinstance(item, Mapping)],
                    estimated_seconds=max(0.0, float(raw.get("estimated_seconds", 15.0) or 0.0)),
                    state=TaskNodeState.PENDING,
                )

            # Validate the complete candidate graph before mutating the live
            # plan.  A malformed expansion therefore has no partial effect.
            known_ids = set(plan.tasks) | set(additions)
            for tid, node in additions.items():
                for dependency in node.dependencies:
                    if dependency not in known_ids:
                        raise SwarmPlanValidationError(f"task {tid!r} depends on unknown task {dependency!r}")
            candidate = SwarmPlan(
                swarm_id=plan.swarm_id,
                goal=plan.goal,
                mode=plan.mode,
                tasks={**plan.tasks, **additions},
                max_concurrency=plan.max_concurrency,
                budget=plan.budget,
            )
            SwarmScheduler(candidate).validate_graph()

            for tid, node in additions.items():
                plan.tasks[tid] = node
                for aggregator_id in ("task-reduce", "task-judge", "task-integrate", "task-evaluator"):
                    aggregator = plan.tasks.get(aggregator_id)
                    if aggregator is not None and aggregator_id != tid and tid not in aggregator.dependencies:
                        aggregator.dependencies.append(tid)
            plan.replan_count += 1
            if plan.status == "partial_success":
                plan.status = "running"
            SwarmTaskDecomposer._compute_critical_path_and_speedup(plan)
            added_ids = list(additions)
            self.append_event(
                swarm_id,
                "SWARM_EXPANDED",
                details={
                    "parent_task_id": parent_task_id,
                    "added_task_ids": added_ids,
                    "new_critical_path": plan.critical_path_seconds,
                    "replan_count": plan.replan_count,
                },
            )
            self.checkpoint(swarm_id)
            return added_ids

    def auto_replan_failed_task(self, swarm_id: str, failed_task_id: str) -> str | None:
        """Add one bounded repair task and redirect its blocked dependents.

        This is intentionally deterministic: it does not ask a second model to
        invent a recovery plan.  The failed task remains failed as an audit
        record; a fresh task carries the repair objective and any downstream
        task depends on the repair instead of the dead attempt.
        """

        with self._lock:
            plan = self._swarms.get(swarm_id)
            if not plan or not plan.auto_replan or is_terminal_swarm_status(plan.status):
                return None
            failed = plan.tasks.get(failed_task_id)
            if failed is None or failed.state != TaskNodeState.FAILED:
                return None
            already_repaired = {str(item) for item in plan.metrics.get("auto_replanned_task_ids", []) if str(item)}
            if failed_task_id in already_repaired:
                return None
            if plan.replan_count >= plan.budget.max_replans:
                plan.metrics["auto_replan_limit"] = "max_replans_exhausted"
                return None
            if not plan.budget.can_add_tasks(1, len(plan.tasks)):
                plan.metrics["auto_replan_limit"] = "max_tasks_exhausted"
                return None

            repair_id = f"task-repair-{plan.replan_count + 1}-{failed_task_id[:80]}"
            if repair_id in plan.tasks:
                return None
            # Only completed prerequisites are safe for the repair attempt.  A
            # failed prerequisite is represented by its own failure/audit
            # record rather than being silently treated as satisfied.
            dependencies = [dependency for dependency in failed.dependencies if dependency in plan.tasks and plan.tasks[dependency].state == TaskNodeState.COMPLETED]
            repair = SwarmTaskNode(
                task_id=repair_id,
                objective=(f"Independently repair or verify the failed task {failed_task_id!r}: {failed.objective[:2000]}. Do not assume its side effects succeeded; report fresh evidence and artifacts."),
                dependencies=dependencies,
                parent_task_id=failed_task_id,
                worker_type="ephemeral",
                capability_tags=list(failed.capability_tags),
                priority=max(0, failed.priority),
                max_attempts=max(1, failed.max_attempts),
                estimated_seconds=max(1.0, failed.estimated_seconds),
                state=TaskNodeState.PENDING,
            )
            candidate_tasks: dict[str, SwarmTaskNode] = {task_id: replace(task, dependencies=list(task.dependencies)) for task_id, task in plan.tasks.items()}
            candidate_tasks[repair_id] = repair
            for task in candidate_tasks.values():
                if task.task_id in {failed_task_id, repair_id} or task.state in {
                    TaskNodeState.COMPLETED,
                    TaskNodeState.FAILED,
                    TaskNodeState.CANCELLED,
                }:
                    continue
                task.dependencies = [repair_id if dep == failed_task_id else dep for dep in task.dependencies]
            SwarmScheduler(SwarmPlan(swarm_id=swarm_id, goal=plan.goal, mode=plan.mode, tasks=candidate_tasks)).validate_graph()

            plan.tasks[repair_id] = repair
            for task in plan.tasks.values():
                if task.task_id in {failed_task_id, repair_id} or task.state in {
                    TaskNodeState.COMPLETED,
                    TaskNodeState.FAILED,
                    TaskNodeState.CANCELLED,
                }:
                    continue
                task.dependencies = [repair_id if dep == failed_task_id else dep for dep in task.dependencies]
            plan.replan_count += 1
            repaired_ids = plan.metrics.get("auto_replanned_task_ids")
            if not isinstance(repaired_ids, list):
                repaired_ids = []
                plan.metrics["auto_replanned_task_ids"] = repaired_ids
            repaired_ids.append(failed_task_id)
            SwarmTaskDecomposer._compute_critical_path_and_speedup(plan)
            if plan.status == "partial_success":
                plan.status = "running"
            self.append_event(
                swarm_id,
                "SWARM_AUTO_REPLAN",
                task_id=failed_task_id,
                details={
                    "repair_task_id": repair_id,
                    "replan_count": plan.replan_count,
                    "reason": "bounded deterministic repair after terminal task failure",
                },
            )
            self.checkpoint(swarm_id)
            return repair_id

    def replan(
        self,
        swarm_id: str,
        new_tasks: list[dict[str, Any]],
        *,
        parent_task_id: str | None = None,
    ) -> list[str]:
        """Explicit alias for a validated mid-flight replan."""

        return self.dynamic_expand(swarm_id, new_tasks, parent_task_id)

    def start_async(self, swarm_id: str):
        """Starts the swarm in background async loop."""
        from alpha.swarm.runner import AsyncSwarmRunner

        runner = AsyncSwarmRunner(self)
        return runner.start_background_swarm(swarm_id)
