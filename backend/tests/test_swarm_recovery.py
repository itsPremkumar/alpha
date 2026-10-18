"""Run-correlated swarm recovery (bot-mode master plan, 3.1).

The acceptance is the plan's: kill the Gateway mid-swarm, restart, and the
swarm is ``paused`` with a reason, resumable, with fresh leases. These tests
build that restart for real — a plan checkpointed as in-flight by one
coordinator, then loaded by a *second* coordinator over the same storage dir —
and then exercise the recovery vocabulary (scan / resume / abandon /
auto-resume modes) plus the router's status filter.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import ValidationError

from alpha.config.swarm_config import SwarmConfig
from alpha.swarm import recovery
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState

SWARM_ID = "sw-restart-1"


def _inflight_plan(swarm_id: str = SWARM_ID, owner_id: str = "default") -> SwarmPlan:
    task = SwarmTaskNode(task_id="t1", objective="ship the thing")
    task.state = TaskNodeState.RUNNING
    task.lease_id = "lease-from-the-dead-process"
    task.lease_owner = "worker:old"
    task.lease_expires_at = time.time() + 3600.0
    return SwarmPlan(
        swarm_id=swarm_id,
        goal="ship the thing",
        mode=SwarmMode.PARALLEL,
        tasks={"t1": task},
        status="running",
        owner_id=owner_id,
    )


@pytest.fixture()
def restarted_coordinator(tmp_path) -> SwarmCoordinator:
    """A coordinator that just came up over storage holding an in-flight plan."""
    first = SwarmCoordinator(storage_dir=tmp_path)
    plan = _inflight_plan()
    first._swarms[plan.swarm_id] = plan  # register directly; persistence below is real
    first.checkpoint(plan.swarm_id)
    return SwarmCoordinator(storage_dir=tmp_path)  # the "restart"


class TestRestartParking:
    def test_inflight_plan_is_parked_with_reason_and_fresh_lease_requirement(self, restarted_coordinator):
        plan = restarted_coordinator.get_swarm(SWARM_ID)
        assert plan is not None
        assert plan.status == "paused"
        assert plan.terminal_reason == recovery.RESTART_TERMINAL_REASON
        task = plan.tasks["t1"]
        assert task.state == TaskNodeState.PENDING
        assert task.lease_id is None and task.lease_owner is None and task.lease_expires_at is None
        assert "fresh lease" in (task.error_message or "")

    def test_recovery_event_is_journalled_and_the_park_is_idempotent(self, restarted_coordinator, tmp_path):
        events = [e.event_type for e in restarted_coordinator.get_events(SWARM_ID)]
        assert "SWARM_RECOVERED_AFTER_RESTART" in events
        third = SwarmCoordinator(storage_dir=tmp_path)  # another restart over the same store
        plan = third.get_swarm(SWARM_ID)
        assert plan is not None and plan.status == "paused"
        assert plan.terminal_reason == recovery.RESTART_TERMINAL_REASON


class TestScan:
    def test_scan_finds_only_restart_recovered_plans(self, restarted_coordinator):
        operator_paused = _inflight_plan(swarm_id="sw-operator")
        operator_paused.status = "paused"
        operator_paused.terminal_reason = None  # an operator pause, not a restart
        restarted_coordinator._swarms[operator_paused.swarm_id] = operator_paused
        found = recovery.scan_recoverable(restarted_coordinator)
        assert [p.swarm_id for p in found] == [SWARM_ID]

    def test_scan_is_owner_scoped_and_bounded(self, restarted_coordinator):
        other = _inflight_plan(swarm_id="sw-tenant", owner_id="tenant-x")
        other.status = "paused"
        other.terminal_reason = recovery.RESTART_TERMINAL_REASON
        restarted_coordinator._swarms[other.swarm_id] = other
        assert {p.swarm_id for p in recovery.scan_recoverable(restarted_coordinator)} == {SWARM_ID, "sw-tenant"}
        assert [p.swarm_id for p in recovery.scan_recoverable(restarted_coordinator, owner_id="default")] == [SWARM_ID]
        assert len(recovery.scan_recoverable(restarted_coordinator, limit=1)) == 1


class TestResume:
    def test_resume_returns_running_with_fresh_leases(self, restarted_coordinator):
        outcome = recovery.resume(SWARM_ID, coordinator=restarted_coordinator)
        assert outcome == {"swarm_id": SWARM_ID, "resumed": True, "status": "running", "reason": "resumed_after_restart"}
        plan = restarted_coordinator.get_swarm(SWARM_ID)
        assert plan.status == "running" and plan.terminal_reason is None
        assert plan.tasks["t1"].state == TaskNodeState.PENDING
        # A fresh scheduler claim succeeds and never reuses the dead lease id.
        lease = restarted_coordinator.claim_task(SWARM_ID, "t1", owner="worker:new")
        assert lease is not None and lease.lease_id != "lease-from-the-dead-process"
        events = [e.event_type for e in restarted_coordinator.get_events(SWARM_ID)]
        assert "SWARM_RECOVERY_RESUME" in events and "SWARM_RESUMED" in events

    def test_resume_refuses_everything_that_is_not_restart_recovered(self, restarted_coordinator):
        recovery.resume(SWARM_ID, coordinator=restarted_coordinator)
        again = recovery.resume(SWARM_ID, coordinator=restarted_coordinator)
        assert again["resumed"] is False and again["reason"] == "not_paused"
        missing = recovery.resume("sw-nope", coordinator=restarted_coordinator)
        assert missing == {"swarm_id": "sw-nope", "resumed": False, "status": "not_found", "reason": "not_found"}
        restarted_coordinator.pause_swarm(SWARM_ID)  # operator pause: paused, no restart reason
        paused = recovery.resume(SWARM_ID, coordinator=restarted_coordinator)
        assert paused["resumed"] is False and paused["reason"] == "not_restart_recovered"

    def test_resume_is_owner_scoped(self, restarted_coordinator):
        outcome = recovery.resume(SWARM_ID, coordinator=restarted_coordinator, owner_id="someone-else")
        assert outcome["resumed"] is False and outcome["reason"] == "not_found"


class TestAbandon:
    def test_abandon_cancels_with_a_named_reason(self, restarted_coordinator):
        outcome = recovery.abandon(SWARM_ID, "not worth resuming", coordinator=restarted_coordinator)
        assert outcome["abandoned"] is True and outcome["reason"] == "not worth resuming"
        plan = restarted_coordinator.get_swarm(SWARM_ID)
        assert plan.status == "cancelled" and plan.terminal_reason == "not worth resuming"
        assert plan.tasks["t1"].state == TaskNodeState.CANCELLED

    def test_abandon_defaults_the_reason_and_unknown_is_not_found(self, restarted_coordinator):
        outcome = recovery.abandon(SWARM_ID, coordinator=restarted_coordinator)
        assert outcome["reason"] == "abandoned_after_restart"
        assert restarted_coordinator.get_swarm(SWARM_ID).terminal_reason == "abandoned_after_restart"
        assert recovery.abandon("sw-nope", coordinator=restarted_coordinator)["status"] == "not_found"

    def test_abandon_refuses_a_plan_that_is_not_paused(self, restarted_coordinator):
        assert recovery.resume(SWARM_ID, coordinator=restarted_coordinator)["resumed"] is True
        refused = recovery.abandon(SWARM_ID, coordinator=restarted_coordinator)
        assert refused["abandoned"] is False and refused["reason"] == "not_paused"


class TestAutoResumeOnStart:
    def test_none_is_the_default_and_touches_nothing(self):
        report = recovery.auto_resume_on_start()
        assert report["mode"] == "none" and report["resumed"] == [] and report["scanned"] == 0
        assert "explicit resume" in report["note"]

    def test_owned_resumes_only_the_installation_owner(self, restarted_coordinator):
        other = _inflight_plan(swarm_id="sw-tenant", owner_id="tenant-x")
        other.status = "paused"
        other.terminal_reason = recovery.RESTART_TERMINAL_REASON
        restarted_coordinator._swarms[other.swarm_id] = other
        report = recovery.auto_resume_on_start("owned", coordinator=restarted_coordinator)
        assert report["resumed"] == [SWARM_ID] and report["skipped"] == ["sw-tenant"]
        assert restarted_coordinator.get_swarm(SWARM_ID).status == "running"
        assert restarted_coordinator.get_swarm("sw-tenant").status == "paused"

    def test_all_resumes_every_recovered_plan_and_reports_truncation(self, restarted_coordinator):
        other = _inflight_plan(swarm_id="sw-tenant", owner_id="tenant-x")
        other.status = "paused"
        other.terminal_reason = recovery.RESTART_TERMINAL_REASON
        restarted_coordinator._swarms[other.swarm_id] = other
        report = recovery.auto_resume_on_start("all", coordinator=restarted_coordinator)
        assert sorted(report["resumed"]) == [SWARM_ID, "sw-tenant"]
        assert report["scan_truncated"] is False
        assert restarted_coordinator.get_swarm("sw-tenant").status == "running"

    def test_an_unknown_mode_is_refused_loudly(self):
        with pytest.raises(ValueError, match="resume_on_start"):
            recovery.auto_resume_on_start("maybe")


class TestCoordinatorStatusFilter:
    def test_list_swarms_filters_by_status_without_dropping_owner_scope(self, restarted_coordinator):
        everything = restarted_coordinator.list_swarms(50)
        assert [p.swarm_id for p in everything] == [SWARM_ID]
        restarted_coordinator.resume_swarm(SWARM_ID)
        assert restarted_coordinator.list_swarms(50, status="paused") == []
        assert [p.swarm_id for p in restarted_coordinator.list_swarms(50, status="running")] == [SWARM_ID]


class TestListRoute:
    def test_status_query_filters_and_unknown_status_is_refused(self, restarted_coordinator, monkeypatch):
        from app.gateway.routers import swarms as swarms_router

        monkeypatch.setattr(swarms_router, "get_swarm_coordinator", lambda: restarted_coordinator)
        rows = asyncio.run(swarms_router.list_swarms(20, "paused", request=None))
        assert [row["swarm_id"] for row in rows] == [SWARM_ID]
        assert rows[0]["terminal_reason"] == recovery.RESTART_TERMINAL_REASON  # the banner's field survives the wire
        running = asyncio.run(swarms_router.list_swarms(20, "running", request=None))
        assert running == []
        with pytest.raises(Exception) as excinfo:
            asyncio.run(swarms_router.list_swarms(20, "bogus", request=None))
        assert getattr(excinfo.value, "status_code", None) == 422


class TestSwarmConfig:
    def test_resume_on_start_defaults_to_none(self):
        assert SwarmConfig().resume_on_start == "none"

    def test_an_unknown_mode_fails_config_load(self):
        with pytest.raises(ValidationError):
            SwarmConfig(resume_on_start="sometimes")
