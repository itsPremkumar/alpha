"""Comprehensive test suite for the Autonomous Agent Swarm Subsystem.

Validates the mathematical benefit estimator, DAG decomposer, critical-path analysis,
topological scheduler, speculative straggler watchdog, conflict-aware aggregator,
coordinator checkpointing, built-in swarm tool, Gateway REST endpoints, and architectural boundaries.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent_workspace.swarm.coordinator as coord_mod
from agent_workspace.swarm.aggregator import SwarmAggregator
from agent_workspace.swarm.coordinator import SwarmCoordinator
from agent_workspace.swarm.decomposer import SwarmTaskDecomposer
from agent_workspace.swarm.estimator import SwarmBenefitEstimator
from agent_workspace.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from agent_workspace.swarm.scheduler import SwarmScheduler
from agent_workspace.swarm.watchdog import SwarmWatchdog


@pytest.fixture(autouse=True)
def _isolated_swarm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    coord_mod._GLOBAL_COORDINATOR = None
    yield
    coord_mod._GLOBAL_COORDINATOR = None


# 1. Swarm Benefit Estimator
def test_swarm_benefit_estimator_decision():
    # Single tiny task -> should NOT swarm
    tiny_dec = SwarmBenefitEstimator.estimate("Fix a small typo in README.md")
    assert tiny_dec.should_swarm is False
    assert tiny_dec.estimated_speedup == 1.0
    assert tiny_dec.recommended_workers == 1

    # Batch workload -> SHOULD swarm with Map-Reduce
    items = [f"https://example.com/item-{i}" for i in range(10)]
    batch_dec = SwarmBenefitEstimator.estimate("Extract pricing data from all target URLs", items=items)
    assert batch_dec.should_swarm is True
    assert batch_dec.mode == SwarmMode.MAP_REDUCE
    assert batch_dec.recommended_workers >= 8
    assert batch_dec.estimated_speedup > 2.0

    # Multi-component coding task -> SHOULD swarm with Coding Worktree
    code_dec = SwarmBenefitEstimator.estimate("Implement frontend and backend architecture for user auth", is_code=True)
    assert code_dec.should_swarm is True
    assert code_dec.mode == SwarmMode.CODING_WORKTREE
    assert code_dec.estimated_speedup > 1.8

    # Debate inquiry -> SHOULD swarm with Debate
    debate_dec = SwarmBenefitEstimator.estimate("Debate the pros and cons of SQLite vs PostgreSQL for local-first apps")
    assert debate_dec.should_swarm is True
    assert debate_dec.mode == SwarmMode.DEBATE


# 2. Dynamic DAG Decomposer & Critical Path
def test_swarm_decomposer_dag_and_critical_path():
    # Map-Reduce decomposition
    items = ["company-A", "company-B", "company-C", "company-D"]
    plan = SwarmTaskDecomposer.decompose("Analyze companies", mode=SwarmMode.MAP_REDUCE, items=items)
    assert len(plan.tasks) == 5  # 4 map + 1 reduce
    assert "task-reduce" in plan.tasks
    assert len(plan.tasks["task-reduce"].dependencies) == 4
    assert plan.estimated_speedup > 1.2
    assert plan.critical_path_seconds > 0

    # Coding worktree decomposition
    code_plan = SwarmTaskDecomposer.decompose("Build authentication suite", mode=SwarmMode.CODING_WORKTREE)
    assert "task-spec" in code_plan.tasks
    assert "task-backend" in code_plan.tasks
    assert "task-frontend" in code_plan.tasks
    assert "task-test" in code_plan.tasks
    assert "task-integrate" in code_plan.tasks
    assert code_plan.tasks["task-backend"].dependencies == ["task-spec"]
    assert "task-backend" in code_plan.tasks["task-test"].dependencies
    assert "task-frontend" in code_plan.tasks["task-test"].dependencies


# 3. Topological Scheduler & Concurrency
def test_swarm_scheduler_dependency_and_dispatch():
    items = ["item-1", "item-2"]
    plan = SwarmTaskDecomposer.decompose("Process items", mode=SwarmMode.MAP_REDUCE, items=items, max_concurrency=4)
    scheduler = SwarmScheduler(plan)

    # Initial ready tasks: only the map tasks (deps empty)
    ready = scheduler.get_ready_tasks()
    ready_ids = [t.task_id for t in ready]
    assert "task-map-1" in ready_ids
    assert "task-map-2" in ready_ids
    assert "task-reduce" not in ready_ids

    # Dispatch ready tasks
    dispatched = scheduler.dispatch_ready_tasks()
    assert len(dispatched) == 2
    assert all(t.state == TaskNodeState.RUNNING for t in dispatched)

    # Mark map tasks as complete
    scheduler.mark_completed("task-map-1", "Completed item 1 data")
    assert not scheduler.is_swarm_finished()

    # Reduce still not ready because map-2 is running
    assert len(scheduler.get_ready_tasks()) == 0

    # Mark map-2 complete -> reduce becomes ready!
    scheduler.mark_completed("task-map-2", "Completed item 2 data")
    ready_after = scheduler.get_ready_tasks()
    assert len(ready_after) == 1
    assert ready_after[0].task_id == "task-reduce"

    # Dispatch and complete reduce
    scheduler.dispatch_ready_tasks()
    scheduler.mark_completed("task-reduce", "Consolidated report synthesized")
    assert scheduler.is_swarm_finished()


# 4. Watchdog & Speculative Straggler Backup
def test_swarm_watchdog_straggler_and_speculative_backup():
    plan = SwarmPlan(
        swarm_id="swm-test",
        goal="Benchmarking",
        mode=SwarmMode.PARALLEL,
        tasks={
            "task-fast-1": SwarmTaskNode(
                task_id="task-fast-1",
                objective="Fast task 1",
                state=TaskNodeState.COMPLETED,
                duration_seconds=5.0,
            ),
            "task-fast-2": SwarmTaskNode(
                task_id="task-fast-2",
                objective="Fast task 2",
                state=TaskNodeState.COMPLETED,
                duration_seconds=5.0,
            ),
            "task-slow": SwarmTaskNode(
                task_id="task-slow",
                objective="Slow hanging task",
                state=TaskNodeState.RUNNING,
                started_at=100.0,  # simulate long run
            ),
        },
    )

    with patch("time.time", return_value=150.0):  # 50s elapsed vs 5s avg
        report = SwarmWatchdog.check_and_reconcile(plan)

    assert "task-slow" in report["stragglers"]
    assert "task-slow" in report["speculative_backups_spawned"]
    assert plan.tasks["task-slow"].backup_worker_launched is True
    assert plan.tasks["task-slow"].state == TaskNodeState.STRAGGLING


# 5. Conflict-Aware Aggregator & Quality Gate
def test_swarm_aggregator_conflict_and_deduplication():
    plan = SwarmPlan(
        swarm_id="swm-debate",
        goal="Audit vendor security claims",
        mode=SwarmMode.DEBATE,
        tasks={
            "task-1": SwarmTaskNode(
                task_id="task-1",
                objective="Audit claims",
                state=TaskNodeState.COMPLETED,
                result_summary="Verified and supported: Encryption in transit is active and passing.",
            ),
            "task-2": SwarmTaskNode(
                task_id="task-2",
                objective="Penetration test",
                state=TaskNodeState.COMPLETED,
                result_summary="Failed and unviable: Unencrypted plaintext socket detected on port 8080.",
            ),
            "task-3": SwarmTaskNode(
                task_id="task-3",
                objective="Audit claims duplicate",
                state=TaskNodeState.COMPLETED,
                result_summary="Verified and supported: Encryption in transit is active and passing.",  # duplicate
            ),
        },
    )

    result = SwarmAggregator.aggregate(plan)
    assert result["completed_tasks"] == 3
    assert result["conflicts_detected"] >= 1
    assert "Reconciled Contradictions" in result["deliverable"]
    assert plan.status in ("completed", "partial_success")
    assert plan.quality_score > 0.0


# 6. Master Coordinator Lifecycle & Checkpointing
def test_swarm_coordinator_lifecycle_and_persistence(tmp_path):
    coord = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = coord.create_swarm("Process dataset batch", mode=SwarmMode.MAP_REDUCE, items=["d1", "d2"])
    sid = plan.swarm_id

    # Verify persisted checkpoint
    checkpoint_file = tmp_path / "swarms" / f"{sid}.json"
    assert checkpoint_file.exists()

    # Step execution
    res1 = coord.step(sid)
    assert len(res1["dispatched"]) == 2

    # Pause and resume
    assert coord.pause_swarm(sid) is True
    assert coord.get_swarm(sid).status == "paused"
    assert coord.resume_swarm(sid) is True
    assert coord.get_swarm(sid).status == "running"

    # Events recorded
    events = coord.get_events(sid)
    assert any(e.event_type == "SWARM_CREATED" for e in events)
    assert any(e.event_type == "SWARM_PAUSED" for e in events)


# 7. Built-in Agent Tool Actions
def test_swarm_tool_actions():
    from agent_workspace.tools.builtins.swarm_tool import swarm_tool

    # 1. Evaluate
    eval_res = swarm_tool.invoke(
        {
            "action": "evaluate",
            "goal": "Scan 50 open source repositories for vulnerabilities",
        }
    )
    assert "Swarm Parallelization Feasibility Analysis" in eval_res
    assert "**Should Swarm**: `YES`" in eval_res

    # 2. Spawn
    spawn_res = swarm_tool.invoke(
        {
            "action": "spawn",
            "goal": "Compare React vs Vue for enterprise dashboard",
            "mode": "debate",
        }
    )
    assert "Autonomous Swarm Successfully Spawned" in spawn_res
    assert "task-pro" in spawn_res
    assert "task-con" in spawn_res

    # Extract swarm ID
    import re

    match = re.search(r"Swarm ID\*\*:\s*`([^`]+)`", spawn_res)
    assert match is not None
    sid = match.group(1)

    # 3. Status
    stat_res = swarm_tool.invoke({"action": "status", "swarm_id": sid})
    assert f"Swarm Status: `{sid}`" in stat_res

    # 4. Step
    step_res = swarm_tool.invoke({"action": "step", "swarm_id": sid})
    assert "dispatched" in step_res

    # 5. Pause & Resume
    pause_res = swarm_tool.invoke({"action": "pause", "swarm_id": sid})
    assert "paused: True" in pause_res
    resume_res = swarm_tool.invoke({"action": "resume", "swarm_id": sid})
    assert "resumed: True" in resume_res

    # 6. Cancel
    cancel_res = swarm_tool.invoke({"action": "cancel", "swarm_id": sid, "reason": "User requested abort"})
    assert "cancelled: True" in cancel_res


# 8. Gateway REST Endpoints
@pytest.mark.asyncio
async def test_gateway_swarms_router():
    from app.gateway.routers import swarms

    admin_req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))

    # 1. Evaluate endpoint
    eval_resp = await swarms.evaluate_swarm_feasibility(swarms.SwarmEvaluateRequest(goal="Benchmark 10 database engines", items=[f"db-{i}" for i in range(10)]))
    assert eval_resp["should_swarm"] is True
    assert eval_resp["estimated_speedup"] > 1.5

    # 2. Create endpoint
    create_resp = await swarms.create_and_spawn_swarm(
        swarms.SwarmCreateRequest(goal="Batch report generation", mode="map_reduce", items=["A", "B"]),
        admin_req,
    )
    sid = create_resp["swarm_id"]
    assert sid.startswith("swm-")

    # 3. Get details endpoint
    detail_resp = await swarms.get_swarm_details(sid)
    assert detail_resp["goal"] == "Batch report generation"
    assert len(detail_resp["tasks"]) == 3

    # 4. Step endpoint
    step_resp = await swarms.step_swarm(sid, admin_req)
    assert len(step_resp["dispatched"]) == 2

    # 5. Complete individual task
    comp_resp = await swarms.complete_swarm_task(
        sid,
        "task-map-1",
        swarms.SwarmTaskCompleteRequest(result_summary="Report for A completed cleanly."),
        admin_req,
    )
    assert comp_resp["state"] == "completed"

    # 6. Events endpoint
    events_resp = await swarms.get_swarm_events(sid)
    assert len(events_resp) >= 2


# 9. Harness Boundary Integrity
def test_swarm_harness_boundary_integrity():
    """Confirms packages/harness/agent_workspace/swarm contains zero forbidden imports from app.*."""
    import pathlib

    swarm_dir = pathlib.Path(__file__).parent.parent / "packages" / "harness" / "deerflow" / "swarm"
    for py_file in swarm_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "from app." not in content, f"Boundary violation in {py_file}: contains 'from app.'"
        assert "import app." not in content, f"Boundary violation in {py_file}: contains 'import app.'"
