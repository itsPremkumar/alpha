"""Deterministic out-of-band watchdog monitoring agent health and anomalies."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from alpha.supervision.models import (
    AgentHealthStatus,
    AnomalyReport,
    AnomalySeverity,
    AnomalyType,
    HeartbeatRecord,
    RecoveryAction,
)

logger = logging.getLogger(__name__)


class DeterministicWatchdog:
    """Out-of-band monitoring engine evaluating heartbeats, progress deltas, and tool loops."""

    def __init__(self, freeze_threshold_beats: int = 3):
        self._freeze_threshold = freeze_threshold_beats
        # worker_id -> list of recent HeartbeatRecord (ordered oldest to newest)
        self._heartbeats: dict[str, list[HeartbeatRecord]] = defaultdict(list)
        # worker_id -> list of recent tool signatures (name:arg_hash)
        self._tool_history: dict[str, list[str]] = defaultdict(list)
        # worker_id -> list of AnomalyReport
        self._anomalies: dict[str, list[AnomalyReport]] = defaultdict(list)
        # worker_id -> manual status override
        self._status_overrides: dict[str, AgentHealthStatus] = {}

    def record_heartbeat(self, record: HeartbeatRecord) -> list[AnomalyReport]:
        """Ingest a worker heartbeat and immediately evaluate liveness and progress deltas."""
        wid = record.worker_id
        history = self._heartbeats[wid]
        history.append(record)
        # Keep last 20 heartbeats
        if len(history) > 20:
            history.pop(0)

        new_anomalies: list[AnomalyReport] = []

        # 1. Evaluate Progress Deltas (Frozen Progress Detection)
        if len(history) >= self._freeze_threshold:
            recent = history[-self._freeze_threshold :]
            first = recent[0]
            last = recent[-1]

            delta_p = last.progress_percent - first.progress_percent
            delta_a = last.artifacts_count - first.artifacts_count
            delta_s = last.step_index - first.step_index

            # If active task exists, not at 100%, and zero progress observed across all metrics
            if last.task_id and last.progress_percent < 100.0 and delta_p <= 0.0 and delta_a <= 0 and delta_s <= 0:
                report = AnomalyReport(
                    worker_id=wid,
                    anomaly_type=AnomalyType.PROGRESS_FROZEN,
                    severity=AnomalySeverity.HIGH,
                    description=(f"Worker {wid} made zero progress over {self._freeze_threshold} consecutive heartbeats on task '{last.task_id}' (delta_p={delta_p}, delta_a={delta_a}, delta_s={delta_s})."),
                    suggested_action=RecoveryAction.HOT_REPLACE,
                    details={"task_id": last.task_id, "step_index": last.step_index},
                )
                self._anomalies[wid].append(report)
                new_anomalies.append(report)

        return new_anomalies

    def record_tool_call(self, worker_id: str, tool_name: str, args_summary: str = "") -> AnomalyReport | None:
        """Track tool execution history and detect circular loops or thrashing."""
        signature = f"{tool_name}:{args_summary}"
        hist = self._tool_history[worker_id]
        hist.append(signature)
        if len(hist) > 30:
            hist.pop(0)

        # 1. Check for immediate repetitive thrashing (same tool called >= 4 times continuously)
        if len(hist) >= 4 and all(hist[-1] == h for h in hist[-4:]):
            report = AnomalyReport(
                worker_id=worker_id,
                anomaly_type=AnomalyType.TOOL_THRASHING,
                severity=AnomalySeverity.MEDIUM,
                description=f"Tool thrashing detected for {worker_id}: '{tool_name}' invoked 4 times with identical arguments.",
                suggested_action=RecoveryAction.RESTART,
                details={"tool_name": tool_name, "repeat_count": 4},
            )
            self._anomalies[worker_id].append(report)
            return report

        # 2. Check for 2-step cycle loop (A -> B -> A -> B -> A -> B)
        if len(hist) >= 6:
            a, b = hist[-2], hist[-1]
            if a != b and hist[-6:] == [a, b, a, b, a, b]:
                report = AnomalyReport(
                    worker_id=worker_id,
                    anomaly_type=AnomalyType.CIRCULAR_LOOP,
                    severity=AnomalySeverity.HIGH,
                    description=f"Circular 2-step tool loop detected for {worker_id} between '{a}' and '{b}'.",
                    suggested_action=RecoveryAction.HOT_REPLACE,
                    details={"pattern": [a, b], "cycles": 3},
                )
                self._anomalies[worker_id].append(report)
                return report

        return None

    def evaluate_fleet(self, now: float | None = None) -> dict[str, dict[str, Any]]:
        """Evaluate current health status and lease validity across all registered workers."""
        current_time = now if now is not None else time.time()
        fleet_status: dict[str, dict[str, Any]] = {}

        for wid, history in self._heartbeats.items():
            if not history:
                continue

            last = history[-1]
            elapsed_since_hb = current_time - last.timestamp
            lease = last.lease_seconds

            # Determine baseline health
            if wid in self._status_overrides:
                status = self._status_overrides[wid]
            elif elapsed_since_hb > (lease * 2.0):
                status = AgentHealthStatus.FAILED
                # Record lease expired anomaly if not already flagged
                if not any(a.anomaly_type == AnomalyType.LEASE_EXPIRED for a in self._anomalies[wid]):
                    self._anomalies[wid].append(
                        AnomalyReport(
                            worker_id=wid,
                            anomaly_type=AnomalyType.LEASE_EXPIRED,
                            severity=AnomalySeverity.CRITICAL,
                            description=f"Worker {wid} lease expired ({elapsed_since_hb:.1f}s elapsed > {lease}s lease). Worker considered dead.",
                            suggested_action=RecoveryAction.SUCCESSOR_HANDOFF,
                        )
                    )
            elif elapsed_since_hb > lease:
                status = AgentHealthStatus.STALLED
            elif any(a.severity in (AnomalySeverity.HIGH, AnomalySeverity.CRITICAL) for a in self._anomalies[wid]):
                status = AgentHealthStatus.DEGRADED
            elif last.progress_percent >= 100.0 or not last.task_id:
                status = AgentHealthStatus.IDLE
            else:
                status = AgentHealthStatus.BUSY

            fleet_status[wid] = {
                "worker_id": wid,
                "status": status.value,
                "last_heartbeat_elapsed_seconds": round(elapsed_since_hb, 2),
                "lease_seconds": lease,
                "progress_percent": last.progress_percent,
                "active_task_id": last.task_id,
                "current_action": last.current_action,
                "unresolved_anomalies_count": len(self._anomalies.get(wid, [])),
            }

        return fleet_status

    def inspect_anomalies(self, worker_id: str | None = None) -> list[AnomalyReport]:
        """Retrieve anomaly reports for a specific worker or the entire fleet."""
        if worker_id:
            return list(self._anomalies.get(worker_id, []))
        all_reports: list[AnomalyReport] = []
        for reports in self._anomalies.values():
            all_reports.extend(reports)
        return sorted(all_reports, key=lambda a: a.timestamp, reverse=True)

    def clear_anomalies(self, worker_id: str) -> None:
        self._anomalies.pop(worker_id, None)

    def set_status_override(self, worker_id: str, status: AgentHealthStatus | None) -> None:
        if status is None:
            self._status_overrides.pop(worker_id, None)
        else:
            self._status_overrides[worker_id] = status
