"""Tests for alpha.graph.nodes — node implementations.

Every node takes exactly one argument (the state), because that is what
LangGraph supplies. An earlier version of these tests called the nodes with
keyword arguments (``normalize_request(state, user_request=..., run_id=...)``),
which passed against the node's signature but raised ``TypeError`` the moment
the real graph ran. Building the state through
:func:`~alpha.graph.runtime.build_initial_state` here keeps the tests and the
graph calling the same code the same way.
"""

from __future__ import annotations

import pytest

from alpha.graph.ledger import LedgerConflict, TaskLedger, TaskLedgerConfig
from alpha.graph.models import Goal, GoalMode, GoalStatus, TaskNode, TaskStatus
from alpha.graph.nodes import (
    collect_context,
    dispatch_worker,
    final_verify,
    normalize_request,
    plan_goal,
    report_result,
    select_ready_tasks,
    validate_plan,
    verify_task,
)
from alpha.graph.runtime import build_initial_state
from alpha.graph.state import AlphaGraphState, GraphStatus


@pytest.fixture()
def ledger(tmp_path) -> TaskLedger:
    return TaskLedger(TaskLedgerConfig(database_path=tmp_path / "test.db"))


@pytest.fixture()
def goal() -> Goal:
    return Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)


@pytest.fixture()
def task(goal: Goal) -> TaskNode:
    """A task with one declared acceptance check, so the verify/succeed paths
    have something real to measure."""
    from alpha.graph.models import AcceptanceReport

    return TaskNode.create(
        goal_id=goal.goal_id,
        title="Task 1",
        acceptance=AcceptanceReport.from_checks(["test passes"]),
    )


def _state(ledger: TaskLedger, **overrides) -> AlphaGraphState:
    base = build_initial_state(user_request="Test the API", run_id="run-1")
    base["_ledger"] = ledger
    base.update(overrides)
    return base


class TestNormalizeRequest:
    def test_normalize_creates_goal(self, ledger: TaskLedger) -> None:
        result = normalize_request(_state(ledger))
        assert result.state_update["goal_id"]
        assert result.state_update["status"] is GraphStatus.ROUTING

    def test_normalize_refuses_empty_request(self, ledger: TaskLedger) -> None:
        with pytest.raises(Exception):
            normalize_request(_state(ledger, user_request=""))

    def test_normalize_refuses_missing_run_id(self, ledger: TaskLedger) -> None:
        with pytest.raises(Exception):
            normalize_request(_state(ledger, run_id=""))


class TestPlanGoal:
    def test_plan_creates_tasks(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        tasks = [
            {"title": "Task 1", "task_type": "code"},
            {"title": "Task 2", "task_type": "test", "depends_on": []},
        ]
        result = plan_goal(_state(ledger, goal_id=goal.goal_id, plan_tasks=tasks))
        assert len(result.state_update["task_ids"]) == 2

    def test_plan_refuses_empty_task_list(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        with pytest.raises(Exception):
            plan_goal(_state(ledger, goal_id=goal.goal_id, plan_tasks=[]))


class TestValidatePlan:
    def test_validate_accepts_valid_plan(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        task1 = TaskNode.create(goal_id=goal.goal_id, title="Task 1")
        task2 = TaskNode.create(goal_id=goal.goal_id, title="Task 2", depends_on=[task1.task_id])
        ledger.create_task(task1)
        ledger.create_task(task2)

        result = validate_plan(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["status"] is GraphStatus.SCHEDULING

    def test_validate_moves_goal_to_running(self, ledger: TaskLedger) -> None:
        """The goal's status must match what the graph is doing.

        ``final_verify`` and every node downstream read the goal's status;
        leaving it ``planning`` while the graph dispatches is a status that
        disagrees with reality.
        """
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        ledger.create_task(TaskNode.create(goal_id=goal.goal_id, title="Task 1"))

        validate_plan(_state(ledger, goal_id=goal.goal_id))
        loaded = ledger.get_goal(goal.goal_id)
        assert loaded is not None
        assert loaded.status is GoalStatus.RUNNING

    def test_validate_rejects_cycle(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        task1 = TaskNode.create(goal_id=goal.goal_id, title="Task 1", depends_on=["task-2"])
        task2 = TaskNode.create(goal_id=goal.goal_id, title="Task 2", depends_on=[task1.task_id])
        ledger.create_task(task1)
        ledger.create_task(task2)

        with pytest.raises(Exception):
            validate_plan(_state(ledger, goal_id=goal.goal_id))


class TestSelectReadyTasks:
    def test_selects_ready_tasks(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        ledger.create_task(TaskNode.create(goal_id=goal.goal_id, title="Task 1"))
        task2 = TaskNode.create(goal_id=goal.goal_id, title="Task 2", depends_on=["task_placeholder"])
        ledger.create_task(task2)

        result = select_ready_tasks(_state(ledger, goal_id=goal.goal_id))
        assert len(result.state_update["ready_task_ids"]) == 1

    def test_no_ready_tasks_reports_final_verify(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Task 1")
        ledger.create_task(task)
        ledger.transition_task(task.task_id, TaskStatus.RUNNING)

        result = select_ready_tasks(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["next_node"] == "final_verify"


class TestDispatchWorker:
    def test_dispatches_ready_task(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Task 1")
        ledger.create_task(task)

        result = dispatch_worker(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["status"] is GraphStatus.EXECUTING
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.RUNNING
        assert loaded.attempt_count == 1

    def test_high_risk_task_parks_for_approval(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import RiskLevel

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Delete data", risk_level=RiskLevel.HIGH)
        ledger.create_task(task)

        result = dispatch_worker(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["status"] is GraphStatus.WAITING_APPROVAL
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.WAITING_APPROVAL

    def test_unresolved_capability_blocks_task(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import CapabilityRequirement

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(
            goal_id=goal.goal_id,
            title="Needs browser",
            required_capabilities=[CapabilityRequirement(capability="browser_navigation")],
        )
        ledger.create_task(task)

        result = dispatch_worker(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["status"] is GraphStatus.SCHEDULING
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.BLOCKED


class TestCaptureArtifact:
    def test_capture_requires_uri_and_hash(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Task 1")
        ledger.create_task(task)

        with pytest.raises(Exception):
            from alpha.graph.nodes import capture_artifact

            capture_artifact(_state(ledger, goal_id=goal.goal_id, active_task_ids=[task.task_id], artifact_request={}))

    def test_capture_persists_artifact(self, ledger: TaskLedger) -> None:
        from alpha.graph.nodes import capture_artifact

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Task 1")
        ledger.create_task(task)

        capture_artifact(
            _state(
                ledger,
                goal_id=goal.goal_id,
                active_task_ids=[task.task_id],
                artifact_request={"uri": "/tmp/out.txt", "content_hash": "abc123", "artifact_type": "test_report"},
            )
        )
        artifacts = ledger.list_artifacts(goal_id=goal.goal_id)
        assert len(artifacts) == 1
        assert artifacts[0].uri == "/tmp/out.txt"


class TestVerifyTask:
    def test_verify_succeeds_with_passing_checks(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import AcceptanceReport

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(
            goal_id=goal.goal_id,
            title="Task 1",
            acceptance=AcceptanceReport.from_checks(["test passes"]),
        )
        ledger.create_task(task)

        checks = [{"check": "test passes", "verdict": True, "evidence": "exit 0"}]
        result = verify_task(_state(ledger, goal_id=goal.goal_id, active_task_ids=[task.task_id], verify_checks=checks))
        assert result.state_update["status"] is GraphStatus.SCHEDULING

    def test_verify_fails_with_failing_checks(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import AcceptanceReport

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(
            goal_id=goal.goal_id,
            title="Task 1",
            acceptance=AcceptanceReport.from_checks(["test passes"]),
        )
        ledger.create_task(task)

        checks = [{"check": "test passes", "verdict": False, "evidence": "exit 1"}]
        result = verify_task(_state(ledger, goal_id=goal.goal_id, active_task_ids=[task.task_id], verify_checks=checks))
        assert result.state_update["status"] is GraphStatus.REPAIRING

    def test_verify_refuses_truthy_string_verdict(self, ledger: TaskLedger) -> None:
        """``"true"`` is truthy in Python and must not read as a pass."""
        from alpha.graph.models import AcceptanceReport

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(
            goal_id=goal.goal_id,
            title="Task 1",
            acceptance=AcceptanceReport.from_checks(["test passes"]),
        )
        ledger.create_task(task)

        checks = [{"check": "test passes", "verdict": "true", "evidence": "said so"}]
        with pytest.raises(Exception):
            verify_task(_state(ledger, goal_id=goal.goal_id, active_task_ids=[task.task_id], verify_checks=checks))


class TestCollectContext:
    def test_collect_context_bounds_claims(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        result = collect_context(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["status"] is GraphStatus.PLANNING


class TestEvaluateAcceptance:
    def test_verdict_survives_the_write(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        """A measured verdict must be durable, not lost to a stale re-read."""
        ledger.create_goal(goal)
        ledger.create_task(task)
        reason = ledger.evaluate_acceptance(
            task.task_id,
            "test passes",
            True,
            evidence="exit 0",
            evaluated_by="verify_task",
        )
        assert reason is None
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.acceptance.passed is True

    def test_unknown_check_raises(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        with pytest.raises(LedgerConflict):
            ledger.evaluate_acceptance(task.task_id, "no such check", True, evidence="x", evaluated_by="y")

    def test_truthy_string_is_refused(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        with pytest.raises(Exception):
            ledger.evaluate_acceptance(task.task_id, "test passes", "true", evidence="x", evaluated_by="y")


class TestMarkTaskSucceeded:
    def test_succeed_persists_acceptance(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        ledger.evaluate_acceptance(task.task_id, "test passes", True, evidence="exit 0", evaluated_by="test")
        updated = ledger.mark_task_succeeded(task.task_id)
        assert updated.status is TaskStatus.SUCCEEDED
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.SUCCEEDED
        assert loaded.acceptance.passed is True

    def test_succeed_refuses_unverified(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        with pytest.raises(LedgerConflict):
            ledger.mark_task_succeeded(task.task_id)


class TestFinalVerify:
    def test_final_verify_reports_partial_without_tasks(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        result = final_verify(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["status"] is GraphStatus.REPORTING
        loaded = ledger.get_goal(goal.goal_id)
        assert loaded is not None
        assert loaded.status is GoalStatus.PARTIAL

    def test_final_verify_succeeds_when_all_tasks_verified(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import AcceptanceReport

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(
            goal_id=goal.goal_id,
            title="Task 1",
            acceptance=AcceptanceReport.from_checks(["done"]),
        )
        ledger.create_task(task)
        ledger.evaluate_acceptance(task.task_id, "done", True, evidence="ok", evaluated_by="test")
        ledger.mark_task_succeeded(task.task_id)

        final_verify(_state(ledger, goal_id=goal.goal_id))
        after = ledger.get_goal(goal.goal_id)
        assert after is not None
        assert after.status is GoalStatus.SUCCEEDED


class TestReportResult:
    def test_report_produces_summary(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        result = report_result(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["final_result"]
        assert result.stop is True

    def test_report_never_claims_success_when_empty(self, ledger: TaskLedger) -> None:
        """A goal with no tasks is not a succeeded goal."""
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        result = report_result(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["status"] is not GraphStatus.SUCCEEDED


class TestCancellation:
    def test_cancel_requested_refuses_every_node(self, ledger: TaskLedger) -> None:
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        ledger.create_task(TaskNode.create(goal_id=goal.goal_id, title="Task 1"))

        state = _state(ledger, goal_id=goal.goal_id, cancel_requested=True)
        with pytest.raises(Exception):
            normalize_request(state)
        with pytest.raises(Exception):
            collect_context(state)
        with pytest.raises(Exception):
            validate_plan(state)
