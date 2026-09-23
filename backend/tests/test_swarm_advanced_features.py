"""Tests for the 7 Advanced Core Functional Swarm Engines.

Validates the background async runner, mid-flight dynamic DAG expansion,
tri-tier scoped memory, rate-limit adaptive governor, autonomous work triggers,
incident succession recovery, advanced tool actions, and Gateway endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import alpha.swarm.coordinator as coord_mod
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.governor import SwarmResourceGovernor
from alpha.swarm.incidents import SwarmIncidentManager
from alpha.swarm.memory import SwarmMemoryManager
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.triggers import AutonomousWorkTrigger


@pytest.fixture(autouse=True)
def _isolated_swarm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None


@pytest.fixture(autouse=True)
def _offline_worker_models(monkeypatch):
    """Full-runner tests exercise dispatch/orchestration, not providers: the
    workers now make a REAL model call (Stage 3), so bind a deterministic
    offline model through the same `_resolve_model` seam the dedicated
    worker-execution tests use (test_swarm_worker_execution.py)."""
    import alpha.swarm.worker as worker_mod

    class _Message:
        def __init__(self, content):
            self.content = content

    class _OfflineModel:
        model_name = "offline-swarm-stub"

        def invoke(self, messages):
            return _Message(f"Offline deliverable for: {messages[-1].content}")

    monkeypatch.setattr(
        worker_mod, "_resolve_model", lambda model_name, *, worker_label: _OfflineModel()
    )
    yield


# 1. Autonomous Async Swarm Runner End-to-End
@pytest.mark.asyncio
async def test_async_swarm_runner_end_to_end(tmp_path):
    coord = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = coord.create_swarm(
        "Audit 3 microservices concurrently",
        mode=SwarmMode.MAP_REDUCE,
        items=["svc-auth", "svc-billing", "svc-notifications"],
    )
    sid = plan.swarm_id

    runner = AsyncSwarmRunner(coord, poll_interval=0.05)
    result = await runner.run_swarm_async(sid)

    assert result["status"] in ("completed", "partial_success")
    updated_plan = coord.get_swarm(sid)
    assert updated_plan is not None
    assert updated_plan.status in ("completed", "partial_success")
    assert updated_plan.completed_at is not None
    assert updated_plan.final_result is not None
    assert "Swarm Synthesis" in updated_plan.final_result

    # All map tasks and reduce task must be completed
    assert all(t.state == TaskNodeState.COMPLETED for t in updated_plan.tasks.values())


# 2. Mid-Flight Dynamic Replanning & DAG Expansion
def test_dynamic_replanning_and_expansion(tmp_path):
    coord = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = coord.create_swarm("Initial benchmark", mode=SwarmMode.MAP_REDUCE, items=["item-1"])
    sid = plan.swarm_id

    orig_tasks_count = len(plan.tasks)
    orig_critical_path = plan.critical_path_seconds

    # Mid-flight worker discovers an extra component
    added_ids = coord.dynamic_expand(
        swarm_id=sid,
        new_tasks=[
            {
                "task_id": "task-discovered-leak",
                "objective": "Investigate memory leak discovered in item-1",
            }
        ],
        parent_task_id="task-map-1",
    )

    assert len(added_ids) == 1
    assert "task-discovered-leak" in added_ids
    assert len(plan.tasks) == orig_tasks_count + 1

    leak_node = plan.tasks["task-discovered-leak"]
    assert "task-map-1" in leak_node.dependencies

    # Reduce task must wait for the discovered task
    reduce_node = plan.tasks["task-reduce"]
    assert "task-discovered-leak" in reduce_node.dependencies

    # Critical path updated
    assert plan.critical_path_seconds >= orig_critical_path


# 3. Tri-Tier Scoped Swarm Memory
def test_tri_tier_swarm_memory():
    mem = SwarmMemoryManager()

    # Tier 1: Ephemeral Worker Scratchpad
    mem.set_task_scratchpad("task-100", "temp_token", "xyz-123")
    assert mem.get_task_scratchpad("task-100")["temp_token"] == "xyz-123"
    mem.clear_task_scratchpad("task-100")
    assert mem.get_task_scratchpad("task-100") == {}

    # Tier 2: Swarm Blackboard
    mem.record_fact("swm-001", "db_port", 5432, confidence=0.99)
    mem.record_artifact("swm-001", "file:///tmp/report.pdf", "Final audit report")
    assert mem.get_facts("swm-001")["db_port"] == 5432
    assert len(mem.get_artifacts("swm-001")) == 1

    # Tier 3: Organization Memory Promotion
    failing_plan = SwarmPlan(
        swarm_id="swm-fail",
        goal="Low quality draft",
        mode=SwarmMode.PARALLEL,
        quality_score=0.4,
        final_result="Draft content",
    )
    assert mem.promote_to_org_memory("swm-fail", failing_plan, min_quality=0.8) is False

    passing_plan = SwarmPlan(
        swarm_id="swm-pass",
        goal="High quality release",
        mode=SwarmMode.PARALLEL,
        quality_score=0.95,
        final_result="Fully verified enterprise deliverable with comprehensive tests.",
    )
    assert mem.promote_to_org_memory("swm-pass", passing_plan, min_quality=0.8) is True


# 4. Heterogeneous Model Routing & Rate-Limit Adaptive Governor
def test_resource_governor_routing_and_throttling():
    gov = SwarmResourceGovernor()

    # Model tier resolution
    assert gov.resolve_model_for_role("Chief Architect") == gov.MODEL_TIERS["frontier"]
    assert gov.resolve_model_for_role("Batch Data Extractor", is_batch=True) == gov.MODEL_TIERS["local"]
    assert gov.resolve_model_for_role("QA Verifier") == gov.MODEL_TIERS["verifier"]
    assert gov.resolve_model_for_role("General Worker") == gov.MODEL_TIERS["fast"]

    # Concurrency without throttling
    assert gov.get_effective_concurrency(base_concurrency=8, provider="openai") == 8

    # Simulate rate-limit hit
    gov.record_rate_limit(provider="openai")
    throttled_concurrency = gov.get_effective_concurrency(base_concurrency=8, provider="openai")
    assert throttled_concurrency <= 4
    assert gov.get_backoff_delay(provider="openai") > 0.0

    status = gov.get_status()
    assert status["is_throttling_active"] is True
    assert "openai" in status["throttled_providers"]


# 5. Autonomous Work Discovery Trigger
def test_autonomous_work_trigger():
    # Large backlog -> auto-triggers swarm
    res = AutonomousWorkTrigger.evaluate_and_trigger_routine(
        bot_name="tester",
        routine_name="nightly_security_regression",
        goal="Run regression test across all 6 service modules",
        backlog_items=[f"mod-{i}" for i in range(6)],
    )
    assert res["triggered"] is True
    assert "swm-" in res["swarm_id"]
    assert res["tasks_count"] >= 7

    # Trivial workload -> does not trigger swarm
    trivial_res = AutonomousWorkTrigger.evaluate_and_trigger_routine(
        bot_name="tester",
        routine_name="check_ping",
        goal="Ping health endpoint",
        backlog_items=[],
    )
    assert trivial_res["triggered"] is False


# 6. Swarm Incident & Succession Recovery
def test_swarm_incident_and_succession_recovery():
    inc_mgr = SwarmIncidentManager()
    failed_task = SwarmTaskNode(
        task_id="task-critical-db",
        objective="Execute schema migration",
        assigned_worker="coder",
        worker_type="permanent_bot",
        attempts=2,
    )

    incident = inc_mgr.record_failure_and_recover(
        swarm_id="swm-incident-test",
        task=failed_task,
        error_message="Deadlock detected during foreign key creation",
    )

    assert incident.incident_id.startswith("inc-")
    assert incident.task_id == "task-critical-db"
    assert incident.resolved is True
    assert incident.assigned_successor is not None
    # Task reassigned and state reset for successor execution
    assert failed_task.assigned_worker == incident.assigned_successor
    assert failed_task.state == TaskNodeState.PENDING


# 7. Built-in Tool Advanced Actions
def test_swarm_tool_advanced_actions():
    from alpha.tools.builtins.swarm_tool import swarm_tool

    # Spawn swarm
    spawn_out = swarm_tool.invoke(
        {
            "action": "spawn",
            "goal": "Scan infrastructure components",
            "mode": "parallel",
            "items_json": '["network", "storage"]',
        }
    )
    import re

    sid = re.search(r"Swarm ID\*\*:\s*`([^`]+)`", spawn_out).group(1)

    # Expand action
    expand_out = swarm_tool.invoke(
        {
            "action": "expand",
            "swarm_id": sid,
            "tasks_json": '[{"task_id": "task-auth-scan", "objective": "Scan auth endpoints"}]',
        }
    )
    assert "dynamically expanded" in expand_out
    assert "task-auth-scan" in expand_out

    # Governor action
    gov_out = swarm_tool.invoke({"action": "governor"})
    assert "Swarm Resource Governor Status" in gov_out
    assert "frontier" in gov_out

    # Incidents action
    inc_out = swarm_tool.invoke({"action": "incidents", "swarm_id": sid})
    assert "No failure incidents" in inc_out or "Swarm Incidents" in inc_out


# 8. Gateway Advanced Endpoints
@pytest.mark.asyncio
async def test_gateway_swarms_advanced_endpoints():
    from app.gateway.routers import swarms

    admin_req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))

    # Create swarm
    create_resp = await swarms.create_and_spawn_swarm(
        swarms.SwarmCreateRequest(goal="Batch processing test", mode="map_reduce", items=["A", "B"]),
        admin_req,
    )
    sid = create_resp["swarm_id"]

    # Expand endpoint
    expand_resp = await swarms.expand_swarm(
        sid,
        swarms.SwarmExpandRequest(
            new_tasks=[{"task_id": "task-extra-gw", "objective": "Extra Gateway task"}],
            parent_task_id="task-map-1",
        ),
        admin_req,
    )
    assert "task-extra-gw" in expand_resp["added_task_ids"]

    # Incidents endpoint
    inc_resp = await swarms.get_swarm_incidents(sid)
    assert isinstance(inc_resp, list)

    # Memory endpoint
    mem_resp = await swarms.get_swarm_memory(sid)
    assert "facts" in mem_resp
    assert "artifacts" in mem_resp

    # Governor status endpoint
    gov_resp = await swarms.get_resource_governor_status()
    assert "model_tiers" in gov_resp

    # Run async endpoint
    async_resp = await swarms.run_swarm_background(sid, admin_req)
    assert async_resp["status"] == "started_async"


# 9. Harness Boundary Integrity
def test_swarm_advanced_harness_boundary():
    """Validates that all newly added swarm engines contain zero imports from app.*."""
    import pathlib

    swarm_dir = pathlib.Path(__file__).parent.parent / "packages" / "harness" / "alpha" / "swarm"
    for py_file in swarm_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app." not in content, f"Boundary violation in {py_file}: contains 'from app.'"
        assert "import app." not in content, f"Boundary violation in {py_file}: contains 'import app.'"


# 10. Regression: a failed dependency must not leave the swarm "running" forever
@pytest.mark.asyncio
async def test_swarm_stranded_dependency_reaches_terminal_status(tmp_path):
    """Before the fix, a PENDING task whose dependency FAILED could never become
    ready, so the runner hit its deadlock break, skipped the aggregation gate
    (``is_swarm_finished()`` was False) and returned ``status="running"`` —
    permanently. Every consumer keyed off terminal status (pause/cancel/expand
    guards) then treated a dead plan as live work."""
    coord = SwarmCoordinator(storage_dir=tmp_path / "swarms-strand")
    plan = SwarmPlan(
        swarm_id="swm-strand-regression",
        goal="Downstream work blocked by a dead upstream task",
        mode=SwarmMode.PARALLEL,
        status="running",
        tasks={
            "task-upstream": SwarmTaskNode(
                task_id="task-upstream",
                objective="Already failed",
                state=TaskNodeState.FAILED,
                error_message="boom",
            ),
            "task-downstream": SwarmTaskNode(
                task_id="task-downstream",
                objective="Never runnable",
                state=TaskNodeState.PENDING,
                dependencies=["task-upstream"],
            ),
        },
    )
    coord._swarms[plan.swarm_id] = plan

    import asyncio

    runner = AsyncSwarmRunner(coord, poll_interval=0.01)
    result = await asyncio.wait_for(runner.run_swarm_async(plan.swarm_id), timeout=10)

    assert result["status"] == "failed"
    assert plan.status == "failed"
    downstream = plan.tasks["task-downstream"]
    assert downstream.state == TaskNodeState.FAILED
    assert "unrunnable" in (downstream.error_message or "")
    assert downstream.completed_at is not None
    event_types = [e.event_type for e in coord.get_events(plan.swarm_id, limit=100)]
    assert "SWARM_STRANDED_TASKS_FAILED" in event_types
    assert "SWARM_FAILED" in event_types


# 11. Regression: pause must suspend dispatch, not orphan the runner
@pytest.mark.asyncio
async def test_swarm_pause_keeps_runner_alive_and_resume_continues(tmp_path, monkeypatch):
    """Before the fix the loop was ``while plan.status == "running"``, so a
    pause EXITED the background task. ``resume_swarm`` then reported
    ``resumed: True`` while nothing executed — the follow-up task stayed
    PENDING forever behind a completed gate."""
    import asyncio
    import threading

    import alpha.swarm.runner as runner_mod

    coord = SwarmCoordinator(storage_dir=tmp_path / "swarms-pause")
    plan = SwarmPlan(
        swarm_id="swm-pause-regression",
        goal="Two-stage pipeline",
        mode=SwarmMode.PARALLEL,
        status="running",
        tasks={
            "task-gate": SwarmTaskNode(task_id="task-gate", objective="Blocks", state=TaskNodeState.PENDING),
            "task-followup": SwarmTaskNode(
                task_id="task-followup",
                objective="Waits for gate",
                state=TaskNodeState.PENDING,
                dependencies=["task-gate"],
            ),
        },
    )
    coord._swarms[plan.swarm_id] = plan

    release = threading.Event()

    def blocking_execute(self, task_node, plan_arg):
        if task_node.task_id == "task-gate":
            release.wait(timeout=10)
        return {"summary": f"done {task_node.task_id}", "evidence": [{"source": "test", "confidence": 0.9}], "artifacts": []}

    monkeypatch.setattr(runner_mod.EphemeralSubagentWorker, "execute_task", blocking_execute)

    runner = AsyncSwarmRunner(coord, poll_interval=0.01)
    runner_task = asyncio.create_task(runner.run_swarm_async(plan.swarm_id))

    async def _wait_state(task_id: str, want) -> None:
        while plan.tasks[task_id].state != want:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_state("task-gate", TaskNodeState.RUNNING), timeout=5)
    assert coord.pause_swarm(plan.swarm_id) is True
    release.set()
    await asyncio.wait_for(_wait_state("task-gate", TaskNodeState.COMPLETED), timeout=5)
    await asyncio.sleep(0.2)  # several poll ticks while paused

    assert not runner_task.done(), "runner must stay alive while paused"
    assert plan.status == "paused"
    assert plan.tasks["task-followup"].state == TaskNodeState.PENDING

    assert coord.resume_swarm(plan.swarm_id) is True
    await asyncio.wait_for(runner_task, timeout=10)

    assert plan.tasks["task-followup"].state == TaskNodeState.COMPLETED
    assert plan.status in ("completed", "partial_success")


# 12. Regression: terminal task states must be immutable
def test_swarm_terminal_states_are_immutable():
    """A late worker finishing after a cancel, or a speculative backup failing
    after the original succeeded, must not resurrect or demote a terminal task."""
    from alpha.swarm.scheduler import SwarmScheduler

    plan = SwarmPlan(swarm_id="swm-immutable", goal="g", mode=SwarmMode.PARALLEL)
    sched = SwarmScheduler(plan)

    plan.tasks["t1"] = SwarmTaskNode(task_id="t1", objective="o", state=TaskNodeState.RUNNING)
    sched.mark_completed("t1", result_summary="first")
    assert plan.tasks["t1"].state == TaskNodeState.COMPLETED
    sched.mark_failed("t1", "late straggler failure")
    assert plan.tasks["t1"].state == TaskNodeState.COMPLETED, "late failure must not demote COMPLETED"

    plan.tasks["t2"] = SwarmTaskNode(task_id="t2", objective="o", state=TaskNodeState.CANCELLED)
    sched.mark_completed("t2", result_summary="late backup result")
    assert plan.tasks["t2"].state == TaskNodeState.CANCELLED, "late completion must not resurrect CANCELLED"

    plan.tasks["t3"] = SwarmTaskNode(task_id="t3", objective="o", state=TaskNodeState.RUNNING, attempts=0)
    sched.mark_failed("t3", "transient")
    assert plan.tasks["t3"].state == TaskNodeState.PENDING, "retryable failure still requeues"
    assert plan.tasks["t3"].error_message == "transient"

    plan.tasks["t4"] = SwarmTaskNode(task_id="t4", objective="o", state=TaskNodeState.RUNNING, attempts=1, max_attempts=1)
    sched.mark_failed("t4", "exhausted")
    assert plan.tasks["t4"].state == TaskNodeState.FAILED


# 13. Regression: background start must be idempotent per swarm
@pytest.mark.asyncio
async def test_swarm_background_start_is_idempotent(tmp_path):
    """Every ``start_async`` call builds a fresh runner, so before the fix each
    request spawned a SECOND loop over the same plan (duplicate dispatch and
    events on a double-click). The module-level registry now returns the live task."""
    import asyncio

    coord = SwarmCoordinator(storage_dir=tmp_path / "swarms-idem")
    plan = coord.create_swarm("Idempotent background start check", mode=SwarmMode.PARALLEL, items=["a", "b"])

    runner = AsyncSwarmRunner(coord, poll_interval=0.01)
    first = runner.start_background_swarm(plan.swarm_id)
    second = runner.start_background_swarm(plan.swarm_id)
    assert first is second, "second start must return the live task, not spawn a duplicate loop"

    await asyncio.wait_for(first, timeout=15)
    final = coord.get_swarm(plan.swarm_id)
    assert final is not None
    assert final.status in ("completed", "partial_success")
