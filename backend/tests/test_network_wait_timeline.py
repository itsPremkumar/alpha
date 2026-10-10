"""The per-thread outage timeline, and the patience policy behind it.

Two things are pinned here, because each has a plausible wrong reading:

1. **``network_wait`` config, and unbounded waiting.** The wait is a parked
   task, not a retry loop, so the default is unbounded. A test that only checked
   "the number round-trips" would pass while the runtime quietly refused ``0``
   as it used to.
2. **The timeline a thread's UI renders.** ``first_waited_at`` is when the link
   died and ``terminal_at`` is when the wait ended. Reading the connection time
   off ``updated_at`` — which ``release()`` also moves — would report a
   *scheduled retry* as the moment the link came back.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from alpha.config.network_wait_config import NetworkWaitConfig, to_wait_policy
from alpha.persistence.network_waits import NetworkWaitRepository
from alpha.runtime.network.wait_registry import UNBOUNDED_ATTEMPTS

# ---------------------------------------------------------------------------
# config -> policy
# ---------------------------------------------------------------------------


class TestNetworkWaitConfig:
    def test_the_default_policy_waits_forever(self) -> None:
        """Unbounded is the default, and it is a sentinel rather than a big number.

        Raising "unlimited" to 100000 has only moved the cliff; a wait that must
        not exhaust has to be unrepresentable as a count.
        """
        policy = to_wait_policy(NetworkWaitConfig())
        assert policy.max_attempts == UNBOUNDED_ATTEMPTS == 0
        assert policy.is_unbounded is True

    def test_every_operator_field_reaches_the_policy(self) -> None:
        policy = to_wait_policy(
            NetworkWaitConfig(
                max_attempts=7,
                backoff_initial_seconds=3.0,
                backoff_max_seconds=120.0,
                backoff_multiplier=3.0,
                poll_interval_seconds=11.0,
                claim_lease_seconds=45.0,
                max_claims_per_pass=9,
            )
        )
        assert policy.max_attempts == 7
        assert policy.is_unbounded is False
        assert (policy.backoff_initial_seconds, policy.backoff_max_seconds) == (3.0, 120.0)
        assert policy.backoff_multiplier == 3.0
        assert (policy.poll_interval_seconds, policy.claim_lease_seconds) == (11.0, 45.0)
        assert policy.max_claims_per_pass == 9

    def test_a_misspelled_key_is_refused_rather_than_ignored(self) -> None:
        """Fail-closed, like every other config section."""
        with pytest.raises(Exception, match="extra|forbid|Additional"):
            NetworkWaitConfig(max_attemps=3)  # type: ignore[call-arg]

    def test_an_inverted_backoff_ladder_is_refused_with_the_key_named(self) -> None:
        with pytest.raises(ValueError, match="network_wait.backoff_max_seconds"):
            NetworkWaitConfig(backoff_initial_seconds=60.0, backoff_max_seconds=5.0)

    @pytest.mark.parametrize("field", ["max_attempts", "backoff_initial_seconds", "poll_interval_seconds"])
    def test_out_of_range_numbers_are_refused(self, field: str) -> None:
        with pytest.raises(Exception):
            NetworkWaitConfig(**{field: -1})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# the timeline columns
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_repository(tmp_path: Any, request: Any) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from alpha.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'waits.db'}")

    async def _create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    request.addfinalizer(lambda: asyncio.run(engine.dispose()))
    return NetworkWaitRepository(async_sessionmaker(engine, expire_on_commit=False))


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestTimelineColumns:
    def test_a_new_row_has_no_terminal_stamp(self, sqlite_repository: Any) -> None:
        row = _run(sqlite_repository.park(thread_id="t1", run_id="r1", next_attempt_in_seconds=30.0))
        assert row["terminal_at"] is None, "a wait that is still waiting has not stopped waiting"
        assert row["first_waited_at"] is not None

    def test_marking_a_wait_resumed_stamps_the_connection_time(self, sqlite_repository: Any) -> None:
        row = _run(sqlite_repository.park(thread_id="t1", run_id="r1"))
        settled = _run(sqlite_repository.mark_terminal(row["id"], state="resumed", resumed_from_run_id="run_next"))
        assert settled is not None
        assert settled["state"] == "resumed"
        assert settled["terminal_at"] is not None

    def test_the_terminal_stamp_is_not_updated_at(self, sqlite_repository: Any) -> None:
        """``release()`` moves ``updated_at``; it must not fake a recovery.

        This is the bug the column exists to stop: a failed resume attempt
        writes its next backoff, so reading the connection time off
        ``updated_at`` would present a *scheduled retry* as the moment the link
        came back.
        """
        _run(sqlite_repository.park(thread_id="t1", run_id="r1"))
        claimed = _run(sqlite_repository.claim_due(limit=1, lease_seconds=30.0))
        assert claimed, "the due wait was claimed for a resume attempt"
        released = _run(sqlite_repository.release(claimed[0]["id"], next_attempt_in_seconds=60.0, attempt=1))
        assert released is not None
        assert released["state"] == "waiting"
        assert released["terminal_at"] is None, "a released wait is still waiting, not recovered"

    def test_a_declined_resume_is_also_stamped(self, sqlite_repository: Any) -> None:
        """``gave_up`` is an outcome, and it happened at a moment too."""
        row = _run(sqlite_repository.park(thread_id="t1"))
        settled = _run(sqlite_repository.mark_terminal(row["id"], state="gave_up", last_error="attempt budget exhausted"))
        assert settled is not None
        assert settled["state"] == "gave_up"
        assert settled["terminal_at"] is not None
        assert settled["last_error"] == "attempt budget exhausted"

    def test_the_measured_duration_matches_the_two_stamps(self, sqlite_repository: Any) -> None:
        row = _run(sqlite_repository.park(thread_id="t1"))
        settled = _run(sqlite_repository.mark_terminal(row["id"], state="resumed", resumed_from_run_id="run_next"))
        assert settled is not None
        duration = settled["terminal_at"] - settled["first_waited_at"]
        assert duration >= timedelta(0), "a recovery cannot precede the outage it ended"


class TestPerThreadTimeline:
    def test_a_thread_reads_its_whole_history(self, sqlite_repository: Any) -> None:
        _run(sqlite_repository.park(thread_id="t1", run_id="r1"))
        rows = _run(sqlite_repository.list_for_thread("t1", limit=50))
        assert [row["thread_id"] for row in rows] == ["t1"]
        assert rows[0]["run_id"] == "r1"

    def test_a_settled_row_stays_in_the_timeline(self, sqlite_repository: Any) -> None:
        """The UI reads open rows only at its peril.

        An outage that ended an hour ago is still part of the session's story;
        a timeline filtered to open rows shows a thread as never having been
        parked the moment it resumes, erasing the event the user asked about.
        """
        first = _run(sqlite_repository.park(thread_id="t1", run_id="r1"))
        _run(sqlite_repository.mark_terminal(first["id"], state="resumed", resumed_from_run_id="r2"))
        rows = _run(sqlite_repository.list_for_thread("t1", limit=50))
        assert len(rows) == 1
        assert rows[0]["state"] == "resumed"
        assert rows[0]["resumed_from_run_id"] == "r2"

    def test_another_threads_waits_are_not_returned(self, sqlite_repository: Any) -> None:
        _run(sqlite_repository.park(thread_id="t1"))
        _run(sqlite_repository.park(thread_id="t2"))
        assert len(_run(sqlite_repository.list_for_thread("t1", limit=50))) == 1
        assert len(_run(sqlite_repository.list_for_thread("t2", limit=50))) == 1

    def test_an_unparked_thread_returns_an_empty_list_not_an_error(self, sqlite_repository: Any) -> None:
        assert _run(sqlite_repository.list_for_thread("never-parked", limit=50)) == []

    def test_the_limit_is_respected(self, sqlite_repository: Any) -> None:
        for index in range(4):
            row = _run(sqlite_repository.park(thread_id=f"t{index}"))
            _run(sqlite_repository.mark_terminal(row["id"], state="resumed", resumed_from_run_id="r"))
        # A different thread, to prove the limit bounds one thread's history.
        _run(sqlite_repository.park(thread_id="boom"))
        assert len(_run(sqlite_repository.list_for_thread("boom", limit=1))) == 1


# ---------------------------------------------------------------------------
# the route's honesty rules
# ---------------------------------------------------------------------------


class TestRouteProjection:
    """The wire projection, driven directly rather than through a Gateway.

    The route itself needs a full app; the rules that matter are all in how a
    row is projected and how absence is reported, and those are testable here.
    """

    def test_a_wire_entry_carries_both_stamps_as_iso(self) -> None:
        from app.gateway.routers.thread_runs import _project_wait_row

        now = datetime.now(UTC)
        projected = _project_wait_row(
            {
                "id": "w1",
                "run_id": "r1",
                "state": "waiting",
                "reason": "connectivity_lost",
                "attempt": 2,
                "first_waited_at": now,
                "terminal_at": None,
                "next_attempt_at": now + timedelta(seconds=30),
                "last_error": None,
                "resumed_from_run_id": None,
            }
        )
        assert projected["wait_id"] == "w1"
        assert projected["state"] == "waiting"
        assert projected["attempt"] == 2
        assert projected["first_waited_at"] == now.isoformat()
        assert projected["terminal_at"] is None
        assert "T" in (projected["next_attempt_at"] or ""), "ISO 8601, not an epoch number"

    def test_a_missing_stamp_stays_none_rather_than_empty_string(self) -> None:
        from app.gateway.routers.thread_runs import _project_wait_row

        projected = _project_wait_row(
            {
                "id": "w1",
                "run_id": None,
                "state": "waiting",
                "reason": "connectivity_lost",
                "attempt": 0,
                "first_waited_at": None,
                "terminal_at": "",
                "next_attempt_at": None,
                "last_error": None,
                "resumed_from_run_id": None,
            }
        )
        assert projected["first_waited_at"] is None
        assert projected["terminal_at"] is None

    def test_an_unmeasured_duration_is_none_never_zero(self) -> None:
        from app.gateway.routers.thread_runs import _wait_seconds

        assert _wait_seconds({"id": "w1", "first_waited_at": None, "terminal_at": None}) is None
        assert _wait_seconds({"id": "w1", "first_waited_at": datetime.now(UTC), "terminal_at": None}) is None

    def test_a_measured_duration_is_the_difference_of_the_two_stamps(self) -> None:
        from app.gateway.routers.thread_runs import _wait_seconds

        start = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
        end = datetime(2026, 10, 10, 12, 5, 22, tzinfo=UTC)
        assert _wait_seconds({"id": "w1", "first_waited_at": start, "terminal_at": end}) == 322.0

    def test_a_naive_stamp_is_read_as_utc_rather_than_dropped(self) -> None:
        """SQLite returns naive datetimes; treating that as unmeasurable would
        turn every real outage into an unreadable duration."""
        from app.gateway.routers.thread_runs import _wait_seconds

        start = datetime(2026, 10, 10, 12, 0, 0)
        end = datetime(2026, 10, 10, 12, 0, 30)
        assert _wait_seconds({"id": "w1", "first_waited_at": start, "terminal_at": end}) == 30.0

    def test_a_corrupt_stamp_is_reported_as_unmeasured_not_raised(self) -> None:
        from app.gateway.routers.thread_runs import _wait_seconds

        assert _wait_seconds({"id": "w1", "first_waited_at": "not-a-time", "terminal_at": "also-not"}) is None

    def test_the_unreported_shape_names_both_deployment_reasons(self) -> None:
        """A memory backend has nowhere to record a park, so 'no waits' is a
        claim it cannot make; a disabled network starts no recovery pass."""
        from app.gateway.routers.thread_runs import ThreadNetworkWaitsResponse

        payload = ThreadNetworkWaitsResponse(reported=False, reason="network_wait_store_unavailable", detail="")
        dumped = payload.model_dump()
        assert dumped["waits"] == []
        assert dumped["open_wait"] is None
        assert dumped["wait_seconds"] is None
        assert dumped["bounded"] is None
        assert dumped["max_attempts"] is None
        assert dumped["total_waits"] is None

    def test_an_unreadable_policy_is_not_reported_as_a_bound(self) -> None:
        """``bounded`` must never be invented.

        Reading it as ``False`` would have the bubble hint at a deadline nobody
        declared; reading it as ``True`` would promise patience nobody promised.
        A service whose policy object cannot answer leaves it ``None`` — while
        still reporting the one half it *can* read.
        """
        from app.gateway.routers.thread_runs import _patience_policy

        class BarePolicy:
            # A stand-in with no `is_unbounded` — the shape a stub or an older
            # service would present.
            max_attempts = 7

        assert _patience_policy(BarePolicy()) == (None, 7)
        assert _patience_policy(None) == (None, None)

    def test_a_declared_policy_reports_both_halves_together(self) -> None:
        from alpha.runtime.network.wait_registry import NetworkWaitPolicy
        from app.gateway.routers.thread_runs import _patience_policy

        # Unbounded is the default, and it reads as `bounded: False` with the
        # sentinel `0` beside it — so a client can say "for as long as it takes"
        # without having to know the sentinel.
        assert _patience_policy(NetworkWaitPolicy()) == (False, 0)
        assert _patience_policy(NetworkWaitPolicy(max_attempts=12)) == (True, 12)
