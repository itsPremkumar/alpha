"""The side-effect ledger is wired to production, and its guards actually bite.

The finding this file answers
-----------------------------
``alpha.runtime.side_effects`` was complete, tested, migrated, and executed zero
times. A ledger nothing calls cannot make an irreversible effect accountable, so
``UNKNOWN`` was a status with no producer and ``reclaim_expired`` was a method
with no caller. These tests are the difference between that claim and the code.

They are written against **the real path**: a real ``McpTaskService.submit``
(the production durable-submit entry point), a real ``SqlSideEffectLedger`` over a
real SQLite database, and a real ``SideEffectReclaimer``. Nothing here calls the
recorder directly to prove the recorder works -- that would only prove the
recorder works with itself.

The three guards that must bite
-------------------------------
1. **The row exists.** A submit that returns normally has a durable ledger row.
2. **The ledger failing does not fail the action.** Break the ledger, submit
   again, and the submit still returns its task while the gap is *recorded*. An
   effect that silently vanishes because the bookkeeping was unavailable is the
   exact bug this layer exists to prevent, so the failure has to be loud.
3. **A lost worker becomes ``UNKNOWN``.** Not lost, not silently failed. The
   reclaimer is what makes the state reachable, and it is started by the Gateway
   (``app/gateway/deps.py``), so this exercises the same code production does.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path
from typing import Any

import pytest

from alpha.mcp.tasks import (
    McpTaskDriverRegistry,
    TaskSnapshot,
    TaskStatus,
    TaskSubmission,
    TaskSubmitRequest,
)
from alpha.persistence.mcp_tasks import DuplicateMcpRemoteTaskError
from alpha.persistence.side_effects import SqlSideEffectLedger
from alpha.runtime.side_effects import (
    SideEffectReclaimer,
    SideEffectRecorder,
    set_side_effect_recorder,
)
from alpha.runtime.side_effects.statuses import SideEffectStatus
from app.mcp_tasks.service import McpTaskService

# ---------------------------------------------------------------------------
# Fixtures / doubles
# ---------------------------------------------------------------------------


class FakeRepository:
    """The durable-task repository, in memory. Real service, real SQL ledger."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.create_error: Exception | None = None

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        if self.create_error is not None:
            raise self.create_error
        row = {"id": kwargs["task_id"], **kwargs}
        self.rows.append(row)
        return row

    async def claim_due_tasks(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def apply_snapshot(self, task_id: str, **kwargs: Any) -> bool:
        return True

    async def release_claim(self, task_id: str, **kwargs: Any) -> bool:
        return True


class FakeDriver:
    """An MCP task driver whose submit is a real irreversible remote effect."""

    def __init__(self, *, submission: TaskSubmission | None = None, submit_error: Exception | None = None) -> None:
        self.submission = submission
        self.submit_error = submit_error
        self.submit_calls: list[TaskSubmitRequest] = []
        self.cancel_calls: list[Any] = []

    async def submit(self, request: TaskSubmitRequest) -> TaskSubmission:
        self.submit_calls.append(request)
        if self.submit_error is not None:
            raise self.submit_error
        assert self.submission is not None
        return self.submission

    async def get_status(self, task: Any) -> TaskSnapshot:  # pragma: no cover - not reached by submit
        return TaskSnapshot(status=TaskStatus.SUBMITTED)

    async def cancel(self, task: Any) -> TaskSnapshot:
        self.cancel_calls.append(task)
        return TaskSnapshot(status=TaskStatus.CANCELLED)


def make_service(repo: Any, driver: Any) -> McpTaskService:
    registry = McpTaskDriverRegistry()
    registry.register("fake", driver)
    return McpTaskService(
        repository=repo,
        drivers=registry,
        poll_interval_seconds=5,
        lease_seconds=120,
        max_concurrent_polls=3,
    )


def make_request(**overrides: Any) -> TaskSubmitRequest:
    payload: dict[str, Any] = {
        "user_id": "user-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "tool_call_id": "call-submit-1",
        "server_name": "reports",
        "task_name": "Generate report",
        "arguments": {"topic": "MCP"},
        "driver_data": {"submit_tool": "submit"},
    }
    payload.update(overrides)
    return TaskSubmitRequest(**payload)


def good_submission() -> TaskSubmission:
    return TaskSubmission(
        remote_task_id="remote-1",
        snapshot=TaskSnapshot(status=TaskStatus.SUBMITTED, poll_after_seconds=9),
        driver_data={"submit_tool": "submit", "status_tool": "status", "cancel_tool": "cancel"},
    )


@pytest.fixture
def sql_ledger(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """A real ``SqlSideEffectLedger`` on a real SQLite database.

    The engine is disposed on a live loop for the same reason
    ``tests/test_side_effect_ledger_sql.py`` documents: Windows holds the file
    open otherwise, and the leaked aiosqlite worker thread is reported as an
    unhandled thread exception that hides real ones.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from alpha.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wired-effects.db'}")

    async def _create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    request.addfinalizer(lambda: asyncio.run(engine.dispose()))
    return SqlSideEffectLedger(async_sessionmaker(engine, expire_on_commit=False))


@pytest.fixture
def installed_recorder(request: pytest.FixtureRequest) -> Any:
    """Install a recorder process-wide and guarantee it is uninstalled after.

    The accessor is process state, so a test that leaves a durable recorder
    installed would silently change what the *next* test observes.
    """

    def _install(recorder: Any) -> Any:
        set_side_effect_recorder(recorder)
        return recorder

    yield _install
    set_side_effect_recorder(None)


# ---------------------------------------------------------------------------
# Guard 1: the row exists after a real submit
# ---------------------------------------------------------------------------


class TestTheLedgerRowExists:
    @pytest.mark.asyncio
    async def test_a_real_submit_writes_a_durable_ledger_row(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """The whole point: the effect Alpha just performed is on disk."""
        installed_recorder(SideEffectRecorder(sql_ledger))
        repo = FakeRepository()
        driver = FakeDriver(submission=good_submission())
        service = make_service(repo, driver)

        created = await service.submit(driver_name="fake", request=make_request())

        assert created["remote_task_id"] == "remote-1", "the submit itself must still work"

        entry = await sql_ledger.get("call-submit-1")
        assert entry is not None, "a real submit produced no ledger row at all"
        assert entry.status is SideEffectStatus.COMPLETED
        assert entry.tool_name == "mcp_task_submit:reports/Generate report"
        assert entry.thread_id == "thread-1"
        assert entry.run_id == "run-1"
        assert entry.user_id == "user-1"
        assert entry.needs_reconciliation is False
        assert await sql_ledger.list_unknown() == ()

    @pytest.mark.asyncio
    async def test_the_row_exists_before_the_remote_call_is_settled(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """``begin`` must precede the effect, not follow it.

        A row written *after* the remote call would be useless: the failure this
        ledger exists for is a process that dies during the call, and a
        post-hoc write cannot survive that.
        """
        installed_recorder(SideEffectRecorder(sql_ledger))
        repo = FakeRepository()
        observed: list[Any] = []

        class ObservingDriver(FakeDriver):
            async def submit(self, request: TaskSubmitRequest) -> TaskSubmission:
                # The remote effect is about to be issued. What does the ledger
                # already say?
                observed.append(await sql_ledger.get("call-submit-1"))
                return await super().submit(request)

        driver = ObservingDriver(submission=good_submission())
        service = make_service(repo, driver)

        await service.submit(driver_name="fake", request=make_request())

        assert len(observed) == 1
        entry = observed[0]
        assert entry is not None, "the ledger knew nothing while the remote call was in progress"
        assert entry.status is SideEffectStatus.IN_FLIGHT
        assert entry.owner_worker_id, "an in-flight entry with no owner has no lease to lose"

    @pytest.mark.asyncio
    async def test_nothing_sensitive_from_the_arguments_is_persisted(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """The submit arguments routinely carry a prompt or a token."""
        from alpha.runtime.side_effects import arguments_digest

        installed_recorder(SideEffectRecorder(sql_ledger))
        secret = "sk-live-do-not-persist"
        repo = FakeRepository()
        driver = FakeDriver(submission=good_submission())
        service = make_service(repo, driver)

        await service.submit(driver_name="fake", request=make_request(arguments={"api_key": secret, "topic": secret}))

        entry = await sql_ledger.get("call-submit-1")
        assert entry is not None
        assert entry.arguments_digest is not None
        assert secret not in entry.arguments_digest
        assert entry.arguments_digest == arguments_digest({"server_name": "reports", "task_name": "Generate report", "arguments": {"api_key": secret, "topic": secret}})

    @pytest.mark.asyncio
    async def test_a_submit_without_a_provider_tool_call_id_is_still_recorded(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """A null ``tool_call_id`` must not become an unrecorded effect.

        The fallback id is derived from ``local_task_id``, so a retry of the same
        submit lands on the same row rather than creating a second one.
        """
        installed_recorder(SideEffectRecorder(sql_ledger))
        repo = FakeRepository()
        driver = FakeDriver(submission=good_submission())
        service = make_service(repo, driver)

        created = await service.submit(
            driver_name="fake",
            request=make_request(tool_call_id=None, local_task_id="mcp-task-fixed"),
        )

        entry = await sql_ledger.get(f"mcp-task-submit:{created['id']}")
        assert entry is not None, "an effect with no provider tool_call_id vanished instead of being recorded"
        assert entry.status is SideEffectStatus.COMPLETED
        assert entry.run_id == "run-1", "the fallback id must not cost the entry its run scope"

    @pytest.mark.asyncio
    async def test_a_submit_with_no_ledger_installed_still_works(self, installed_recorder: Any) -> None:
        """Memory backend, embedded client, harness-only process: no ledger."""
        installed_recorder(None)
        repo = FakeRepository()
        driver = FakeDriver(submission=good_submission())
        service = make_service(repo, driver)

        created = await service.submit(driver_name="fake", request=make_request())

        assert created["remote_task_id"] == "remote-1"

        from alpha.runtime.side_effects import side_effect_recorder_stats

        stats = side_effect_recorder_stats()
        assert stats["announced"] == 1
        assert stats["unavailable"] == 1, "an absent ledger must be counted, not silently ignored"


# ---------------------------------------------------------------------------
# Guard 2: a ledger failure does not fail the action, and is recorded
# ---------------------------------------------------------------------------


class BrokenLedger:
    """A ledger whose every write fails, the way a dead database does."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def _fail(self, phase: str) -> None:
        self.calls.append(phase)
        raise RuntimeError("database unavailable")

    async def begin(self, **_kwargs: Any) -> Any:
        await self._fail("begin")

    async def mark_in_flight(self, *_args: Any, **_kwargs: Any) -> Any:
        await self._fail("mark_in_flight")

    async def complete(self, *_args: Any, **_kwargs: Any) -> Any:
        await self._fail("complete")

    async def fail(self, *_args: Any, **_kwargs: Any) -> Any:
        await self._fail("fail")

    async def reconcile(self, *_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - not used
        await self._fail("reconcile")

    async def reclaim_expired(self, **_kwargs: Any) -> tuple[()]:  # pragma: no cover - not used
        await self._fail("reclaim_expired")
        return ()

    async def list_unknown(self, **_kwargs: Any) -> tuple[()]:  # pragma: no cover - not used
        await self._fail("list_unknown")
        return ()

    async def get(self, _tool_call_id: str) -> Any:  # pragma: no cover - not used
        await self._fail("get")
        return None


class TestTheGuardBites:
    @pytest.mark.asyncio
    async def test_a_broken_ledger_does_not_fail_the_submit(self, installed_recorder: Any) -> None:
        """Fail-open, in the direction that matters.

        The remote task is submitted. The user's action must succeed regardless
        of whether Alpha could write a row about it -- a bookkeeping outage is a
        worse outcome than an unrecorded effect, never a second failure.
        """
        broken = BrokenLedger()
        installed_recorder(SideEffectRecorder(broken))
        repo = FakeRepository()
        driver = FakeDriver(submission=good_submission())
        service = make_service(repo, driver)

        created = await service.submit(driver_name="fake", request=make_request())

        assert created["remote_task_id"] == "remote-1", "the ledger broke and took the user's submit with it"
        assert len(driver.submit_calls) == 1, "the remote effect must actually have been attempted"
        assert repo.rows, "the durable task row must still have been written"
        assert broken.calls, "the broken ledger was never even asked to record anything"

    @pytest.mark.asyncio
    async def test_a_broken_ledger_is_reported_rather_than_swallowed(self, installed_recorder: Any, caplog: pytest.LogCaptureFixture) -> None:
        """The second half of fail-open: the gap must be *visible*.

        An effect that silently vanishes because the ledger was unavailable is
        the precise bug this layer exists to prevent, so a write failure has to
        leave a countable, loggable trace.
        """
        installed_recorder(SideEffectRecorder(BrokenLedger()))
        repo = FakeRepository()
        service = make_service(repo, FakeDriver(submission=good_submission()))

        with caplog.at_level(logging.WARNING, logger="alpha.runtime.side_effects.recorder"):
            await service.submit(driver_name="fake", request=make_request())

        messages = [record.getMessage() for record in caplog.records]
        assert any("the side-effect ledger rejected a begin" in message for message in messages), messages
        assert any("call-submit-1" in message for message in messages), "the warning must name the effect that went unrecorded"

    @pytest.mark.asyncio
    async def test_the_unrecorded_effect_is_counted_and_retained_in_process(self, installed_recorder: Any) -> None:
        """A process-local, bounded record -- stated as *not* durable.

        This is not a substitute for the ledger row it replaces; it exists so
        that a bookkeeping outage is reported rather than absorbed.
        """
        recorder = installed_recorder(SideEffectRecorder(BrokenLedger()))
        service = make_service(FakeRepository(), FakeDriver(submission=good_submission()))

        await service.submit(driver_name="fake", request=make_request())

        stats = recorder.stats()
        assert stats.announced == 1
        assert stats.unrecorded == 2, "both the announce and the settle failed and both must be counted"
        assert stats.settled == 0
        assert [item.phase for item in stats.recent_unaccounted] == ["begin", "complete"]
        assert stats.recent_unaccounted[0].tool_call_id == "call-submit-1"
        assert stats.recent_unaccounted[0].tool_name == "mcp_task_submit:reports/Generate report"
        assert "RuntimeError" in stats.recent_unaccounted[0].error

    @pytest.mark.asyncio
    async def test_the_counters_are_reachable_process_wide(self, installed_recorder: Any) -> None:
        """An operator surface reads these; that is the only reason they exist."""
        from alpha.runtime.side_effects import side_effect_recorder_stats

        installed_recorder(SideEffectRecorder(BrokenLedger()))
        service = make_service(FakeRepository(), FakeDriver(submission=good_submission()))
        await service.submit(driver_name="fake", request=make_request())

        stats = side_effect_recorder_stats()
        assert stats["announced"] == 1
        assert stats["unrecorded"] == 2
        assert stats["recent_unaccounted"], "the retained records must be part of the reported shape"

    @pytest.mark.asyncio
    async def test_a_submit_that_raises_does_not_propagate_a_ledger_error(self, installed_recorder: Any) -> None:
        """The *real* failure must reach the caller unmasked.

        A ledger that fails must not be able to replace the exception the user
        actually needs to see with its own.
        """
        installed_recorder(SideEffectRecorder(BrokenLedger()))
        repo = FakeRepository()
        service = make_service(repo, FakeDriver(submission=good_submission(), submit_error=RuntimeError("remote refused")))

        with pytest.raises(RuntimeError, match="remote refused"):
            await service.submit(driver_name="fake", request=make_request())


# ---------------------------------------------------------------------------
# Guard 3: a lost worker becomes UNKNOWN, not lost and not "failed"
# ---------------------------------------------------------------------------


class TestALostWorkerBecomesUnknown:
    @pytest.mark.asyncio
    async def test_a_crash_during_submit_leaves_the_effect_unaccounted_not_failed(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """The submit never returned and the worker went with it.

        The remote may or may not have started. ``FAILED`` would be the optimistic
        guess this ledger exists to avoid, so the entry is left ``IN_FLIGHT`` with
        a lease, and the reclaimer -- the same loop ``app/gateway/deps.py``
        starts -- turns it into ``UNKNOWN``.
        """
        installed_recorder(SideEffectRecorder(sql_ledger, lease_seconds=1.0))
        repo = FakeRepository()
        driver = FakeDriver(submission=good_submission(), submit_error=RuntimeError("connection reset after the request was sent"))
        service = make_service(repo, driver)

        with pytest.raises(RuntimeError, match="connection reset"):
            await service.submit(driver_name="fake", request=make_request())

        entry = await sql_ledger.get("call-submit-1")
        assert entry is not None, "the crash lost the record of the effect entirely"
        assert entry.status is not SideEffectStatus.FAILED, "an ambiguous outcome must never be recorded as a definite failure"
        assert entry.status in (SideEffectStatus.IN_FLIGHT, SideEffectStatus.UNKNOWN)

        # The reclaimer is the only thing that can make this UNKNOWN, and before
        # its pass the entry is still owed an answer by a worker that is gone.
        reclaimer = SideEffectReclaimer(sql_ledger)
        assert await reclaimer.run_once() == (), "the lease had not expired yet, so nothing may be reclaimed"

        reclaimed = await sql_ledger.reclaim_expired(now=dt.datetime.now(dt.UTC).timestamp() + 3600.0)
        assert len(reclaimed) == 1, "a dead worker's in-flight effect was lost rather than reclaimed"
        assert reclaimed[0].tool_call_id == "call-submit-1"
        assert reclaimed[0].status is SideEffectStatus.UNKNOWN
        assert "stopped reporting" in reclaimed[0].detail

    @pytest.mark.asyncio
    async def test_a_reclaimed_effect_is_enumerable_for_reconciliation(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """``UNKNOWN`` is only useful if a human or an agent can find it.

        This is the read path the whole package is for: "run 42 might have
        charged someone" is not actionable, "tool call X may have" is.
        """
        installed_recorder(SideEffectRecorder(sql_ledger, lease_seconds=1.0))
        repo = FakeRepository()
        service = make_service(repo, FakeDriver(submission=good_submission(), submit_error=RuntimeError("gateway died mid-call")))

        with pytest.raises(RuntimeError):
            await service.submit(driver_name="fake", request=make_request())

        await sql_ledger.reclaim_expired(now=dt.datetime.now(dt.UTC).timestamp() + 3600.0)

        unknown = await sql_ledger.list_unknown(thread_id="thread-1")
        assert len(unknown) == 1
        assert unknown[0].tool_call_id == "call-submit-1"
        assert unknown[0].tool_name == "mcp_task_submit:reports/Generate report"
        assert unknown[0].needs_reconciliation is True
        assert unknown[0].level.value == "high_risk", "an UNKNOWN submit must carry the risk that makes it worth a human's time"

        # And the scope filters a reconciler actually uses.
        assert await sql_ledger.list_unknown(run_id="run-1") == unknown
        assert await sql_ledger.list_unknown(thread_id="some-other-thread") == ()

    @pytest.mark.asyncio
    async def test_a_reclaimed_effect_cannot_be_declared_complete_afterwards(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """The transition table is what stops a late worker guessing."""
        from alpha.runtime.side_effects.statuses import IllegalSideEffectTransition

        installed_recorder(SideEffectRecorder(sql_ledger, lease_seconds=1.0))
        service = make_service(FakeRepository(), FakeDriver(submission=good_submission(), submit_error=RuntimeError("gone")))

        with pytest.raises(RuntimeError):
            await service.submit(driver_name="fake", request=make_request())

        await sql_ledger.reclaim_expired(now=dt.datetime.now(dt.UTC).timestamp() + 3600.0)

        with pytest.raises(IllegalSideEffectTransition):
            await sql_ledger.complete("call-submit-1", result={"ok": True})
        assert (await sql_ledger.get("call-submit-1")).status is SideEffectStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_the_reclaimer_loop_the_gateway_starts_actually_turns_entries_unknown(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """Drive the real loop, not ``reclaim_expired`` directly.

        ``app/gateway/deps.py`` starts ``SideEffectReclaimer``; this is the same
        object, so the assertion is about the production wiring rather than the
        method in isolation.
        """
        installed_recorder(SideEffectRecorder(sql_ledger, lease_seconds=1.0))
        service = make_service(FakeRepository(), FakeDriver(submission=good_submission(), submit_error=RuntimeError("worker vanished")))

        with pytest.raises(RuntimeError):
            await service.submit(driver_name="fake", request=make_request())

        reclaimer = SideEffectReclaimer(sql_ledger)
        await reclaimer.run_once()
        assert (await sql_ledger.get("call-submit-1")).status is not SideEffectStatus.UNKNOWN, "the lease has not expired in real time yet"

        # Time passes -- the process was gone for longer than its lease.
        await sql_ledger.reclaim_expired(now=dt.datetime.now(dt.UTC).timestamp() + 3600.0)
        await reclaimer.run_once()

        entry = await sql_ledger.get("call-submit-1")
        assert entry is not None and entry.status is SideEffectStatus.UNKNOWN
        assert entry.owner_worker_id is None, "a reclaimed entry must not keep a lease somebody could renew"
        assert entry.lease_expires_at is None

    @pytest.mark.asyncio
    async def test_a_settled_submit_is_never_reclaimed(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """A reclaimer that converted a *completed* effect would be a data-loss bug.

        This is the false-positive half of guard 3: `reclaim_expired` must leave
        a normally finished submit alone no matter how long ago it ran.
        """
        installed_recorder(SideEffectRecorder(sql_ledger, lease_seconds=1.0))
        service = make_service(FakeRepository(), FakeDriver(submission=good_submission()))

        await service.submit(driver_name="fake", request=make_request())

        reclaimer = SideEffectReclaimer(sql_ledger)
        await reclaimer.run_once()
        assert await sql_ledger.reclaim_expired(now=dt.datetime.now(dt.UTC).timestamp() + 86_400.0) == ()

        entry = await sql_ledger.get("call-submit-1")
        assert entry is not None and entry.status is SideEffectStatus.COMPLETED
        assert await sql_ledger.list_unknown() == ()

    @pytest.mark.asyncio
    async def test_a_duplicate_remote_handle_settles_rather_than_becoming_unknown(self, sql_ledger: Any, installed_recorder: Any) -> None:
        """A duplicate is a *known* outcome, and must not page a human.

        The remote work exists and has a durable owner, so the bracket settles it
        before the re-raise. Leaving it in flight would put an entry in the
        reconciliation queue for a non-problem.
        """
        installed_recorder(SideEffectRecorder(sql_ledger, lease_seconds=1.0))
        repo = FakeRepository()
        repo.create_error = DuplicateMcpRemoteTaskError("already tracked")
        service = make_service(repo, FakeDriver(submission=good_submission()))

        with pytest.raises(DuplicateMcpRemoteTaskError):
            await service.submit(driver_name="fake", request=make_request())

        entry = await sql_ledger.get("call-submit-1")
        assert entry is not None and entry.status is SideEffectStatus.COMPLETED
        assert await sql_ledger.reclaim_expired(now=dt.datetime.now(dt.UTC).timestamp() + 3600.0) == ()
        assert await sql_ledger.list_unknown() == ()
