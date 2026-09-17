"""Built-in LangChain tool for Deterministic Supervisor Watchdog & Recovery."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.supervision.models import (
    AnomalyReport,
    AnomalyType,
    HeartbeatRecord,
)
from agent_workspace.supervision.recovery import WatchdogRecoveryManager
from agent_workspace.supervision.watchdog import DeterministicWatchdog

_GLOBAL_WATCHDOG = DeterministicWatchdog(freeze_threshold_beats=3)
_GLOBAL_RECOVERY = WatchdogRecoveryManager(watchdog=_GLOBAL_WATCHDOG)


@tool("supervisor_watchdog", parse_docstring=True)
def supervision_tool(
    action: str,
    worker_id: str = "",
    task_id: str = "",
    progress_percent: float = 0.0,
    current_action: str = "",
    tool_name: str = "",
    args_summary: str = "",
    successor_id: str = "",
) -> str:
    """Monitor agent health, report heartbeats, inspect anomalies, and trigger recovery.

    Args:
        action: 'heartbeat', 'record_tool', 'fleet_health', 'anomalies', 'recover', 'adopt_orphans'.
        worker_id: Target agent or worker identifier.
        task_id: Currently active task identifier.
        progress_percent: Worker completion progress (0-100).
        current_action: Description of active work being performed.
        tool_name: Executed tool name when tracking tool call patterns.
        args_summary: Summarized args for loop thrashing detection.
        successor_id: Optional designated successor worker ID for handoffs.
    """
    try:
        if action == "heartbeat":
            if not worker_id:
                return "Error: 'worker_id' is required for heartbeat action."
            rec = HeartbeatRecord(
                worker_id=worker_id,
                task_id=task_id or None,
                progress_percent=progress_percent,
                current_action=current_action,
            )
            anomalies = _GLOBAL_WATCHDOG.record_heartbeat(rec)
            return json.dumps(
                {
                    "status": "heartbeat_recorded",
                    "worker_id": worker_id,
                    "anomalies_detected": [a.model_dump() for a in anomalies],
                },
                indent=2,
            )

        elif action == "record_tool":
            if not worker_id or not tool_name:
                return "Error: 'worker_id' and 'tool_name' are required for record_tool."
            anom = _GLOBAL_WATCHDOG.record_tool_call(worker_id, tool_name, args_summary)
            return json.dumps(
                {
                    "worker_id": worker_id,
                    "anomaly": anom.model_dump() if anom else None,
                },
                indent=2,
            )

        elif action == "fleet_health":
            fleet = _GLOBAL_WATCHDOG.evaluate_fleet()
            return json.dumps(fleet, indent=2)

        elif action == "anomalies":
            reports = _GLOBAL_WATCHDOG.inspect_anomalies(worker_id or None)
            return json.dumps([r.model_dump() for r in reports], indent=2)

        elif action == "recover":
            if not worker_id:
                return "Error: 'worker_id' is required for recover action."
            reports = _GLOBAL_WATCHDOG.inspect_anomalies(worker_id)
            if not reports:
                # Create default synthetic anomaly for manual recovery request
                dummy_anomaly = AnomalyReport(
                    worker_id=worker_id,
                    anomaly_type=AnomalyType.PROGRESS_FROZEN,
                    description="Manual recovery requested by supervisor.",
                )
                rec_res = _GLOBAL_RECOVERY.execute_recovery(worker_id, dummy_anomaly, successor_id or None)
            else:
                rec_res = _GLOBAL_RECOVERY.execute_recovery(worker_id, reports[-1], successor_id or None)
            return json.dumps(rec_res, indent=2)

        elif action == "adopt_orphans":
            if not worker_id:
                return "Error: 'worker_id' (new supervisor) is required for adopt_orphans."
            adopted = _GLOBAL_RECOVERY.adopt_orphans(worker_id)
            return json.dumps({"status": "orphans_adopted", "new_supervisor": worker_id, "adopted_worker_ids": adopted}, indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error executing supervisor_watchdog tool: {exc}"
