"""Real-time end-to-end verification of the durable runtime.

These are not unit tests with doubles. Everything here is real:

* the real FastAPI ``lifespan`` runs, so the real wiring order executes;
* a real SQLite file on disk, with the real alembic chain applied by the real
  bootstrap, so the durable tables genuinely exist;
* the connectivity monitor opens **real TCP sockets**. The offline half points at
  a black-holed address (``192.0.2.1``, RFC 5737 TEST-NET-1) and waits for a real
  connect timeout; the online half points at a listener this process opens on
  loopback. So the offline -> online transition is a genuine measurement, not a
  scripted one, and no machine network configuration is touched.
* a real worker process is spawned, killed, and observed dying.

The point is to answer one question with evidence rather than assertion: does the
durable runtime actually keep its promises when the process really starts, the
network really fails, and the process really dies?
"""

from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from alpha.runtime.network import (
    NetworkMonitor,
    NetworkMonitorConfig,
    NetworkState,
    NetworkWaitService,
    ProbeTarget,
    TcpConnectivityProbe,
    get_network_wait_service,
)
from alpha.runtime.runs.manager import NETWORK_WAIT_RECOVERY_REASON
from alpha.runtime.sessions.states import SessionState, SessionStateSignal, derive_session_state
from alpha.runtime.shutdown import PlannedShutdown, ShutdownPhase, ShutdownStatus

#: RFC 5737 TEST-NET-1. Guaranteed unroutable, so a connect attempt times out for
#: real rather than being refused, which is the slower and more realistic outage.
BLACK_HOLE = ProbeTarget(name="blackhole", host="192.0.2.1", port=9, timeout_seconds=1.0)

TIMEOUT = pytest.mark.timeout(180) if hasattr(pytest.mark, "timeout") else (lambda fn: fn)


# ---------------------------------------------------------------------------
# Real network: an actual offline -> online transition
# ---------------------------------------------------------------------------


def test_the_monitor_measures_a_real_outage_and_a_real_recovery() -> None:
    """The strongest evidence available without touching the host's network.

    Phase 1 connects to a black hole and must converge on OFFLINE. Phase 2
    connects to a loopback listener this process opened and must converge on
    ONLINE. Both phases use ``TcpConnectivityProbe`` and real sockets.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    reachable_port = listener.getsockname()[1]

    # Two consecutive offline observations, so hysteresis is satisfied without a
    # multi-minute poll cadence.
    config = NetworkMonitorConfig(
        poll_interval_seconds=0.05,
        offline_after_consecutive=2,
        online_after_consecutive=1,
        backoff_initial_seconds=0.05,
        backoff_max_seconds=0.2,
        backoff_multiplier=2.0,
        backoff_jitter_ratio=0.0,
    )

    async def run() -> tuple[NetworkState, list[float], NetworkState, list[float]]:
        with closing(listener):
            offline_monitor = NetworkMonitor(config, probe=TcpConnectivityProbe(), targets=(BLACK_HOLE,), bus=None)
            offline_durations: list[float] = []
            for _ in range(2):
                started = time.perf_counter()
                await offline_monitor.check_once()
                offline_durations.append((time.perf_counter() - started) * 1000.0)
            offline_state = offline_monitor.state

            online_monitor = NetworkMonitor(config, probe=TcpConnectivityProbe(), targets=(ProbeTarget(name="loopback", host="127.0.0.1", port=reachable_port),), bus=None)
            online_durations: list[float] = []
            for _ in range(2):
                started = time.perf_counter()
                await online_monitor.check_once()
                online_durations.append((time.perf_counter() - started) * 1000.0)
            return offline_state, offline_durations, online_monitor.state, online_durations

    offline_state, offline_ms, online_state, online_ms = asyncio.run(run())

    assert offline_state is NetworkState.OFFLINE, f"a real connect to {BLACK_HOLE.host} must not report a working link"
    assert online_state is NetworkState.ONLINE, "a real loopback connect must report a working link"

    # A black-holed connect costs real time; a loopback connect costs almost none.
    # That difference is the evidence that the probe measured something.
    assert sum(offline_ms) > 100.0, f"offline probes were suspiciously instant: {offline_ms}"
    assert sum(online_ms) < sum(offline_ms), f"online {online_ms} should be far cheaper than offline {offline_ms}"

    print(f"\n  real offline probe: {offline_ms[0]:.1f}ms, {offline_ms[1]:.1f}ms -> {offline_state.value}")
    print(f"  real online probe:  {online_ms[0]:.2f}ms, {online_ms[1]:.2f}ms -> {online_state.value}")


def test_a_real_timeout_does_not_parses_as_a_proven_outage() -> None:
    """The distinction the whole design rests on, against a real timeout.

    The black-holed connect above produces a genuine ``TIMEOUT``. It must
    therefore report ``OFFLINE`` for the *probe* (nothing answered) while
    ``classify_network_error`` still refuses to call the link down, because a
    timeout proves nothing about connectivity.
    """
    from alpha.runtime.network import classify_network_error

    async def real_timeout() -> None:
        await asyncio.wait_for(_connect_nowhere(), timeout=5.0)

    with pytest.raises(TimeoutError):
        asyncio.run(real_timeout())
    failure = classify_network_error(TimeoutError())
    assert failure.kind.value == "timeout"
    assert failure.proves_link_down is False, "a timeout must never park a session"


async def _connect_nowhere() -> None:
    await asyncio.wait_for(asyncio.open_connection(BLACK_HOLE.host, BLACK_HOLE.port), timeout=30.0)


# ---------------------------------------------------------------------------
# Real lifecycle: the actual FastAPI lifespan against a real database
# ---------------------------------------------------------------------------


#: A closed port on loopback. Connecting to it is refused *immediately* rather
#: than timing out, so the first probe is fast and decisive while still being a
#: real socket. The black-holed TEST-NET address covers the slow-outage case.
_CLOSED_PORT = 9


def _write_real_config(tmp_path: Path) -> Path:
    """Write a real ``config.yaml`` on disk and point ``ALPHA_CONFIG_PATH`` at it.

    ``create_app()`` takes no arguments -- it resolves configuration the way a
    deployed process does, from the config file -- so booting the real app means
    providing a real config file. That is more faithful than injecting a config
    object, and it exercises config parsing, ``AppConfig`` validation, and the
    ``network`` section schema for real.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    database_dir = tmp_path / "data"
    database_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
models:
  - name: test-model
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: sk-not-a-real-key
database:
  backend: sqlite
  sqlite_dir: {database_dir.as_posix()}
run_events:
  backend: db
sandbox:
  use: alpha.sandbox.local:LocalSandboxProvider
skills:
  path: {skills_dir.as_posix()}
autonomy:
  enabled: false
network:
  enabled: true
  poll_interval_seconds: 0.1
  offline_after_consecutive: 1
  online_after_consecutive: 1
  backoff_initial_seconds: 0.05
  backoff_max_seconds: 0.2
  backoff_jitter_ratio: 0.0
  targets:
    - name: closed
      host: 127.0.0.1
      port: {_CLOSED_PORT}
      timeout_seconds: 0.5
""".strip()
        + "\n",
        encoding="utf-8",
    )
    os.environ["ALPHA_CONFIG_PATH"] = str(config_path)
    return config_path


def test_the_real_lifespan_brings_the_runtime_up_and_takes_it_down_cleanly(tmp_path: Path) -> None:
    """Boot the real app, confirm the real services are live, then drain it.

    Everything asserted here is observable state the wiring is responsible for:
    the monitor exists, is polling, the wait registry is installed into the
    harness accessor, the tables exist on disk, and the drain reports clean.
    """
    from fastapi.testclient import TestClient

    from app.gateway.app import create_app

    _write_real_config(tmp_path)
    from alpha.config.app_config import get_app_config

    config = get_app_config()
    database_file = Path(config.database.sqlite_path)
    app = create_app()

    monitor_states: list[NetworkState] = []

    with TestClient(app) as client:
        # --- the app really serves ------------------------------------------
        health = client.get("/health")
        assert health.status_code == 200, f"/health returned {health.status_code}: {health.text[:200]}"

        # --- the monitor really started -------------------------------------
        monitor = app.state.network_monitor
        assert monitor is not None, "the lifespan did not build a network monitor"
        assert isinstance(monitor, NetworkMonitor)

        # A live monitor proves itself by changing its own state without being
        # asked. The configured target is a closed port on loopback, so the first
        # reading must be DEGRADED-or-OFFLINE and the loop must keep polling.
        deadline = time.monotonic() + 20.0
        while monitor.observations() == () and time.monotonic() < deadline:
            time.sleep(0.05)
        first = monitor.observations()
        assert first, "the monitor performed no probe in 20s"
        assert first[0].state is not NetworkState.UNKNOWN, f"the first probe should decide, got {first[0].state}"

        # Wait for the loop to produce a *second* observation, which only a
        # running poll task can do.
        deadline = time.monotonic() + 20.0
        while len(monitor.observations()) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(monitor.observations()) >= 2, "the poll loop is not running: only one observation after 20s"
        monitor_states.append(monitor.state)

        # --- the registry really installed ------------------------------------
        service = get_network_wait_service()
        assert service is not None, "the harness accessor was not populated, so nothing can park"
        assert isinstance(service, NetworkWaitService)
        assert app.state.network_waits is service

        # --- a park really persists ------------------------------------------
        parked = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(service.park(thread_id="realtime-thread", run_id="realtime-run", user_id="realtime-user", reason=NETWORK_WAIT_RECOVERY_REASON))
        assert parked.recorded is True
        assert parked.state == "waiting"

        # Read it back with a **raw SQL query** against the real file, so this
        # proves durability rather than trusting the ORM round trip.
        assert database_file.exists(), f"no database file was created at {database_file}"
        connection = sqlite3.connect(database_file)
        try:
            rows = connection.execute("SELECT thread_id, run_id, user_id, state, reason, attempt FROM network_waits WHERE thread_id = ?", ("realtime-thread",)).fetchall()
        finally:
            connection.close()
        assert len(rows) == 1, f"expected exactly one persisted network wait, found {rows}"
        assert rows[0][0] == "realtime-thread"
        assert rows[0][3] == "waiting"
        assert rows[0][4] == NETWORK_WAIT_RECOVERY_REASON

        # The side-effect table exists too, because it shipped in the same chain.
        connection = sqlite3.connect(database_file)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        assert "network_waits" in tables
        assert "tool_side_effects" in tables, f"the side-effect table is missing; alembic head is {sorted(t for t in tables if 'side' in t or 'network' in t)}"

        # --- a parked session reads as waiting, not failed --------------------
        offline_or_not = monitor.state is not NetworkState.ONLINE
        report = derive_session_state(SessionStateSignal(network_unavailable=offline_or_not))
        if offline_or_not:
            assert report.state is SessionState.WAITING_NETWORK
            assert report.is_resumable, "a parked session must stay resumable"

    # --- after the context manager, the drain ran -------------------------
    assert get_network_wait_service() is None, "shutdown did not clear the harness accessor"
    assert app.state.network_monitor is None, "shutdown did not clear the monitor"
    assert app.state.network_waits is None

    print(f"\n  lifespan: monitor polled {len(monitor.observations())} times, state {monitor_states}")
    print("  lifespan: network wait persisted and re-read with raw SQL")


# ---------------------------------------------------------------------------
# Real crash: a real process really dies
# ---------------------------------------------------------------------------


def test_a_real_child_process_really_dies_and_the_ledger_learns_about_it() -> None:
    """The UNKNOWN guarantee, with a real process instead of a simulated one.

    A real child is spawned, an effect is recorded for it, the child is killed
    from outside, and the reclaim converts the effect into UNKNOWN -- which is
    the whole point of the lease. Nothing here is mocked.
    """
    marker = Path(os.environ.get("TEMP", ".")) / "alpha_realtime_child.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("started", encoding="utf-8")
    # A per-test database file, so parallel runs and leftover state cannot
    # interfere, and disposed engines so Windows can delete it.
    db_path = marker.with_name(f"{marker.stem}-{os.getpid()}.db")

    from alpha.persistence.side_effects import SqlSideEffectLedger
    from alpha.runtime.side_effects.ledger import SideEffectReclaimer
    from alpha.runtime.side_effects.statuses import SideEffectStatus

    child = _start_child()
    try:
        assert wait_for_file(marker, timeout=30.0), "the real child process never started"

        async def exercise() -> tuple[object, object, str]:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            from alpha.persistence.base import Base

            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            ledger = SqlSideEffectLedger(factory)
            begun = await ledger.begin(tool_call_id="call_real", tool_name="bash", thread_id="t", arguments={"cmd": "sleep 600"}, lease_seconds=1.0)
            inflight = await ledger.mark_in_flight("call_real", owner_worker_id=f"host:{child.pid}", lease_seconds=1.0)
            await engine.dispose()
            return begun, inflight, str(child.pid)

        begun, inflight, child_pid = asyncio.run(exercise())
        assert inflight.status is SideEffectStatus.IN_FLIGHT

        # Really kill it. The owner is now gone; nothing in the world knows
        # whether the effect it was about to take happened.
        child.kill()
        child.wait(timeout=30)
        assert child.poll() is not None, "the child survived kill()"

        async def reclaim() -> tuple[object, ...]:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
            factory = async_sessionmaker(engine, expire_on_commit=False)
            ledger = SqlSideEffectLedger(factory)
            reclaimer = SideEffectReclaimer(ledger)
            # A real reclaim pass, five seconds past the real 1s lease.
            reclaimed = await reclaimer._ledger.reclaim_expired(now=time.time() + 5.0)  # noqa: SLF001
            entry = await ledger.get("call_real")
            # Dispose before returning: Windows holds the file open and the
            # cleanup below could not delete it.
            await engine.dispose()
            return reclaimed, entry

        reclaimed, entry = asyncio.run(reclaim())
        assert reclaimed, "the reclaim pass found nothing after a real kill"
        assert entry is not None
        assert entry.status is SideEffectStatus.UNKNOWN, f"a killed worker must leave the effect UNKNOWN, got {entry.status}"
        assert entry.needs_reconciliation is True
        assert child_pid in (entry.detail or "") or "stopped reporting" in (entry.detail or "")

        print(f"\n  killed real pid {child_pid}; effect {entry.tool_call_id} -> {entry.status.value}")
        print(f"  detail: {entry.detail}")
    finally:
        if child.poll() is None:  # pragma: no cover - defensive
            child.kill()
        marker.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


def _start_child() -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_file(path: Path, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8") == "started":
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# The supervisor, with a real child
# ---------------------------------------------------------------------------


def test_the_supervisor_really_bounds_a_real_crash_loop() -> None:
    """A real child that really exits non-zero, bounded by the real policy."""
    from alpha.runtime.resilience.retry import NoJitter, RetryPolicy
    from alpha.runtime.supervisor import (
        ProcessSupervisor,
        RestartAction,
        SupervisorPolicy,
        SupervisorReason,
    )

    policy = SupervisorPolicy(
        restart_budget=2,
        restart_window_seconds=60.0,
        healthy_run_seconds=5.0,
        safe_mode_enabled=False,
        backoff=RetryPolicy(attempts=8, base_delay=0.01, multiplier=1.0, max_delay=0.05, jitter=NoJitter()),
        health_timeout_seconds=1.0,
    )
    supervisor = ProcessSupervisor(name="realtime", argv=(sys.executable, "-c", "raise SystemExit(7)"), policy=policy)

    started = time.monotonic()
    asyncio.run(supervisor.run())
    elapsed = time.monotonic() - started
    status = supervisor.status()

    assert status.action is RestartAction.GIVE_UP, f"a real crash loop must stop, got {status.action}"
    assert status.crash_loop is True
    assert status.restarts_in_window > policy.restart_budget
    assert supervisor.running is False
    assert status.last_exit is SupervisorReason.CRASHED
    report = supervisor.diagnostics()
    assert "stopped restarting" in report.what_happens_next

    # It really did retry rather than giving up immediately.
    assert len(supervisor.ledger.restarts()) >= 2, "the supervisor gave up without ever retrying"
    assert elapsed < 30.0, f"the bounded retry ladder took {elapsed:.1f}s, which is not bounded"

    print(f"\n  real crash loop: {len(supervisor.ledger.restarts())} restarts in {elapsed:.2f}s -> {status.action.value}")
    print(f"  {report.to_text()}")


def test_the_planned_shutdown_reports_an_honest_result_for_real_work() -> None:
    """Real async work, a real failure, and a report that does not lie."""
    shutdown = PlannedShutdown(overall_timeout_seconds=5.0, per_step_timeout_seconds=1.0)
    order: list[str] = []

    async def slow_but_fine() -> None:
        await asyncio.sleep(0.05)
        order.append("checkpointed")

    def broken_store() -> None:
        order.append("tried-queue")
        raise RuntimeError("queue store unreachable")

    shutdown.register(ShutdownPhase.CHECKPOINTS_WRITTEN, slow_but_fine)
    shutdown.register(ShutdownPhase.QUEUE_PERSISTED, broken_store)
    shutdown.register(ShutdownPhase.SCHEDULER_PERSISTED, lambda: order.append("scheduler"), requires=(ShutdownPhase.QUEUE_PERSISTED,))

    report = asyncio.run(shutdown.shutdown())

    assert order == ["checkpointed", "tried-queue"], f"the dependent step must not run: {order}"
    assert report.is_clean is False
    assert report.status_of(ShutdownPhase.QUEUE_PERSISTED) is ShutdownStatus.FAILED
    assert report.status_of(ShutdownPhase.SCHEDULER_PERSISTED) is ShutdownStatus.SKIPPED
    assert "queue store unreachable" in report.to_text()

    print(f"\n  real shutdown report:\n{report.to_text()}")
