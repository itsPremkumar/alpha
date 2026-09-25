"""Swarm Incident Engine: Automated Failure Diagnosis and Succession Recovery."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha.bots.handoff import resolve_succession
from alpha.swarm.models import SwarmTaskNode, TaskNodeState

logger = logging.getLogger(__name__)

_GLOBAL_INCIDENT_MANAGER: SwarmIncidentManager | None = None


def get_swarm_incident_manager() -> SwarmIncidentManager:
    global _GLOBAL_INCIDENT_MANAGER
    if _GLOBAL_INCIDENT_MANAGER is None:
        _GLOBAL_INCIDENT_MANAGER = SwarmIncidentManager()
    return _GLOBAL_INCIDENT_MANAGER


@dataclass
class SwarmIncident:
    incident_id: str = field(default_factory=lambda: f"inc-{uuid.uuid4().hex[:8]}")
    swarm_id: str = ""
    task_id: str = ""
    failed_worker: str = ""
    error_message: str = ""
    attempt: int = 1
    assigned_successor: str | None = None
    resolved: bool = False
    reason: str = ""
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SwarmIncidentManager:
    """Detects worker failures, records incidents, and triggers automatic succession recovery."""

    def __init__(self):
        # swarm_id -> list of incidents
        self._incidents: dict[str, list[SwarmIncident]] = {}

    def record_failure_and_recover(
        self,
        swarm_id: str,
        task: SwarmTaskNode,
        error_message: str,
        *,
        allow_retry: bool = True,
    ) -> SwarmIncident:
        """Record a failure and optionally apply a bounded succession recovery."""
        failed_worker = task.assigned_worker or "ephemeral-worker"
        incident = SwarmIncident(
            swarm_id=swarm_id,
            task_id=task.task_id,
            failed_worker=failed_worker,
            error_message=error_message,
            attempt=task.attempts,
        )

        # Trigger autonomous succession resolution
        successor = resolve_succession(failed_worker)
        if successor and successor != failed_worker and allow_retry and task.attempts < task.max_attempts:
            task.assigned_worker = successor
            task.worker_type = "permanent_bot"
            task.state = TaskNodeState.PENDING
            task.lease_expires_at = None
            task.lease_id = None
            task.lease_owner = None
            incident.assigned_successor = successor
            incident.resolved = True
            logger.info(f"Swarm incident {incident.incident_id} recovered: task {task.task_id} reassigned from @{failed_worker} to successor @{successor}.")
        else:
            incident.resolved = False
            incident.reason = "no eligible succession fallback"
            if successor and not allow_retry:
                incident.reason = "succession candidate found but task retry budget is exhausted"
            logger.warning(f"Swarm incident {incident.incident_id} unresolved: no eligible succession fallback for @{failed_worker}.")

        if swarm_id not in self._incidents:
            self._incidents[swarm_id] = []
        self._incidents[swarm_id].append(incident)
        return incident

    def get_incidents(self, swarm_id: str) -> list[SwarmIncident]:
        return list(self._incidents.get(swarm_id, []))
