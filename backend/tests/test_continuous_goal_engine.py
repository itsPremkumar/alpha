"""Unit and integration tests for Continuous Goal Engine, Runner, and Tools."""

from pathlib import Path
from uuid import uuid4

from agent_workspace.harness.continuous.runner import ContinuousGoalRunner
from agent_workspace.harness.continuous.store import GoalStore
from agent_workspace.tools.builtins.canvas_widget_tool import canvas_widget_tool
from agent_workspace.tools.builtins.goal_engine_tool import goal_engine_tool
from agent_workspace.tools.builtins.trajectory_audit_tool import trajectory_audit_tool
from agent_workspace.trajectory.store import TrajectoryStore


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
    assert goal.status == "executing"
    assert len(goal.milestones) == 3

    # 2. Simulate milestone 1 execution
    res1 = runner.step(goal.goal_id)
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


def test_goal_engine_and_canvas_tools():
    # 1. Start goal via tool
    goal_title = f"Goal_{uuid4().hex[:6]}"
    res_start = goal_engine_tool.invoke({
        "action": "start",
        "title": goal_title,
        "description": "Continuous test automation goal",
    })
    assert "Continuous Autonomous Goal initialized" in res_start
    goal_id = res_start.split("Goal initialized: ")[1].split("\n")[0].strip()

    # 2. Inspect goal
    res_inspect = goal_engine_tool.invoke({"action": "inspect", "goal_id": goal_id})
    assert "=== Autonomous Goal:" in res_inspect
    assert "Milestones" in res_inspect

    # 3. Step goal
    res_step = goal_engine_tool.invoke({"action": "step", "goal_id": goal_id})
    assert "Step executed on" in res_step

    # 4. Pause and resume
    res_pause = goal_engine_tool.invoke({"action": "pause", "goal_id": goal_id})
    assert "paused" in res_pause

    res_resume = goal_engine_tool.invoke({"action": "resume", "goal_id": goal_id})
    assert "executing" in res_resume

    # 5. Canvas widget tool
    res_canvas = canvas_widget_tool.invoke({
        "action": "render",
        "title": "Continuous Harness Monitor",
        "kind": "dashboard",
        "data_json": '{"goal_id": "' + goal_id + '", "health": "excellent"}',
    })
    assert "Canvas Widget rendered" in res_canvas
    wid = res_canvas.split("Widget rendered: ")[1].split("\n")[0].strip()

    res_c_inspect = canvas_widget_tool.invoke({"action": "inspect", "widget_id": wid})
    assert "Continuous Harness Monitor" in res_c_inspect
    assert "excellent" in res_c_inspect

    # 6. Trajectory audit tool
    res_traj = trajectory_audit_tool.invoke({"action": "inspect", "goal_id": goal_id})
    assert "Execution Trajectory for Goal" in res_traj
