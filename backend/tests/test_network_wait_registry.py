"""Durable network waits: parking, bounded resume, and honest give-up.

The property under test is the one the durable-runtime contract names: **an
internet outage must not become a task failure**. Concretely — a parked session
survives (it is a row, not an in-memory flag), it is retried on a durable
backoff (a reboot cannot turn it into a hot loop), it is bounded (it is not
retried forever), and when the bound is reached it is *reported* rather than
quietly dropped.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from alpha.persistence.network_waits import NetworkWaitRepository
from alpha.runtime.network.states import NetworkState
from alpha.runtime.network.wait_registry import (
    UNBOUNDED_ATTEMPTS,
    NetworkWaitPolicy,
    NetworkWaitService,
)
from alpha.runtime.sessions.states import SessionState

# ---------------------------------------------------------------------------
# In-memory store double: the semantics the SQL repository must also satisfy
# ---------------------------------------------------------------------------


class InMemoryWaitStore:
    """A store that speaks the same protocol, with deadlines in float seconds."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self.clock = 0.0

    def _next(self) -> str:
        self._seq += 1
        return f"wait_{self._seq}"

    async def park(self, *, thread_id: str, run_id: str | None = None, user_id: str | None = None, reason: str = "connectivity_lost", next_attempt_in_seconds: float = 0.0, last_error: str | None = None) -> dict[str, Any]:
        for row in self.rows.values():
            if row["thread_id"] == thread_id and row["state"] in ("waiting", "resuming"):
                row.update({"run_id": run_id or row["run_id"], "user_id": user_id or row["user_id"], "reason": reason, "next_attempt_at": self.clock + next_attempt_in_seconds, "lease_owner": None, "lease_expires_at": None})
                if last_error:
                    row["last_error"] = last_error
                return dict(row)
        wait_id = self._next()
        self.rows[wait_id] = {
            "id": wait_id,
            "thread_id": thread_id,
            "run_id": run_id,
            "user_id": user_id,
            "state": "waiting",
            "reason": reason,
            "attempt": 0,
            "next_attempt_at": self.clock + next_attempt_in_seconds,
            "last_error": last_error,
            "lease_owner": None,
            "lease_expires_at": None,
            "resumed_from_run_id": None,
        }
        return dict(self.rows[wait_id])

    async def claim_due(self, *, limit: int = 10, lease_seconds: float = 30.0, owner: str | None = None) -> list[dict[str, Any]]:
        due = [row for row in self.rows.values() if row["state"] == "waiting" and row["next_attempt_at"] <= self.clock]
        claimed: list[dict[str, Any]] = []
        for row in due[:limit]:
            row["state"] = "resuming"
            row["attempt"] += 1
            row["lease_owner"] = owner or "w"
            row["lease_expires_at"] = self.clock + lease_seconds
            claimed.append(dict(row))
        return claimed

    async def release(self, wait_id: str, *, next_attempt_in_seconds: float, last_error: str | None = None, attempt: int | None = None) -> dict[str, Any] | None:
        row = self.rows.get(wait_id)
        if row is None:
            return None
        row.update({"state": "waiting", "next_attempt_at": self.clock + next_attempt_in_seconds, "lease_owner": None, "lease_expires_at": None})
        if last_error is not None:
            row["last_error"] = last_error
        if attempt is not None:
            row["attempt"] = attempt
        return dict(row)

    async def mark_terminal(self, wait_id: str, *, state: str, resumed_from_run_id: str | None = None, last_error: str | None = None) -> dict[str, Any] | None:
        row = self.rows.get(wait_id)
        if row is None:
            return None
        row.update({"state": state, "lease_owner": None, "lease_expires_at": None})
        if resumed_from_run_id is not None:
            row["resumed_from_run_id"] = resumed_from_run_id
        if last_error is not None:
            row["last_error"] = last_error
        return dict(row)

    async def reclaim_expired_leases(self, *, next_attempt_in_seconds: float = 0.0) -> int:
        count = 0
        for row in self.rows.values():
            if row["state"] == "resuming" and row["lease_expires_at"] is not None and row["lease_expires_at"] < self.clock:
                row.update({"state": "waiting", "next_attempt_at": self.clock + next_attempt_in_seconds, "lease_owner": None, "lease_expires_at": None})
                count += 1
        return count

    async def get_open_for_thread(self, thread_id: str) -> dict[str, Any] | None:
        for row in self.rows.values():
            if row["thread_id"] == thread_id and row["state"] in ("waiting", "resuming"):
                return dict(row)
        return None

    async def list_open(self, *, user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values() if row["state"] in ("waiting", "resuming") and (user_id is None or row["user_id"] == user_id)][:limit]


#: Distinguishes "no launcher configured" from "use the default one", which a
#: plain ``None`` default cannot express.
_NO_LAUNCHER = object()


def make_service(*, network: NetworkState = NetworkState.ONLINE, launcher: Any = _NO_LAUNCHER, policy: NetworkWaitPolicy | None = None) -> tuple[NetworkWaitService, InMemoryWaitStore, list[str]]:
    store = InMemoryWaitStore()
    launched: list[str] = []
    clock = [0.0]

    async def default_launcher(row: dict[str, Any]) -> str:
        launched.append(row["thread_id"])
        return f"run_{row['thread_id']}"

    service = NetworkWaitService(
        store,  # type: ignore[arg-type]
        launcher=default_launcher if launcher is _NO_LAUNCHER else launcher,
        network_state=lambda: network,
        policy=policy,
        now_fn=lambda: clock[0],
    )
    service._store = store  # type: ignore[assignment]
    return service, store, launched


class TestParking:
    @pytest.mark.asyncio
    async def test_a_park_is_a_durable_row_not_an_in_memory_flag(self) -> None:
        service, store, _ = make_service(network=NetworkState.OFFLINE)
        outcome = await service.park(thread_id="t1", run_id="r1", user_id="u1")
        assert outcome.recorded is True
        assert outcome.state == "waiting"
        assert len(store.rows) == 1
        assert store.rows[outcome.wait_id]["thread_id"] == "t1"

    @pytest.mark.asyncio
    async def test_parking_the_same_thread_twice_does_not_create_a_second_row(self) -> None:
        """Two open rows for one thread would mean two continuations racing for it."""
        service, store, _ = make_service()
        first = await service.park(thread_id="t1", run_id="r1")
        second = await service.park(thread_id="t1", run_id="r2")
        assert len(store.rows) == 1
        assert second.wait_id == first.wait_id
        assert second.already_waiting is True
        assert store.rows[first.wait_id]["run_id"] == "r2", "the newer evidence wins"

    @pytest.mark.asyncio
    async def test_a_park_requires_a_thread(self) -> None:
        service, _, _ = make_service()
        with pytest.raises(ValueError, match="thread_id"):
            await service.park(thread_id="")

    @pytest.mark.asyncio
    async def test_the_first_backoff_is_written_at_park_time(self) -> None:
        """So a reboot cannot turn the first retry into an immediate one."""
        service, store, _ = make_service()
        outcome = await service.park(thread_id="t1")
        assert store.rows[outcome.wait_id]["next_attempt_at"] == 15.0


class TestResumeGate:
    @pytest.mark.asyncio
    async def test_a_resume_is_refused_while_the_link_is_still_down(self) -> None:
        """A wait exists BECAUSE the link was gone; retrying now spends budget on a certainty."""
        service, store, launched = make_service(network=NetworkState.OFFLINE)
        await service.park(thread_id="t1")
        store.clock = 1000.0
        assert await service.resume_due() == []
        assert launched == []

    @pytest.mark.asyncio
    async def test_an_unknown_link_still_permits_an_attempt(self) -> None:
        """Not knowing is not the same as knowing the link is down."""
        service, store, launched = make_service(network=NetworkState.UNKNOWN)
        await service.park(thread_id="t1")
        store.clock = 1000.0
        assert len(await service.resume_due()) == 1
        assert launched == ["t1"]

    @pytest.mark.asyncio
    async def test_a_degraded_link_permits_an_attempt(self) -> None:
        service, store, launched = make_service(network=NetworkState.DEGRADED)
        await service.park(thread_id="t1")
        store.clock = 1000.0
        assert len(await service.resume_due()) == 1
        assert launched == ["t1"]

    @pytest.mark.asyncio
    async def test_without_a_launcher_nothing_is_claimed(self) -> None:
        """A wait must not be marked as attempted by a pass that cannot attempt it."""
        service, store, _ = make_service(launcher=None)
        await service.park(thread_id="t1")
        store.clock = 1000.0
        assert await service.resume_due() == []
        assert all(row["state"] == "waiting" for row in store.rows.values())

    @pytest.mark.asyncio
    async def test_a_wait_that_is_not_due_yet_is_not_claimed(self) -> None:
        service, store, launched = make_service()
        await service.park(thread_id="t1")
        store.clock = 1.0  # backoff is 15s
        assert await service.resume_due() == []
        assert launched == []


class TestResumeOutcome:
    @pytest.mark.asyncio
    async def test_a_successful_resume_settles_the_wait_and_records_the_new_run(self) -> None:
        service, store, _ = make_service()
        await service.park(thread_id="t1")
        store.clock = 1000.0
        outcomes = await service.resume_due()
        assert [outcome.state for outcome in outcomes] == ["resumed"]
        assert outcomes[0].run_id == "run_t1"
        assert store.rows[outcomes[0].wait_id]["resumed_from_run_id"] == "run_t1"

    @pytest.mark.asyncio
    async def test_a_declined_checkpoint_settles_the_wait_rather_than_retrying_forever(self) -> None:
        """The recovery owner refusing a side-effect-unsafe checkpoint is the correct outcome."""

        async def decline(row: dict[str, Any]) -> str | None:
            return None

        service, store, _ = make_service(launcher=decline)
        await service.park(thread_id="t1")
        store.clock = 1000.0
        outcomes = await service.resume_due()
        assert outcomes[0].state == "gave_up"
        assert outcomes[0].gave_up is True
        assert "declined" in store.rows[outcomes[0].wait_id]["last_error"]

    @pytest.mark.asyncio
    async def test_a_crashing_launcher_releases_the_wait_with_a_durable_backoff(self) -> None:
        async def boom(row: dict[str, Any]) -> str:
            raise RuntimeError("checkpointer pool closed")

        service, store, _ = make_service(launcher=boom)
        await service.park(thread_id="t1")
        store.clock = 1000.0
        outcomes = await service.resume_due()
        assert outcomes[0].state == "waiting"
        assert "PoolClosed" in outcomes[0].error or "RuntimeError" in outcomes[0].error
        assert store.rows[outcomes[0].wait_id]["next_attempt_at"] == 1000.0 + 30.0, "the next backoff must be durable, not recomputed in memory"

    @pytest.mark.asyncio
    async def test_a_cancelled_resume_releases_the_wait_rather_than_stranding_it(self) -> None:
        started = asyncio.Event()

        async def slow_decline(row: dict[str, Any]) -> str | None:
            started.set()
            await asyncio.sleep(10)
            return None

        service, store, _ = make_service(launcher=slow_decline)
        await service.park(thread_id="t1")
        store.clock = 1000.0
        task = asyncio.create_task(service.resume_due())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(row["state"] == "waiting" for row in store.rows.values()), "a cancelled pass must not leave a wait in resuming"


class TestBoundedAttempts:
    @pytest.mark.asyncio
    async def test_a_failing_resume_is_bounded_and_then_reported(self) -> None:
        """A session retried forever against a dead link is the outage restart loop."""
        policy = NetworkWaitPolicy(max_attempts=2, backoff_initial_seconds=1.0, backoff_max_seconds=2.0)
        service, store, _ = make_service(policy=policy)

        async def boom(row: dict[str, Any]) -> str:
            raise RuntimeError("checkpointer pool closed")

        service._launcher = boom  # type: ignore[assignment]
        await service.park(thread_id="t1")

        store.clock = 100.0
        first = await service.resume_due()
        assert first[0].state == "waiting", "a failed attempt re-arms with the next backoff"

        store.clock = 1000.0
        second = await service.resume_due()
        assert second[0].state == "gave_up", "the last permitted attempt reports the give-up"
        assert second[0].gave_up is True
        assert "budget" in second[0].detail
        assert "attempts" in store.rows[second[0].wait_id]["last_error"]

        store.clock = 5000.0
        assert await service.resume_due() == [], "a given-up wait is never claimed again"

    def test_a_policy_that_may_never_retry_is_not_a_policy(self) -> None:
        """Kept from the pre-unbounded contract: a *negative* budget is still refused.

        ``max_attempts`` of 0 is no longer that case. It is
        :data:`~alpha.runtime.network.wait_registry.UNBOUNDED_ATTEMPTS`, the
        default, because a wait is a parked task rather than a retry loop — an
        internet outage that outlasts a counter must not abandon work that did
        nothing wrong. A negative value is still refused, because "retry this
        -1 times" is not a policy.
        """
        with pytest.raises(ValueError, match="max_attempts"):
            NetworkWaitPolicy(max_attempts=-1)

    def test_zero_attempts_means_unbounded_rather_than_never(self) -> None:
        """0 is the unbounded sentinel, and it is the default.

        The two facts a caller must not confuse: 0 does *not* mean "never retry"
        (that would strand every parked session), and the default really is
        unbounded rather than a large finite number.
        """
        policy = NetworkWaitPolicy()
        assert policy.max_attempts == UNBOUNDED_ATTEMPTS == 0
        assert policy.is_unbounded is True
        assert NetworkWaitPolicy(max_attempts=0).is_unbounded is True
        assert NetworkWaitPolicy(max_attempts=1).is_unbounded is False

    @pytest.mark.asyncio
    async def test_an_unbounded_wait_is_never_surrendered(self) -> None:
        """The outage-duration invariant: the bound cannot be reached.

        The launcher fails on purpose. A wait whose recovery owner keeps being
        unable to continue is the *only* shape that ever reaches the give-up
        branch, so this is the case where a finite budget would actually bite —
        and an unbounded policy must keep re-arming it across a simulated
        days-long outage rather than reporting `gave_up`.
        """
        service, store, launched = make_service(policy=NetworkWaitPolicy(max_attempts=0, backoff_initial_seconds=1.0, backoff_max_seconds=2.0))

        async def boom(row: dict[str, Any]) -> str:
            launched.append(row["thread_id"])
            raise RuntimeError("checkpointer pool closed")

        service._launcher = boom  # type: ignore[assignment]
        await service.park(thread_id="t1")

        for clock in (100.0, 1000.0, 5000.0, 50_000.0, 500_000.0):
            store.clock = clock
            outcomes = await service.resume_due()
            assert len(outcomes) == 1
            assert outcomes[0].gave_up is False, f"clock {clock}: an unbounded wait reported a give-up"
            assert outcomes[0].state == "waiting", f"clock {clock}: an unbounded wait was surrendered"
            assert store.rows[outcomes[0].wait_id]["state"] == "waiting"

        assert len(launched) == 5, "every pass still hands the wait back to the recovery owner"
        assert store.rows[outcomes[0].wait_id]["attempt"] == 5

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"backoff_initial_seconds": 0.0},
            {"backoff_initial_seconds": 100.0, "backoff_max_seconds": 5.0},
            {"backoff_multiplier": 0.5},
            {"max_claims_per_pass": 0},
        ],
    )
    def test_out_of_range_policy_values_are_refused(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            NetworkWaitPolicy(**kwargs)  # type: ignore[arg-type]

    def test_the_backoff_doubles_and_then_caps(self) -> None:
        policy = NetworkWaitPolicy(backoff_initial_seconds=10.0, backoff_max_seconds=40.0, backoff_multiplier=2.0)
        assert [policy.backoff_for(n) for n in (1, 2, 3, 4, 10)] == [10.0, 20.0, 40.0, 40.0, 40.0]


class TestBacklogBound:
    @pytest.mark.asyncio
    async def test_one_pass_claims_a_bounded_number_of_waits(self) -> None:
        """A large backlog must not stampede the provider the instant the link returns."""
        policy = NetworkWaitPolicy(max_claims_per_pass=2)
        service, store, launched = make_service(policy=policy)
        for index in range(5):
            await service.park(thread_id=f"t{index}")
        store.clock = 1000.0  # past the first backoff, so all five are due
        outcomes = await service.resume_due()
        assert len(outcomes) == 2
        assert len(launched) == 2

    @pytest.mark.asyncio
    async def test_the_remainder_is_left_due_for_the_next_pass(self) -> None:
        policy = NetworkWaitPolicy(max_claims_per_pass=1)
        service, store, _ = make_service(policy=policy)
        for index in range(3):
            await service.park(thread_id=f"t{index}")
        store.clock = 1000.0
        await service.resume_due()
        assert len(await service.resume_due()) == 1
        assert len(await service.resume_due()) == 1
        assert await service.resume_due() == []


class TestReporting:
    @pytest.mark.asyncio
    async def test_status_counts_what_the_service_did(self) -> None:
        service, store, _ = make_service()
        await service.park(thread_id="t1")
        await service.park(thread_id="t2")
        status = await service.status()
        assert status.open_waits == 2
        assert status.network_state is NetworkState.ONLINE
        assert status.to_dict()["open_waits"] == 2

    @pytest.mark.asyncio
    async def test_a_parked_thread_is_reported_as_waiting_not_failed(self) -> None:
        service, _, _ = make_service(network=NetworkState.OFFLINE)
        assert service.session_state_for() is SessionState.WAITING_NETWORK

    @pytest.mark.asyncio
    async def test_the_derived_state_uses_the_shared_state_machine(self) -> None:
        """A UI reads one derivation rather than re-deciding 'waiting or failed' for itself."""
        service, _, _ = make_service(network=NetworkState.OFFLINE)
        assert service.session_state_for().value == "waiting_network"
        service_online, _, _ = make_service(network=NetworkState.ONLINE)
        assert service_online.session_state_for() is not SessionState.WAITING_NETWORK


class TestLoop:
    @pytest.mark.asyncio
    async def test_the_pass_loop_runs_and_stops(self) -> None:
        policy = NetworkWaitPolicy(poll_interval_seconds=0.01)
        service, store, launched = make_service(policy=policy)
        await service.park(thread_id="t1")
        store.clock = 1000.0
        task = service.start()
        assert service.start() is task
        for _ in range(100):
            if launched:
                break
            await asyncio.sleep(0.01)
        await service.stop(timeout=5.0)
        assert launched, "the loop should have resumed the due wait"

    @pytest.mark.asyncio
    async def test_a_failing_pass_does_not_kill_the_loop(self) -> None:
        policy = NetworkWaitPolicy(poll_interval_seconds=0.01)
        service, store, _ = make_service(policy=policy)
        calls = [0]

        async def flaky(row: dict[str, Any]) -> str:
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("transient store error")
            return "run_ok"

        service._launcher = flaky  # type: ignore[assignment]
        await service.park(thread_id="t1")
        store.clock = 1000.0
        task = service.start()
        try:
            for _ in range(200):
                if calls[0] >= 1:
                    break
                await asyncio.sleep(0.01)
            assert calls[0] == 1
            assert not task.done(), "one failed pass must not end the loop"
            # The failed attempt re-armed the wait with the next backoff, so a
            # second attempt is *deferred* rather than hammered. The loop
            # surviving and the wait being deferred are two different
            # guarantees, and both have to hold.
            row = next(iter(store.rows.values()))
            assert row["state"] == "waiting"
            assert row["next_attempt_at"] == 1000.0 + 30.0
        finally:
            await service.stop(timeout=5.0)

    @pytest.mark.asyncio
    async def test_stopping_a_service_that_never_ran_is_a_no_op(self) -> None:
        service, _, _ = make_service()
        await service.stop(timeout=0.1)


# ---------------------------------------------------------------------------
# SQL repository: the same semantics against a real database
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_repository(tmp_path: Any, request: Any) -> Any:
    """A real SQLite wait repository, with the engine and loop cleaned up.

    ``asyncio.run`` owns and closes its own loop; a loop built with
    ``get_event_loop_policy().new_event_loop()`` and never closed leaks the loop
    and its aiosqlite connection worker thread, which then dies with "Event loop
    is closed" as an unhandled thread exception.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from alpha.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'waits.db'}")

    async def _create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    request.addfinalizer(lambda: asyncio.run(engine.dispose()))
    return NetworkWaitRepository(async_sessionmaker(engine, expire_on_commit=False))


class TestSqlRepository:
    @pytest.mark.asyncio
    async def test_a_park_writes_a_readable_row(self, sqlite_repository: Any) -> None:
        row = await sqlite_repository.park(thread_id="t1", run_id="r1", user_id="u1", reason="connectivity_lost", next_attempt_in_seconds=30.0)
        assert row["thread_id"] == "t1"
        assert row["state"] == "waiting"
        fetched = await sqlite_repository.get(row["id"])
        assert fetched is not None
        assert fetched["run_id"] == "r1"

    @pytest.mark.asyncio
    async def test_a_second_park_for_the_same_thread_updates_the_existing_row(self, sqlite_repository: Any) -> None:
        first = await sqlite_repository.park(thread_id="t1", run_id="r1")
        second = await sqlite_repository.park(thread_id="t1", run_id="r2", last_error="connection refused")
        assert second["id"] == first["id"]
        assert second["run_id"] == "r2"
        assert second["last_error"] == "connection refused"
        assert await sqlite_repository.count_by_state() == {"waiting": 1}

    @pytest.mark.asyncio
    async def test_only_due_waits_are_claimed(self, sqlite_repository: Any) -> None:
        soon = await sqlite_repository.park(thread_id="soon", next_attempt_in_seconds=300.0)
        assert soon["state"] == "waiting"
        # A zero-offset wait is due immediately.
        await sqlite_repository.park(thread_id="due", next_attempt_in_seconds=0.0)
        claimed = await sqlite_repository.claim_due(limit=10)
        assert [row["thread_id"] for row in claimed] == ["due"]
        assert claimed[0]["state"] == "resuming"
        assert claimed[0]["attempt"] == 1

    @pytest.mark.asyncio
    async def test_a_claim_is_exclusive(self, sqlite_repository: Any) -> None:
        """Two gateway instances must not both launch a continuation for one wait."""
        await sqlite_repository.park(thread_id="t1", next_attempt_in_seconds=0.0)
        first = await sqlite_repository.claim_due(limit=10, owner="host:a")
        second = await sqlite_repository.claim_due(limit=10, owner="host:b")
        assert len(first) == 1
        assert second == [], "the second pass must find the row already claimed"

    @pytest.mark.asyncio
    async def test_releasing_returns_the_row_to_waiting_with_a_durable_backoff(self, sqlite_repository: Any) -> None:
        await sqlite_repository.park(thread_id="t1", next_attempt_in_seconds=0.0)
        claimed = await sqlite_repository.claim_due(limit=1)
        released = await sqlite_repository.release(claimed[0]["id"], next_attempt_in_seconds=120.0, last_error="pool closed")
        assert released is not None
        assert released["state"] == "waiting"
        assert released["lease_owner"] is None
        assert released["last_error"] == "pool closed"
        assert released["attempt"] == 1, "the attempt count is preserved so the bound still applies"
        assert await sqlite_repository.claim_due(limit=1) == [], "the backoff must actually defer the next attempt"

    @pytest.mark.asyncio
    async def test_an_expired_claim_lease_is_reclaimed(self, sqlite_repository: Any) -> None:
        """A pass that claimed a row and then died must not strand it in resuming."""
        await sqlite_repository.park(thread_id="t1", next_attempt_in_seconds=0.0)
        await sqlite_repository.claim_due(limit=1, lease_seconds=-1.0)
        assert await sqlite_repository.reclaim_expired_leases(next_attempt_in_seconds=0.0) == 1
        open_rows = await sqlite_repository.list_open()
        assert [row["state"] for row in open_rows] == ["waiting"]

    @pytest.mark.asyncio
    async def test_a_terminal_transition_settles_the_row(self, sqlite_repository: Any) -> None:
        row = await sqlite_repository.park(thread_id="t1", next_attempt_in_seconds=0.0)
        settled = await sqlite_repository.mark_terminal(row["id"], state="resumed", resumed_from_run_id="run_new")
        assert settled is not None
        assert settled["state"] == "resumed"
        assert settled["resumed_from_run_id"] == "run_new"
        assert await sqlite_repository.get_open_for_thread("t1") is None

    @pytest.mark.asyncio
    async def test_a_settled_thread_may_wait_again(self, sqlite_repository: Any) -> None:
        """History accumulates; only *open* rows are constrained per thread."""
        first = await sqlite_repository.park(thread_id="t1", next_attempt_in_seconds=0.0)
        await sqlite_repository.mark_terminal(first["id"], state="gave_up")
        second = await sqlite_repository.park(thread_id="t1", next_attempt_in_seconds=0.0)
        assert second["id"] != first["id"]
        assert await sqlite_repository.count_by_state() == {"waiting": 1, "gave_up": 1}

    @pytest.mark.asyncio
    async def test_an_unknown_terminal_state_is_refused(self, sqlite_repository: Any) -> None:
        row = await sqlite_repository.park(thread_id="t1", next_attempt_in_seconds=0.0)
        with pytest.raises(ValueError, match="terminal"):
            await sqlite_repository.mark_terminal(row["id"], state="teleported")

    @pytest.mark.asyncio
    async def test_listing_is_owner_scoped(self, sqlite_repository: Any) -> None:
        await sqlite_repository.park(thread_id="t1", user_id="u1", next_attempt_in_seconds=0.0)
        await sqlite_repository.park(thread_id="t2", user_id="u2", next_attempt_in_seconds=0.0)
        assert len(await sqlite_repository.list_open(user_id="u1")) == 1
        assert len(await sqlite_repository.list_open()) == 2


class TestMigration:
    def test_the_revision_chains_onto_the_previous_head(self) -> None:
        import importlib

        from alpha.persistence.migrations import versions as _  # noqa: F401

        module = importlib.import_module("alpha.persistence.migrations.versions.0026_network_waits")  # type: ignore[attr-defined]
        assert module.revision == "0026_network_waits"
        assert module.down_revision == "0025_run_recovery_index"

    def test_the_table_is_present_on_the_orm_base(self) -> None:
        from alpha.persistence.base import Base
        from alpha.persistence.network_waits import NetworkWaitRow

        assert NetworkWaitRow.__tablename__ in Base.metadata.tables

    def test_the_open_thread_index_is_partial(self) -> None:
        """The whole point: history accumulates, but two *open* rows may not.

        The predicate is read back off the *compiled* table rather than the
        ``Index`` object, because SQLAlchemy attaches ``sqlite_where`` to the
        dialect-specific index it builds, not to the object in ``__table_args__``.
        """
        from sqlalchemy.dialects import postgresql, sqlite
        from sqlalchemy.schema import CreateIndex

        from alpha.persistence.network_waits import NetworkWaitRow

        compiled = next(index for index in NetworkWaitRow.__table__.indexes if index.name == "uq_network_waits_thread_open")
        assert compiled.unique is True
        for dialect in (sqlite.dialect(), postgresql.dialect()):
            ddl = str(CreateIndex(compiled).compile(dialect=dialect))
            assert "UNIQUE" in ddl.upper()
            assert "waiting" in ddl and "resuming" in ddl, f"{dialect.name} lost the partial predicate"

    def test_the_model_and_the_migration_agree_on_the_partial_predicate(self) -> None:
        import importlib
        import re

        module = importlib.import_module("alpha.persistence.migrations.versions.0026_network_waits")  # type: ignore[attr-defined]
        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        # The migration must apply the same predicate the ORM declares, or
        # create_all and alembic would produce different schemas.
        assert re.search(r"state IN \('waiting', 'resuming'\)", text), "migration predicate drifted from the ORM"
