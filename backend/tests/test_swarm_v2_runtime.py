"""Regression tests for the additive swarm v2 contracts."""

from __future__ import annotations

import pytest

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.communication import SwarmMessageBus, SwarmMessageValidationError
from alpha.swarm.consensus import ConsensusPolicy, evaluate_consensus
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.models import SwarmBudget, SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.runner import AsyncSwarmRunner
from alpha.swarm.scheduler import SwarmPlanValidationError, SwarmScheduler


def test_budget_records_only_measured_usage_and_fails_closed():
    budget = SwarmBudget(max_tokens=3, max_tool_calls=1)
    first = budget.record_usage(input_tokens=2, output_tokens=0, tool_calls=1)
    assert first["used_tokens"] == 2
    assert first["exhausted"] is True
    assert budget.exhausted_reason in {"token_budget_exhausted", "tool_call_budget_exhausted"}
    assert budget.to_dict()["used_tokens"] == 2


def test_message_bus_is_bounded_idempotent_and_serializable():
    bus = SwarmMessageBus("swm-bus", max_messages=2, max_content_chars=20)
    first = bus.publish(topic="results", sender="worker-a", content="x" * 100, idempotency_key="same")
    duplicate = bus.publish(topic="results", sender="worker-a", content="different", idempotency_key="same")
    assert duplicate.message_id == first.message_id
    bus.publish(topic="notes", sender="worker-b", content="second")
    bus.publish(topic="notes", sender="worker-b", content="third")
    messages = bus.read()
    assert len(messages) == 2
    assert len(messages[0].content) <= 40
    restored = SwarmMessageBus.from_dict(bus.to_dict())
    assert [message.message_id for message in restored.read()] == [message.message_id for message in messages]
    with pytest.raises(SwarmMessageValidationError):
        bus.publish(topic="x", sender="worker", trust="verified", content="client cannot self-assert")


def test_consensus_requires_explicit_evidence_and_never_invents_missing_voters():
    approved = evaluate_consensus(
        [
            {"voter": "qa-1", "stance": "approve", "evidence": ["test:123"]},
            {"voter": "qa-2", "stance": "approve", "evidence": ["review:456"]},
        ],
        ConsensusPolicy(min_voters=2, threshold=0.75, require_evidence=True),
    )
    assert approved.status == "approved"
    assert approved.approved is True

    unresolved = evaluate_consensus(
        [{"voter": "qa-1", "stance": "approve"}],
        ConsensusPolicy(min_voters=2, threshold=0.75, require_evidence=True),
    )
    assert unresolved.status == "unresolved"
    duplicate = evaluate_consensus(
        [
            {"voter": "qa-1", "stance": "approve", "evidence": ["test:1"]},
            {"voter": "qa-1", "stance": "approve", "evidence": ["test:2"]},
            {"voter": "qa-2", "stance": "approve", "evidence": ["test:3"]},
        ],
        ConsensusPolicy(min_voters=2, require_evidence=True),
    )
    assert duplicate.approved is False
    assert "duplicate voter" in duplicate.reason


def test_scheduler_fences_stale_attempt_and_validates_dag():
    plan = SwarmPlan(
        swarm_id="swm-lease",
        goal="lease test",
        mode=SwarmMode.PARALLEL,
        tasks={"task-1": SwarmTaskNode(task_id="task-1", objective="do work")},
    )
    scheduler = SwarmScheduler(plan)
    task = scheduler.dispatch_ready_tasks(lease_seconds=30)[0]
    old_lease = task.lease_id
    assert old_lease
    scheduler.mark_failed("task-1", "transient", lease_id=old_lease)
    assert plan.tasks["task-1"].state == TaskNodeState.PENDING
    assert scheduler.mark_completed("task-1", "late result", lease_id=old_lease) is None
    retried = scheduler.dispatch_ready_tasks()[0]
    assert retried.lease_id != old_lease
    assert scheduler.mark_completed("task-1", "fresh result", lease_id=retried.lease_id).state == TaskNodeState.COMPLETED

    invalid = SwarmPlan(
        swarm_id="swm-cycle",
        goal="cycle",
        mode=SwarmMode.PARALLEL,
        tasks={
            "a": SwarmTaskNode(task_id="a", objective="a", dependencies=["b"]),
            "b": SwarmTaskNode(task_id="b", objective="b", dependencies=["a"]),
        },
    )
    with pytest.raises(SwarmPlanValidationError):
        SwarmScheduler(invalid).validate_graph()


def test_coordinator_persists_events_messages_and_owner_scope(tmp_path):
    path = tmp_path / "swarms"
    first = SwarmCoordinator(storage_dir=path)
    plan = first.create_swarm("Owner-scoped work", mode=SwarmMode.PARALLEL, owner_id="alice", idempotency_key="admit-1")
    duplicate = first.create_swarm("Owner-scoped work", mode=SwarmMode.PARALLEL, owner_id="alice", idempotency_key="admit-1")
    assert duplicate.swarm_id == plan.swarm_id
    with pytest.raises(ValueError, match="different swarm request"):
        first.create_swarm("Different work", mode=SwarmMode.PARALLEL, owner_id="alice", idempotency_key="admit-1")
    first.publish_message(plan.swarm_id, topic="notes", sender="alice", content="hello", idempotency_key="n1")
    before = plan.tasks["task-research"].to_dict()
    with pytest.raises(SwarmPlanValidationError):
        first.dynamic_expand(plan.swarm_id, [{"task_id": "bad", "objective": "bad", "dependencies": ["missing"]}])
    assert plan.tasks["task-research"].to_dict() == before

    second = SwarmCoordinator(storage_dir=path)
    restored = second.get_swarm(plan.swarm_id)
    assert restored is not None
    assert restored.owner_id == "alice"
    assert second.get_swarm(plan.swarm_id, owner_id="bob") is None
    assert "SWARM_MESSAGE" in [event.event_type for event in second.get_events(plan.swarm_id, limit=20)]
    assert second.get_messages(plan.swarm_id)[0].content == "hello"
    assert not list(path.glob("*.tmp"))
    assert second.checkpoint_if_revision(plan.swarm_id, plan.revision + 1) is False
    current_revision = second.get_swarm(plan.swarm_id).revision
    assert second.checkpoint_if_revision(plan.swarm_id, current_revision) is True


@pytest.mark.asyncio
async def test_runner_reports_budget_exhaustion_without_dispatch(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-budget",
        goal="bounded run",
        mode=SwarmMode.PARALLEL,
        status="running",
        budget=SwarmBudget(max_tokens=0),
        tasks={"task-1": SwarmTaskNode(task_id="task-1", objective="must not run")},
    )
    coordinator._swarms[plan.swarm_id] = plan
    result = await AsyncSwarmRunner(coordinator, poll_interval=0.01).run_swarm_async(plan.swarm_id)
    assert result["status"] == "budget_exhausted"
    assert plan.status == "budget_exhausted"
    assert plan.tasks["task-1"].state == TaskNodeState.CANCELLED
    assert any(event.event_type == "SWARM_BUDGET_EXHAUSTED" for event in coordinator.get_events(plan.swarm_id))


def test_acceptance_failure_is_separate_from_execution_success():
    plan = SwarmPlan(
        swarm_id="swm-acceptance",
        goal="verify",
        mode=SwarmMode.PARALLEL,
        tasks={
            "task-1": SwarmTaskNode(
                task_id="task-1",
                objective="do work",
                state=TaskNodeState.COMPLETED,
                result_summary="The worker returned a result.",
                acceptance_criteria=[{"id": "test", "required_evidence_kinds": ["test_result"]}],
                acceptance_status="failed",
                verification={"verified": False},
            )
        },
    )
    result = SwarmAggregator.aggregate(plan)
    assert result["completed_tasks"] == 1
    assert result["acceptance_failed_tasks"] == 1
    assert plan.status == "partial_success"


def test_plan_serialization_exposes_derived_progress_and_legacy_objective():
    plan = SwarmPlan(
        swarm_id="swm-projection",
        goal="project status",
        mode=SwarmMode.PARALLEL,
        tasks={
            "done": SwarmTaskNode(task_id="done", objective="done", state=TaskNodeState.COMPLETED),
            "next": SwarmTaskNode(task_id="next", objective="next"),
        },
    )
    payload = plan.to_dict()
    assert payload["objective"] == "project status"
    assert payload["progress"] == {
        "total": 2,
        "pending": 1,
        "running": 0,
        "completed": 1,
        "failed": 0,
        "cancelled": 0,
    }


def test_external_completion_applies_acceptance_overlay_without_retrying(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-external-acceptance",
        goal="verify external result",
        mode=SwarmMode.PARALLEL,
        status="running",
        tasks={
            "task-1": SwarmTaskNode(
                task_id="task-1",
                objective="verify",
                state=TaskNodeState.RUNNING,
                lease_id="lease-current",
                acceptance_criteria=[{"id": "test", "required_evidence_kinds": ["test_result"]}],
            )
        },
    )
    coordinator._swarms[plan.swarm_id] = plan
    updated = coordinator.complete_task(
        plan.swarm_id,
        "task-1",
        result_summary="worker returned",
        evidence=[{"criterion_id": "test", "kind": "test_result", "passed": True, "reference": "test:1"}],
        lease_id="lease-current",
    )
    assert updated is not None
    assert updated.state == TaskNodeState.COMPLETED
    assert updated.acceptance_status == "passed"
    assert coordinator.complete_task(plan.swarm_id, "task-1", result_summary="stale", lease_id="stale") is None
    assert plan.tasks["task-1"].result_summary == "worker returned"


def test_auto_replan_redirects_dependents_within_budget(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-auto-replan",
        goal="repair a failed branch",
        mode=SwarmMode.PARALLEL,
        status="running",
        auto_replan=True,
        tasks={
            "root": SwarmTaskNode(task_id="root", objective="root", state=TaskNodeState.FAILED),
            "dependent": SwarmTaskNode(
                task_id="dependent",
                objective="downstream",
                dependencies=["root"],
            ),
        },
    )
    coordinator._swarms[plan.swarm_id] = plan
    repair_id = coordinator.auto_replan_failed_task(plan.swarm_id, "root")
    assert repair_id
    assert plan.tasks[repair_id].state == TaskNodeState.PENDING
    assert plan.tasks["dependent"].dependencies == [repair_id]
    assert plan.replan_count == 1
    assert any(event.event_type == "SWARM_AUTO_REPLAN" for event in coordinator.get_events(plan.swarm_id))
    assert coordinator.auto_replan_failed_task(plan.swarm_id, "root") is None


@pytest.mark.asyncio
async def test_runner_auto_replans_before_aggregating_terminal_failure(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-runner-auto-replan",
        goal="repair terminal branch",
        mode=SwarmMode.PARALLEL,
        status="running",
        auto_replan=True,
        tasks={"root": SwarmTaskNode(task_id="root", objective="failed root", state=TaskNodeState.FAILED)},
    )
    coordinator._swarms[plan.swarm_id] = plan

    class Worker:
        def execute_task(self, task, _plan):
            return {"summary": "repair completed", "evidence": [], "artifacts": [], "tool_calls": 1}

    runner = AsyncSwarmRunner(coordinator, poll_interval=0.01)
    runner._build_worker = lambda _task: Worker()
    result = await runner.run_swarm_async(plan.swarm_id)
    assert result["status"] == "partial_success"
    assert plan.replan_count == 1
    assert any(task.task_id.startswith("task-repair-") and task.state == TaskNodeState.COMPLETED for task in plan.tasks.values())
    assert any(event.event_type == "SWARM_AUTO_REPLAN" for event in coordinator.get_events(plan.swarm_id))


def test_worktree_runner_sanitizes_task_ids_for_git_refs(tmp_path):
    runner = AsyncSwarmRunner(SwarmCoordinator(storage_dir=tmp_path / "swarms"), poll_interval=0.01)
    worker = runner._build_worker(SwarmTaskNode(task_id="task:unsafe..id", objective="code", worktree_path=".worktrees/task"))
    assert ":" not in worker.branch_name
    assert ".." not in worker.branch_name
    assert worker.branch_name.startswith("wt-")


def test_consecutive_failure_circuit_is_bounded_and_resets_on_success():
    budget = SwarmBudget(max_consecutive_failures=2)
    budget.record_task_failure()
    assert budget.exhausted is False
    budget.record_task_failure()
    assert budget.exhausted is True
    assert budget.exhausted_reason == "consecutive_failure_budget_exhausted"
    budget.record_task_success()
    assert budget.consecutive_failures == 0
    # Exhaustion is sticky even after a later success; it cannot silently reopen
    # a run after a hard circuit has tripped.
    assert budget.exhausted is True


def test_message_bus_bounds_corrupt_loaded_messages():
    bus = SwarmMessageBus(
        "swm-loaded-bound",
        max_content_chars=20,
        max_data_chars=32,
        messages=[
            {
                "message_id": "msg-corrupt",
                "swarm_id": "swm-loaded-bound",
                "sequence": 1,
                "topic": "notes",
                "sender": "worker",
                "kind": "observation",
                "content": "x" * 100,
                "data": {"payload": "y" * 100},
                "trust": "verified",
                "created_at": 1,
            }
        ],
    )
    message = bus.read()[0]
    assert len(message.content) <= 20
    assert message.data["truncated"] is True
    assert message.trust == "untrusted"


def test_sync_runner_start_uses_bounded_thread_fallback(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-sync-start",
        goal="synchronous tool fallback",
        mode=SwarmMode.PARALLEL,
        status="running",
        budget=SwarmBudget(max_tokens=0),
        tasks={"task-1": SwarmTaskNode(task_id="task-1", objective="bounded")},
    )
    coordinator._swarms[plan.swarm_id] = plan
    runner = AsyncSwarmRunner(coordinator, poll_interval=0.01)
    thread = runner.start_background_swarm(plan.swarm_id)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert plan.status == "budget_exhausted"


def test_message_bus_rejects_unbounded_data_payload():
    bus = SwarmMessageBus("swm-data-bound", max_data_chars=32)
    with pytest.raises(SwarmMessageValidationError):
        bus.publish(topic="notes", sender="worker", data={"payload": "x" * 100})


@pytest.mark.asyncio
async def test_runner_fails_invalid_persisted_graph_instead_of_spinning(tmp_path):
    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = SwarmPlan(
        swarm_id="swm-invalid-runner",
        goal="invalid",
        mode=SwarmMode.PARALLEL,
        status="running",
        tasks={
            "a": SwarmTaskNode(task_id="a", objective="a", dependencies=["b"]),
            "b": SwarmTaskNode(task_id="b", objective="b", dependencies=["a"]),
        },
    )
    coordinator._swarms[plan.swarm_id] = plan
    result = await AsyncSwarmRunner(coordinator, poll_interval=0.01).run_swarm_async(plan.swarm_id)
    assert result["status"] == "failed"
    assert plan.terminal_reason.startswith("invalid_swarm_plan:")
    assert any(event.event_type == "SWARM_PLAN_INVALID" for event in coordinator.get_events(plan.swarm_id))


@pytest.mark.asyncio
async def test_worker_context_stays_in_data_channel(monkeypatch):
    import alpha.swarm.worker as worker_mod

    class Message:
        def __init__(self, content):
            self.content = content

    class Model:
        def __init__(self):
            self.seen = []

        def invoke(self, messages):
            self.seen = messages
            return Message("done")

    model = Model()
    monkeypatch.setattr(worker_mod, "_resolve_model", lambda model_name, *, worker_label: model)
    worker = worker_mod.EphemeralSubagentWorker("context-test")
    worker.set_context([{"content": "UNTRUSTED-CONTEXT-MARKER"}])
    outcome = worker.execute_task(SwarmTaskNode(task_id="t", objective="objective"), SwarmPlan(swarm_id="s", goal="g", mode=SwarmMode.PARALLEL))
    assert outcome["summary"] == "done"
    assert "UNTRUSTED-CONTEXT-MARKER" not in model.seen[0].content
    assert "UNTRUSTED-CONTEXT-MARKER" in model.seen[1].content
