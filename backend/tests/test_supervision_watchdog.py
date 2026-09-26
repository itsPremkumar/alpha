"""Comprehensive tests for the Deterministic Out-of-Band Supervisor & Watchdog."""

from __future__ import annotations

import pytest

from alpha.supervision import (
    AgentHealthStatus,
    AnomalyReport,
    AnomalyType,
    DeterministicWatchdog,
    HeartbeatRecord,
    RecoveryAction,
    WatchdogRecoveryManager,
)
from alpha.tools.builtins.supervision_tool import supervision_tool


def test_watchdog_frozen_progress_detection():
    watchdog = DeterministicWatchdog(freeze_threshold_beats=3)
    worker_id = "worker-101"

    # Send 3 heartbeats with zero progress delta while task is active
    for step in range(3):
        rec = HeartbeatRecord(
            worker_id=worker_id,
            task_id="task-long-running",
            progress_percent=25.0,  # Frozen at 25%
            artifacts_count=1,  # No new artifacts
            step_index=2,  # Stuck on step 2
            current_action="Waiting on API",
        )
        anomalies = watchdog.record_heartbeat(rec)

    # Third heartbeat should detect frozen progress
    assert len(anomalies) > 0
    assert anomalies[0].anomaly_type == AnomalyType.PROGRESS_FROZEN
    assert anomalies[0].suggested_action == RecoveryAction.HOT_REPLACE


def test_watchdog_tool_thrashing_and_loop_detection():
    watchdog = DeterministicWatchdog()
    worker_id = "worker-loop"

    # 1. Tool thrashing: 4 identical invocations
    for _ in range(3):
        assert watchdog.record_tool_call(worker_id, "search_web", "query=python") is None
    fourth = watchdog.record_tool_call(worker_id, "search_web", "query=python")
    assert fourth is not None
    assert fourth.anomaly_type == AnomalyType.TOOL_THRASHING

    # 2. Circular loop: A -> B -> A -> B -> A -> B
    worker_loop = "worker-2step"
    calls = ["read_file", "search_web"] * 3
    last_report = None
    for tool in calls:
        res = watchdog.record_tool_call(worker_loop, tool, "target=foo")
        if res:
            last_report = res

    assert last_report is not None
    assert last_report.anomaly_type == AnomalyType.CIRCULAR_LOOP


def test_watchdog_lease_expiration_and_fleet_evaluation():
    watchdog = DeterministicWatchdog()
    worker_id = "worker-leased"

    # Send initial heartbeat with 10 second lease
    now = 1000.0
    rec = HeartbeatRecord(
        worker_id=worker_id,
        task_id="task-1",
        timestamp=now,
        lease_seconds=10.0,
        progress_percent=50.0,
    )
    watchdog.record_heartbeat(rec)

    # 5 seconds later -> still BUSY
    status_5s = watchdog.evaluate_fleet(now=now + 5.0)
    assert status_5s[worker_id]["status"] == AgentHealthStatus.BUSY.value

    # 15 seconds later -> lease exceeded (15s > 10s) -> STALLED
    status_15s = watchdog.evaluate_fleet(now=now + 15.0)
    assert status_15s[worker_id]["status"] == AgentHealthStatus.STALLED.value

    # 25 seconds later -> > 2x lease -> FAILED
    status_25s = watchdog.evaluate_fleet(now=now + 25.0)
    assert status_25s[worker_id]["status"] == AgentHealthStatus.FAILED.value


def test_recovery_ladder_and_orphan_adoption():
    watchdog = DeterministicWatchdog()
    recovery = WatchdogRecoveryManager(watchdog=watchdog)
    worker_id = "worker-stalled"
    anomaly = AnomalyReport(
        worker_id=worker_id,
        anomaly_type=AnomalyType.PROGRESS_FROZEN,
        description="Frozen progress",
    )

    # Attempt 1: RETRY
    r1 = recovery.execute_recovery(worker_id, anomaly)
    assert r1["action_executed"] == RecoveryAction.RETRY.value

    # Attempt 2: RESTART
    r2 = recovery.execute_recovery(worker_id, anomaly)
    assert r2["action_executed"] == RecoveryAction.RESTART.value

    # Attempt 3: HOT_REPLACE
    r3 = recovery.execute_recovery(worker_id, anomaly)
    assert r3["action_executed"] == RecoveryAction.HOT_REPLACE.value
    # Honesty pin: HOT_REPLACE performs no replacement or rehydration, so the
    # message must say exactly that instead of claiming a successful swap.
    assert "hot-replace not implemented" in r3["status_message"]
    assert "restart required" in r3["status_message"]
    assert r3["restarted"] is False

    # Parent Failure & Orphan Adoption test
    manager_id = "manager-1"
    child_a = "child-worker-a"
    child_b = "child-worker-b"
    recovery.register_child_relation(manager_id, child_a)
    recovery.register_child_relation(manager_id, child_b)

    # Manager fails and escalates to successor
    recovery.execute_recovery(manager_id, anomaly, successor_id="manager-successor")

    # Children should be in orphan queue
    orphan_queue = recovery.get_orphan_queue()
    assert len(orphan_queue) == 2
    assert {o["child_id"] for o in orphan_queue} == {child_a, child_b}

    # Supervisor adopts orphans
    adopted = recovery.adopt_orphans(new_supervisor_id="org-supervisor")
    assert set(adopted) == {child_a, child_b}
    assert len(recovery.get_orphan_queue()) == 0


def test_supervision_tool_and_gateway_router():
    # 1. Tool heartbeat
    hb_out = supervision_tool.invoke(
        {
            "action": "heartbeat",
            "worker_id": "test-bot-tool",
            "progress_percent": 60.0,
            "current_action": "Processing batch",
        }
    )
    assert "heartbeat_recorded" in hb_out

    # 2. Tool fleet health
    fleet_out = supervision_tool.invoke({"action": "fleet_health"})
    assert "test-bot-tool" in fleet_out


@pytest.mark.asyncio
async def test_gateway_supervision_router():
    from app.gateway.routers import supervision as sup_router

    # Ingest heartbeat via router
    req = sup_router.HeartbeatIngestRequest(
        worker_id="gateway-worker",
        progress_percent=80.0,
        current_action="Running validation",
    )
    resp = await sup_router.ingest_heartbeat(req)
    assert resp["status"] == "heartbeat_recorded"

    # Get fleet
    fleet = await sup_router.get_fleet_health()
    assert "gateway-worker" in fleet
