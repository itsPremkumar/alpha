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

from alpha.graph.ids import GoalId, TaskId
from alpha.graph.ledger import LedgerConflict, TaskLedger, TaskLedgerConfig
from alpha.graph.models import AcceptanceReport, EventType, Goal, GoalMode, GoalStatus, TaskNode, TaskStatus
from alpha.graph.nodes import (
    approval_gate,
    capture_artifact,
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

    def test_prohibited_risk_is_refused_not_parked(self, ledger: TaskLedger) -> None:
        """Plan §13.1: a Prohibited tier is *denied and recorded*, not parked.

        Parking it would offer an operator an approval nobody is allowed to
        give, which is worse than refusing it.
        """
        from alpha.graph.models import RiskLevel

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Drop the database", risk_level=RiskLevel.PROHIBITED)
        ledger.create_task(task)

        result = dispatch_worker(_state(ledger, goal_id=goal.goal_id))

        assert result.state_update["status"] is GraphStatus.SCHEDULING
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.BLOCKED
        assert "prohibited" in (loaded.failure_summary or "").lower()
        # No attempt, no approval record: it was refused, not offered.
        assert loaded.attempt_count == 0
        assert ledger.list_approvals(run_id="run-1") == []

    def test_parked_task_creates_a_reachable_approval(self, ledger: TaskLedger) -> None:
        """The approval gate used to be unreachable.

        ``dispatch_worker`` parked the task but never created an
        ``ApprovalRequest`` and never put its id in state, so ``approval_gate``
        always raised USER_APPROVAL_REQUIRED -- the graph could park a task and
        then fail instead of waiting for a human.
        """
        from alpha.graph.models import ApprovalDecision, RiskLevel

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Delete data", risk_level=RiskLevel.HIGH)
        ledger.create_task(task)

        state = _state(ledger, goal_id=goal.goal_id)
        result = dispatch_worker(state)
        approval_id = result.state_update.get("approval_request_id")

        assert approval_id, "a parked task must carry the approval id the gate reads"
        approval = ledger.get_approval(approval_id)
        assert approval is not None
        # Bound to the exact operation, not a blank grant.
        assert approval.payload_digest
        assert approval.task_id == task.task_id

        # Pending: the gate parks the run rather than self-approving.
        parked = approval_gate(_state(ledger, goal_id=goal.goal_id, approval_request_id=approval_id))
        assert parked.state_update["status"] is GraphStatus.WAITING_APPROVAL
        assert parked.state_update["next_node"] == "__end__"

        # Granted: the gate resumes into capture_artifact.
        ledger.decide_approval(approval_id, ApprovalDecision.APPROVE, decided_by="operator")
        resumed = approval_gate(_state(ledger, goal_id=goal.goal_id, approval_request_id=approval_id))
        assert resumed.state_update["status"] is GraphStatus.EXECUTING
        assert resumed.state_update["next_node"] == "capture_artifact"

    def test_approval_cannot_be_moved_by_a_changed_operation(self, ledger: TaskLedger) -> None:
        """A grant is bound to the payload it was shown, not to the task name.

        If the operation changes after the operator approves it, the decision is
        refused rather than silently extending the old grant to new work.
        """
        from alpha.graph.models import ApprovalDecision, RiskLevel

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Delete data", risk_level=RiskLevel.HIGH)
        ledger.create_task(task)

        result = dispatch_worker(_state(ledger, goal_id=goal.goal_id))
        approval_id = result.state_update["approval_request_id"]

        with pytest.raises(Exception):
            ledger.decide_approval(
                approval_id,
                ApprovalDecision.APPROVE,
                decided_by="operator",
                operation_payload={"target": "Delete everything instead"},
            )

        # Still pending, so the run stays parked rather than executing.
        reloaded = ledger.get_approval(approval_id)
        assert reloaded is not None
        assert reloaded.state.value == "pending"

    def test_declared_capability_gap_blocks_with_a_roster(self, ledger: TaskLedger) -> None:
        """A declared capability no specialist declares is a *named* gap."""
        from alpha.graph.models import CapabilityRequirement
        from alpha.swarm.team import SpecialistProfile, register_roster_provider, unregister_roster_provider

        register_roster_provider(lambda: [SpecialistProfile(name="coder", capabilities=["python"])], source="test")
        try:
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
            assert "capability gap" in (loaded.failure_summary or "")
        finally:
            unregister_roster_provider()

    def test_no_roster_does_not_block_dispatch(self, ledger: TaskLedger) -> None:
        """No specialists configured is not a team outage.

        Without a registered roster the specialist resolution is *disclosed*,
        not enforced -- otherwise a fresh install would block every task and
        read as a broken multi-agent system.
        """
        from alpha.swarm.team import unregister_roster_provider

        unregister_roster_provider()
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        task = TaskNode.create(goal_id=goal.goal_id, title="Ordinary work")
        ledger.create_task(task)

        result = dispatch_worker(_state(ledger, goal_id=goal.goal_id))

        assert result.state_update["status"] is GraphStatus.EXECUTING
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.RUNNING

    def test_specialist_assignment_lands_on_the_task(self, ledger: TaskLedger) -> None:
        """The swarm team composition's decision reaches the ledger."""
        from alpha.graph.models import CapabilityRequirement
        from alpha.swarm.team import SpecialistProfile, register_roster_provider, unregister_roster_provider

        register_roster_provider(lambda: [SpecialistProfile(name="qa-engineer", capabilities=["shell"])], source="test")
        try:
            goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
            ledger.create_goal(goal)
            task = TaskNode.create(
                goal_id=goal.goal_id,
                title="Run it",
                description="Run the request",
                required_capabilities=[CapabilityRequirement(capability="shell")],
            )
            ledger.create_task(task)

            dispatch_worker(_state(ledger, goal_id=goal.goal_id))

            loaded = ledger.get_task(task.task_id)
            assert loaded is not None
            assert loaded.assigned_worker == "qa-engineer"
        finally:
            unregister_roster_provider()


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


class TestDefectRegressions:
    """Regressions for the five defects found in the implementation audit.

    Each of these passed while the bug was live, because the buggy code was
    internally consistent -- the tests asserted what the code did, not what the
    plan required. They are pinned here so the same shape cannot come back.
    """

    def test_d1_repair_task_does_not_depend_on_the_failure_it_repairs(self, ledger: TaskLedger) -> None:
        """D1: a repair gated on a FAILED task can never become ready.

        ``SATISFIED_TASK_STATUSES`` is ``{SUCCEEDED}`` only, so ``depends_on``
        pointing at the failed original made the repair branch structurally
        unreachable: the graph routed repair -> select_ready -> final_verify and
        the repair sat in the ledger forever.
        """
        from alpha.graph.nodes import repair_task

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        failed = TaskNode.create(
            goal_id=goal.goal_id,
            title="Do the thing",
            max_attempts=2,
            acceptance=AcceptanceReport.from_checks(["verified"]),
        )
        ledger.create_task(failed)
        ledger.begin_attempt(failed.task_id)
        ledger.mark_task_failed(failed.task_id, reason="worker crashed", error_code="WORKER_CRASHED")

        result = repair_task(_state(ledger, goal_id=goal.goal_id, active_task_ids=[failed.task_id]))

        repair_id = result.state_update["task_ids"][0]
        repair = ledger.get_task(TaskId(repair_id))
        assert repair is not None
        assert repair.depends_on == [], "a repair must not be gated on the failure it repairs"

        from alpha.graph.dag import SCHEDULING_EDGE_TYPES, build_graph_id, ready_tasks
        from alpha.graph.models import EdgeType

        edges = ledger.list_edges(graph_id=build_graph_id(goal.goal_id))
        repairs = [e for e in edges if e.edge_type is EdgeType.REPAIRS]
        assert repairs and repairs[0].source_id == failed.task_id
        assert EdgeType.REPAIRS not in SCHEDULING_EDGE_TYPES

        ready = ready_tasks(ledger.list_tasks(goal_id=goal.goal_id), concurrency_cap=2)
        assert [t.task_id for t in ready] == [repair_id]

    def test_d2_nodes_record_events(self, ledger: TaskLedger) -> None:
        """D2: no node ever called ``append_event``, so ``events`` was write-never."""
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        normalize_request(_state(ledger))
        plan_goal(_state(ledger, goal_id=goal.goal_id, plan_tasks=[{"title": "Task 1", "task_type": "code"}]))

        events = ledger.list_events(run_id="run-1")
        assert events, "a run must leave events behind; without them nothing records what actually ran"
        types = {e.event_type for e in events}
        assert EventType.NODE_COMPLETED in types
        assert EventType.TASK_CREATED in types

    def test_d2_event_failure_never_kills_the_run(self, ledger: TaskLedger) -> None:
        """Telemetry degradation must not become task failure (plan section 19)."""
        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)

        def boom(event):
            raise RuntimeError("event store unavailable")

        original = ledger.append_event
        ledger.append_event = boom  # type: ignore[method-assign]
        try:
            result = normalize_request(_state(ledger))
            assert result.state_update["goal_id"]
        finally:
            ledger.append_event = original  # type: ignore[method-assign]

    def test_d3_plan_persists_dependency_edges(self, ledger: TaskLedger) -> None:
        """D3: ``create_edge`` was never called, so ``GraphEdge`` was dead code."""
        from alpha.graph.dag import build_graph_id
        from alpha.graph.models import EdgeType

        goal = Goal.create(run_id="run-1", user_request="t", normalized_objective="t", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        # A resolvable plan: task one first, task two depends on it.
        first = plan_goal(_state(ledger, goal_id=goal.goal_id, plan_tasks=[{"title": "Solo", "task_type": "code"}]))
        solo_id = first.state_update["task_ids"][0]
        plan_goal(
            _state(
                ledger,
                goal_id=goal.goal_id,
                plan_tasks=[{"title": "Second", "task_type": "test", "depends_on": [solo_id]}],
            )
        )

        edges = ledger.list_edges(graph_id=build_graph_id(goal.goal_id))
        depends = [e for e in edges if e.edge_type is EdgeType.DEPENDS_ON]
        assert depends, "a declared dependency must persist as a DEPENDS_ON edge"
        assert all(e.source_id != e.target_id for e in depends)

        result = validate_plan(_state(ledger, goal_id=goal.goal_id))
        assert result.state_update["next_node"] == "select_ready_tasks"

    def test_d3_repairs_edge_is_not_a_scheduling_prerequisite(self, ledger: TaskLedger) -> None:
        """A causal REPAIRS edge must not make the repair un-ready (D1 via D3)."""
        from alpha.graph.dag import build_graph_id, ready_tasks, validate_tasks
        from alpha.graph.models import EdgeType, GraphEdge

        goal_id = GoalId("g")
        a = TaskNode.create(goal_id=goal_id, title="A", acceptance=AcceptanceReport.from_checks(["ok"]))
        b = TaskNode.create(goal_id=goal_id, title="B", acceptance=AcceptanceReport.from_checks(["ok"]))
        edges = [
            GraphEdge(
                edge_id="edge_repair",
                graph_id=build_graph_id(goal_id),
                source_id=a.task_id,
                target_id=b.task_id,
                edge_type=EdgeType.REPAIRS,
                reason="causal, not scheduling",
            )
        ]
        report = validate_tasks([a, b], goal_id=goal_id, edges=edges)
        assert report.unknown_dependencies == ()
        ready = ready_tasks([a, b], concurrency_cap=2)
        assert len(ready) == 2

    def test_d4_lead_agent_adapter_never_reports_success(self) -> None:
        """D4: the adapter returned ``succeeded`` for a task it never ran."""
        import asyncio

        from alpha.graph.adapter import LeadAgentAdapter
        from alpha.graph.ids import GoalId

        adapter = LeadAgentAdapter(agent=object())
        task = TaskNode.create(goal_id=GoalId("g"), title="Do research")
        result = asyncio.run(adapter.execute_task(task))
        assert result["outcome"] != "succeeded", "an unbound worker must not claim success"
        assert result["reason"]

    def test_d5_ledger_is_not_written_into_checkpointed_state(self, tmp_path) -> None:
        """D5: the ledger rode in state, so a checkpointer would serialise a store."""
        from alpha.graph.ledger import TaskLedger, TaskLedgerConfig
        from alpha.graph.runtime import GraphRuntime, build_initial_state

        ledger = TaskLedger(TaskLedgerConfig(database_path=tmp_path / "rt.db"))
        captured = {}

        class SpyGraph:
            def invoke(self, state, config=None):
                captured["state"] = state
                captured["config"] = config
                return state

        runtime = GraphRuntime(SpyGraph(), ledger)
        state = build_initial_state(user_request="t", run_id="run-1")
        runtime.invoke(state)

        assert "_ledger" not in captured["state"], "a live store must never enter checkpointed state"
        assert captured["config"]["configurable"]["_ledger"] is ledger
