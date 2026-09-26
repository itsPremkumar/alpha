"""Concurrent gateway runs must be bounded, and the bound must be honest.

``app.gateway.services.start_run`` attached one ``asyncio.Task`` per accepted
run with no ceiling. Every one of those tasks ends in a burst of durable
writes -- run status, delivery receipt, completion row, thread title, workspace
changes -- and all of them draw from a single SQLAlchemy engine whose
``database.pool_size`` defaults to 5 with a 30s ``command_timeout``. Under a
burst, finalization writes queue behind each other until that timeout expires,
so a healthy Gateway starts reporting failed runs and leaves receipts
unwritten.

The gate is :class:`alpha.runtime.lane_scheduler.RunAdmissionController`,
taken synchronously between durable admission and task attachment (that window
must not await) and released by a done callback on the worker task. It refuses
rather than queues: a queued gateway run would hold an HTTP request open across
the whole wait, and its durable row would sit ``pending`` with no worker around
to observe a cancellation that arrives meanwhile.

The unit tests pin the controller's arithmetic. The integration tests go through
the real ``start_run`` -- real ``RunManager``, real ``MemoryRunStore``, real
thread-store admission -- because a cap that is correct in isolation and not
wired to task attachment is not a cap at all.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from alpha.config.app_config import AppConfig, reset_app_config, set_app_config
from alpha.runtime import RunManager, RunStatus
from alpha.runtime.lane_scheduler import (
    MAX_CONCURRENT_RUNS_ENV_VAR,
    RUN_ADMISSION_REJECTED_CODE,
    AdmissionDecision,
    ExecutionLane,
    RunAdmissionController,
    RunAdmissionRejected,
    default_max_concurrent_runs,
    get_run_admission_controller,
    resolve_execution_lane,
    set_run_admission_controller,
)
from alpha.runtime.runs.store.memory import MemoryRunStore


@pytest.fixture(autouse=True)
def _stub_app_config():
    """Pin the config and the process-wide budget so a developer-local
    ``config.yaml`` or a leaked controller cannot move the numbers under test."""
    set_app_config(AppConfig.model_validate({"sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"}}))
    previous = set_run_admission_controller(None)
    yield
    set_run_admission_controller(previous)
    reset_app_config()


@pytest.fixture(scope="module", autouse=True)
def _warm_lazy_imports():
    """Pay ``start_run``'s one-time lazy-import cost before the timed sections.

    The first ``start_run`` reaches a large lazily-imported graph (agent tools,
    the MCP session pool, uvicorn), and an import holds the import lock
    *synchronously* on the event-loop thread. Left to the concurrent sections
    that would serialise the burst behind the import lock and make the
    assertions race the import rather than the admission gate. One real
    (already-completed) launch warms it, and every later call in this module
    measures the gate.
    """
    asyncio.run(_warm_one_run())


async def _warm_one_run() -> None:
    from app.gateway.services import start_run

    set_run_admission_controller(RunAdmissionController(max_concurrent_runs=1))

    async def no_op_run_agent(*args: Any, **kwargs: Any) -> None:
        return None

    run_manager = RunManager(store=MemoryRunStore())
    request = _request(run_manager)
    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=object()),
        patch("app.gateway.services.run_agent", side_effect=no_op_run_agent),
    ):
        record = await start_run(_run_body(), "thread-warmup", request)
        await record.task
        await _drain_callbacks()
    set_run_admission_controller(None)


# ---------------------------------------------------------------------------
# RunAdmissionController
# ---------------------------------------------------------------------------


def test_admission_is_capped_and_the_next_caller_is_refused_with_honest_numbers():
    controller = RunAdmissionController(max_concurrent_runs=3)

    decisions = [controller.try_admit() for _ in range(3)]
    assert [d.admitted for d in decisions] == [True, True, True]
    assert all(bool(d) for d in decisions)
    assert [d.active for d in decisions] == [1, 2, 3]

    refused = controller.try_admit()
    assert refused.admitted is False
    assert bool(refused) is False
    assert refused.code == RUN_ADMISSION_REJECTED_CODE
    assert refused.active == 3
    assert refused.limit == 3
    # A refusal must not have consumed a slot.
    assert controller.active == 3


def test_a_refused_caller_does_not_learn_more_than_the_cap_allows():
    controller = RunAdmissionController(max_concurrent_runs=1)
    assert controller.try_admit().admitted is True
    for _ in range(50):
        assert controller.try_admit().admitted is False
    assert controller.active == 1
    assert controller.snapshot()["peak"] == 1
    assert controller.snapshot()["rejected"] == 50


def test_releasing_a_slot_readmits_exactly_one_caller():
    controller = RunAdmissionController(max_concurrent_runs=1)
    assert controller.try_admit().admitted is True
    assert controller.try_admit().admitted is False

    assert controller.release() == 0
    assert controller.try_admit().admitted is True
    assert controller.try_admit().admitted is False


def test_over_release_cannot_invent_capacity():
    """A release for a run that never took a slot must not drive the counter
    negative, which would hand out capacity that does not exist."""
    controller = RunAdmissionController(max_concurrent_runs=2)
    assert controller.release() == 0
    assert controller.release() == 0
    admitted = [controller.try_admit() for _ in range(2)]
    assert all(d.admitted for d in admitted)
    assert controller.try_admit().admitted is False


def test_the_budget_is_global_across_lanes():
    """Lane bookkeeping is observability, not a second budget: an interactive
    run cannot spend the capacity a background run needs, and vice versa."""
    controller = RunAdmissionController(max_concurrent_runs=2)
    assert controller.try_admit(lane=ExecutionLane.USER_INTERACTION).admitted is True
    assert controller.try_admit(lane=ExecutionLane.BACKGROUND_AUTONOMOUS).admitted is True
    assert controller.try_admit(lane=ExecutionLane.BACKGROUND_AUTONOMOUS).admitted is False
    assert controller.try_admit(lane=ExecutionLane.SYSTEM_MAINTENANCE).admitted is False

    controller.release(lane=ExecutionLane.USER_INTERACTION)
    assert controller.try_admit(lane=ExecutionLane.SYSTEM_MAINTENANCE).admitted is True

    stats = controller.snapshot()
    assert stats["active_by_lane"] == {
        ExecutionLane.USER_INTERACTION.value: 0,
        ExecutionLane.BACKGROUND_AUTONOMOUS.value: 1,
        ExecutionLane.SYSTEM_MAINTENANCE.value: 1,
    }


def test_admit_or_raise_carries_the_decision_and_a_retry_hint():
    controller = RunAdmissionController(max_concurrent_runs=1, retry_after_seconds=2.5)
    assert controller.admit_or_raise(lane=ExecutionLane.USER_INTERACTION).admitted is True

    with pytest.raises(RunAdmissionRejected) as excinfo:
        controller.admit_or_raise(lane=ExecutionLane.USER_INTERACTION)

    exc = excinfo.value
    assert exc.code == RUN_ADMISSION_REJECTED_CODE
    assert exc.decision == AdmissionDecision(
        admitted=False,
        code=RUN_ADMISSION_REJECTED_CODE,
        active=1,
        limit=1,
        lane=ExecutionLane.USER_INTERACTION,
    )
    assert exc.retry_after_seconds == 2.5
    assert "1/1 runs in flight" in str(exc)


@pytest.mark.parametrize("bad_limit", [0, -1, 1.5, True, "4", None])
def test_the_cap_must_be_a_positive_integer(bad_limit):
    with pytest.raises(ValueError):
        RunAdmissionController(max_concurrent_runs=bad_limit)


def test_the_retry_hint_must_be_positive():
    with pytest.raises(ValueError):
        RunAdmissionController(max_concurrent_runs=1, retry_after_seconds=0)


def test_default_budget_scales_with_the_pooled_connection_count():
    """The shipped ``DatabaseConfig.pool_size`` default is 5, so the derived
    budget must track config rather than hardcode a number."""
    assert default_max_concurrent_runs() == 10

    set_app_config(
        AppConfig.model_validate(
            {
                "sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"},
                "database": {"pool_size": 3},
            }
        )
    )
    assert default_max_concurrent_runs() == 6


def test_an_unloadable_config_falls_back_to_the_constant_budget():
    with patch(
        "alpha.config.app_config.get_app_config",
        side_effect=RuntimeError("config unavailable"),
    ):
        assert default_max_concurrent_runs() == 10


def test_the_env_override_wins_over_the_derived_budget(monkeypatch):
    monkeypatch.setenv(MAX_CONCURRENT_RUNS_ENV_VAR, " 7 ")
    assert default_max_concurrent_runs() == 7


@pytest.mark.parametrize("raw", ["", "0", "-2", "many", "2.5"])
def test_an_unusable_env_override_falls_back_instead_of_crashing(monkeypatch, raw):
    """Admission control must never be the thing that takes the Gateway down."""
    monkeypatch.setenv(MAX_CONCURRENT_RUNS_ENV_VAR, raw)
    assert default_max_concurrent_runs() == 10


def test_the_process_wide_controller_can_be_installed_and_cleared():
    set_run_admission_controller(RunAdmissionController(max_concurrent_runs=4))

    installed = get_run_admission_controller()
    assert installed.max_concurrent_runs == 4
    # Memoised: a second caller gets the same budget object, not a new one.
    assert get_run_admission_controller() is installed

    set_run_admission_controller(None)
    # Clearing re-resolves from configuration rather than pinning the old cap.
    assert get_run_admission_controller().max_concurrent_runs == default_max_concurrent_runs()


@pytest.mark.parametrize(
    ("autonomous", "background", "expected"),
    [
        (False, False, ExecutionLane.USER_INTERACTION),
        (True, False, ExecutionLane.SYSTEM_MAINTENANCE),
        (False, True, ExecutionLane.BACKGROUND_AUTONOMOUS),
        (True, True, ExecutionLane.BACKGROUND_AUTONOMOUS),
    ],
)
def test_runs_are_classified_for_lane_bookkeeping(autonomous, background, expected):
    assert resolve_execution_lane(autonomous=autonomous, background=background) is expected


# ---------------------------------------------------------------------------
# start_run wiring
# ---------------------------------------------------------------------------


def _run_body(**overrides: Any) -> SimpleNamespace:
    """A complete run body derived from the real request model."""
    from pydantic_core import PydanticUndefined

    from app.gateway.run_models import RunCreateRequest

    defaults: dict[str, Any] = {}
    for name, spec in RunCreateRequest.model_fields.items():
        if spec.default is not PydanticUndefined:
            defaults[name] = spec.default
        elif spec.default_factory is not None:
            defaults[name] = spec.default_factory()
        else:
            defaults[name] = None
    defaults.update(
        {
            "assistant_id": "lead_agent",
            "input": {"messages": [{"role": "human", "content": "hi"}]},
            "metadata": {},
            "config": None,
            "context": None,
            "on_disconnect": "continue",
            "multitask_strategy": "reject",
            "stream_mode": None,
            "stream_subgraphs": False,
            "interrupt_before": None,
            "interrupt_after": None,
        }
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _request(run_manager: RunManager) -> SimpleNamespace:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from alpha.persistence.thread_meta.memory import MemoryThreadMetaStore
    from alpha.runtime.events.store.memory import MemoryRunEventStore

    store = InMemoryStore()
    return SimpleNamespace(
        headers={},
        state=SimpleNamespace(auth_source="session", user=SimpleNamespace(id="u1", system_role="user")),
        app=SimpleNamespace(
            state=SimpleNamespace(
                stream_bridge=SimpleNamespace(),
                run_manager=run_manager,
                checkpointer=InMemorySaver(),
                store=store,
                run_event_store=MemoryRunEventStore(),
                run_events_config=None,
                thread_store=MemoryThreadMetaStore(store),
            )
        ),
    )


async def _drain_callbacks(rounds: int = 5) -> None:
    """Let the event loop deliver done callbacks for finished tasks."""
    for _ in range(rounds):
        await asyncio.sleep(0)


async def _settle_slot(controller: RunAdmissionController) -> None:
    """Wait for a released slot to be observable, without polling the clock."""
    for _ in range(50):
        if controller.active == 0:
            return
        await _drain_callbacks(1)
    raise AssertionError(f"run admission slot was never released: {controller.snapshot()}")


async def _start_runs(
    *,
    attempts: int,
    release: asyncio.Event,
) -> tuple[list[Any], list[HTTPException], RunManager]:
    """Launch *attempts* runs whose workers block until *release* is set.

    Returns the admitted records, the refusals, and the run manager. The number
    that matters is the ratio: with a cap of N, at most N runs may come back
    admitted however many were asked for at once.
    """
    from app.gateway.services import start_run

    run_manager = RunManager(store=MemoryRunStore())
    request = _request(run_manager)

    async def blocking_run_agent(*args: Any, **kwargs: Any) -> None:
        await release.wait()

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=object()),
        patch("app.gateway.services.run_agent", side_effect=blocking_run_agent),
    ):
        results = await asyncio.gather(
            *(start_run(_run_body(), f"thread-admission-{index}", request) for index in range(attempts)),
            return_exceptions=True,
        )
        # Admitted workers are attached but have not been scheduled yet.
        await _drain_callbacks()

    started: list[Any] = []
    refused: list[HTTPException] = []
    for result in results:
        if isinstance(result, HTTPException):
            refused.append(result)
        else:
            started.append(result)
    return started, refused, run_manager


async def _durable_records(run_manager: RunManager, attempts: int) -> list[Any]:
    records: list[Any] = []
    for index in range(attempts):
        records.extend(await run_manager.list_by_thread(f"thread-admission-{index}"))
    return records


def _is_refused(record: Any) -> bool:
    return bool(record.error) and RUN_ADMISSION_REJECTED_CODE in record.error


@pytest.mark.anyio
async def test_a_burst_beyond_the_cap_is_refused_instead_of_all_admitted():
    """The regression: 8 concurrent run creations against a cap of 3 must yield
    exactly 3 live run tasks and 5 honest 429s, not 8 run tasks."""
    cap, attempts = 3, 8
    controller = RunAdmissionController(max_concurrent_runs=cap)
    set_run_admission_controller(controller)
    release = asyncio.Event()

    started, refused, run_manager = await asyncio.wait_for(
        _start_runs(attempts=attempts, release=release),
        timeout=20.0,
    )

    try:
        assert len(started) == cap, [record.run_id for record in started]
        assert len(refused) == attempts - cap
        assert controller.active == cap
        assert controller.snapshot()["peak"] == cap
        assert controller.snapshot()["rejected"] == attempts - cap
        # Every live run really has an attached, un-finished worker task.
        assert all(record.task is not None and not record.task.done() for record in started)

        for exc in refused:
            assert exc.status_code == 429
            assert exc.headers is not None
            assert exc.headers["Retry-After"] == "1"
            detail = exc.detail
            assert isinstance(detail, dict)
            assert detail["code"] == RUN_ADMISSION_REJECTED_CODE
            assert detail["max_concurrent_runs"] == cap
            assert detail["active_runs"] == cap
            assert detail["retry_after_seconds"] == 1

        # A refused run leaves no worker and no dangling ``pending`` row: the
        # durable record is failed with the refusal as its reason, so the
        # thread's active-run slot is not left to be mis-attributed by lease
        # recovery to a run that was refused rather than executed.
        durable = await _durable_records(run_manager, attempts)
        refused_records = [record for record in durable if _is_refused(record)]
        assert len(refused_records) == attempts - cap
        for record in refused_records:
            assert record.status == RunStatus.error
            assert record.task is None
        # Every admitted run has a durable row too, and none of them is a refusal.
        assert len(durable) == attempts
    finally:
        release.set()
        for record in started:
            if record.task is not None:
                await asyncio.gather(record.task, return_exceptions=True)
        await _drain_callbacks()


@pytest.mark.anyio
async def test_finishing_a_run_returns_its_slot_so_the_next_one_is_admitted():
    """The release has to be real: a cap that never frees up is an outage."""
    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)
    release = asyncio.Event()

    started, refused, _run_manager = await asyncio.wait_for(_start_runs(attempts=1, release=release), timeout=20.0)
    assert len(started) == 1
    assert refused == []
    assert controller.active == 1

    # Let the worker finish; the done callback on its task returns the slot.
    release.set()
    await asyncio.gather(started[0].task, return_exceptions=True)
    await _settle_slot(controller)

    # A second run now fits, proving the slot came back rather than the budget
    # simply being exhausted forever.
    started_again, refused_again, _ = await asyncio.wait_for(_start_runs(attempts=1, release=release), timeout=20.0)
    assert len(started_again) == 1
    assert refused_again == []
    await asyncio.gather(started_again[0].task, return_exceptions=True)
    await _settle_slot(controller)


@pytest.mark.anyio
async def test_a_cancelled_worker_still_returns_its_slot():
    """Cancellation completes the task, which fires the done callback. If the
    release only ran on a clean return, a cancelled run would leak its slot and
    the Gateway would wedge at the cap."""
    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)
    release = asyncio.Event()

    started, refused, _run_manager = await asyncio.wait_for(_start_runs(attempts=1, release=release), timeout=20.0)
    assert len(started) == 1
    assert refused == []
    assert controller.active == 1

    started[0].task.cancel()
    await asyncio.gather(started[0].task, return_exceptions=True)
    await _settle_slot(controller)

    # The budget is usable again, so the cancel did not leak the slot.
    release.set()
    started_again, refused_again, _ = await asyncio.wait_for(_start_runs(attempts=1, release=release), timeout=20.0)
    assert len(started_again) == 1
    assert refused_again == []
    await asyncio.gather(started_again[0].task, return_exceptions=True)
    await _settle_slot(controller)


@pytest.mark.anyio
async def test_terminal_runs_do_not_consume_the_budget():
    """The cap counts in-flight run tasks, not runs that have already finished.
    Without the done-callback release this wedges after ``cap`` runs."""
    controller = RunAdmissionController(max_concurrent_runs=2)
    set_run_admission_controller(controller)
    release = asyncio.Event()
    release.set()

    for round_index in range(5):
        started, refused, _run_manager = await asyncio.wait_for(_start_runs(attempts=1, release=release), timeout=20.0)
        assert len(started) == 1, f"round {round_index}: a finished run must not consume the cap ({refused})"
        assert refused == []
        await asyncio.gather(started[0].task, return_exceptions=True)
        await _settle_slot(controller)


@pytest.mark.anyio
async def test_background_runs_share_the_same_budget_as_interactive_ones():
    """A scheduled occurrence must not be able to bypass the cap by arriving
    through the background lane: the budget is global, lanes are bookkeeping."""
    controller = RunAdmissionController(max_concurrent_runs=1)
    set_run_admission_controller(controller)
    release = asyncio.Event()

    scheduled = _run_body(metadata={"scheduled_task_run_id": "task-run-1"})
    interactive = _run_body()

    from app.gateway.services import start_run

    run_manager = RunManager(store=MemoryRunStore())
    request = _request(run_manager)

    admitted: list[Any] = []
    refusals: list[HTTPException] = []

    async def blocking_run_agent(*args: Any, **kwargs: Any) -> None:
        await release.wait()

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=object()),
        patch("app.gateway.services.run_agent", side_effect=blocking_run_agent),
    ):
        for body, thread_id in ((scheduled, "thread-bg-0"), (interactive, "thread-bg-1")):
            try:
                admitted.append(await start_run(body, thread_id, request))
            except HTTPException as exc:
                refusals.append(exc)
        await _drain_callbacks()

    try:
        assert len(admitted) == 1
        assert len(refusals) == 1
        assert refusals[0].status_code == 429
        assert refusals[0].detail["code"] == RUN_ADMISSION_REJECTED_CODE
        # Whichever lane was admitted, the other lane got nothing.
        lane_counts = controller.snapshot()["active_by_lane"]
        assert sum(lane_counts.values()) == 1
        assert lane_counts[ExecutionLane.BACKGROUND_AUTONOMOUS.value] + lane_counts[ExecutionLane.USER_INTERACTION.value] == 1
    finally:
        release.set()
        for record in admitted:
            if record.task is not None:
                await asyncio.gather(record.task, return_exceptions=True)
        await _drain_callbacks()
