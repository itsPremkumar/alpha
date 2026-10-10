"""Tests for ``GET /api/bots/working`` — which bots are working right now.

The route exists because ``/health/overview`` cannot answer the question. Its
liveness verdict comes from heartbeats, and a heartbeat is only written by
``POST /{name}/heartbeat``, which nothing in a stock deployment calls. Measured
on a live install: 96 bots, 73 rows reading ``healthy``, and **every** row
reporting "no heartbeat recorded" — so the only honest reading that surface can
give about a bot mid-run is "responsive, no task reported".

The run store does know: a run row records the assistant the Gateway was asked
to run, and the frontend sends ``assistant_id = activeBot.name`` for a bot chat.

What is pinned here:

1. a non-terminal run naming a bot attributes to it, under the lowercased key
   the health engine also uses, carrying run/thread/status/model/elapsed;
2. a run naming nobody is **counted, not attributed** — guessing from the
   thread is how a run lands on a bot that was never asked to do it;
3. terminal runs and internal ``operation_kind`` rows are not work;
4. a memory backend refuses with 503 naming the backend, because there is no run
   history and "0 bots working" would claim a measured idle fleet;
5. a store that exists but fails the read answers ``reported: false`` with its
   reason and **null** counts — a failed read is never an idle fleet;
6. the route is declared before the ``/{name}`` catch-all, or Starlette answers
   ``Room 'working' not found``-style with a bot profile lookup instead.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from alpha.persistence.base import Base
from alpha.persistence.run.model import RunRow
from alpha.persistence.thread_meta.model import ThreadMetaRow
from app.gateway.routers import bots

# Pinned so the seeded elapsed times are exactly what the route computes.
NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)


class _FrozenDatetime(datetime):
    """``datetime`` whose ``now()`` is fixed, for deterministic durations."""

    _frozen: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        if cls._frozen is None:  # pragma: no cover - defensive fallback
            return super().now(tz)
        return cls._frozen if tz is None else cls._frozen.astimezone(tz)


def _seed() -> tuple[list[ThreadMetaRow], list[RunRow]]:
    # t1 and t2 carry the live work; t3/t4 exist because the run table enforces
    # `uq_runs_thread_active` — at most one pending/running row per thread — so
    # the two other live rows need threads of their own.
    threads = [
        ThreadMetaRow(thread_id="t1", user_id="user-a", display_name="investigate flaky timeout"),
        # t2 deliberately has no threads_meta row: the title must stay null.
    ]
    runs = [
        # The live run the whole feature is for: a bot chat run in flight.
        RunRow(
            run_id="run-live-1",
            thread_id="t1",
            assistant_id="Coder",  # mixed case: the key must fold
            status="running",
            model_name="union-alpha",
            operation_kind="run",
            created_at=NOW - timedelta(seconds=90),
            updated_at=NOW - timedelta(seconds=5),
        ),
        # A second live run on another bot, in a thread with no display name.
        RunRow(
            run_id="run-live-2",
            thread_id="t2",
            assistant_id="qa-lead",
            status="pending",
            operation_kind="run",
            created_at=NOW - timedelta(seconds=12),
            updated_at=NOW - timedelta(seconds=12),
        ),
        # Nobody is named, so nobody may be blamed for it.
        RunRow(
            run_id="run-orphan-1",
            thread_id="t3",
            assistant_id=None,
            status="running",
            operation_kind="run",
            created_at=NOW - timedelta(seconds=30),
            updated_at=NOW - timedelta(seconds=30),
        ),
        # Live, but internal bookkeeping rather than an agent working.
        RunRow(
            run_id="checkpoint-write-1",
            thread_id="t4",
            assistant_id="Coder",
            status="running",
            operation_kind="checkpoint_write",
            created_at=NOW - timedelta(seconds=10),
            updated_at=NOW - timedelta(seconds=10),
        ),
        # A finished run on the same bot and thread as run-live-1: t1 may hold
        # many terminal rows, but only one live one.
        RunRow(
            run_id="run-done-1",
            thread_id="t1",
            assistant_id="Coder",
            status="success",
            operation_kind="run",
            created_at=NOW - timedelta(hours=2),
            updated_at=NOW - timedelta(hours=2) + timedelta(minutes=1),
        ),
    ]
    for status in ("success", "error", "timeout", "interrupted"):
        runs.append(
            RunRow(
                run_id=f"run-terminal-{status}",
                thread_id="t1",
                assistant_id="tester",
                status=status,
                operation_kind="run",
                created_at=NOW - timedelta(minutes=5),
                updated_at=NOW - timedelta(minutes=4),
            )
        )
    return threads, runs


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bots-working.db'}", poolclass=NullPool)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        threads, runs = _seed()
        async with sf() as session:
            session.add_all(threads)
            session.add_all(runs)
            await session.commit()

    asyncio.run(_setup())
    yield sf
    asyncio.run(engine.dispose())


@pytest.fixture()
def client(session_factory, monkeypatch):
    import alpha.persistence.engine as engine_module

    monkeypatch.setattr(engine_module, "get_session_factory", lambda: session_factory)
    # The route folds the wall clock to NOW so elapsed times are deterministic.
    monkeypatch.setattr(bots, "datetime", _FrozenDatetime)
    _FrozenDatetime._frozen = NOW
    app = make_authed_test_app()
    app.include_router(bots.router)
    return TestClient(app)


class TestBotsWorking:
    def test_live_run_is_attributed_to_the_bot_it_names(self, client):
        body = client.get("/api/bots/working").json()

        assert body["reported"] is True
        assert body["reason"] is None
        by_bot = body["active_runs_by_bot"]
        assert set(by_bot) == {"coder", "qa-lead"}, "the key is folded exactly as alpha.bots.health keys its rows, so the two payloads join"

        coder = by_bot["coder"]
        assert len(coder) == 1, "a terminal run on the same bot is not live work"
        entry = coder[0]
        assert entry["bot_name"] == "Coder", "the server's own spelling is preserved beside the folded key"
        assert entry["run_id"] == "run-live-1"
        assert entry["thread_id"] == "t1"
        assert entry["thread_title"] == "investigate flaky timeout"
        assert entry["status"] == "running"
        assert entry["model_name"] == "union-alpha"
        assert entry["started_at"] is not None
        assert entry["elapsed_seconds"] == pytest.approx(90.0), "elapsed is measured from the row's start"

        # "pending" is live work too: the run exists and has not finished.
        assert by_bot["qa-lead"][0]["status"] == "pending"
        assert by_bot["qa-lead"][0]["elapsed_seconds"] == pytest.approx(12.0)

    def test_counts_agree_with_the_map_and_name_the_unattributed_runs(self, client):
        body = client.get("/api/bots/working").json()
        counts = body["counts"]
        # Three live `run` rows (two named, one not); the live checkpoint_write
        # row is deliberately NOT counted, and neither are the terminal rows.
        assert counts["active_runs"] == 3
        assert counts["attributed_runs"] == 2
        assert counts["unattributed_runs"] == 1
        assert counts["bots_working"] == len(body["active_runs_by_bot"])
        assert "checkpoint-write-1" not in str(body), "internal operation kinds are never an agent working"
        assert "run-terminal-error" not in str(body), "a terminal run is not live work"

    def test_a_run_naming_nobody_is_counted_never_attributed(self, client):
        body = client.get("/api/bots/working").json()
        by_bot = body["active_runs_by_bot"]
        # The unattributed run is counted once and attributed to no key at all.
        assert body["counts"]["unattributed_runs"] == 1
        assert body["counts"]["attributed_runs"] + body["counts"]["unattributed_runs"] == body["counts"]["active_runs"]
        assert all(entry["run_id"] != "run-orphan-1" for entries in by_bot.values() for entry in entries)

    def test_a_thread_with_no_display_name_keeps_a_null_title(self, client):
        entry = client.get("/api/bots/working").json()["active_runs_by_bot"]["qa-lead"][0]
        assert entry["thread_title"] is None, "an untitled thread is disclosed, never named by the client"

    def test_unreported_model_stays_null(self, client):
        entry = client.get("/api/bots/working").json()["active_runs_by_bot"]["qa-lead"][0]
        assert entry["model_name"] is None, "absent is null, not '' and not a default model"

    def test_the_memory_backend_refuses_with_503_naming_itself(self, monkeypatch):
        import alpha.persistence.engine as engine_module

        monkeypatch.setattr(engine_module, "get_session_factory", lambda: None)
        app = make_authed_test_app()
        app.include_router(bots.router)
        resp = TestClient(app).get("/api/bots/working")
        assert resp.status_code == 503
        assert "memory" in resp.json()["detail"], "the refusal names the backend, so '0 working' is never implied"

    def test_a_failing_read_is_not_an_idle_fleet(self, monkeypatch):
        import alpha.persistence.engine as engine_module

        class _Boom:
            async def __aenter__(self):
                raise RuntimeError("sqlite is locked")

            async def __aexit__(self, *exc):
                return False

        def _broken():
            return _Boom()

        monkeypatch.setattr(engine_module, "get_session_factory", _broken)
        app = make_authed_test_app()
        app.include_router(bots.router)
        resp = TestClient(app).get("/api/bots/working")
        assert resp.status_code == 200, "the store exists; this read failed — the route says so instead of 503-ing"
        body = resp.json()
        assert body["reported"] is False
        assert "could not be read" in body["reason"]
        assert body["active_runs_by_bot"] == {}
        # Every counter is null: "we could not look" is not "nobody is working".
        assert body["counts"] == {
            "active_runs": None,
            "attributed_runs": None,
            "unattributed_runs": None,
            "bots_working": None,
        }

    def test_the_route_is_declared_before_the_name_catch_all(self, client):
        # `working` matches the bot-name grammar, so a catch-all declared first
        # would answer `404 Bot 'working' not found` instead of the report.
        resp = client.get("/api/bots/working")
        assert resp.status_code == 200
        assert "active_runs_by_bot" in resp.json()
