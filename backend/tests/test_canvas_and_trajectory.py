"""Unit tests for Canvas Manager and SQLite Trajectory Store."""

from pathlib import Path

from agent_workspace.canvas.manager import CanvasManager
from agent_workspace.trajectory.store import TrajectoryStore


def test_canvas_manager_lifecycle():
    manager = CanvasManager()

    # Create widget
    w = manager.create_widget(
        title="Active Goal Metrics",
        kind="dashboard",
        html_content="<div>Metrics Dashboard</div>",
        data={"milestones_completed": 3, "success_rate": 0.95},
    )
    assert w.title == "Active Goal Metrics"
    assert w.kind == "dashboard"
    assert w.data["milestones_completed"] == 3

    # Update widget
    updated = manager.update_widget(w.widget_id, data={"milestones_completed": 4})
    assert updated is not None
    assert updated.data["milestones_completed"] == 4

    # Standalone HTML rendering
    html = w.render_standalone_html()
    assert "<!DOCTYPE html>" in html
    assert "Active Goal Metrics" in html
    assert '"milestones_completed": 4' in html


def test_trajectory_store_sqlite(tmp_path: Path):
    db_file = tmp_path / "audit.db"
    store = TrajectoryStore(db_path=db_file)

    # Record step 1
    s1 = store.record_step(
        goal_id="g_alpha",
        step_index=1,
        thought="Inspecting repository files",
        tool_name="bash",
        tool_input={"command": "ls -la"},
        tool_output="file1.py\nfile2.py",
        milestone_id="m1",
        status="success",
    )
    assert s1.step_id is not None

    # Record step 2
    store.record_step(
        goal_id="g_alpha",
        step_index=2,
        thought="Running test suite",
        tool_name="bash",
        tool_input={"command": "pytest"},
        tool_output="3 passed",
        milestone_id="m2",
        status="success",
    )

    # Retrieve trajectory
    trace = store.get_trajectory("g_alpha")
    assert trace.total_steps == 2
    assert trace.steps[0].tool_name == "bash"
    assert trace.steps[1].tool_input == {"command": "pytest"}

    # Export to JSONL
    export_file = tmp_path / "trajectory.jsonl"
    path = store.export_jsonl("g_alpha", export_file)
    assert Path(path).exists()

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 2
    assert "Inspecting repository files" in lines[0]
