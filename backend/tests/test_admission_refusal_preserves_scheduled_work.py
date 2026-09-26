"""A capacity refusal is a wait, not a verdict: scheduled work must survive one.

The Gateway's in-flight run budget (``RunAdmissionController``) answers "no" to
a run that arrives while the process is saturated, and it answers with 429
``gateway_run_capacity_exhausted``. Two durable producers of background work go
through that same admission:

* the scheduled-task poller, whose every occurrence is a
  ``scheduled-task:{occurrence}`` run;
* the MCP task notifier, whose every delivery is an
  ``mcp-task:{task}:{version}:{attempt}`` run.

Both reuse a *deterministic* idempotency key, which is what makes the refusal
expensive: the Gateway creates the run row first and only then discovers it has
no slot, so the key is spent on a row that is failed and had no worker. A
refusal is therefore not just "this attempt did not run" -- it is "this attempt
consumed durable state", and the retry has to be arranged around that or it
resolves to the dead row forever.

These tests drive the real ``start_run`` through the real producers and assert on
the real durable rows, because the defect lives in the seam: a unit test of the
controller proves the cap is real, and nothing about what the cap does to a
producer's bookkeeping.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from alpha.config.app_config import AppConfig, reset_app_config, set_app_config
from alpha.config.database_config import DatabaseConfig
from alpha.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from alpha.persistence.scheduled_task_runs import ScheduledTaskRunRepository
from alpha.persistence.scheduled_tasks import ScheduledTaskRepository
from alpha.runtime import RunManager, RunStatus
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.lane_scheduler import (
    RUN_ADMISSION_REJECTED_CODE,
    RunAdmissionController,
    RunAdmissionRejected,
    capacity_refusal_detail,
    capacity_refusal_retry_after_seconds,
    has_run_capacity,
    is_run_capacity_refusal,
    set_run_admission_controller,
)
from alpha.runtime.runs.store.memory import MemoryRunStore
from app.mcp_tasks.service import McpTaskService
from app.scheduler.service import ScheduledTaskService


@pytest.fixture(autouse=True)
def _stub_app_config():
    """Pin config and the process-wide budget so a developer-local
    ``config.yaml`` or a leaked controller cannot move the numbers under test."""
    set_app_config(AppConfig.model_validate({"sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"}}))
    previous = set_run_admission_controller(None)
    yield
    set_run_admission_controller(previous)
    reset_app_config()


def _app(run_manager: RunManager) -> SimpleNamespace:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from alpha.persistence.thread_meta.memory import MemoryThreadMetaStore

    store = InMemoryStore()
    return SimpleNamespace(
        state=SimpleNamespace(
            stream_bridge=SimpleNamespace(),
            run_manager=run_manager,
            checkpointer=InMemorySaver(),
            store=store,
            run_event_store=MemoryRunEventStore(),
            run_events_config=None,
            thread_store=MemoryThreadMetaStore(store),
        )
    )


async def _drain_callbacks(rounds: int = 5) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


@contextlib.contextmanager
def _no_worker_patches():
    """Keep ``run_agent`` out of it: these tests are about admission, not agents."""

    async def no_op_run_agent(*args, **kwargs):
        return None

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=object()),
        patch("app.gateway.services.run_agent", side_effect=no_op_run_agent),
    ):
        yield


# ---------------------------------------------------------------------------
# The refusal has to be recognisable at all
# ---------------------------------------------------------------------------


def test_the_refusal_status_constant_matches_the_gateway():
    """The classifier lives here so no producer imports the Gateway; if the two
    constants drift, every refusal silently becomes "some other error"."""
    from alpha.runtime.lane_scheduler import RUN_CAPACITY_REFUSAL_STATUS
    from app.gateway.services import RUN_ADMISSION_REJECTED_STATUS

    assert RUN_CAPACITY_REFUSAL_STATUS == RUN_ADMISSION_REJECTED_STATUS == 429


def test_a_capacity_refusal_is_distinguishable_from_a_real_failure():
    from fastapi import HTTPException

    from app.gateway.services import _run_admission_rejected_http_error

    controller = RunAdmissionController(max_concurrent_runs=1)
    controller.try_admit()
    refusal = _run_admission_rejected_http_error(
        RunAdmissionRejected(
            controller.try_admit(),
            retry_after_seconds=3.0,
        )
    )
    assert is_run_capacity_refusal(refusal) is True
    assert capacity_refusal_retry_after_seconds(refusal) == 3.0
    assert RUN_ADMISSION_REJECTED_CODE in capacity_refusal_detail(refusal)

    # A 429 from some other rate limiter, and a genuine run failure, are not
    # capacity refusals and must keep their existing handling.
    assert is_run_capacity_refusal(HTTPException(status_code=429, detail="slow down")) is False
    assert is_run_capacity_refusal(HTTPException(status_code=429, detail={"code": "other_limiter"})) is False
    assert is_run_capacity_refusal(RuntimeError("the model provider exploded")) is False
    assert is_run_capacity_refusal(HTTPException(status_code=409, detail="thread busy")) is False


def test_a_retry_hint_that_is_missing_or_nonsense_falls_back():
    from fastapi import HTTPException

    exc = HTTPException(status_code=429, detail={"code": RUN_ADMISSION_REJECTED_CODE})
    assert capacity_refusal_retry_after_seconds(exc, default=7.0) == 7.0
    exc = HTTPException(
        status_code=429,
        detail={"code": RUN_ADMISSION_REJECTED_CODE, "retry_after_seconds": "soon"},
    )
    assert capacity_refusal_retry_after_seconds(exc, default=7.0) == 7.0


def test_the_snapshot_separates_who_is_losing_the_race_for_slots():
    from alpha.runtime.lane_scheduler import ExecutionLane

    controller = RunAdmissionController(max_concurrent_runs=1)
    controller.try_admit(lane=ExecutionLane.USER_INTERACTION)
    controller.try_admit(lane=ExecutionLane.BACKGROUND_AUTONOMOUS)
    controller.try_admit(lane=ExecutionLane.BACKGROUND_AUTONOMOUS)

    snapshot = controller.snapshot()
    assert snapshot["rejected"] == 2
    assert snapshot["rejected_by_lane"][ExecutionLane.BACKGROUND_AUTONOMOUS.value] == 2
    assert snapshot["rejected_by_lane"][ExecutionLane.USER_INTERACTION.value] == 0
    assert snapshot["available"] == 0


# ---------------------------------------------------------------------------
# Scheduled occurrences
# ---------------------------------------------------------------------------


def _make_scheduler(task_repo, run_repo, launch_run, *, poll_interval_seconds: int = 5):
    return ScheduledTaskService(
        task_repo=task_repo,
        task_run_repo=run_repo,
        launch_run=launch_run,
        poll_interval_seconds=poll_interval_seconds,
        lease_seconds=120,
        max_concurrent_runs=3,
    )


async def _seed_cron_task(task_repo, *, task_id: str, now: datetime) -> dict:
    await task_repo.create(
        task_id=task_id,
        user_id="user-1",
        thread_id="thread-1",
        context_mode="fresh_thread_per_run",
        assistant_id="lead_agent",
        title="Nightly digest",
        prompt="Summarise yesterday",
        schedule_type="cron",
        schedule_spec={"cron": "* * * * *"},
        timezone="UTC",
        next_run_at=now - timedelta(minutes=1),
    )
    task = await task_repo.get(task_id, user_id="user-1")
    assert task is not None
    return task


@pytest.mark.asyncio
async def test_a_refused_scheduled_occurrence_is_queued_and_retried_not_failed(tmp_path):
    """The regression.

    A saturated Gateway refuses the occurrence's run with 429. The occurrence
    must come out the other side as ``queued`` -- durable, active, retried -- and
    must launch for real as soon as a slot frees. Before the fix the same refusal
    wrote ``status='failed'`` plus a parent ``last_error`` and was never
    retried: the scheduled work was destroyed by a full run budget.
    """
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    try:
        sf = get_session_factory()
        assert sf is not None
        task_repo = ScheduledTaskRepository(sf)
        run_repo = ScheduledTaskRunRepository(sf)
        now = datetime.now(UTC)
        await _seed_cron_task(task_repo, task_id="task-cap", now=now)

        run_manager = RunManager(store=MemoryRunStore())
        app = _app(run_manager)

        controller = RunAdmissionController(max_concurrent_runs=1)
        set_run_admission_controller(controller)
        # Occupy the only slot with a run nobody finishes.
        controller.try_admit()

        from app.gateway.services import launch_scheduled_thread_run

        service = _make_scheduler(
            task_repo,
            run_repo,
            lambda **kwargs: launch_scheduled_thread_run(app=app, **kwargs),
        )

        with _no_worker_patches():
            # Poll 1: the occurrence is claimed and refused.
            await service.run_once(now=now)
            rows = await run_repo.list_by_task("task-cap")
            assert len(rows) == 1
            occurrence = rows[0]
            # NOT a failure: the occurrence is still owed its attempt.
            assert occurrence["status"] == "queued", occurrence
            assert occurrence["error"] is None or "capacity" in occurrence["error"]
            assert occurrence["finished_at"] is None, occurrence

            # The parent task is not told the job failed either.
            task = await task_repo.get("task-cap", user_id="user-1")
            assert task["last_error"] is None, task
            assert task["run_count"] == 0, task
            assert task["status"] == "enabled", task

            # The occurrence never reached the Gateway at all, so there is no
            # refused run row, no burnt `scheduled-task:{id}` idempotency key,
            # and nothing stranded in `pending` with no worker. The gate is what
            # makes the refusal affordable: asking would have spent all three.
            assert await run_manager.list_by_thread(occurrence["thread_id"]) == []
            assert controller.snapshot()["rejected"] == 0

            # Poll 2 inside the backoff window must not even try: a busy gateway
            # must not be walked into a 429 storm, and the refusal must be
            # announced once rather than once per poll for the whole window.
            await service.run_once(now=now + timedelta(seconds=1))
            assert controller.snapshot()["rejected"] == 0, controller.snapshot()

            # The slot frees.
            controller.release()
            assert has_run_capacity() is True

            await service.run_once(now=now + timedelta(seconds=60))
            await _drain_callbacks()

            rows = await run_repo.list_by_task("task-cap")
            assert rows[0]["status"] == "running", rows[0]
            assert rows[0]["run_id"] is not None
            task = await task_repo.get("task-cap", user_id="user-1")
            assert task["run_count"] == 1, task
            assert task["last_error"] is None, task
    finally:
        await close_engine()


@pytest.mark.asyncio
async def test_a_refused_scheduled_occurrence_never_pretends_to_have_launched(tmp_path):
    """The residue, made honest.

    The pre-flight gate keeps a saturated gateway from being asked at all, but a
    slot can still be taken between the gate and the launch. The gateway then
    creates the run row *before* it discovers it has no slot, so the refusal has
    already spent this occurrence's deterministic ``scheduled-task:{id}``
    idempotency key. A blind retry would resolve that key, get the dead row
    back, and record it as launched -- an occurrence pointing at a run that
    never executed, waiting forever for a completion hook that cannot fire.

    So the refused occurrence waits, loudly, instead of launching a ghost.
    """
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    try:
        sf = get_session_factory()
        assert sf is not None
        task_repo = ScheduledTaskRepository(sf)
        run_repo = ScheduledTaskRunRepository(sf)
        now = datetime.now(UTC)
        await _seed_cron_task(task_repo, task_id="task-residue", now=now)

        run_manager = RunManager(store=MemoryRunStore())
        app = _app(run_manager)

        controller = RunAdmissionController(max_concurrent_runs=4)
        set_run_admission_controller(controller)
        # Slot is free when the gate looks, taken by the time the launch lands.
        races = 1

        from app.gateway.services import launch_scheduled_thread_run

        async def racing_launch(**kwargs):
            nonlocal races
            if races:
                races -= 1
                # Saturated exactly in the window the gate cannot close.
                for _ in range(4):
                    controller.try_admit()
            return await launch_scheduled_thread_run(app=app, **kwargs)

        service = _make_scheduler(
            task_repo,
            run_repo,
            racing_launch,
        )

        with _no_worker_patches():
            await service.run_once(now=now)
            rows = await run_repo.list_by_task("task-residue")
            assert rows[0]["status"] == "queued", rows[0]
            assert rows[0]["run_id"] is None, "a refused occurrence must not carry a run id"
            assert rows[0]["finished_at"] is None
            task = await task_repo.get("task-residue", user_id="user-1")
            assert task["last_error"] is None, task
            assert task["run_count"] == 0, task

            # The occurrence is recorded as still owed its attempt, with the
            # backoff that stops the poll from becoming a refusal storm.
            assert service._capacity_refusals, "the refusal must be tracked, not swallowed"
            assert controller.snapshot()["rejected"] == 1
            assert controller.snapshot()["rejected_by_lane"]["background_autonomous"] == 1

            # The Gateway really did create a run row before it discovered it had
            # no slot, and that row is not left pending with no worker: it is
            # failed, tagged with the stable code, so nothing can mistake it for a
            # run that executed and nothing can be stranded waiting on it.
            durable = await run_manager.list_by_thread(rows[0]["thread_id"])
            assert len(durable) == 1, durable
            assert durable[0].status == RunStatus.error
            assert durable[0].task is None
            assert RUN_ADMISSION_REJECTED_CODE in (durable[0].error or "")
            # And that is precisely why the occurrence must not simply retry the
            # same key: the key now resolves to the row above forever.
            assert durable[0].idempotency_key == f"scheduled-task:{rows[0]['id']}"
            assert durable[0].idempotency_reused is False

            # Nothing is stranded in a state that pretends work happened.
            for row in await run_repo.list_by_task("task-residue"):
                assert row["status"] == "queued"
                assert row["run_id"] is None
    finally:
        await close_engine()


@pytest.mark.asyncio
async def test_a_refused_manual_trigger_reports_queued_rather_than_failed(tmp_path):
    """A manual trigger is an explicit request, so its answer matters most: under
    saturation the honest answer is "queued, we will run it", not a 502 that
    reads as "your trigger failed"."""
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    try:
        sf = get_session_factory()
        assert sf is not None
        task_repo = ScheduledTaskRepository(sf)
        run_repo = ScheduledTaskRunRepository(sf)
        now = datetime.now(UTC)
        task = await _seed_cron_task(task_repo, task_id="task-manual", now=now)

        controller = RunAdmissionController(max_concurrent_runs=1)
        set_run_admission_controller(controller)
        controller.try_admit()

        from app.gateway.services import launch_scheduled_thread_run

        run_manager = RunManager(store=MemoryRunStore())
        app = _app(run_manager)
        service = _make_scheduler(
            task_repo,
            run_repo,
            lambda **kwargs: launch_scheduled_thread_run(app=app, **kwargs),
        )

        with _no_worker_patches():
            result = await service.dispatch_task(task, now=now, trigger="manual")

        assert result["outcome"] == "queued", result
        rows = await run_repo.list_by_task("task-manual")
        assert rows[0]["status"] == "queued"
        refreshed = await task_repo.get("task-manual", user_id="user-1")
        assert refreshed["status"] == "paused" or refreshed["status"] == "enabled"
        assert refreshed["run_count"] == 0
    finally:
        await close_engine()


# ---------------------------------------------------------------------------
# MCP task notifications
# ---------------------------------------------------------------------------


class _RecordingNotificationRepository:
    """Repository double that keeps the *real* transition semantics we assert on.

    ``release_notification_claim(replace_with_latest=True)`` puts the row back in
    ``pending`` without advancing ``dispatch_attempt``; only
    ``finish_notification_run(delivered=False)`` advances it. Mirroring that
    matters, because whether the next delivery gets a fresh idempotency key is
    the whole question.
    """

    def __init__(self) -> None:
        self.notification_status = "claimed"
        self.notification_attempt_count = 0
        self.dispatch_attempt = 0
        self.notification_run_id: str | None = None
        self.notification_error: str | None = None
        self.dead_letters: list[dict] = []
        self.finishes: list[dict] = []

    def record(self) -> dict:
        return {
            "id": "task-1",
            "user_id": "user-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "driver_name": "fake",
            "dispatch_version": 2,
            "dispatch_attempt": self.dispatch_attempt,
            "dispatch_event": {"status": "completed"},
            "notification_status": self.notification_status,
            "notification_attempt_count": self.notification_attempt_count,
            "notification_run_id": self.notification_run_id,
            "notification_error": self.notification_error,
        }

    async def release_notification_claim(
        self,
        task_id,
        *,
        lease_owner,
        next_notification_at,
        error,
        replace_with_latest,
        count_failure=False,
    ):
        self.notification_status = "pending" if replace_with_latest else "retry"
        self.notification_error = error
        if replace_with_latest:
            self.notification_run_id = None
        if count_failure:
            self.notification_attempt_count += 1
        return True

    async def mark_notification_dispatched(self, task_id, *, lease_owner, dispatch_version, run_id, now):
        self.notification_status = "dispatched"
        self.notification_run_id = run_id
        self.notification_error = None
        return True

    async def finish_notification_run(self, task_id, *, lease_owner, dispatch_version, delivered, next_notification_at, error, now):
        self.finishes.append({"delivered": delivered, "error": error})
        if delivered:
            self.notification_status = "delivered"
            self.notification_run_id = None
            self.dispatch_attempt = 0
            self.notification_attempt_count = 0
        else:
            self.notification_status = "retry"
            self.dispatch_attempt += 1
            self.notification_attempt_count += 1
            self.notification_run_id = None
            self.notification_error = error
        return True

    async def defer_dispatched_notification(self, *args, **kwargs):
        return True

    async def dead_letter_notification(self, task_id, *, lease_owner, dispatch_version, error, count_failure, now):
        self.dead_letters.append({"error": error})
        self.notification_status = "dead_letter"
        return True


def _make_mcp_service(repo, launch, get_run) -> McpTaskService:
    from alpha.mcp.tasks import McpTaskDriverRegistry

    return McpTaskService(
        repository=repo,
        drivers=McpTaskDriverRegistry(),
        poll_interval_seconds=5,
        lease_seconds=120,
        max_concurrent_polls=3,
        launch_notification=launch,
        get_run=get_run,
    )


@pytest.mark.asyncio
async def test_a_refused_mcp_notification_is_waited_out_and_never_dead_lettered():
    """The regression.

    A saturated Gateway refuses the delivery run with 429. That must not spend
    the five-attempt dead-letter budget: the notification is not broken, the
    server is busy. Drive it far past the old budget and it must still be
    deliverable, with a fresh idempotency key on the attempt that finally gets a
    slot.
    """
    repo = _RecordingNotificationRepository()
    run_manager = RunManager(store=MemoryRunStore())
    app = _app(run_manager)
    await app.state.thread_store.create("thread-1", assistant_id="lead_agent", user_id=None)

    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)
    controller.try_admit()

    from app.gateway.services import launch_mcp_task_notification_run

    launched_keys: list[str] = []

    async def launch(**kwargs):
        launched_keys.append(f"mcp-task:{kwargs['task_id']}:{kwargs['dispatch_version']}:{kwargs['dispatch_attempt']}")
        return await launch_mcp_task_notification_run(app=app, **kwargs)

    service = _make_mcp_service(repo, launch, run_manager.get)

    now = datetime.now(UTC)
    with _no_worker_patches():
        # More polls than the old five-attempt budget: a busy gateway used to
        # dead-letter the notification somewhere in here.
        for poll in range(12):
            await service._notify_one(repo.record(), now=now + timedelta(seconds=5 * poll))
            # A claim rebuilds the snapshot, as claim_notification_work does.
            if repo.notification_status == "pending":
                repo.notification_status = "claimed"

        assert repo.dead_letters == [], f"capacity must never dead-letter: {repo.dead_letters}"
        # Not one delivery attempt was spent on a busy gateway.
        assert repo.notification_attempt_count == 0, repo.notification_attempt_count
        # The last thing that happened was a wait, not a claim of delivery.
        assert repo.notification_status != "dispatched"
        # The gateway was never even asked: a refusal would have created and then
        # failed a durable run row, spending the notification's idempotency key
        # for an attempt that could not run.
        assert launched_keys == [], launched_keys
        assert await run_manager.list_by_thread("thread-1") == []

        # Give the notification a slot and it delivers for real.
        controller.release()
        assert has_run_capacity() is True
        repo.notification_status = "claimed"
        await service._notify_one(repo.record(), now=now + timedelta(seconds=600))
        await _drain_callbacks()

        assert repo.notification_status == "dispatched", repo.notification_status
        assert repo.notification_run_id is not None
        # Exactly one real attempt, under the key nobody had spent.
        assert len(launched_keys) == 1, launched_keys
        runs = await run_manager.list_by_thread("thread-1")
        live = [record for record in runs if record.status == RunStatus.pending]
        assert len(live) == 1, [(r.run_id, r.status) for r in runs]
        assert live[0].run_id == repo.notification_run_id
        assert live[0].idempotency_key == "mcp-task:task-1:2:0"


@pytest.mark.asyncio
async def test_a_refused_launch_retries_under_a_fresh_idempotency_key():
    """The residue, and the property that makes a retry real work.

    The gate keeps a saturated gateway from being asked, but a slot can be taken
    between the gate and the launch. Then the Gateway creates the run row *before*
    it discovers it has no slot, and the refusal has already spent the
    notification's deterministic ``mcp-task:{task}:{version}:{attempt}`` key
    against a row that is failed and had no worker.

    So the retry must be launched under an attempt number the refused one did not
    consume. Reusing the key would resolve to the dead row, report "dispatched",
    and book its ``error`` status as a delivery failure -- a duplicate-looking
    success that delivers nothing.
    """
    repo = _RecordingNotificationRepository()
    run_manager = RunManager(store=MemoryRunStore())
    app = _app(run_manager)
    await app.state.thread_store.create("thread-1", assistant_id="lead_agent", user_id=None)

    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)

    from app.gateway.services import launch_mcp_task_notification_run

    launched_keys: list[str] = []
    saturate = {"armed": True}

    async def launch(**kwargs):
        launched_keys.append(f"mcp-task:{kwargs['task_id']}:{kwargs['dispatch_version']}:{kwargs['dispatch_attempt']}")
        if saturate["armed"]:
            # Saturated exactly in the window the gate cannot close.
            saturate["armed"] = False
            controller.try_admit()
        return await launch_mcp_task_notification_run(app=app, **kwargs)

    service = _make_mcp_service(repo, launch, run_manager.get)
    now = datetime.now(UTC)

    with _no_worker_patches():
        # Attempt 1: refused for real, after a durable row was created and failed.
        await service._notify_one(repo.record(), now=now)
        assert launched_keys == ["mcp-task:task-1:2:0"], launched_keys
        assert repo.notification_status == "pending"
        assert repo.notification_attempt_count == 0
        refused = await run_manager.list_by_thread("thread-1")
        assert len(refused) == 1
        assert refused[0].status == RunStatus.error
        assert RUN_ADMISSION_REJECTED_CODE in (refused[0].error or "")

        # Still saturated: the pre-flight gate declines to even ask, so no second
        # key is spent while there is provably nowhere to put a run.
        repo.notification_status = "claimed"
        await service._notify_one(repo.record(), now=now + timedelta(seconds=600))
        assert launched_keys == ["mcp-task:task-1:2:0"], launched_keys
        assert repo.notification_status == "pending", "a retry must not claim delivery"
        assert repo.notification_run_id is None
        assert repo.dead_letters == []

        # Capacity frees. This retry asks for real, and it must ask under a key
        # the refusal did not spend -- otherwise it resolves straight back to the
        # dead run row and reports a delivery that never happened.
        controller.release()
        repo.notification_status = "claimed"
        await service._notify_one(repo.record(), now=now + timedelta(seconds=1200))
        await _drain_callbacks()

    assert launched_keys == ["mcp-task:task-1:2:0", "mcp-task:task-1:2:1"], launched_keys
    assert repo.notification_status == "dispatched"
    runs = await run_manager.list_by_thread("thread-1")
    assert len(runs) == 2, [(r.run_id, r.status, r.idempotency_key) for r in runs]
    live = [record for record in runs if record.status == RunStatus.pending]
    assert len(live) == 1, [(r.run_id, r.status) for r in runs]
    assert live[0].idempotency_key == "mcp-task:task-1:2:1"
    assert live[0].run_id == repo.notification_run_id


@pytest.mark.asyncio
async def test_a_retry_that_resolves_to_a_refused_run_is_not_recorded_as_dispatched():
    """The half-dispatch, closed.

    An idempotency key the Gateway already spent resolves straight back to the
    refused run row, so a launch that *returns* is not the same as a launch that
    *ran*. Recording that as dispatched would point the durable task at a run
    that never executed and book its ``error`` status as a delivery failure.
    """
    repo = _RecordingNotificationRepository()
    run_manager = RunManager(store=MemoryRunStore())
    app = _app(run_manager)
    await app.state.thread_store.create("thread-1", assistant_id="lead_agent", user_id=None)

    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)
    saturate = {"armed": True}

    from app.gateway.services import launch_mcp_task_notification_run

    async def launch(**kwargs):
        if saturate["armed"]:
            # Saturate inside the launch, so the refusal is real: a durable run
            # row is created, found full, and failed with no worker.
            saturate["armed"] = False
            controller.try_admit()
        return await launch_mcp_task_notification_run(app=app, **kwargs)

    service = _make_mcp_service(repo, launch, run_manager.get)
    now = datetime.now(UTC)

    with _no_worker_patches():
        # The real first refusal, and the durable run row it leaves behind.
        await service._notify_one(repo.record(), now=now)
        assert repo.notification_status == "pending"
        assert repo.notification_attempt_count == 0
        refused = await run_manager.list_by_thread("thread-1")
        assert len(refused) == 1
        assert RUN_ADMISSION_REJECTED_CODE in (refused[0].error or "")

        # Capacity frees and the offset is lost -- a restart, or the retry budget
        # being pruned -- so the next attempt replays the key the refusal spent
        # and the Gateway hands back the same dead row instead of a run.
        controller.release()
        repo.notification_status = "claimed"
        record = repo.record()
        record["dispatch_attempt"] = 0
        service._capacity_spent_keys[("task-1", 2)] = 0
        service._capacity_retry_at[("task-1", 2)] = now
        await service._notify_one(record, now=now + timedelta(seconds=300))
        await _drain_callbacks()

    assert repo.notification_status == "pending", (
        "a replay that resolved to a refused run must wait, not claim dispatch"
    )
    assert repo.notification_run_id is None
    assert repo.dead_letters == []
    assert repo.notification_attempt_count == 0
    assert service._capacity_waits, "the replay must re-arm the capacity wait"
    # The replay created no second run: the key resolved to the existing row.
    assert len(await run_manager.list_by_thread("thread-1")) == 1


@pytest.mark.asyncio
async def test_a_capacity_wait_does_not_pin_delivery_to_a_stale_event():
    """The wait is a real wait: the next claim rebuilds the snapshot, so a
    notification cannot be delivered against a payload that has since been
    superseded while it sat out a full run budget."""
    repo = _RecordingNotificationRepository()
    run_manager = RunManager(store=MemoryRunStore())
    app = _app(run_manager)
    await app.state.thread_store.create("thread-1", assistant_id="lead_agent", user_id=None)

    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)
    controller.try_admit()

    from app.gateway.services import launch_mcp_task_notification_run

    async def launch(**kwargs):
        return await launch_mcp_task_notification_run(app=app, **kwargs)

    service = _make_mcp_service(repo, launch, run_manager.get)
    now = datetime.now(UTC)
    with _no_worker_patches():
        await service._notify_one(repo.record(), now=now)
    assert repo.notification_status == "pending", "the wait must ask for the latest event"


@pytest.mark.asyncio
async def test_the_real_repository_still_dead_letters_a_genuinely_broken_delivery():
    """The change must not disarm the budget that exists for real failures."""
    from fastapi import HTTPException

    repo = _RecordingNotificationRepository()
    launch = AsyncMock(side_effect=RuntimeError("the agent factory is broken"))
    service = _make_mcp_service(repo, launch, AsyncMock(return_value=None))
    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)

    now = datetime.now(UTC)
    for poll in range(6):
        record = repo.record()
        record["notification_attempt_count"] = poll
        await service._notify_one(record, now=now + timedelta(seconds=5 * poll))
        if repo.notification_status == "pending":
            repo.notification_status = "claimed"

    assert repo.dead_letters, "a real launch failure must still exhaust the budget"
    assert "Notification delivery stopped" in repo.dead_letters[-1]["error"]
    assert not is_run_capacity_refusal(HTTPException(status_code=500, detail="boom"))


@pytest.mark.asyncio
async def test_a_refused_notification_is_visible_to_an_operator(caplog):
    """Starvation that is only visible as a notification that never arrives is
    indistinguishable, to whoever is looking, from one that was never sent."""
    repo = _RecordingNotificationRepository()
    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)
    controller.try_admit()

    from alpha.ops.metrics import get_metrics_registry

    registry = get_metrics_registry()

    async def launch(**kwargs):
        raise HTTPException(
            status_code=429,
            detail={
                "code": RUN_ADMISSION_REJECTED_CODE,
                "message": "Gateway run capacity exhausted (1/1 runs in flight); retry in 1s",
                "retry_after_seconds": 1,
                "active_runs": 1,
                "max_concurrent_runs": 1,
            },
        )

    service = _make_mcp_service(repo, launch, AsyncMock(return_value=None))
    with caplog.at_level(logging.WARNING, logger="app.mcp_tasks.service"):
        await service._notify_one(repo.record(), now=datetime.now(UTC))

    assert repo.notification_status == "pending"
    assert repo.notification_attempt_count == 0
    assert "waiting on Gateway run capacity" in caplog.text
    exposition = registry.render_prometheus()
    assert "alpha_mcp_task_capacity_refusals" in exposition
    assert "alpha_mcp_task_capacity_waiting_tasks" in exposition
    assert "alpha_mcp_task_capacity_backoff_seconds" in exposition


def test_the_json_shape_of_the_snapshot_is_stable_and_additive():
    """``snapshot()`` is a diagnostics contract: existing keys keep their meaning,
    the new ones are what makes starvation attributable to a lane."""
    controller = RunAdmissionController(max_concurrent_runs=2)
    snapshot = controller.snapshot()
    for key in ("active", "peak", "limit", "admitted", "rejected", "active_by_lane"):
        assert key in snapshot, key
    for key in ("available", "rejected_by_lane"):
        assert key in snapshot, key
    json.dumps(snapshot)  # must stay JSON-serialisable for log/health use
    assert snapshot["available"] == 2
    assert sum(snapshot["rejected_by_lane"].values()) == 0
