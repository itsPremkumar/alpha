"""Tests for alpha.graph.ledger — the SQLite-backed durable ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha.graph.ids import new_goal_id, new_task_id
from alpha.graph.ledger import LedgerConflict, LedgerError, TaskLedger, TaskLedgerConfig
from alpha.graph.models import (
    AcceptanceReport,
    Artifact,
    Goal,
    GoalMode,
    GoalStatus,
    TaskNode,
    TaskStatus,
    TaskType,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> TaskLedger:
    config = TaskLedgerConfig(database_path=tmp_path / "test.db")
    return TaskLedger(config)


@pytest.fixture()
def goal() -> Goal:
    return Goal.create(
        run_id="run-1",
        user_request="Test the API",
        normalized_objective="Test the API",
        mode=GoalMode.EXECUTE,
    )


@pytest.fixture()
def task(goal: Goal) -> TaskNode:
    return TaskNode.create(
        goal_id=goal.goal_id,
        title="Test endpoint",
        task_type=TaskType.TEST,
        acceptance=AcceptanceReport.from_checks(["test passes"]),
    )


class TestGoalCrud:
    def test_create_and_get_goal(self, ledger: TaskLedger, goal: Goal) -> None:
        ledger.create_goal(goal)
        loaded = ledger.get_goal(goal.goal_id)
        assert loaded is not None
        assert loaded.goal_id == goal.goal_id
        assert loaded.status is GoalStatus.PLANNING

    def test_get_missing_goal_returns_none(self, ledger: TaskLedger) -> None:
        assert ledger.get_goal(new_goal_id()) is None

    def test_create_duplicate_goal_raises(self, ledger: TaskLedger, goal: Goal) -> None:
        ledger.create_goal(goal)
        with pytest.raises(LedgerConflict):
            ledger.create_goal(goal)

    def test_update_goal(self, ledger: TaskLedger, goal: Goal) -> None:
        ledger.create_goal(goal)
        goal.transition_to(GoalStatus.RUNNING)
        ledger.update_goal(goal)
        loaded = ledger.get_goal(goal.goal_id)
        assert loaded is not None
        assert loaded.status is GoalStatus.RUNNING

    def test_update_missing_goal_raises(self, ledger: TaskLedger, goal: Goal) -> None:
        with pytest.raises(LedgerError):
            ledger.update_goal(goal)

    def test_list_goals_by_run(self, ledger: TaskLedger, goal: Goal) -> None:
        ledger.create_goal(goal)
        goals = ledger.list_goals(run_id="run-1")
        assert len(goals) == 1
        goals = ledger.list_goals(run_id="other")
        assert len(goals) == 0

    def test_list_goals_by_status(self, ledger: TaskLedger, goal: Goal) -> None:
        ledger.create_goal(goal)
        goals = ledger.list_goals(status=GoalStatus.PLANNING)
        assert len(goals) == 1
        goals = ledger.list_goals(status=GoalStatus.RUNNING)
        assert len(goals) == 0


class TestTaskCrud:
    def test_create_and_get_task(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.task_id == task.task_id
        assert loaded.status is TaskStatus.PENDING

    def test_get_missing_task_returns_none(self, ledger: TaskLedger) -> None:
        assert ledger.get_task(new_task_id()) is None

    def test_create_duplicate_task_raises(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        with pytest.raises(LedgerConflict):
            ledger.create_task(task)

    def test_list_tasks_by_goal(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        tasks = ledger.list_tasks(goal_id=goal.goal_id)
        assert len(tasks) == 1

    def test_list_tasks_by_status(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        tasks = ledger.list_tasks(status=TaskStatus.PENDING)
        assert len(tasks) == 1
        tasks = ledger.list_tasks(status=TaskStatus.RUNNING)
        assert len(tasks) == 0


class TestTaskTransitions:
    def test_transition_task(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        ledger.transition_task(task.task_id, TaskStatus.RUNNING)
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.RUNNING

    def test_transition_missing_task_raises(self, ledger: TaskLedger, task: TaskNode) -> None:
        with pytest.raises(LedgerError):
            ledger.transition_task(task.task_id, TaskStatus.RUNNING)

    def test_invalid_transition_raises_conflict(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        """``PENDING -> SUCCEEDED`` is legal (a task verified complete without an
        attempt); ``CANCELLED -> RUNNING`` is not, and is what a caller that
        skips the transition table actually hits."""
        ledger.create_goal(goal)
        ledger.create_task(task)
        ledger.transition_task(task.task_id, TaskStatus.CANCELLED)
        with pytest.raises(LedgerConflict):
            ledger.transition_task(task.task_id, TaskStatus.RUNNING)

    def test_begin_attempt_persists_the_counter(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        """The attempt counter must survive the write.

        The obvious composition -- ``task.begin_attempt()`` then
        ``transition_task(..., RUNNING)`` -- re-reads the task inside
        ``transition_task`` and writes back the stale ``attempt_count=0`` while
        the row says ``RUNNING``: an attempt in flight that the ledger claims
        never happened. ``ledger.begin_attempt`` folds both writes.
        """
        ledger.create_goal(goal)
        ledger.create_task(task)
        attempt, updated = ledger.begin_attempt(task.task_id)
        assert updated.attempt_count == 1
        assert updated.status is TaskStatus.RUNNING
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.attempt_count == 1
        assert loaded.status is TaskStatus.RUNNING
        persisted = ledger.get_attempt(attempt.attempt_id)
        assert persisted is not None
        assert persisted.attempt_number == 1

    def test_idempotent_transition(self, ledger: TaskLedger, goal: Goal, task: TaskNode) -> None:
        ledger.create_goal(goal)
        ledger.create_task(task)
        ledger.transition_task(task.task_id, TaskStatus.PENDING)
        loaded = ledger.get_task(task.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.PENDING


class TestArtifactCrud:
    def test_create_and_get_artifact(self, ledger: TaskLedger, goal: Goal) -> None:
        ledger.create_goal(goal)
        artifact = Artifact.create(
            run_id="run-1",
            artifact_type="test_report",
            uri="/tmp/test.txt",
            content_hash="abc123",
            goal_id=goal.goal_id,
        )
        ledger.create_artifact(artifact)
        loaded = ledger.get_artifact(artifact.artifact_id)
        assert loaded is not None
        assert loaded.artifact_id == artifact.artifact_id

    def test_list_artifacts_by_goal(self, ledger: TaskLedger, goal: Goal) -> None:
        ledger.create_goal(goal)
        artifact = Artifact.create(
            run_id="run-1",
            artifact_type="test_report",
            uri="/tmp/test.txt",
            content_hash="abc123",
            goal_id=goal.goal_id,
        )
        ledger.create_artifact(artifact)
        artifacts = ledger.list_artifacts(goal_id=goal.goal_id)
        assert len(artifacts) == 1


class TestEventCrud:
    def test_append_and_list_events(self, ledger: TaskLedger, goal: Goal) -> None:
        from alpha.graph.models import EventSeverity, EventType, RunEvent

        ledger.create_goal(goal)
        event = RunEvent.create(
            run_id="run-1",
            event_type=EventType.NODE_STARTED,
            severity=EventSeverity.INFO,
            goal_id=goal.goal_id,
        )
        ledger.append_event(event)
        events = ledger.list_events(goal_id=goal.goal_id)
        assert len(events) == 1
        assert events[0].event_id == event.event_id

    def test_list_events_by_type(self, ledger: TaskLedger, goal: Goal) -> None:
        from alpha.graph.models import EventType, RunEvent

        ledger.create_goal(goal)
        event = RunEvent.create(run_id="run-1", event_type=EventType.NODE_STARTED, goal_id=goal.goal_id)
        ledger.append_event(event)
        events = ledger.list_events(event_type=EventType.NODE_STARTED)
        assert len(events) == 1
        events = ledger.list_events(event_type=EventType.NODE_COMPLETED)
        assert len(events) == 0

    def test_duplicate_event_raises(self, ledger: TaskLedger, goal: Goal) -> None:
        from alpha.graph.models import EventType, RunEvent

        ledger.create_goal(goal)
        event = RunEvent.create(run_id="run-1", event_type=EventType.NODE_STARTED, goal_id=goal.goal_id)
        ledger.append_event(event)
        with pytest.raises(LedgerConflict):
            ledger.append_event(event)


class TestClaimCrud:
    def test_create_and_get_claim(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import ClaimStatus, EvidenceClaim

        claim = EvidenceClaim.create(
            subject_entity_id="entity-1",
            predicate="USES",
            claim_text="Alpha uses LangGraph",
        )
        ledger.create_claim(claim)
        loaded = ledger.get_claim(claim.claim_id)
        assert loaded is not None
        assert loaded.status is ClaimStatus.CANDIDATE

    def test_list_claims_by_subject(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import EvidenceClaim

        claim = EvidenceClaim.create(subject_entity_id="entity-1", predicate="USES", claim_text="text")
        ledger.create_claim(claim)
        claims = ledger.list_claims(subject_entity_id="entity-1")
        assert len(claims) == 1
        claims = ledger.list_claims(subject_entity_id="other")
        assert len(claims) == 0


class TestApprovalCrud:
    def test_create_and_get_approval(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import ApprovalRequest

        approval = ApprovalRequest.create(run_id="run-1", operation="delete", target="/tmp")
        ledger.create_approval(approval)
        loaded = ledger.get_approval(approval.approval_id)
        assert loaded is not None
        assert loaded.state.value == "pending"

    def test_list_approvals_by_state(self, ledger: TaskLedger) -> None:
        from alpha.graph.models import ApprovalRequest

        approval = ApprovalRequest.create(run_id="run-1", operation="delete", target="/tmp")
        ledger.create_approval(approval)
        approvals = ledger.list_approvals(state=approval.state)
        assert len(approvals) == 1


class TestLedgerClose:
    def test_close_allows_reopen(self, tmp_path: Path) -> None:
        config = TaskLedgerConfig(database_path=tmp_path / "test.db")
        ledger = TaskLedger(config)
        goal = Goal.create(run_id="run-1", user_request="test", normalized_objective="test", mode=GoalMode.EXECUTE)
        ledger.create_goal(goal)
        ledger.close()
        # Reopen
        ledger2 = TaskLedger(config)
        loaded = ledger2.get_goal(goal.goal_id)
        assert loaded is not None
