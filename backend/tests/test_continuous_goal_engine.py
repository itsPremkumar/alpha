"""Unit and integration tests for Continuous Goal Engine, Runner, and Tools."""

import importlib
from pathlib import Path
from uuid import uuid4

from alpha.harness.continuous.runner import ContinuousGoalRunner
from alpha.harness.continuous.store import GoalStore
from alpha.tools.builtins.canvas_widget_tool import canvas_widget_tool
from alpha.tools.builtins.goal_engine_tool import goal_engine_tool
from alpha.tools.builtins.trajectory_audit_tool import trajectory_audit_tool
from alpha.trajectory.store import TrajectoryStore


def test_continuous_goal_runner_autonomous_lifecycle(tmp_path: Path):
    store = GoalStore(storage_path=tmp_path / "goals.json")
    traj = TrajectoryStore(db_path=tmp_path / "audit.db")

    runner = ContinuousGoalRunner(store=store)
    runner.trajectory = traj

    # 1. Start continuous goal
    goal = runner.start_goal(
        title="Deploy microservice cluster",
        description="Configure k8s manifests, build docker images, verify health",
    )
    assert goal.status == "planning"
    assert len(goal.milestones) == 3

    def successful_executor(ms):
        return True, f"Performed and verified: {ms.title}", ""

    # 2. Simulate milestone 1 execution
    res1 = runner.step(goal.goal_id, executor_fn=successful_executor)
    assert res1["status"] == "ok"
    assert res1["success"] is True

    # 3. Simulate failure on milestone 2 -> triggers self-healing & strategy adaptation
    fail_count = 0

    def failing_executor(ms):
        nonlocal fail_count
        if ms.title == "Implement core solution" and fail_count == 0:
            fail_count += 1
            return False, "", "Build failed: port 8080 collision"
        return True, "Port reassigned to 8081. Build succeeded.", ""

    # Failure attempt: does NOT give up, status pivots to 'adapting'
    res_fail = runner.step(goal.goal_id, executor_fn=failing_executor)
    assert res_fail["status"] == "ok"
    assert res_fail["success"] is False

    g_adapting = store.get_goal(goal.goal_id)
    assert g_adapting.status == "adapting"
    assert len(g_adapting.strategy_notes) >= 1
    assert "Pivoting strategy" in g_adapting.strategy_notes[-1]

    # Retry attempt with adapted strategy -> succeeds
    res_retry = runner.step(goal.goal_id, executor_fn=failing_executor)
    assert res_retry["success"] is True

    # 4. Milestone 3 execution -> goal complete!
    res3 = runner.step(goal.goal_id, executor_fn=failing_executor)
    assert res3["success"] is True

    # Check goal is achieved
    final_step = runner.step(goal.goal_id)
    assert final_step["status"] == "achieved"
    assert store.get_goal(goal.goal_id).status == "achieved"

    # Trajectory audit recorded all steps
    trace = traj.get_trajectory(goal.goal_id)
    assert trace.total_steps >= 4


def test_goal_engine_and_canvas_tools(monkeypatch, tmp_path: Path):
    goal_engine_module = importlib.import_module("alpha.tools.builtins.goal_engine_tool")
    store = GoalStore(storage_path=tmp_path / "goals.json")
    runner = ContinuousGoalRunner(store=store)
    monkeypatch.setattr(goal_engine_module, "get_goal_store", lambda: store)
    monkeypatch.setattr(goal_engine_module, "get_goal_runner", lambda: runner)

    # 1. Start goal via tool
    goal_title = f"Goal_{uuid4().hex[:6]}"
    res_start = goal_engine_tool.invoke(
        {
            "action": "start",
            "title": goal_title,
            "description": "Continuous test automation goal",
        }
    )
    assert "Continuous Autonomous Goal initialized" in res_start
    goal_id = res_start.split("Goal initialized: ")[1].split("\n")[0].strip()

    # 2. Inspect goal
    res_inspect = goal_engine_tool.invoke({"action": "inspect", "goal_id": goal_id})
    assert "=== Autonomous Goal:" in res_inspect
    assert "Milestones" in res_inspect

    # 3. Step goal
    res_step = goal_engine_tool.invoke({"action": "step", "goal_id": goal_id})
    assert "was not executed" in res_step
    assert "no milestone was run or verified" in res_step
    assert store.get_goal(goal_id).iteration == 0
    assert all(m.status == "pending" for m in store.get_goal(goal_id).milestones.values())

    # Without an executor the legacy runner must not invent work or evidence.
    result = runner.step(goal_id)
    assert result["status"] == "awaiting_executor"
    assert store.get_goal(goal_id).iteration == 0
    assert all(m.status == "pending" for m in store.get_goal(goal_id).milestones.values())

    # 4. Pause and resume
    res_pause = goal_engine_tool.invoke({"action": "pause", "goal_id": goal_id})
    assert "paused" in res_pause
    assert store.get_goal(goal_id).status == "paused"

    res_resume = goal_engine_tool.invoke({"action": "resume", "goal_id": goal_id})
    assert "executing" in res_resume
    assert store.get_goal(goal_id).status == "executing"

    # 5. Canvas widget tool
    res_canvas = canvas_widget_tool.invoke(
        {
            "action": "render",
            "title": "Continuous Harness Monitor",
            "kind": "dashboard",
            "data_json": '{"goal_id": "' + goal_id + '", "health": "excellent"}',
        }
    )
    assert "Canvas Widget rendered" in res_canvas
    wid = res_canvas.split("Widget rendered: ")[1].split("\n")[0].strip()

    res_c_inspect = canvas_widget_tool.invoke({"action": "inspect", "widget_id": wid})
    assert "Continuous Harness Monitor" in res_c_inspect
    assert "excellent" in res_c_inspect

    # 6. Trajectory audit tool
    res_traj = trajectory_audit_tool.invoke({"action": "inspect", "goal_id": goal_id})
    assert "No steps found" in res_traj


def test_goal_engine_pause_and_resume_report_persistence_failure(monkeypatch, tmp_path: Path):
    goal_engine_module = importlib.import_module("alpha.tools.builtins.goal_engine_tool")
    store_path = tmp_path / "goals.json"
    store = GoalStore(storage_path=store_path)
    runner = ContinuousGoalRunner(store=store)
    monkeypatch.setattr(goal_engine_module, "get_goal_store", lambda: store)
    monkeypatch.setattr(goal_engine_module, "get_goal_runner", lambda: runner)
    goal = runner.start_goal(title="Persistence failure probe")

    monkeypatch.setattr(
        store,
        "_save",
        lambda: setattr(store, "save_error", "disk full") or False,
    )
    failed_pause = goal_engine_tool.invoke({"action": "pause", "goal_id": goal.goal_id})
    assert "could NOT be durably paused" in failed_pause
    assert GoalStore(storage_path=store_path).get_goal(goal.goal_id).status == "planning"
