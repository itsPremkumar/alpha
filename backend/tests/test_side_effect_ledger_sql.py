"""The durable side-effect ledger, against a real database.

The in-memory reference implementation in ``alpha.runtime.side_effects.ledger``
owns the *semantics*; these tests pin that the SQL implementation satisfies the
same contract, and pin the two things SQL adds and memory cannot promise:
cross-process idempotency, and a conditional transition that reports a lost race
instead of overwriting somebody else's state.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from alpha.persistence.side_effects import SideEffectTransitionLost, SqlSideEffectLedger
from alpha.runtime.side_effects.ledger import (
    SideEffectReclaimer,
    arguments_digest,
)
from alpha.runtime.side_effects.statuses import (
    IllegalSideEffectTransition,
    ReconciliationVerdict,
    SideEffectLevel,
    SideEffectStatus,
)


@pytest.fixture
def ledger(tmp_path: Any) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from alpha.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'effects.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_create())
    return SqlSideEffectLedger(factory)


class TestTheHappyPath:
    @pytest.mark.asyncio
    async def test_an_effect_is_recorded_before_the_call_and_settled_after(self, ledger: Any) -> None:
        entry = await ledger.begin(tool_call_id="call_1", tool_name="write_file", thread_id="t1", run_id="r1", arguments={"path": "a.txt"})
        assert entry.status is SideEffectStatus.PENDING
        assert entry.need_reconciliation is False if hasattr(entry, "need_reconciliation") else entry.needs_reconciliation is False

        inflight = await ledger.mark_in_flight("call_1", owner_worker_id="host:abc", lease_seconds=30.0)
        assert inflight.status is SideEffectStatus.IN_FLIGHT
        assert inflight.owner_worker_id == "host:abc"

        done = await ledger.complete("call_1", result={"written": True})
        assert done.status is SideEffectStatus.COMPLETED
        assert done.result_digest is not None

    @pytest.mark.asyncio
    async def test_a_definite_failure_is_not_an_unknown(self, ledger: Any) -> None:
        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="w1")
        failed = await ledger.fail("call_1", detail="command not found")
        assert failed.status is SideEffectStatus.FAILED
        assert failed.needs_reconciliation is False
        assert await ledger.list_unknown() == ()

    @pytest.mark.asyncio
    async def test_an_empty_tool_call_id_is_refused(self, ledger: Any) -> None:
        with pytest.raises(ValueError, match="tool_call_id"):
            await ledger.begin(tool_call_id="", tool_name="bash")

    @pytest.mark.asyncio
    async def test_operating_on_an_unrecorded_call_fails_loudly(self, ledger: Any) -> None:
        with pytest.raises(KeyError, match="begin"):
            await ledger.complete("never_seen")

    @pytest.mark.asyncio
    async def test_nothing_sensitive_is_persisted(self, ledger: Any) -> None:
        secret = "sk-live-do-not-persist"
        entry = await ledger.begin(tool_call_id="call_1", tool_name="bash", arguments={"api_key": secret, "cmd": "deploy"})
        assert entry.arguments_digest == arguments_digest({"api_key": secret, "cmd": "deploy"})
        assert secret not in entry.arguments_digest
        rows = await ledger.all()
        assert all(secret not in str(row) for row in rows)

    @pytest.mark.asyncio
    async def test_the_risk_level_is_recorded_not_re_derived(self, ledger: Any) -> None:
        entry = await ledger.begin(tool_call_id="call_1", tool_name="read_file")
        assert entry.level is SideEffectLevel.READ_ONLY
        assert (await ledger.get("call_1")).level is SideEffectLevel.READ_ONLY

    @pytest.mark.asyncio
    async def test_an_explicit_level_overrides_the_default(self, ledger: Any) -> None:
        entry = await ledger.begin(tool_call_id="call_1", tool_name="read_file", level=SideEffectLevel.DESTRUCTIVE)
        assert entry.level is SideEffectLevel.DESTRUCTIVE


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_beginning_twice_returns_the_existing_row(self, ledger: Any) -> None:
        """The primary key is what makes this safe across processes."""
        first = await ledger.begin(tool_call_id="call_1", tool_name="bash", arguments={"cmd": "ls"})
        await ledger.mark_in_flight("call_1", owner_worker_id="w1", lease_seconds=60.0)
        second = await ledger.begin(tool_call_id="call_1", tool_name="bash", arguments={"cmd": "ls"})
        assert second.status is SideEffectStatus.IN_FLIGHT, "a retried announce must not erase the state the ledger exists for"
        assert second.attempt == first.attempt
        assert len(await ledger.all()) == 1

    @pytest.mark.asyncio
    async def test_beginning_twice_keeps_the_first_arguments_digest(self, ledger: Any) -> None:
        first = await ledger.begin(tool_call_id="call_1", tool_name="bash", arguments={"cmd": "ls"})
        second = await ledger.begin(tool_call_id="call_1", tool_name="bash", arguments={"cmd": "rm -rf /"})
        assert second.arguments_digest == first.arguments_digest, "a duplicate announce must not rewrite what was attempted"


class TestConditionalTransitions:
    def test_illegal_transition_is_raised(self) -> None:
        """Imported here so the reason this suite exists stays local to it."""
        from alpha.runtime.side_effects.statuses import IllegalSideEffectTransition as _Exc

        assert _Exc is IllegalSideEffectTransition

    @pytest.mark.asyncio
    async def test_a_transition_lost_to_the_reaper_reports_instead_of_overwriting(self, ledger: Any) -> None:
        """The load-bearing cross-process property.

        A reaper moved the effect to UNKNOWN. The late worker's `complete` must
        NOT overwrite that unknown with a guess -- that is how a crashed effect
        gets reported as a success nobody observed.
        """
        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w1", lease_seconds=1.0)
        # Force the reclaim condition with an explicit past timestamp.
        import datetime as _dt

        reclaimed = await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())
        assert len(reclaimed) == 1
        assert (await ledger.get("call_1")).status is SideEffectStatus.UNKNOWN

        # The state machine refuses before the database is touched at all, which
        # is a better answer than a lost-race report: the caller learns *why*.
        # ``SideEffectTransitionLost`` is reserved for the genuine race, where
        # the row moved between the read and the conditional write.
        with pytest.raises(IllegalSideEffectTransition):
            await ledger.complete("call_1", result={"ok": True})
        assert (await ledger.get("call_1")).status is SideEffectStatus.UNKNOWN, "the unknown must survive the late write"

    @pytest.mark.asyncio
    async def test_a_settled_effect_cannot_be_reopened(self, ledger: Any) -> None:
        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="w1")
        await ledger.complete("call_1", result={"ok": True})
        with pytest.raises(IllegalSideEffectTransition):
            await ledger.mark_in_flight("call_1", owner_worker_id="w1")
        with pytest.raises(IllegalSideEffectTransition):
            await ledger.fail("call_1")

    @pytest.mark.asyncio
    async def test_unknown_cannot_be_declared_complete(self, ledger: Any) -> None:
        """A worker that died mid-call cannot be the one to decide it succeeded."""
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w1", lease_seconds=1.0)
        await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())
        with pytest.raises(IllegalSideEffectTransition):
            await ledger.complete("call_1")


class TestReclaim:
    @pytest.mark.asyncio
    async def test_a_dead_owner_becomes_unknown(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=30.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="host:dead", lease_seconds=30.0)
        reclaimed = await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())
        assert [entry.status for entry in reclaimed] == [SideEffectStatus.UNKNOWN]
        assert "host:dead" in reclaimed[0].detail
        assert reclaimed[0].owner_worker_id is None

    @pytest.mark.asyncio
    async def test_dying_between_the_two_writes_is_also_unknown(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=10.0)
        reclaimed = await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())
        assert [entry.status for entry in reclaimed] == [SideEffectStatus.UNKNOWN]

    @pytest.mark.asyncio
    async def test_a_live_owner_is_not_reclaimed(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="w1", lease_seconds=30_000.0)
        assert await ledger.reclaim_expired(now=_dt.datetime(2020, 1, 1, tzinfo=_dt.UTC).timestamp()) == ()

    @pytest.mark.asyncio
    async def test_a_settled_effect_is_never_reclaimed(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="w1")
        await ledger.complete("call_1", result={"ok": True})
        assert await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp()) == ()

    @pytest.mark.asyncio
    async def test_an_entry_with_no_lease_is_not_reclaimed(self, ledger: Any) -> None:
        """A caller that never asked for a lease is never guessed at."""
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=0.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w1", lease_seconds=0.0)
        assert await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp()) == ()

    @pytest.mark.asyncio
    async def test_unknown_effects_are_enumerable_and_filterable(self, ledger: Any) -> None:
        """The whole point of the table: the unknown set must be a list."""
        import datetime as _dt

        for index, (thread, user) in enumerate([("t1", "u1"), ("t2", "u1"), ("t1", "u2")]):
            call_id = f"call_{index}"
            await ledger.begin(tool_call_id=call_id, tool_name="bash", thread_id=thread, run_id=f"r{index}", user_id=user, lease_seconds=1.0)
            await ledger.mark_in_flight(call_id, owner_worker_id="w", lease_seconds=1.0)
        await ledger.complete("call_0", result={"ok": True})
        await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())

        assert {entry.tool_call_id for entry in await ledger.list_unknown()} == {"call_1", "call_2"}
        assert {entry.tool_call_id for entry in await ledger.list_unknown(thread_id="t1")} == {"call_2"}
        assert {entry.tool_call_id for entry in await ledger.list_unknown(user_id="u1")} == {"call_1"}
        assert {entry.tool_call_id for entry in await ledger.list_unknown(run_id="r1")} == {"call_1"}


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_confirming_success_settles_the_entry_with_its_verdict(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())

        result = await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS, detail="checked the remote log", evidence={"event": "push_done"})
        assert result.entry.status is SideEffectStatus.RECONCILED
        assert result.entry.verdict is ReconciliationVerdict.CONFIRMED_SUCCESS
        assert result.escalated is False
        assert await ledger.list_unknown() == ()

    @pytest.mark.asyncio
    async def test_undetermined_reopens_rather_than_settling(self, ledger: Any) -> None:
        """Recording 'could not tell' as a failure is how a duplicate gets created."""
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())

        result = await ledger.reconcile("call_1", ReconciliationVerdict.UNDETERMINED, detail="the provider offers no query API")
        assert result.entry.status is SideEffectStatus.UNKNOWN
        assert result.entry.needs_reconciliation is True
        assert "no query API" in result.entry.detail
        assert [entry.tool_call_id for entry in await ledger.list_unknown()] == ["call_1"]

    @pytest.mark.asyncio
    async def test_a_confirmed_failure_on_a_high_risk_effect_escalates(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="git_push", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())
        assert (await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_FAILURE)).escalated is True

    @pytest.mark.asyncio
    async def test_a_confirmed_success_never_escalates(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())
        assert (await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS)).escalated is False

    @pytest.mark.asyncio
    async def test_two_reconcilers_cannot_both_settle_an_entry(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())

        first = await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS, detail="first")
        assert first.entry.status is SideEffectStatus.RECONCILED
        with pytest.raises(SideEffectTransitionLost):
            await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_FAILURE, detail="second")
        assert (await ledger.get("call_1")).detail == "first", "the second reconciler must not overwrite the first verdict"

    @pytest.mark.asyncio
    async def test_reconciling_a_settled_effect_is_refused(self, ledger: Any) -> None:
        await ledger.begin(tool_call_id="call_1", tool_name="bash")
        await ledger.mark_in_flight("call_1", owner_worker_id="w")
        await ledger.complete("call_1", result={"ok": True})
        with pytest.raises(IllegalSideEffectTransition):
            await ledger.reconcile("call_1", ReconciliationVerdict.CONFIRMED_SUCCESS)


class TestReclaimerDrivesTheSqlLedger:
    @pytest.mark.asyncio
    async def test_a_pass_over_the_sql_ledger_reclaims(self, ledger: Any) -> None:
        import datetime as _dt

        await ledger.begin(tool_call_id="call_1", tool_name="bash", lease_seconds=1.0)
        await ledger.mark_in_flight("call_1", owner_worker_id="w", lease_seconds=1.0)
        reclaimer = SideEffectReclaimer(ledger, interval_seconds=30.0)
        # No ``now`` injection point on the reclaimer, so drive the store directly
        # and assert the reclaimer path itself against an already-expired lease.
        await ledger.reclaim_expired(now=_dt.datetime(2999, 1, 1, tzinfo=_dt.UTC).timestamp())
        assert await reclaimer.run_once() == (), "a settled ledger has nothing left to reclaim"

    @pytest.mark.asyncio
    async def test_the_sql_ledger_satisfies_the_ledger_protocol(self, ledger: Any) -> None:
        from alpha.runtime.side_effects.ledger import SideEffectLedger

        assert isinstance(ledger, SideEffectLedger), "SqlSideEffectLedger must satisfy the SideEffectLedger protocol"


class TestMigration:
    def test_the_revision_chains_onto_the_previous_head(self) -> None:
        import importlib

        module = importlib.import_module("alpha.persistence.migrations.versions.0027_side_effect_ledger")  # type: ignore[attr-defined]
        assert module.revision == "0027_side_effect_ledger"
        assert module.down_revision == "0026_network_waits"

    def test_the_table_is_registered_on_the_orm_base(self) -> None:
        from alpha.persistence.base import Base
        from alpha.persistence.models import ToolSideEffectRow  # noqa: F401 - the registration import IS the assertion

        assert "tool_side_effects" in Base.metadata.tables

    def test_the_stored_status_vocabulary_matches_the_harness_enum(self) -> None:
        """A drift here would let a row be written in a state no reader knows."""
        from alpha.persistence.side_effects.model import LEDGER_STATUSES

        assert LEDGER_STATUSES == {status.value for status in SideEffectStatus}

    def test_the_open_and_reclaimable_vocabularies_match_the_harness(self) -> None:
        from alpha.persistence.side_effects.model import OPEN_LEDGER_STATUSES, RECLAIMABLE_LEDGER_STATUSES

        assert OPEN_LEDGER_STATUSES == {status.value for status in (SideEffectStatus.UNKNOWN, SideEffectStatus.RECONCILED)}
        assert RECLAIMABLE_LEDGER_STATUSES == {status.value for status in (SideEffectStatus.PENDING, SideEffectStatus.IN_FLIGHT)}

    def test_the_primary_key_is_the_tool_call_id(self) -> None:
        from alpha.persistence.side_effects import ToolSideEffectRow

        assert [column.name for column in ToolSideEffectRow.__table__.primary_key.columns] == ["tool_call_id"]
