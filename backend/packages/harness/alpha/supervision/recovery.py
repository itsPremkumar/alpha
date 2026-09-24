"""Automated recovery and self-healing engine driven by watchdog anomaly reports."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from alpha.supervision.models import (
    AgentHealthStatus,
    AnomalyReport,
    RecoveryAction,
)
from alpha.supervision.watchdog import DeterministicWatchdog

logger = logging.getLogger(__name__)


class WatchdogRecoveryManager:
    """Coordinates automated self-healing, worker replacement, and orphan adoption."""

    def __init__(
        self,
        watchdog: DeterministicWatchdog,
        *,
        restart_worker: Callable[[str], bool] | None = None,
        backoff_base: float = 0.0,
    ):
        """Create a recovery manager.

        ``restart_worker`` is the only thing that can actually restart a worker.
        It is injected rather than assumed: the supervisor has no privileged
        handle on the execution environment, so until a caller wires one in the
        manager must report that a restart was *requested* rather than claim one
        happened. ``None`` preserves the historical status-only behaviour.
        """
        self._watchdog = watchdog
        self._restart_worker = restart_worker
        self._backoff_base = backoff_base
        # worker_id -> list of recovery action timestamps
        self._recovery_history: dict[str, list[dict[str, Any]]] = {}
        # parent_id -> set of child worker_ids
        self._parent_child_map: dict[str, set[str]] = {}
        # Queue of orphaned child worker_ids waiting for adoption
        self._orphan_queue: list[dict[str, Any]] = []

    def register_child_relation(self, parent_id: str, child_id: str) -> None:
        self._parent_child_map.setdefault(parent_id, set()).add(child_id)

    def determine_recovery_action(self, worker_id: str, anomaly: AnomalyReport) -> RecoveryAction:
        """Determines the escalated recovery action based on prior incident frequency."""
        history = self._recovery_history.get(worker_id, [])
        recent_attempts = len(history)

        if recent_attempts == 0:
            return anomaly.suggested_action
        elif recent_attempts == 1:
            return RecoveryAction.RESTART
        elif recent_attempts == 2:
            return RecoveryAction.HOT_REPLACE
        elif recent_attempts == 3:
            return RecoveryAction.SUCCESSOR_HANDOFF
        else:
            return RecoveryAction.ESCALATE

    def execute_recovery(
        self,
        worker_id: str,
        anomaly: AnomalyReport,
        successor_id: str | None = None,
        action_override: RecoveryAction | None = None,
    ) -> dict[str, Any]:
        """Execute automated recovery workflow according to the recovery ladder."""
        if action_override:
            action = action_override
        elif successor_id:
            action = RecoveryAction.SUCCESSOR_HANDOFF
        else:
            action = self.determine_recovery_action(worker_id, anomaly)
        timestamp = time.time()
        incident_id = f"rec-{uuid.uuid4().hex[:8]}"

        record = {
            "incident_id": incident_id,
            "worker_id": worker_id,
            "action": action.value,
            "anomaly_type": anomaly.anomaly_type.value,
            "timestamp": timestamp,
            "successor_id": successor_id,
        }
        self._recovery_history.setdefault(worker_id, []).append(record)

        if action == RecoveryAction.RETRY:
            self._watchdog.clear_anomalies(worker_id)
            status_msg = f"Retrying task for worker {worker_id}; cleared transient anomalies."

        elif action == RecoveryAction.RESTART:
            self._watchdog.clear_anomalies(worker_id)
            self._watchdog.set_status_override(worker_id, AgentHealthStatus.RECOVERING)
            restarted = False
            if self._restart_worker is not None:
                try:
                    restarted = bool(self._restart_worker(worker_id))
                except Exception:
                    # Fault isolation: a failing restart hook must not take down
                    # the supervision loop or lose the audit record.
                    logger.warning(
                        "Restart hook raised for worker %s; reporting restart as not performed",
                        worker_id, exc_info=True,
                    )
                    restarted = False
            record["restarted"] = restarted
            if restarted:
                status_msg = f"Worker {worker_id} restarted via wired restart hook."
            elif self._restart_worker is None:
                # Previously this claimed a restart unconditionally. Without a
                # wired runner no process is actually restarted, and lying about
                # it makes incident review useless.
                status_msg = (
                    f"Worker {worker_id} restart requested (no runner wired); "
                    "status set to RECOVERING but no process was restarted."
                )
            else:
                status_msg = (
                    f"Worker {worker_id} restart hook was wired but did not report success; "
                    "status set to RECOVERING but no process was restarted."
                )

        elif action == RecoveryAction.HOT_REPLACE:
            self._watchdog.clear_anomalies(worker_id)
            # No ephemeral replacement or checkpoint rehydration is performed
            # by this manager; claiming either would be a false success.
            status_msg = (
                f"Worker {worker_id} hot-replace not implemented — no replacement or "
                "checkpoint rehydration was performed; restart required."
            )

        elif action == RecoveryAction.SUCCESSOR_HANDOFF:
            target = successor_id or f"successor-{worker_id}"
            self._watchdog.set_status_override(worker_id, AgentHealthStatus.FAILED)
            status_msg = f"Worker {worker_id} failed irrevocably. Responsibilities and active lease handed off to designated successor {target}."
            # Handle potential orphaned children if this worker was a manager
            self._handle_manager_failure(worker_id)

        else:  # ESCALATE
            self._watchdog.set_status_override(worker_id, AgentHealthStatus.FAILED)
            status_msg = f"Worker {worker_id} recovery failed across multiple attempts. Escalated to Organization Executive with full diagnostic trace."

        return {
            "incident_id": incident_id,
            "worker_id": worker_id,
            "action_executed": action.value,
            "status_message": status_msg,
            "timestamp": timestamp,
            # Present on every action so callers can assert on it uniformly;
            # only RESTART can ever set it True.
            "restarted": bool(record.get("restarted", False)),
        }

    def _handle_manager_failure(self, failed_manager_id: str) -> None:
        """Enqueue all active children of the failed manager for supervisor adoption."""
        children = self._parent_child_map.pop(failed_manager_id, set())
        for child_id in children:
            self._orphan_queue.append(
                {
                    "child_id": child_id,
                    "former_parent_id": failed_manager_id,
                    "orphaned_at": time.time(),
                }
            )

    def adopt_orphans(self, new_supervisor_id: str) -> list[str]:
        """Adopt queued orphaned workers under a new supervisor without terminating them."""
        adopted_ids: list[str] = []
        while self._orphan_queue:
            item = self._orphan_queue.pop(0)
            child_id = item["child_id"]
            self._parent_child_map.setdefault(new_supervisor_id, set()).add(child_id)
            adopted_ids.append(child_id)
        return adopted_ids

    def get_orphan_queue(self) -> list[dict[str, Any]]:
        return list(self._orphan_queue)
