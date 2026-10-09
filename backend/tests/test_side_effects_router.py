"""The effect-journal REST surface: the queue, the projection, the verdicts.

`runtime/side_effects/` has always been able to answer "which external effects
are unaccounted for" — nothing could *ask* it over HTTP until
`app/gateway/routers/side_effects.py` existed. These tests pin the five
properties the router's own docstring promises, because each one is a way the
surface could quietly start lying:

1. **Route order.** ``/summary`` is declared before ``/{tool_call_id}``, or
   Starlette answers ``404 tool_call_id='summary'`` — the same trap as
   ``skills/{skill_name}``.
2. **Digests only.** Arguments and results never cross the wire; the explicit
   projection is pinned key-for-key so a new ``SideEffectEntry`` field must be
   *chosen* to appear rather than leaking by default.
3. **Owner scoping.** A member reads their own rows; an entry with an empty
   ``user_id`` is admin-only; a foreign id answers the same 404 an absent one
   does, so the detail route is not an existence oracle. A PAT never carries
   admin, through the repository's one predicate.
4. **Honest failure.** No ledger is 503 with its reason — never a body of
   zeros; an *empty* healthy ledger is the only thing that reports ``0``, and
   ``oldest_unknown_age_seconds`` is null, never ``0``, when nothing waits.
5. **The ledger owns the transitions.** This router records verdicts; it never
   decides one. Settled/in-flight entries refuse with 409 carrying the
   ledger's own words, ``undetermined`` *reopens*, a confirmed failure on a
   high-risk effect reports ``escalated``, and reconcile is admin-only.

Each ledger is installed through the same process-wide accessor the Gateway
installs production's SQL ledger with, and every install is cleared on teardown
so a later suite never inherits somebody else's queue.
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.persistence.side_effects import SqlSideEffectLedger
from alpha.runtime.resilience.clock import ManualClock
from alpha.runtime.side_effects import (
    InMemorySideEffectLedger,
    SideEffectLevel,
    SideEffectRecorder,
    set_side_effect_recorder,
)
from app.gateway.auth_disabled import AUTH_SOURCE_PAT
from app.gateway.routers import side_effects as side_effects_router

#: The explicit projection, pinned key-for-key. A field added to
#: ``SideEffectEntry`` must be *chosen* for the wire (and this set edited in
#: the same change), not inherited through ``__dict__``.
WIRE_KEYS = {
    "tool_call_id",
    "tool_name",
    "status",
    "level",
    "thread_id",
    "run_id",
    "user_id",
    "arguments_digest",
    "result_digest",
    "verdict",
    "detail",
    "owner_worker_id",
    "lease_expires_at",
    "attempt",
    "created_at",
    "updated_at",
    "needs_reconciliation",
    "reconcilable",
}

#: Literal, not the router's constants: these strings *are* the wire contract,
#: so renaming a code must break the client's tests, not just the client.
CODE_NOT_FOUND = "side_effect_not_found"
CODE_NO_STORE = "side_effect_store_unavailable"
CODE_TRANSITION = "side_effect_transition_refused"
CODE_INVALID_FILTER = "invalid_filter"
CODE_FORBIDDEN = "forbidden"


class _Holder:
    """A mutable principal, so one app + one client can wear every identity.

    One ``TestClient`` means one portal (one event loop), which is what lets an
    ``asyncio.Lock``-bearing in-memory ledger and ``client.portal.call``
    seeding coexist; mutating the holder between requests keeps that loop
    instead of paying for a new client per identity.
    """

    def __init__(self) -> None:
        self.user: object | None = None
        self.auth_source: str | None = None


def _app(holder: _Holder) -> FastAPI:
    """A bare app: the router plus the principal injection AuthMiddleware does."""
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        if holder.user is not None:
            request.state.user = holder.user
        if holder.auth_source is not None:
            request.state.auth_source = holder.auth_source
        return await call_next(request)

    app.include_router(side_effects_router.router)
    return app


def _member(uid: str) -> SimpleNamespace:
    # The shape production sends: ``system_role``, never an ``is_admin`` flag
    # (see tests/test_apex_authz.py for the incident that teaches this).
    return SimpleNamespace(id=uid, system_role="user")


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id="ops-admin", system_role="admin")


async def _seed(ledger: InMemorySideEffectLedger, clock: ManualClock) -> None:
    """Four entries whose timestamps are deterministic and realistic.

    Timeline (``t0`` is 10 minutes before "now", so summary ages read like a
    production value rather than an epoch-0 artifact):

    * ``call_d`` — FAILED, **no owner** (an admin-only row)          at t0
    * ``call_c`` — COMPLETED, user-1, read_only                      at t0+10
    * ``call_a`` — UNKNOWN, user-1, destructive, secret arguments     at t0+81
    * ``call_b`` — UNKNOWN, user-2, high_risk                        at t0+142
    """
    await ledger.begin(
        tool_call_id="call_d",
        tool_name="payment_charge",
        user_id="",
        level=SideEffectLevel.HIGH_RISK,
        owner_worker_id="w0",
        lease_seconds=60.0,
        arguments={"amount": 100},
    )
    await ledger.mark_in_flight("call_d", owner_worker_id="w0", lease_seconds=60.0)
    await ledger.fail("call_d", detail="the provider refused the charge")

    clock.advance(10)
    await ledger.begin(
        tool_call_id="call_c",
        tool_name="web_fetch",
        thread_id="thread-1",
        run_id="run-1",
        user_id="user-1",
        level=SideEffectLevel.READ_ONLY,
        owner_worker_id="w0",
        lease_seconds=60.0,
        arguments={"url": "https://example.com/private"},
    )
    await ledger.mark_in_flight("call_c", owner_worker_id="w0", lease_seconds=60.0)
    await ledger.complete("call_c", detail="fetched 200")

    clock.advance(10)
    await ledger.begin(
        tool_call_id="call_a",
        tool_name="bash",
        thread_id="thread-1",
        run_id="run-1",
        user_id="user-1",
        level=SideEffectLevel.DESTRUCTIVE,
        owner_worker_id="w0",
        lease_seconds=60.0,
        arguments={"cmd": "rm -rf sk-live-SENSITIVE"},
    )
    await ledger.mark_in_flight("call_a", owner_worker_id="w0", lease_seconds=60.0)
    clock.advance(61)  # the lease is dead: this is a worker that never reported
    await ledger.reclaim_expired()

    await ledger.begin(
        tool_call_id="call_b",
        tool_name="git_push",
        thread_id="thread-1",
        run_id="run-1",
        user_id="user-2",
        level=SideEffectLevel.HIGH_RISK,
        owner_worker_id="w0",
        lease_seconds=60.0,
        arguments={"remote": "origin"},
    )
    await ledger.mark_in_flight("call_b", owner_worker_id="w0", lease_seconds=60.0)
    clock.advance(61)
    await ledger.reclaim_expired()


@pytest.fixture()
def journal() -> Any:
    """An installed in-memory ledger with a clock anchored 10 minutes ago."""
    clock = ManualClock(start=time.time() - 600.0)
    ledger = InMemorySideEffectLedger(clock=clock)
    set_side_effect_recorder(SideEffectRecorder(ledger))
    try:
        yield (ledger, clock)
    finally:
        set_side_effect_recorder(None)  # never leak a queue into another suite


@contextmanager
def _open(holder: _Holder, journal: tuple[InMemorySideEffectLedger, ManualClock]):
    """One client, one portal, seeded before the first request.

    Seeding must run *inside* the ``with TestClient`` block: the in-memory
    ledger's ``asyncio.Lock`` binds to the first loop that awaits it, and only
    the portal loop serves this client's requests.
    """
    ledger, clock = journal
    with TestClient(_app(holder)) as client:
        client.portal.call(_seed, ledger, clock)
        yield client


@pytest.fixture()
def sql_ledger(tmp_path: Path, request: Any) -> Any:
    """A real SQLite ledger, mirroring tests/test_side_effect_ledger_sql.py.

    Finalizers run LIFO: the recorder is cleared before the engine is
    disposed, so nothing can reach a disposed engine through the accessor.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from alpha.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'router_effects.db'}")

    async def _create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    request.addfinalizer(lambda: asyncio.run(engine.dispose()))
    ledger = SqlSideEffectLedger(async_sessionmaker(engine, expire_on_commit=False))
    set_side_effect_recorder(SideEffectRecorder(ledger))
    request.addfinalizer(lambda: set_side_effect_recorder(None))
    return ledger


async def _seed_sql_unknown(ledger: SqlSideEffectLedger) -> None:
    await ledger.begin(
        tool_call_id="sql_call",
        tool_name="git_push",
        user_id="user-1",
        level=SideEffectLevel.HIGH_RISK,
        owner_worker_id="w0",
        lease_seconds=60.0,
        arguments={"remote": "origin"},
    )
    await ledger.reclaim_expired(now=time.time() + 3600.0)


class TestAuthenticationAndRouteOrder:
    def test_unauthenticated_callers_get_401_on_every_route(self, journal: Any) -> None:
        holder = _Holder()  # no principal at all
        with _open(holder, journal) as client:
            assert client.get("/api/side-effects").status_code == 401
            assert client.get("/api/side-effects/summary").status_code == 401
            assert client.get("/api/side-effects/call_a").status_code == 401
            response = client.post(
                "/api/side-effects/call_a/reconcile",
                json={"verdict": "confirmed_success", "reason": "checked the provider"},
            )
            assert response.status_code == 401

    def test_a_member_with_no_id_is_401_not_every_owners_rows(self, journal: Any) -> None:
        """An unscoped member read would degrade to "no filter" — i.e. everyone's."""
        holder = _Holder()
        holder.user = SimpleNamespace(id="", system_role="user")
        with _open(holder, journal) as client:
            response = client.get("/api/side-effects")
            assert response.status_code == 401

    def test_summary_is_declared_before_the_id_catch_all(self, journal: Any) -> None:
        """The Starlette route-order trap: a catch-all first swallows /summary."""
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            response = client.get("/api/side-effects/summary")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reported"] is True
        assert "unknown" in body["by_status"]

    def test_the_id_catch_all_still_answers_404_with_a_code(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            response = client.get("/api/side-effects/absent-call")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == CODE_NOT_FOUND
        assert isinstance(body["detail"], str)


class TestProjection:
    def test_entries_carry_digests_and_never_payload(self, journal: Any) -> None:
        """A ledger row lands in support bundles: no argument/result crosses."""
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            detail = client.get("/api/side-effects/call_a").json()
            assert re.fullmatch(r"[0-9a-f]{64}", detail["arguments_digest"] or "")
            # The outcome was never established — absent, not guessed.
            assert detail["result_digest"] is None
            assert detail["lease_expires_at"] is None  # cleared by the reclaimer
            assert detail["reconcilable"] is True
            assert detail["needs_reconciliation"] is True
            assert "T" in detail["created_at"]  # ISO 8601, not a raw epoch

            everything = client.get("/api/side-effects").text
            assert "SENSITIVE" not in everything
            assert "rm -rf" not in everything
            assert "example.com" not in everything
            # Key names too: `"arguments"`/`"result"` are absent even though
            # `"arguments_digest"`/`"result_digest"` are present.
            assert '"arguments"' not in everything
            assert '"result"' not in everything
            assert '"metadata"' not in everything

    def test_the_wire_shape_is_the_explicit_projection(self, journal: Any) -> None:
        """Pinned key-for-key: new entry fields are chosen, never inherited."""
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            entries = client.get("/api/side-effects").json()["entries"]
            assert entries
            for entry in entries:
                assert set(entry) == WIRE_KEYS
            one = client.get("/api/side-effects/call_c").json()
            assert set(one) == WIRE_KEYS


class TestReadsAndScoping:
    def test_the_unknown_queue_is_oldest_first_and_owner_scoped(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            queue = client.get("/api/side-effects", params={"status": "unknown"}).json()
            assert queue["order"] == "oldest_first"
            assert queue["status_filter"] == "unknown"
            assert [entry["tool_call_id"] for entry in queue["entries"]] == ["call_a", "call_b"]

            # One member's queue is their own — scope applied at the source.
            holder.user = _member("user-1")
            queue = client.get("/api/side-effects", params={"status": "unknown"}).json()
            assert [entry["tool_call_id"] for entry in queue["entries"]] == ["call_a"]
            assert queue["scope"] == "owner"

    def test_the_default_list_is_newest_first(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            data = client.get("/api/side-effects").json()
        assert data["order"] == "newest_first"
        assert [entry["tool_call_id"] for entry in data["entries"]] == ["call_b", "call_a", "call_c", "call_d"]

    def test_a_member_reads_only_their_own_scope(self, journal: Any) -> None:
        """Empty-``user_id`` rows exist but are admin-only; foreign rows too."""
        holder = _Holder()
        holder.user = _member("user-1")
        with _open(holder, journal) as client:
            data = client.get("/api/side-effects").json()
            assert {entry["tool_call_id"] for entry in data["entries"]} == {"call_a", "call_c"}
            assert data["scope"] == "owner"
            # Asking for another owner's id must not widen the scope.
            data = client.get("/api/side-effects", params={"user_id": "user-2"}).json()
            assert {entry["tool_call_id"] for entry in data["entries"]} == {"call_a", "call_c"}

    def test_detail_of_a_foreign_or_absent_entry_answers_the_same_404(self, journal: Any) -> None:
        """Both routes name the id; neither may leak that the row exists."""
        holder = _Holder()
        holder.user = _member("user-1")
        with _open(holder, journal) as client:
            foreign = client.get("/api/side-effects/call_b")  # user-2's row
            absent = client.get("/api/side-effects/never-recorded")
        assert foreign.status_code == absent.status_code == 404
        assert foreign.json()["code"] == absent.json()["code"] == CODE_NOT_FOUND
        assert foreign.json().get("status") is None  # no projection leaks through

    def test_a_pat_administrator_is_not_an_administrator(self, journal: Any) -> None:
        """The repository's one admin predicate suppresses admin for a PAT."""
        holder = _Holder()
        holder.user = _admin()
        holder.auth_source = AUTH_SOURCE_PAT
        with _open(holder, journal) as client:
            data = client.get("/api/side-effects").json()
            assert data["scope"] == "owner"  # not "all"
            assert data["entries"] == []  # ops-admin owns no entries
            response = client.post(
                "/api/side-effects/call_a/reconcile",
                json={"verdict": "confirmed_success", "reason": "checked the provider"},
            )
        assert response.status_code == 403
        assert response.json()["code"] == CODE_FORBIDDEN


class TestSummary:
    def test_summary_counts_everything_with_an_honest_age(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            data = client.get("/api/side-effects/summary").json()
        assert data["reported"] is True
        assert data["scope"] == "all"
        assert data["total"] == 4
        assert data["by_status"] == {"pending": 0, "in_flight": 0, "completed": 1, "failed": 1, "unknown": 2, "reconciled": 0}
        assert data["unknown"] == 2
        assert data["by_level"] == {"read_only": 1, "low_risk": 0, "moderate": 0, "high_risk": 2, "destructive": 1}
        age = data["oldest_unknown_age_seconds"]
        # The oldest unknown waited ~8.5 of the 10 simulated minutes.
        assert age is not None and 400.0 <= age <= 700.0, age
        assert data["generated_at"]

    def test_a_member_summary_is_scoped(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _member("user-1")
        with _open(holder, journal) as client:
            data = client.get("/api/side-effects/summary").json()
        assert data["scope"] == "owner"
        assert data["total"] == 2  # call_a + call_c; never user-2's or the ownerless row
        assert data["unknown"] == 1

    def test_an_empty_ledger_reports_zero_and_a_null_age(self, journal: Any) -> None:
        """ "I looked and found nothing" — the only honest source of a 0."""
        clock = ManualClock(start=time.time() - 600.0)
        set_side_effect_recorder(SideEffectRecorder(InMemorySideEffectLedger(clock=clock)))
        holder = _Holder()
        holder.user = _admin()
        try:
            with _open(holder, journal) as client:
                data = client.get("/api/side-effects/summary").json()
        finally:
            set_side_effect_recorder(None)
        assert data["total"] == 0
        assert data["unknown"] == 0
        assert data["oldest_unknown_age_seconds"] is None  # null, never 0

    def test_no_store_is_503_never_a_body_of_zeros(self) -> None:
        """``database.backend: memory`` installs no ledger; that is *could not
        look*, and it must not be renderable as "nothing was ever recorded"."""
        set_side_effect_recorder(None)
        holder = _Holder()
        holder.user = _admin()
        try:
            with TestClient(_app(holder)) as client:
                summary = client.get("/api/side-effects/summary")
                listing = client.get("/api/side-effects")
                detail = client.get("/api/side-effects/call_a")
        finally:
            set_side_effect_recorder(None)
        for response in (summary, listing, detail):
            assert response.status_code == 503, response.text
            assert response.json()["code"] == CODE_NO_STORE
        assert summary.json()["detail"]  # the real reason, in words


class TestFilters:
    def test_invalid_filters_are_422_with_a_code(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            bad_status = client.get("/api/side-effects", params={"status": "bogus"})
            bad_level = client.get("/api/side-effects", params={"level": "bogus"})
        for response in (bad_status, bad_level):
            assert response.status_code == 422
            body = response.json()
            assert body["code"] == CODE_INVALID_FILTER
            assert isinstance(body["detail"], str)

    def test_malformed_thread_id_filter_is_refused_not_silently_empty(self, journal: Any) -> None:
        """A garbage ``thread_id`` filter must be refused, never read as "no such thread".

        ``bad.thread.id`` matches no entry, so an unvalidated filter answers an
        empty page that is identical to a real thread with no recorded effects —
        "you sent an identifier the contract rejects" and "nothing was recorded
        for this thread" would be the same bytes. The refusal carries this
        router's own ``invalid_filter`` code and a *string* detail, matching
        ``status``/``level`` above, because the shared client parses string
        details only.
        """
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            refused = client.get("/api/side-effects", params={"thread_id": "bad.thread.id"})
            assert refused.status_code == 422
            body = refused.json()
            assert body["code"] == CODE_INVALID_FILTER
            assert isinstance(body["detail"], str)
            assert "thread_id" in body["detail"]

            # The canonical contract still answers and still filters.
            accepted = client.get("/api/side-effects", params={"thread_id": "thread-1"})
            assert accepted.status_code == 200
            rows = accepted.json()["entries"]
            assert rows, "the filter must still select the rows it narrowed to"
            assert {row["thread_id"] for row in rows} == {"thread-1"}, "and nothing else"

            # The distinction this pins: a *canonical* id that matches nothing
            # answers an honest empty page, while the malformed one is refused.
            # Under the old behaviour both returned the same bytes.
            absent = client.get("/api/side-effects", params={"thread_id": "no-such-thread"})
            assert absent.status_code == 200
            assert absent.json()["count"] == 0


class TestReconciliation:
    def test_a_member_cannot_reconcile(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _member("user-1")
        with _open(holder, journal) as client:
            response = client.post(
                "/api/side-effects/call_a/reconcile",
                json={"verdict": "confirmed_success", "reason": "checked the provider log"},
            )
            assert response.status_code == 403
            assert response.json()["code"] == CODE_FORBIDDEN
            # And the entry is untouched: still waiting.
            entry = client.get("/api/side-effects/call_a").json()
            assert entry["status"] == "unknown"

    def test_reconcile_settles_confirmed_success_without_escalation(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            response = client.post(
                "/api/side-effects/call_a/reconcile",
                json={"verdict": "confirmed_success", "reason": "the provider log shows the command ran once"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
        assert body["status"] == "reconciled"
        assert body["verdict"] == "confirmed_success"
        assert body["reopened"] is False
        assert body["escalated"] is False  # a confirmed success is not an incident
        assert body["reconcilable"] is False
        assert body["entry"]["verdict"] == "confirmed_success"
        assert "ran once" in body["entry"]["detail"]  # the reason is recorded
        assert body["entry"]["needs_reconciliation"] is False

    def test_a_confirmed_failure_on_a_high_risk_effect_escalates(self, journal: Any) -> None:
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            response = client.post(
                "/api/side-effects/call_b/reconcile",
                json={"verdict": "confirmed_failure", "reason": "the push failed after the remote accepted the ref"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
        assert body["status"] == "reconciled"
        assert body["verdict"] == "confirmed_failure"
        assert body["escalated"] is True  # high_risk + confirmed failure

    def test_undetermined_reopens_the_entry(self, journal: Any) -> None:
        """Recording "could not tell" as a failure is how a duplicate is created."""
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            response = client.post(
                "/api/side-effects/call_a/reconcile",
                json={"verdict": "undetermined", "reason": "the provider offers no query API"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "unknown"
            assert body["reopened"] is True
            assert body["reconcilable"] is True
            assert body["entry"]["verdict"] is None  # not settled
            assert "no query API" in body["entry"]["detail"]
            # It is back in the queue for a later attempt.
            queue = client.get("/api/side-effects", params={"status": "unknown"}).json()
            assert [entry["tool_call_id"] for entry in queue["entries"]] == ["call_a", "call_b"]

    def test_reconciling_a_settled_entry_is_409_with_the_ledgers_words(self, journal: Any) -> None:
        """The transition table — not this router — owns what is legal."""
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            response = client.post(
                "/api/side-effects/call_c/reconcile",
                json={"verdict": "confirmed_failure", "reason": "second-guessing a completed fetch"},
            )
            assert response.status_code == 409
            body = response.json()
            assert body["code"] == CODE_TRANSITION
            assert "completed" in body["detail"]  # the ledger's own words

    def test_a_reason_is_required(self, journal: Any) -> None:
        """A verdict with no stated basis is exactly what this ledger prevents."""
        holder = _Holder()
        holder.user = _admin()
        with _open(holder, journal) as client:
            missing = client.post("/api/side-effects/call_a/reconcile", json={"verdict": "confirmed_success"})
            empty = client.post("/api/side-effects/call_a/reconcile", json={"verdict": "confirmed_success", "reason": ""})
        assert missing.status_code == 422
        assert empty.status_code == 422
        # Refused before anything was written:
        # (the entry is untouched — checked by the settle tests' fresh ledgers)

    def test_a_second_settle_on_the_durable_ledger_is_409(self, sql_ledger: Any) -> None:
        """Two reconcilers cannot both settle one entry — the SQL guard's job.

        The in-memory reference allows the self-transition the deterministic
        table permits; the durable implementation refuses it through the
        conditional write. This is the property production relies on, so it is
        pinned against the real ledger.
        """
        holder = _Holder()
        holder.user = _admin()
        with TestClient(_app(holder)) as client:
            client.portal.call(_seed_sql_unknown, sql_ledger)

            first = client.post(
                "/api/side-effects/sql_call/reconcile",
                json={"verdict": "confirmed_success", "reason": "checked the remote ref"},
            )
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "reconciled"

            second = client.post(
                "/api/side-effects/sql_call/reconcile",
                json={"verdict": "confirmed_failure", "reason": "changed my mind"},
            )
            assert second.status_code == 409, second.text
            assert second.json()["code"] == CODE_TRANSITION

            # The first verdict is what the ledger still records.
            entry = client.get("/api/side-effects/sql_call").json()
            assert entry["verdict"] == "confirmed_success"
            assert entry["status"] == "reconciled"
