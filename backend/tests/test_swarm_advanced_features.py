"""Tests for the 7 Advanced Core Functional Swarm Engines.

Validates the background async runner, mid-flight dynamic DAG expansion,
tri-tier scoped memory, rate-limit adaptive governor, autonomous work triggers,
incident succession recovery, advanced tool actions, and Gateway endpoints.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent_workspace.swarm.coordinator as coord_mod
from agent_workspace.swarm.coordinator import SwarmCoordinator
from agent_workspace.swarm.governor import SwarmResourceGovernor
from agent_workspace.swarm.incidents import SwarmIncidentManager
from agent_workspace.swarm.memory import SwarmMemoryManager
from agent_workspace.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from agent_workspace.swarm.runner import AsyncSwarmRunner
from agent_workspace.swarm.triggers import AutonomousWorkTrigger


@pytest.fixture(autouse=True)
def _isolated_swarm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None


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
    from agent_workspace.tools.builtins.swarm_tool import swarm_tool

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

    swarm_dir = pathlib.Path(__file__).parent.parent / "packages" / "harness" / "agent_workspace" / "swarm"
    for py_file in swarm_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app." not in content, f"Boundary violation in {py_file}: contains 'from app.'"
        assert "import app." not in content, f"Boundary violation in {py_file}: contains 'import app.'"
