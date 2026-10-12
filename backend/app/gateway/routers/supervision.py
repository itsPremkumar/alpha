"""Gateway REST Router for Deterministic Out-of-Band Supervision & Watchdog.

An empty fleet is an unobserved watchdog, not a healthy one
-----------------------------------------------------------

``DeterministicWatchdog`` is a **host-process** monitor: it holds heartbeats and
leases for workers that run outside this process and report in over
``POST /api/supervision/heartbeat``. Alpha's own background loops are not such
workers -- they are short ticks with no lease, no progress, and no parent
process to adopt -- and the Gateway does not ship an out-of-band worker host.

So the measured behaviour of this router on a stock deployment is that
``_GLOBAL_WATCHDOG`` has never been written to and ``GET /api/supervision/fleet``
answers HTTP 200 with ``{}``. That is *correct* (nothing is being watched) and
also the most dangerous possible shape for an operator surface, because ``{}``
is indistinguishable at a glance from "the fleet is fine". The route therefore
carries its own observational state, and the client is expected to branch on
``observed`` rather than on the emptiness of the map.

Known structural gap, deliberately not papered over here
---------------------------------------------------------

``alpha/tools/builtins/supervision_tool.py`` constructs its **own**
``DeterministicWatchdog``. The model-facing supervision tool and this REST
router therefore hold two independent, independently-empty fleets, and a
heartbeat ingested here is invisible to the tool. The single-instance fix
belongs in ``alpha/supervision/`` (both callers import it from there); the
harness package is not owned by the change that added this disclosure, so the
duplication is reported rather than half-fixed. Making this router delegate to
the tool's instance instead would invert the dependency (app -> a model tool
module) and leave the tool's own lifetime wrong.
"""

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

#: Why an empty fleet is empty, as a closed vocabulary rather than prose, so a
#: consumer can branch on it instead of re-deriving the reason from an empty map.
NO_WORKERS_REASON_NO_HEARTBEAT_RECEIVED = "no_worker_has_posted_a_heartbeat_to_this_process"
NO_WORKERS_REASON_UNKNOWN = ""


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
    """Real-time health and lease status across every worker that reported in.

    The payload stays a flat ``worker_id -> status`` map so existing consumers
    keep working, and gains four sibling keys describing whether this process is
    watching anything at all. They are siblings rather than fields because the
    per-worker values are consumed as a map, and a reserved key inside it would
    be read as a worker whose id happened to be ``observed``.
    """
    fleet = _GLOBAL_WATCHDOG.evaluate_fleet()
    if fleet:
        return {
            **fleet,
            "observed": True,
            "observed_reason": "",
            "observed_worker_count": len(fleet),
            "watching": True,
        }
    # The whole point: an empty map must not read as a healthy fleet.
    return {
        **fleet,
        "observed": False,
        "observed_reason": NO_WORKERS_REASON_NO_HEARTBEAT_RECEIVED,
        "observed_worker_count": 0,
        "watching": False,
    }


@router.get("/observation")
async def get_watchdog_observation():
    """Whether this process is watching anything, and why not when it is not.

    A separate route rather than only a map key so an operator (or a monitor)
    can ask the single yes/no question without having to interpret the shape of
    the fleet map, and so the answer is legible even if the fleet map's reserved
    keys are ever dropped by a consumer that only wants workers.
    """
    fleet = _GLOBAL_WATCHDOG.evaluate_fleet()
    anomalies = _GLOBAL_WATCHDOG.inspect_anomalies()
    return {
        "observed": bool(fleet),
        "watching": bool(fleet),
        "observed_worker_count": len(fleet),
        "observed_reason": "" if fleet else NO_WORKERS_REASON_NO_HEARTBEAT_RECEIVED,
        "unresolved_anomaly_count": len(anomalies),
        "heartbeat_endpoint": "POST /api/supervision/heartbeat",
        "note": (
            "This watchdog holds heartbeats and leases for out-of-band workers that report in "
            "over the heartbeat endpoint. Alpha's own background loops are short in-process ticks "
            "with no lease and no parent process, so they are deliberately not registered here: "
            "reporting them would make the fleet non-empty without making anything supervised."
        ),
    }


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
