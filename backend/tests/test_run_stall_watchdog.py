"""Run stall watchdog: a live run that stops making progress must be finalised.

Production evidence (2026-10-02): run ``4565d275-1268-4016-b6b2-9dd337c14a56``
hung on an internal await and stayed ``status=running`` for 20+ minutes with no
heartbeat, no SSE frames and no error surfaced anywhere. Manual
``POST .../runs/{id}/cancel`` finalised it cleanly (``interrupted``), proving
``RunManager.cancel`` is sound — what was missing was anything calling it
*automatically*. This watchdog is that missing caller:

* scans inflight store rows for ``status == running`` whose ``updated_at``
  (bumped by every progress snapshot) is older than ``timeout_seconds``;
* double-reads the row so progress that lands mid-scan wins;
* cancels through the single universal primitive (local / durable / lease
  paths all converge in ``RunManager.cancel``);
* then persists a *terminal error* with an explanatory message and
  ``stop_reason="stalled"``, retrying once if the worker's own
  interrupted-finalisation races and clobbers it.

The watchdog is deliberately a pure consumer of public APIs
(``RunStore.list_inflight``/``get`` + ``RunManager.cancel``/``set_status``):
``runtime/runs/manager.py`` and ``worker.py`` are owned elsewhere and must not
change for this feature.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from alpha.runtime.runs.manager import RunManager, RunStartOutcome
from alpha.runtime.runs.schemas import RunStatus
from alpha.runtime.runs.store.memory import MemoryRunStore
from alpha.runtime.selfheal.run_stall import (
    STALL_STOP_REASON,
    RunStallWatchdog,
    RunStallSettings,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeClock:
    """Controllable clock: the watchdog compares ``now_fn()`` to row timestamps."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def _running_run(manager: RunManager, store: MemoryRunStore, thread_id: str):
    record = await manager.create(thread_id)
    outcome = await manager.try_start(record.run_id)
    assert outcome is RunStartOutcome.started
    # A progress snapshot at "real now" so the fake clock (also started at
    # real now) sees a fresh heartbeat until it advances.
    await manager.update_run_progress(record.run_id, message_count=1)
    return record


async def test_stale_running_run_is_cancelled_and_terminalised_as_stalled_error():
    clock = FakeClock()
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await _running_run(manager, store, "t-stale")
    watchdog = RunStallWatchdog(run_manager=manager, run_store=store, settings=RunStallSettings(timeout_seconds=900), now_fn=clock)

    # Fresh heartbeat: no action yet.
    assert await watchdog.scan_once() == []

    # 1000s without progress > 900s budget: stall.
    clock.advance(1000)
    acted = await watchdog.scan_once()
    assert acted == [record.run_id]

    row = await store.get(record.run_id)
    assert row is not None
    assert row["status"] == RunStatus.error
    assert row["error"] and "stall" in row["error"].lower()
    assert "900" in row["error"]
    assert row["stop_reason"] == STALL_STOP_REASON

    live = await manager.get(record.run_id)
    assert live is not None
    assert live.status == RunStatus.error
    assert live.stop_reason == STALL_STOP_REASON


async def test_running_run_with_recent_progress_is_left_alone():
    clock = FakeClock()
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await _running_run(manager, store, "t-fresh")
    watchdog = RunStallWatchdog(run_manager=manager, run_store=store, settings=RunStallSettings(timeout_seconds=900), now_fn=clock)

    clock.advance(60)  # well inside the budget
    assert await watchdog.scan_once() == []
    row = await store.get(record.run_id)
    assert row is not None
    assert row["status"] == RunStatus.running


async def test_pending_run_is_never_stall_claimed():
    """A pending run has no progress heartbeats by design; admission, not the
    watchdog, owns its fate."""
    clock = FakeClock()
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("t-pending")  # stays pending
    watchdog = RunStallWatchdog(run_manager=manager, run_store=store, settings=RunStallSettings(timeout_seconds=900), now_fn=clock)

    clock.advance(10_000)
    assert await watchdog.scan_once() == []
    row = await store.get(record.run_id)
    assert row is not None
    assert row["status"] == RunStatus.pending


async def test_terminal_runs_are_not_candidates():
    clock = FakeClock()
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await _running_run(manager, store, "t-done")
    await manager.set_status(record.run_id, RunStatus.success)
    watchdog = RunStallWatchdog(run_manager=manager, run_store=store, settings=RunStallSettings(timeout_seconds=900), now_fn=clock)

    clock.advance(100_000)
    assert await watchdog.scan_once() == []
    row = await store.get(record.run_id)
    assert row is not None
    assert row["status"] == RunStatus.success


async def test_store_only_row_not_in_this_process_registry_is_skipped():
    """Rows owned by another worker or already reclaimed by startup recovery are
    not this process's to cancel: ``RunManager.cancel`` returns
    ``not_active_locally`` and the watchdog must leave the row untouched."""
    clock = FakeClock()
    store = MemoryRunStore()
    owner = RunManager(store=store)
    record = await _running_run(owner, store, "t-foreign")

    foreign_manager = RunManager(store=store)  # shares the store, not the registry
    watchdog = RunStallWatchdog(run_manager=foreign_manager, run_store=store, settings=RunStallSettings(timeout_seconds=900), now_fn=clock)

    clock.advance(100_000)
    assert await watchdog.scan_once() == []
    row = await store.get(record.run_id)
    assert row is not None
    assert row["status"] == RunStatus.running


async def test_stall_error_survives_worker_clobber_of_interrupted():
    """The worker's own cancellation path persists ``interrupted`` after
    ``cancel()`` returns; if that lands after the watchdog's error write it
    would hide the stall reason from every UI. The watchdog re-reads and
    re-writes until the terminal row is stable."""

    class ClobberingManager:
        """Delegates to a real RunManager but simulates the worker race: the
        first ``set_status`` is followed by the worker persisting
        ``interrupted``."""

        def __init__(self, real: RunManager) -> None:
            self._real = real
            self._set_calls = 0
            self.clobber_attempts = 0

        async def cancel(self, run_id, *, action="interrupt"):
            return await self._real.cancel(run_id, action=action)

        async def set_status(self, run_id, status, *, error=None, stop_reason=None, persist=True):
            await self._real.set_status(run_id, status, error=error, stop_reason=stop_reason, persist=persist)
            self._set_calls += 1
            if self._set_calls == 1:
                # Worker finalisation lands late and tries to persist
                # interrupted over the stall error. The store's terminal-state
                # guard must refuse it, or the reason disappears from every UI.
                self.clobber_attempts += 1
                await self._real.set_status(run_id, RunStatus.interrupted)

    clock = FakeClock()
    store = MemoryRunStore()
    real = RunManager(store=store)
    record = await _running_run(real, store, "t-clobber")
    clobberer = ClobberingManager(real)
    watchdog = RunStallWatchdog(run_manager=clobberer, run_store=store, settings=RunStallSettings(timeout_seconds=900), now_fn=clock)

    clock.advance(1000)
    acted = await watchdog.scan_once()
    assert acted == [record.run_id]

    # The late worker write was genuinely attempted...
    assert clobberer.clobber_attempts == 1
    # ...and lost: the stall reason survives as the terminal state.
    row = await store.get(record.run_id)
    assert row is not None
    assert row["status"] == RunStatus.error
    assert row["stop_reason"] == STALL_STOP_REASON


async def test_scan_survives_a_broken_store():
    clock = FakeClock()

    class BrokenStore:
        async def list_inflight(self, *, before=None):
            raise RuntimeError("store exploded")

        async def get(self, run_id):
            raise RuntimeError("store exploded")

    watchdog = RunStallWatchdog(run_manager=None, run_store=BrokenStore(), settings=RunStallSettings(timeout_seconds=900), now_fn=clock)
    # A watchdog must never let a transient store failure kill its loop.
    assert await watchdog.scan_once() == []


async def test_start_and_stop_lifecycle():
    store = MemoryRunStore()
    manager = RunManager(store=store)

    class CountingWatchdog(RunStallWatchdog):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.scans = 0

        async def scan_once(self):
            self.scans += 1
            return []

    watchdog = CountingWatchdog(
        run_manager=manager,
        run_store=store,
        settings=RunStallSettings(timeout_seconds=900, interval_seconds=0.01),
    )
    watchdog.start()
    await asyncio.sleep(0.3)
    await watchdog.stop()
    assert watchdog.scans >= 1


async def test_settings_schema_defaults():
    defaults = RunStallSettings()
    assert defaults.timeout_seconds == 900
    assert defaults.interval_seconds == 30


class TestGatewayLifespanWiring:
    """A library that is tested and never started is a feature nobody gets.

    Same source-assertion style as ``tests/test_network_wiring.py``: the
    watchdog must be constructed from config, started with the run manager,
    and stopped inside the single composed admission-close step (the
    one-registration-per-phase pin in ``test_network_wiring.py`` forbids a
    second registration).
    """

    @staticmethod
    def _deps_source() -> str:
        import inspect
        from pathlib import Path

        import app.gateway.deps as deps_module

        return Path(inspect.getfile(deps_module)).read_text(encoding="utf-8")

    def test_lifespan_constructs_and_starts_the_watchdog(self) -> None:
        source = self._deps_source()
        assert "RunStallWatchdog(" in source, "the watchdog must be built by the lifespan"
        assert "stall_watchdog.start()" in source, "the scan loop must actually be started"
        assert "app.state.run_stall_watchdog = stall_watchdog" in source, "state must expose it for shutdown and ops"

    def test_lifespan_reads_run_stall_config(self) -> None:
        source = self._deps_source()
        assert 'getattr(config, "run_stall", None)' in source, "budgets must come from config.yaml's run_stall section"

    def test_shutdown_stops_the_watchdog_inside_admission_close(self) -> None:
        source = self._deps_source()
        close_at = source.index("async def close_admission")
        stop_at = source.index("stall_watchdog.stop")
        assert stop_at > close_at, "the watchdog must stop with admission close, not later"
        assert source.count("ShutdownPhase.ADMISSION_CLOSED,") == 1, "compose into the existing step; register() replaces per phase"
