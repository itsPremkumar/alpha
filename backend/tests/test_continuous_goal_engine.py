"""Unit and integration tests for Continuous Goal Engine, Runner, and Tools.

The legacy continuous-goal surface is retired: nothing dispatches these
records, so a stored row is never evidence that a step ran or that work was
verified. These tests pin both halves of that — the refusals, and the read
projection that keeps a stale `executing` row from reading as live work.
"""

import importlib
from pathlib import Path
from uuid import uuid4

from alpha.harness.continuous.models import LEGACY_MIGRATION_GUIDANCE, LEGACY_RETIREMENT_REASON
from alpha.harness.continuous.runner import ContinuousGoalRunner
from alpha.harness.continuous.store import GoalStore, describe_goal
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


def test_recording_a_goal_claims_no_executor_and_nothing_is_dispatched(tmp_path: Path):
    """`start_goal` records an objective. It must not imply a worker exists."""
    store = GoalStore(storage_path=tmp_path / "goals.json")
    runner = ContinuousGoalRunner(store=store)

    goal = runner.start_goal(title="Refactor the billing service", description="split the module")
    notes = " ".join(goal.strategy_notes)

    assert goal.status == "planning"
    assert goal.retired is False
    assert "No executor is dispatched" in notes
    assert LEGACY_MIGRATION_GUIDANCE in notes


def test_stepping_without_an_executor_refuses_and_moves_nothing(tmp_path: Path):
    store = GoalStore(storage_path=tmp_path / "goals.json")
    runner = ContinuousGoalRunner(store=store)
    goal = runner.start_goal(title="No executor probe")

    result = runner.step(goal.goal_id)

    assert result["status"] == "awaiting_executor"
    # `verified: False` is a claim; "never" would be the honest absence. The
    # refusal must not let a caller read `success`/`verified` as a measurement.
    assert result["success"] is False
    assert result["verified"] is False
    assert "no milestone was run or verified" in result["message"]
    assert result["migration"] == LEGACY_MIGRATION_GUIDANCE

    stored = store.get_goal(goal.goal_id)
    assert stored.iteration == 0
    assert all(m.status == "pending" for m in stored.milestones.values())


def test_a_record_claiming_verified_evidence_still_reads_as_never_verified(tmp_path: Path):
    """The load-bearing honesty pin.

    The store is a JSON file an earlier build could write: a row may carry
    `status="executing"` and a milestone stamped `verified` with canned
    evidence. The retirement is not cosmetic — the read projection has to
    refuse to repeat either claim, or a stale file resurrects a fake run.
    """
    store = GoalStore(storage_path=tmp_path / "goals.json")
    goal = store.create_goal(title="Stale record from an older build")
    milestone = store.add_milestone(goal.goal_id, "Implement core solution")
    store.update_goal_status(goal.goal_id, "executing", strategy_note="Worker claimed the goal.")
    store.update_milestone_status(goal.goal_id, milestone.milestone_id, "verified", evidence="All tests pass.")
    store.update_goal_status(goal.goal_id, "achieved", strategy_note="All milestones successfully verified.")

    view = describe_goal(store.get_goal(goal.goal_id))

    assert view["recorded_status"] == "achieved"
    assert view["status"] == "retired"
    assert view["verified"] == "never"
    assert view["retirement_reason"] == LEGACY_RETIREMENT_REASON
    assert view["migration"] == LEGACY_MIGRATION_GUIDANCE
    assert view["milestones"][0]["recorded_status"] == "verified"
    assert view["milestones"][0]["status"] == "unverified"


def test_goal_engine_refuses_every_mutating_action_and_still_answers_reads(monkeypatch, tmp_path: Path):
    goal_engine_module = importlib.import_module("alpha.tools.builtins.goal_engine_tool")
    store = GoalStore(storage_path=tmp_path / "goals.json")
    runner = ContinuousGoalRunner(store=store)
    monkeypatch.setattr(goal_engine_module, "get_goal_store", lambda: store)

    goal = runner.start_goal(title=f"Goal_{uuid4().hex[:6]}", description="Continuous test automation goal")

    for action in ("start", "step", "pause", "resume"):
        result = goal_engine_tool.invoke({"action": action, "title": "x", "goal_id": goal.goal_id})
        assert f"action='{action}' is retired" in result
        assert "Nothing was created, run, or recorded as progress." in result
        assert LEGACY_MIGRATION_GUIDANCE in result

    # A refused `start` must not have created anything.
    assert len(store.list_goals()) == 1
    # ...and a refused `resume` must not have written the `executing` claim.
    assert store.get_goal(goal.goal_id).status == "planning"

    listing = goal_engine_tool.invoke({"action": "list"})
    assert "Retired Goal Records (1 total)" in listing
    assert "verified: never" in listing
    assert LEGACY_RETIREMENT_REASON in listing

    detail = goal_engine_tool.invoke({"action": "inspect", "goal_id": goal.goal_id})
    assert "Verified: never" in detail
    assert "none executed" in detail
    assert "[UNVERIFIED]" in detail

    assert "required for 'inspect'" in goal_engine_tool.invoke({"action": "inspect"})
    assert "not found" in goal_engine_tool.invoke({"action": "inspect", "goal_id": "goal_missing"})


def test_goal_engine_caps_the_listing_and_reports_an_unreadable_store(monkeypatch, tmp_path: Path):
    goal_engine_module = importlib.import_module("alpha.tools.builtins.goal_engine_tool")
    store = GoalStore(storage_path=tmp_path / "goals.json")
    monkeypatch.setattr(goal_engine_module, "get_goal_store", lambda: store)
    for index in range(55):
        store.create_goal(title=f"Goal {index}")

    listing = goal_engine_tool.invoke({"action": "list"})
    assert "Retired Goal Records (55 total)" in listing
    assert "5 additional record(s) omitted" in listing

    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{not json", encoding="utf-8")
    corrupt = GoalStore(storage_path=corrupt_path)
    monkeypatch.setattr(goal_engine_module, "get_goal_store", lambda: corrupt)
    degraded = goal_engine_tool.invoke({"action": "list"})
    assert "Goal state is UNKNOWN" in degraded
    assert "this is not an empty catalog" in degraded


def test_retire_stamps_every_record_additively_and_survives_a_reload(tmp_path: Path):
    """Retirement is a label. Nothing may be deleted, and the stamp must land."""
    store_path = tmp_path / "goals.json"
    store = GoalStore(storage_path=store_path)
    runner = ContinuousGoalRunner(store=store)
    keep = runner.start_goal(title="Keep every field")
    runner.step(keep.goal_id, executor_fn=lambda ms: (True, "real evidence", ""))

    before = store.get_goal(keep.goal_id).to_dict()
    outcome = store.retire_all()

    assert outcome == {"retired": 1, "total": 1, "durable": True, "error": None}

    reloaded = GoalStore(storage_path=store_path).get_goal(keep.goal_id)
    after = reloaded.to_dict()
    # Every pre-existing field survives the stamp...
    for field in ("goal_id", "title", "description", "milestones", "strategy_notes", "iteration"):
        assert after[field] == before[field]
    # ...and the record was not deleted to achieve that.
    assert len(after["milestones"]) == len(before["milestones"]) == 3
    assert reloaded.retired is True
    assert reloaded.retired_reason == LEGACY_RETIREMENT_REASON
    assert reloaded.retired_at is not None

    # Idempotent: a second call has nothing left to stamp and still succeeds.
    assert store.retire_all() == {"retired": 0, "total": 1, "durable": True, "error": None}


def test_retire_refuses_on_an_unreadable_store_rather_than_rewriting_it(tmp_path: Path):
    """The one outcome that *would* destroy data is a rewrite from a partial view."""
    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text('{"goals": [{"goal_id"', encoding="utf-8")
    before = corrupt_path.read_bytes()

    corrupt = GoalStore(storage_path=corrupt_path)
    assert corrupt.is_degraded
    outcome = corrupt.retire_all()

    assert outcome["durable"] is False
    assert outcome["retired"] == 0
    assert "nothing was rewritten" in outcome["error"]
    assert corrupt_path.read_bytes() == before


def test_retire_reports_a_failed_persistence_instead_of_claiming_success(monkeypatch, tmp_path: Path):
    goal_engine_module = importlib.import_module("alpha.tools.builtins.goal_engine_tool")
    store_path = tmp_path / "goals.json"
    store = GoalStore(storage_path=store_path)
    ContinuousGoalRunner(store=store).start_goal(title="Persistence failure probe")
    monkeypatch.setattr(goal_engine_module, "get_goal_store", lambda: store)

    monkeypatch.setattr(store, "_save", lambda: setattr(store, "save_error", "disk full") or False)
    result = goal_engine_tool.invoke({"action": "retire"})

    assert "did NOT persist" in result
    assert "Records are unchanged on disk" in result


def test_canvas_and_trajectory_tools_are_unaffected_by_the_retirement(monkeypatch, tmp_path: Path):
    """The non-goal tools in this module must keep working: the retirement is scoped."""
    goal_engine_module = importlib.import_module("alpha.tools.builtins.goal_engine_tool")
    store = GoalStore(storage_path=tmp_path / "goals.json")
    runner = ContinuousGoalRunner(store=store)
    monkeypatch.setattr(goal_engine_module, "get_goal_store", lambda: store)
    goal = runner.start_goal(title="Canvas adjacency probe")

    res_canvas = canvas_widget_tool.invoke(
        {
            "action": "render",
            "title": "Continuous Harness Monitor",
            "kind": "dashboard",
            "data_json": '{"goal_id": "' + goal.goal_id + '", "health": "excellent"}',
        }
    )
    assert "Canvas Widget rendered" in res_canvas
    wid = res_canvas.split("Widget rendered: ")[1].split("\n")[0].strip()

    res_c_inspect = canvas_widget_tool.invoke({"action": "inspect", "widget_id": wid})
    assert "Continuous Harness Monitor" in res_c_inspect
    assert "excellent" in res_c_inspect

    res_traj = trajectory_audit_tool.invoke({"action": "inspect", "goal_id": goal.goal_id})
    assert "No steps found" in res_traj
