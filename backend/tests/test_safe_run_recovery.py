"""Safe, durable continuation policy for interrupted Alpha runs."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from alpha.config.run_ownership_config import RunOwnershipConfig
from alpha.runtime.checkpoint_mode import CheckpointModeMismatchError
from alpha.runtime.runs.manager import (
    ORPHAN_RECOVERY_STOP_REASON,
    RunRecord,
)
from alpha.runtime.runs.schemas import DisconnectMode, RunStatus
from app.gateway.run_models import RunCreateRequest
from app.gateway.run_recovery import (
    RECOVERY_BLOCKED_REASON,
    RECOVERY_CONFIRMATION_REASON,
    RECOVERY_EXHAUSTED_REASON,
    RECOVERY_NO_WORK_REASON,
    RecoveryDisposition,
    SafeRunRecoveryService,
    assess_checkpoint_snapshot,
    select_recovery_snapshot,
)


def test_safe_recovery_config_defaults_are_bounded_and_enabled() -> None:
    config = RunOwnershipConfig()

    assert config.auto_resume is True
    assert config.max_resume_attempts == 3
    assert config.resume_poll_interval_seconds == 5.0
    assert config.max_concurrent_resumes == 2


def _checkpoint(*, next_nodes: tuple[str, ...] = (), tasks: tuple[object, ...] = (), checkpoint_id: str = "cp-1"):
    return SimpleNamespace(
        next=next_nodes,
        tasks=tasks,
        config={"configurable": {"checkpoint_id": checkpoint_id}},
    )


def _record(*, attempt: int = 0, stop_reason: str = ORPHAN_RECOVERY_STOP_REASON) -> RunRecord:
    now = datetime.now(UTC).isoformat()
    return RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id="lead-agent",
        status=RunStatus.error,
        on_disconnect=DisconnectMode.continue_,
        user_id="user-1",
        model_name="old-model",
        metadata={"recovery_attempt": attempt},
        created_at=now,
        updated_at=now,
        stop_reason=stop_reason,
        store_only=True,
    )


class _FakeRecoveryManager:
    def __init__(self, candidates: list[RunRecord]) -> None:
        self.candidates = candidates
        self.transitions: list[dict] = []
        self.latest: dict[str, list[RunRecord]] = {}

    async def list_recovery_candidates(self, **kwargs) -> list[RunRecord]:
        return list(self.candidates)

    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 100) -> list[RunRecord]:
        return self.latest.get(thread_id, self.candidates)[:limit]

    async def transition_recovery_stop_reason(
        self,
        run_id: str,
        *,
        expected_status: str,
        expected_stop_reason: str,
        stop_reason: str,
        error: str | None = None,
    ) -> bool:
        self.transitions.append(
            {
                "run_id": run_id,
                "expected_status": expected_status,
                "expected_stop_reason": expected_stop_reason,
                "stop_reason": stop_reason,
                "error": error,
            }
        )
        return True


@pytest.mark.parametrize("next_nodes", [("model",), ("agent",), ("lead-agent",)])
def test_checkpoint_assessment_allows_model_resume(next_nodes: tuple[str, ...]) -> None:
    assessment = assess_checkpoint_snapshot(_checkpoint(next_nodes=next_nodes))

    assert assessment.disposition is RecoveryDisposition.RESUME
    assert assessment.checkpoint_id == "cp-1"
    assert assessment.pending_nodes == next_nodes


@pytest.mark.parametrize(
    "next_nodes",
    [
        ("tools",),
        ("tools:acme-1",),
        ("submit_payment",),
        ("call_mcp",),
        ("after_model",),
        ("custom_model",),
        ("charge_customer_model",),
        ("unknown:model",),
    ],
)
def test_checkpoint_assessment_requires_confirmation_for_uncertain_side_effects(next_nodes: tuple[str, ...]) -> None:
    assessment = assess_checkpoint_snapshot(_checkpoint(next_nodes=next_nodes))

    assert assessment.disposition is RecoveryDisposition.REQUIRE_CONFIRMATION
    assert assessment.pending_nodes == next_nodes


def test_checkpoint_assessment_uses_task_names_when_next_is_empty() -> None:
    task = SimpleNamespace(name="tools", error=None)

    assessment = assess_checkpoint_snapshot(_checkpoint(tasks=(task,)))

    assert assessment.disposition is RecoveryDisposition.REQUIRE_CONFIRMATION
    assert assessment.pending_nodes == ("tools",)


def test_checkpoint_assessment_stops_when_graph_has_no_pending_work() -> None:
    assessment = assess_checkpoint_snapshot(_checkpoint())

    assert assessment.disposition is RecoveryDisposition.STOP
    assert assessment.reason == "checkpoint has no pending graph work"


def test_checkpoint_assessment_resumes_active_goal_continuation_without_graph_task() -> None:
    snapshot = _checkpoint()
    snapshot.values = {"goal": {"status": "active"}}

    assessment = assess_checkpoint_snapshot(snapshot)

    assert assessment.disposition is RecoveryDisposition.RESUME
    assert assessment.pending_nodes == ("goal_continuation",)


def test_model_failure_recovery_rewinds_only_the_marked_fallback_message() -> None:
    head = _checkpoint(checkpoint_id="cp-failed")
    head.values = {
        "messages": [
            SimpleNamespace(
                additional_kwargs={"agent_workspace_error_fallback": True, "error_reason": "transient"},
            )
        ]
    }
    parent = _checkpoint(next_nodes=("model",), checkpoint_id="cp-before-failure")
    record = _record(stop_reason="model_failure")

    selected = select_recovery_snapshot(record=record, head=head, history=(head, parent))

    assert selected is parent


def test_non_model_recovery_never_rewinds_a_terminal_checkpoint() -> None:
    head = _checkpoint(checkpoint_id="cp-failed")
    head.values = {
        "messages": [
            SimpleNamespace(
                additional_kwargs={"agent_workspace_error_fallback": True, "error_reason": "transient"},
            )
        ]
    }
    parent = _checkpoint(next_nodes=("model",), checkpoint_id="cp-before-failure")
    record = _record(stop_reason=ORPHAN_RECOVERY_STOP_REASON)

    selected = select_recovery_snapshot(record=record, head=head, history=(head, parent))

    assert selected is head


def test_model_failure_rewind_reads_raw_full_checkpoint_message_dicts() -> None:
    head = _checkpoint(checkpoint_id="cp-failed")
    head.values = {
        "messages": [
            {
                "additional_kwargs": {
                    "agent_workspace_error_fallback": True,
                    "error_reason": "transient",
                }
            }
        ]
    }
    parent = _checkpoint(next_nodes=("model",), checkpoint_id="cp-before-failure")
    record = _record(stop_reason="model_failure")

    selected = select_recovery_snapshot(record=record, head=head, history=(head, parent))

    assert selected is parent


def test_non_transient_model_failure_is_not_retried() -> None:
    head = _checkpoint(checkpoint_id="cp-failed")
    head.values = {
        "messages": [
            SimpleNamespace(
                additional_kwargs={
                    "agent_workspace_error_fallback": True,
                    "error_reason": "quota",
                },
            )
        ]
    }
    parent = _checkpoint(next_nodes=("model",), checkpoint_id="cp-before-failure")
    record = _record(stop_reason="model_failure")

    selected = select_recovery_snapshot(record=record, head=head, history=(head, parent))

    assert selected is head


@pytest.mark.anyio
async def test_recovery_service_does_not_retry_auth_or_quota_model_failures() -> None:
    record = _record(stop_reason="model_failure")
    manager = _FakeRecoveryManager([record])
    snapshot = _checkpoint()
    snapshot.values = {
        "messages": [
            SimpleNamespace(
                additional_kwargs={
                    "agent_workspace_error_fallback": True,
                    "error_reason": "quota",
                }
            )
        ]
    }

    async def read_checkpoint(_record: RunRecord):
        return snapshot

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True),
        checkpoint_reader=read_checkpoint,
        launcher=None,
    )

    result = await service.recover_once()

    assert result.stopped == ("run-1",)
    assert manager.transitions[0]["stop_reason"] == RECOVERY_BLOCKED_REASON


@pytest.mark.anyio
async def test_recovery_service_resumes_safe_checkpoint_with_next_attempt() -> None:
    record = _record()
    manager = _FakeRecoveryManager([record])
    launches: list[dict] = []

    async def read_checkpoint(_record: RunRecord):
        return _checkpoint(next_nodes=("model",))

    async def launch(*, source: RunRecord, checkpoint_id: str, attempt: int, reason: str, owner_user_id: str):
        launches.append(
            {
                "source": source.run_id,
                "checkpoint_id": checkpoint_id,
                "attempt": attempt,
                "reason": reason,
                "owner_user_id": owner_user_id,
            }
        )
        return SimpleNamespace(run_id="run-2")

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True, resume_backoff_seconds=0),
        checkpoint_reader=read_checkpoint,
        launcher=launch,
    )

    result = await service.recover_once()

    assert result.resumed == ("run-1",)
    assert launches == [
        {
            "source": "run-1",
            "checkpoint_id": "cp-1",
            "attempt": 1,
            "reason": ORPHAN_RECOVERY_STOP_REASON,
            "owner_user_id": "user-1",
        }
    ]
    assert manager.transitions == []


@pytest.mark.anyio
async def test_recovery_service_start_and_stop_owns_a_supervised_loop() -> None:
    manager = _FakeRecoveryManager([])
    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True, resume_poll_interval_seconds=0.1),
        checkpoint_reader=None,
        launcher=None,
    )
    await service.start()
    task = service._task
    assert task is not None and not task.done()
    await service.stop(timeout=1.0)
    assert task.done()
    assert service._task is None


@pytest.mark.anyio
async def test_recovery_pass_isolates_one_unexpected_candidate_failure() -> None:
    record = _record()
    manager = _FakeRecoveryManager([record])

    async def broken_latest(*_args, **_kwargs):
        raise RuntimeError("temporary store failure")

    manager.list_by_thread = broken_latest
    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True),
        checkpoint_reader=None,
        launcher=None,
    )

    result = await service.recover_once()

    assert result.failed == ("run-1",)
    assert manager.transitions == []


@pytest.mark.anyio
async def test_recovery_service_blocks_permanent_admission_validation_failure() -> None:
    record = _record()
    manager = _FakeRecoveryManager([record])

    async def read_checkpoint(_record: RunRecord):
        return _checkpoint(next_nodes=("model",))

    async def invalid_launch(**_kwargs):
        RunCreateRequest(acceptance_criteria=[{"id": "", "description": "invalid"}])

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True, resume_backoff_seconds=0),
        checkpoint_reader=read_checkpoint,
        launcher=invalid_launch,
    )

    result = await service.recover_once()

    assert result.stopped == ("run-1",)
    assert manager.transitions[0]["stop_reason"] == RECOVERY_BLOCKED_REASON


@pytest.mark.anyio
async def test_recovery_service_blocks_permanent_checkpoint_mode_mismatch() -> None:
    record = _record()
    manager = _FakeRecoveryManager([record])

    async def read_checkpoint(_record: RunRecord):
        raise CheckpointModeMismatchError("delta/full mismatch")

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True),
        checkpoint_reader=read_checkpoint,
        launcher=None,
    )

    result = await service.recover_once()

    assert result.stopped == ("run-1",)
    assert manager.transitions[0]["stop_reason"] == RECOVERY_BLOCKED_REASON


@pytest.mark.anyio
async def test_recovery_service_pauses_before_replaying_uncertain_tool_effect() -> None:
    record = _record()
    manager = _FakeRecoveryManager([record])
    launches: list[str] = []

    async def read_checkpoint(_record: RunRecord):
        return _checkpoint(next_nodes=("tools",))

    async def launch(**_kwargs):
        launches.append("unexpected")
        raise AssertionError("ambiguous tool execution must not be replayed")

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True, resume_backoff_seconds=0),
        checkpoint_reader=read_checkpoint,
        launcher=launch,
    )

    result = await service.recover_once()

    assert result.awaiting_confirmation == ("run-1",)
    assert launches == []
    assert manager.transitions[0]["stop_reason"] == RECOVERY_CONFIRMATION_REASON
    assert "may already have taken effect" in manager.transitions[0]["error"]


@pytest.mark.anyio
async def test_recovery_service_stops_when_checkpoint_has_no_work() -> None:
    record = _record()
    manager = _FakeRecoveryManager([record])

    async def read_checkpoint(_record: RunRecord):
        return _checkpoint()

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True, resume_backoff_seconds=0),
        checkpoint_reader=read_checkpoint,
        launcher=None,
    )

    result = await service.recover_once()

    assert result.stopped == ("run-1",)
    assert manager.transitions[0]["stop_reason"] == RECOVERY_NO_WORK_REASON


@pytest.mark.anyio
async def test_recovery_service_leaves_scheduled_dispatch_to_its_durable_service() -> None:
    record = _record()
    record.metadata["scheduled_task_id"] = "task-1"
    record.metadata["scheduled_task_run_id"] = "occurrence-1"
    manager = _FakeRecoveryManager([record])

    async def read_checkpoint(_record: RunRecord):
        raise AssertionError("chat recovery must not replay a scheduler-owned occurrence")

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True),
        checkpoint_reader=read_checkpoint,
        launcher=None,
    )

    result = await service.recover_once()

    assert result.stopped == ("run-1",)
    assert manager.transitions[0]["stop_reason"] == RECOVERY_BLOCKED_REASON


@pytest.mark.anyio
async def test_recovery_service_does_not_replay_edit_or_regenerate_lineage() -> None:
    record = _record()
    record.metadata["replay_kind"] = "edit"
    record.metadata["regenerate_from_run_id"] = "source-run"
    manager = _FakeRecoveryManager([record])

    async def read_checkpoint(_record: RunRecord):
        raise AssertionError("replacement replay must stop before checkpoint launch")

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True, resume_backoff_seconds=0),
        checkpoint_reader=read_checkpoint,
        launcher=None,
    )

    result = await service.recover_once()

    assert result.stopped == ("run-1",)
    assert manager.transitions[0]["stop_reason"] == RECOVERY_BLOCKED_REASON


@pytest.mark.anyio
async def test_recovery_service_bounds_durable_lineage_attempts() -> None:
    record = _record(attempt=2)
    manager = _FakeRecoveryManager([record])

    async def read_checkpoint(_record: RunRecord):
        raise AssertionError("exhausted recovery must not inspect or launch a checkpoint")

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True, max_resume_attempts=2, resume_backoff_seconds=0),
        checkpoint_reader=read_checkpoint,
        launcher=None,
    )

    result = await service.recover_once()

    assert result.exhausted == ("run-1",)
    assert manager.transitions[0]["stop_reason"] == RECOVERY_EXHAUSTED_REASON


@pytest.mark.anyio
async def test_production_checkpoint_reader_rewinds_marked_model_fallback_to_parent(monkeypatch) -> None:
    head = _checkpoint(checkpoint_id="cp-failed")
    head.values = {
        "messages": [
            SimpleNamespace(
                additional_kwargs={"agent_workspace_error_fallback": True, "error_reason": "transient"},
            )
        ]
    }
    parent = _checkpoint(next_nodes=("model",), checkpoint_id="cp-before-failure")
    record = _record(stop_reason="model_failure")
    accessor = SimpleNamespace(
        aget=lambda _config: _immediate(head),
        ahistory=lambda _config, limit: _immediate([head, parent]),
    )

    captured: dict = {}

    async def fake_accessor_builder(*_args, **kwargs):
        captured.update(kwargs)
        return accessor, {"configurable": {"thread_id": record.thread_id}}

    monkeypatch.setattr("app.gateway.services.abuild_checkpoint_state_accessor", fake_accessor_builder)
    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=_FakeRecoveryManager([record]),
        config=RunOwnershipConfig(auto_resume=True),
    )

    selected = await service._read_checkpoint(record)

    assert selected is parent
    assert captured["require_graph"] is True


async def _immediate(value):
    return value


@pytest.mark.anyio
async def test_production_checkpoint_reader_blocks_when_required_graph_cannot_assemble(monkeypatch) -> None:
    record = _record()
    manager = _FakeRecoveryManager([record])

    async def broken_builder(*_args, **_kwargs):
        raise RuntimeError("model config broken")

    monkeypatch.setattr("app.gateway.services.abuild_checkpoint_state_accessor", broken_builder)
    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True),
        checkpoint_reader=None,
        launcher=None,
    )

    result = await service.recover_once()

    assert result.stopped == ("run-1",)
    assert manager.transitions[0]["stop_reason"] == RECOVERY_BLOCKED_REASON


@pytest.mark.anyio
async def test_recovery_launch_uses_current_model_config_instead_of_pinning_failed_model(monkeypatch) -> None:
    record = _record()
    record.metadata["autonomous"] = True
    manager = _FakeRecoveryManager([record])
    captured: dict = {}

    async def fake_start_run(body, thread_id, request, **kwargs):
        captured.update(
            {
                "body": body,
                "thread_id": thread_id,
                "request": request,
                "kwargs": kwargs,
            }
        )
        return SimpleNamespace(run_id="run-2")

    monkeypatch.setattr("app.gateway.services.start_run", fake_start_run)
    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True),
    )

    await service._launch_recovery(
        source=record,
        checkpoint_id="cp-1",
        attempt=1,
        reason=ORPHAN_RECOVERY_STOP_REASON,
        owner_user_id="user-1",
    )

    body = captured["body"]
    assert body.input is None
    assert body.checkpoint_id == "cp-1"
    assert body.on_disconnect == "continue"
    assert "model_name" not in body.context
    assert body.context["non_interactive"] is True
    assert body.metadata["auto_recovery"]["source_model_name"] == "old-model"
    assert captured["kwargs"]["idempotency_key"] == "auto-recovery:run-1"
    assert captured["kwargs"]["require_existing_thread"] is True


@pytest.mark.anyio
async def test_recovery_service_skips_backoff_before_next_attempt() -> None:
    record = _record(attempt=1)
    manager = _FakeRecoveryManager([record])
    launches: list[int] = []

    async def read_checkpoint(_record: RunRecord):
        return _checkpoint(next_nodes=("model",))

    async def launch(*, attempt: int, **_kwargs):
        launches.append(attempt)
        return SimpleNamespace(run_id="run-2")

    service = SafeRunRecoveryService(
        app=SimpleNamespace(),
        run_manager=manager,
        config=RunOwnershipConfig(auto_resume=True, resume_backoff_seconds=60, resume_poll_interval_seconds=0.1),
        checkpoint_reader=read_checkpoint,
        launcher=launch,
    )

    first = await service.recover_once()
    second = await service.recover_once()

    assert first.deferred == ("run-1",)
    assert second.deferred == ("run-1",)
    assert launches == []


def test_run_creation_defaults_to_surviving_browser_network_disconnect() -> None:
    """The explicit cancel endpoint remains the stop mechanism; a dropped SSE is not a user cancel."""

    assert RunCreateRequest().on_disconnect == "continue"
