"""Gateway REST Router for Deterministic Out-of-Band Supervision & Watchdog."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from alpha.supervision import (
    AnomalyReport,
    AnomalyType,
    DeterministicWatchdog,
    HeartbeatRecord,
    WatchdogRecoveryManager,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/supervision", tags=["supervision"])

_GLOBAL_WATCHDOG = DeterministicWatchdog(freeze_threshold_beats=3)
_GLOBAL_RECOVERY = WatchdogRecoveryManager(watchdog=_GLOBAL_WATCHDOG)


class HeartbeatIngestRequest(BaseModel):
    worker_id: str = Field(..., description="Worker identifier")
    task_id: str | None = Field(default=None, description="Active task identifier")
    progress_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    current_action: str = Field(default="", description="Active action description")
    lease_seconds: float = Field(default=60.0)
    artifacts_count: int = Field(default=0)
    step_index: int = Field(default=0)
    cpu_usage: float = Field(default=0.0)
    memory_mb: float = Field(default=0.0)


class TriggerRecoveryRequest(BaseModel):
    worker_id: str = Field(..., description="Target worker identifier")
    successor_id: str | None = Field(default=None, description="Optional designated successor")


class AdoptOrphansRequest(BaseModel):
    supervisor_id: str = Field(..., description="New supervisor worker identifier")


@router.post("/heartbeat")
async def ingest_heartbeat(payload: HeartbeatIngestRequest):
    """Ingest heartbeat and evaluate liveness and progress deltas."""
    rec = HeartbeatRecord(
        worker_id=payload.worker_id,
        task_id=payload.task_id,
        progress_percent=payload.progress_percent,
        current_action=payload.current_action,
        lease_seconds=payload.lease_seconds,
        artifacts_count=payload.artifacts_count,
        step_index=payload.step_index,
        cpu_usage=payload.cpu_usage,
        memory_mb=payload.memory_mb,
    )
    new_anomalies = _GLOBAL_WATCHDOG.record_heartbeat(rec)
    return {
        "status": "heartbeat_recorded",
        "worker_id": payload.worker_id,
        "new_anomalies_detected": [a.model_dump() for a in new_anomalies],
    }


@router.get("/fleet")
async def get_fleet_health():
    """Returns real-time health and lease status across all registered workers."""
    return _GLOBAL_WATCHDOG.evaluate_fleet()


@router.get("/anomalies")
async def get_anomalies(worker_id: str | None = None):
    """Returns detected anomalies (progress frozen, circular loops, expired leases)."""
    reports = _GLOBAL_WATCHDOG.inspect_anomalies(worker_id=worker_id)
    return [r.model_dump() for r in reports]


@router.post("/recover")
async def trigger_recovery(payload: TriggerRecoveryRequest):
    """Triggers automated self-healing / hot-replacement for an anomalous worker."""
    reports = _GLOBAL_WATCHDOG.inspect_anomalies(worker_id=payload.worker_id)
    if not reports:
        dummy_anomaly = AnomalyReport(
            worker_id=payload.worker_id,
            anomaly_type=AnomalyType.PROGRESS_FROZEN,
            description="Manual recovery requested via gateway API.",
        )
        rec_res = _GLOBAL_RECOVERY.execute_recovery(payload.worker_id, dummy_anomaly, payload.successor_id)
    else:
        rec_res = _GLOBAL_RECOVERY.execute_recovery(payload.worker_id, reports[-1], payload.successor_id)
    return rec_res


@router.post("/adopt")
async def adopt_orphans(payload: AdoptOrphansRequest):
    """Reattaches orphaned workers whose managers have failed to a new supervisor."""
    adopted = _GLOBAL_RECOVERY.adopt_orphans(payload.supervisor_id)
    return {
        "status": "orphans_adopted",
        "supervisor_id": payload.supervisor_id,
        "adopted_worker_ids": adopted,
    }
