"""Tests for alpha.graph.models — the canonical data model."""

from __future__ import annotations

import pytest

from alpha.graph.models import (
    AcceptanceCheck,
    AcceptanceReport,
    ClaimStatus,
    EvidenceClaim,
    Goal,
    GoalMode,
    GoalStatus,
    GraphBudget,
    IllegalTransition,
    ModelValidationError,
    RiskLevel,
    TaskNode,
    TaskStatus,
    TaskType,
)


class TestGoal:
    def test_create_goal(self) -> None:
        goal = Goal.create(
            run_id="run-1",
            user_request="Fix the bug",
            normalized_objective="Reproduce and fix the API bug",
            mode=GoalMode.EXECUTE,
        )
        assert goal.status is GoalStatus.PLANNING
        assert goal.mode is GoalMode.EXECUTE
        assert goal.requires_evidence is True

    def test_answer_goal_does_not_require_evidence(self) -> None:
        goal = Goal.create(
            run_id="run-1",
            user_request="What is LangGraph?",
            normalized_objective="Explain LangGraph",
            mode=GoalMode.ANSWER,
        )
        assert goal.requires_evidence is False

    def test_goal_transition_valid(self) -> None:
        goal = Goal.create(run_id="run-1", user_request="test", normalized_objective="test", mode=GoalMode.EXECUTE)
        goal.transition_to(GoalStatus.RUNNING)
        assert goal.status is GoalStatus.RUNNING

    def test_goal_transition_invalid(self) -> None:
        goal = Goal.create(run_id="run-1", user_request="test", normalized_objective="test", mode=GoalMode.EXECUTE)
        with pytest.raises(IllegalTransition):
            goal.transition_to(GoalStatus.SUCCEEDED)

    def test_goal_cancel_is_sticky(self) -> None:
        goal = Goal.create(run_id="run-1", user_request="test", normalized_objective="test", mode=GoalMode.EXECUTE)
        goal.transition_to(GoalStatus.RUNNING)
        goal.request_cancel("user stopped")
        assert goal.status is GoalStatus.CANCELLED
        assert goal.cancel_requested is True
        with pytest.raises(IllegalTransition, match="cancellation"):
            goal.transition_to(GoalStatus.RUNNING)

    def test_goal_cancel_is_idempotent(self) -> None:
        goal = Goal.create(run_id="run-1", user_request="test", normalized_objective="test", mode=GoalMode.EXECUTE)
        goal.request_cancel("first")
        goal.request_cancel("second")
        assert goal.status is GoalStatus.CANCELLED


class TestGraphBudget:
    def test_valid_budget(self) -> None:
        budget = GraphBudget(max_model_calls=30, max_concurrent_tasks=2)
        assert budget.max_model_calls == 30
        assert budget.max_concurrent_tasks == 2

    def test_zero_concurrency_is_refused(self) -> None:
        with pytest.raises(ModelValidationError, match="refused"):
            GraphBudget(max_concurrent_tasks=0)

    def test_negative_attempts_is_refused(self) -> None:
        with pytest.raises(ModelValidationError, match="refused"):
            GraphBudget(max_task_attempts=-1)

    def test_none_means_unbounded(self) -> None:
        budget = GraphBudget(max_model_calls=None)
        assert budget.max_model_calls is None

    def test_from_mapping_ignores_unknown_keys(self) -> None:
        budget = GraphBudget.from_mapping({"max_model_calls": 10, "unknown_key": 999})
        assert budget.max_model_calls == 10


class TestTaskNode:
    def test_create_task(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Test the API", task_type=TaskType.TEST)
        assert task.status is TaskStatus.PENDING
        assert task.attempt_count == 0
        assert task.needs_approval is False

    def test_high_risk_task_needs_approval(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Delete data", risk_level=RiskLevel.HIGH)
        assert task.needs_approval is True

    def test_prohibited_task_does_not_need_approval(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Steal credentials", risk_level=RiskLevel.PROHIBITED)
        assert task.needs_approval is False

    def test_self_dependency_is_refused(self) -> None:
        with pytest.raises(ModelValidationError, match="self-dependency"):
            TaskNode.create(goal_id="goal-1", title="Self", depends_on=["task-1"], task_id="task-1")

    def test_mark_succeeded_requires_acceptance(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Test")
        with pytest.raises(ModelValidationError, match="no acceptance checks"):
            task.mark_succeeded()

    def test_mark_succeeded_requires_evaluated_checks(self) -> None:
        task = TaskNode.create(
            goal_id="goal-1",
            title="Test",
            acceptance=AcceptanceReport.from_checks(["test passes"]),
        )
        with pytest.raises(ModelValidationError, match="never evaluated"):
            task.mark_succeeded()

    def test_mark_succeeded_with_passing_checks(self) -> None:
        task = TaskNode.create(
            goal_id="goal-1",
            title="Test",
            acceptance=AcceptanceReport.from_checks(["test passes"]),
        )
        task.acceptance.criteria[0].evaluate(True, evidence="exit 0", evaluated_by="verify_task")
        task.mark_succeeded()
        assert task.status is TaskStatus.SUCCEEDED

    def test_mark_succeeded_with_failing_checks(self) -> None:
        task = TaskNode.create(
            goal_id="goal-1",
            title="Test",
            acceptance=AcceptanceReport.from_checks(["test passes"]),
        )
        task.acceptance.criteria[0].evaluate(False, evidence="exit 1", evaluated_by="verify_task")
        with pytest.raises(ModelValidationError, match="did not hold"):
            task.mark_succeeded()

    def test_begin_attempt_increments_count(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Test")
        attempt = task.begin_attempt()
        assert task.attempt_count == 1
        assert task.status is TaskStatus.RUNNING
        assert attempt.attempt_number == 1

    def test_begin_attempt_respects_ceiling(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Test", max_attempts=1)
        task.begin_attempt()
        with pytest.raises(IllegalTransition, match="ceiling"):
            task.begin_attempt()

    def test_mark_failed_sets_failure_summary(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Test")
        task.mark_failed(reason="tool crashed", error_code="TOOL_CRASHED")
        assert task.status is TaskStatus.FAILED
        assert task.failure_summary == "tool crashed"
        assert task.error_code == "TOOL_CRASHED"

    def test_mark_blocked_requires_reason(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Test")
        with pytest.raises(ModelValidationError, match="reason"):
            task.mark_blocked(reason="")

    def test_ready_to_dispatch_with_unsatisfied_dependency(self) -> None:
        task = TaskNode.create(goal_id="goal-1", title="Test", depends_on=["task-0"])
        ready, reason = task.ready_to_dispatch(dependency_status={"task-0": TaskStatus.PENDING})
        assert ready is False
        assert "unsatisfied" in reason


class TestAcceptanceReport:
    def test_empty_report_is_not_passed(self) -> None:
        report = AcceptanceReport()
        assert report.passed is False
        assert "no acceptance checks" in report.refusal_reason()

    def test_unevaluated_check_is_not_passed(self) -> None:
        report = AcceptanceReport.from_checks(["check1"])
        assert report.all_evaluated is False
        assert report.passed is False

    def test_failing_check_is_not_passed(self) -> None:
        report = AcceptanceReport.from_checks(["check1"])
        report.criteria[0].evaluate(False, evidence="failed", evaluated_by="test")
        assert report.all_evaluated is True
        assert report.all_hold is False
        assert report.passed is False

    def test_all_passing_checks_is_passed(self) -> None:
        report = AcceptanceReport.from_checks(["check1", "check2"])
        for check in report.criteria:
            check.evaluate(True, evidence="passed", evaluated_by="test")
        assert report.passed is True
        assert report.refusal_reason() is None

    def test_truthy_string_is_refused(self) -> None:
        check = AcceptanceCheck(check="test")
        with pytest.raises(ModelValidationError, match="real bool"):
            check.evaluate("true", evidence="yes", evaluated_by="test")


class TestEvidenceClaim:
    def test_create_claim_is_candidate(self) -> None:
        claim = EvidenceClaim.create(
            subject_entity_id="entity-1",
            predicate="USES_FRAMEWORK",
            claim_text="Alpha uses LangGraph",
        )
        assert claim.status is ClaimStatus.CANDIDATE
        assert claim.is_current is True

    def test_mark_verified_requires_locator(self) -> None:
        claim = EvidenceClaim.create(subject_entity_id="entity-1", predicate="USES", claim_text="text")
        with pytest.raises(ModelValidationError, match="source"):
            claim.mark_verified(source_locator="", verified_by="test")

    def test_mark_verified_with_locator(self) -> None:
        claim = EvidenceClaim.create(subject_entity_id="entity-1", predicate="USES", claim_text="text")
        claim.mark_verified(source_locator="file.py#L10", verified_by="test")
        assert claim.status is ClaimStatus.VERIFIED
        assert claim.is_current is True

    def test_mark_superseded_is_not_current(self) -> None:
        claim = EvidenceClaim.create(subject_entity_id="entity-1", predicate="USES", claim_text="text")
        claim.mark_superseded()
        assert claim.status is ClaimStatus.SUPERSEDED
        assert claim.is_current is False

    def test_confidence_must_be_in_range(self) -> None:
        with pytest.raises(ModelValidationError, match="outside"):
            EvidenceClaim.create(subject_entity_id="e", predicate="p", claim_text="t", confidence=1.5)

    def test_valid_to_before_valid_from_is_refused(self) -> None:
        with pytest.raises(ModelValidationError, match="precedes"):
            EvidenceClaim.create(
                subject_entity_id="e",
                predicate="p",
                claim_text="t",
                valid_from="2025-01-02",
                valid_to="2025-01-01",
            )


class TestTaskAttempt:
    def test_create_attempt(self) -> None:
        from alpha.graph.models import TaskAttempt

        attempt = TaskAttempt.create(task_id="task-1", goal_id="goal-1", attempt_number=1)
        assert attempt.attempt_number == 1
        assert attempt.idempotency_key == "task-1:a1"

    def test_complete_attempt(self) -> None:
        from alpha.graph.models import AttemptOutcome, TaskAttempt

        attempt = TaskAttempt.create(task_id="task-1", goal_id="goal-1", attempt_number=1)
        attempt.complete(AttemptOutcome.SUCCEEDED, reason="done")
        assert attempt.outcome is AttemptOutcome.SUCCEEDED
        assert attempt.finished_at is not None

    def test_cannot_recomplete_attempt(self) -> None:
        from alpha.graph.models import AttemptOutcome, TaskAttempt

        attempt = TaskAttempt.create(task_id="task-1", goal_id="goal-1", attempt_number=1)
        attempt.complete(AttemptOutcome.SUCCEEDED)
        with pytest.raises(IllegalTransition, match="already closed"):
            attempt.complete(AttemptOutcome.FAILED)


class TestApprovalRequest:
    def test_create_approval(self) -> None:
        from alpha.graph.models import ApprovalRequest

        approval = ApprovalRequest.create(
            run_id="run-1",
            operation="delete_file",
            target="/tmp/data",
        )
        assert approval.state.value == "pending"
        assert approval.payload_digest

    def test_decide_approve(self) -> None:
        from alpha.graph.models import ApprovalDecision, ApprovalRequest

        approval = ApprovalRequest.create(run_id="run-1", operation="delete", target="/tmp")
        approval.decide(ApprovalDecision.APPROVE, decided_by="admin")
        assert approval.is_approved is True

    def test_decide_reject(self) -> None:
        from alpha.graph.models import ApprovalDecision, ApprovalRequest

        approval = ApprovalRequest.create(run_id="run-1", operation="delete", target="/tmp")
        approval.decide(ApprovalDecision.REJECT, decided_by="admin")
        assert approval.is_approved is False
        assert approval.state.value == "rejected"

    def test_decide_twice_is_refused(self) -> None:
        from alpha.graph.models import ApprovalDecision, ApprovalRequest

        approval = ApprovalRequest.create(run_id="run-1", operation="delete", target="/tmp")
        approval.decide(ApprovalDecision.APPROVE, decided_by="admin")
        with pytest.raises(IllegalTransition, match="already"):
            approval.decide(ApprovalDecision.REJECT, decided_by="admin")

    def test_decide_with_changed_arguments_is_refused(self) -> None:
        from alpha.graph.models import ApprovalDecision, ApprovalRequest

        approval = ApprovalRequest.create(run_id="run-1", operation="delete", target="/tmp", arguments={"path": "/tmp"})
        with pytest.raises(IllegalTransition, match="arguments changed"):
            approval.decide(
                ApprovalDecision.APPROVE,
                decided_by="admin",
                operation_payload={"operation": "delete", "target": "/etc", "arguments": {"path": "/etc"}},
            )
