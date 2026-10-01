"""The internet-connectivity surface: `GET|POST /api/ops/network`.

What this file exists to prove
------------------------------
`alpha.runtime.network` has measured connectivity for the whole life of the
process since it was written, and until this route existed **no production
caller read it**: the monitor sat on ``app.state.network_monitor`` and its
reading died with the poll tick. An operator whose link was down could not be
told so, and could not ask for a fresh measurement.

Three things are therefore load-bearing here, and each has its own failure mode:

1. **The measured round-trip is reported, and its absence is not a zero.**
   ``latency_ms`` is the mean over the *reachable* targets only. Averaging in a
   timed-out target would describe neither, and rendering ``None`` as ``0`` would
   draw the worst possible link as the fastest possible one.
2. **``UNKNOWN`` still permits a network attempt.** The flag is read from the
   monitor's own decision rather than re-derived from the state string in the
   Gateway, because a Gateway that copied the vocabulary would eventually
   disagree with the harness about exactly this case — and that disagreement
   parks live work on a broken probe.
3. **A manual retry goes through the same serialized transition as the poll
   loop.** Not beside it, not instead of it. A click landing on the same tick as
   a poll must not double-count hysteresis corroboration and publish a state the
   ladder has not seen twice.

Everything else in this file is an honesty guard: the checks exist because a
reasonable reader would otherwise take a compact payload for more than it says.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from alpha.runtime.network import NetworkMonitor, NetworkMonitorConfig, ProbeOutcome
from alpha.runtime.resilience.clock import ManualClock
from app.gateway import ops_runtime
from app.gateway.ops_runtime import network_snapshot
from app.gateway.routers import ops as ops_router

BACKEND = Path(__file__).resolve().parents[1]

#: Two endpoints, so a DEGRADED reading and a partial-latency mean are both
#: reachable without inventing a third.
TARGETS = ("cloudflare", "google")


def _read(relative: str) -> str:
    return (BACKEND / relative).read_text(encoding="utf-8")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(ops_router.router)
    return app


async def _get(app: FastAPI, path: str) -> dict[str, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


async def _post(app: FastAPI, path: str) -> dict[str, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.post(path)
    assert response.status_code == 200, response.text
    return response.json()


# ══════════════════════════════════════════════════════════════════════════
# Fakes
# ══════════════════════════════════════════════════════════════════════════


class ScriptedReachableProbe:
    """A probe driven by a per-call list of booleans, one value per target."""

    def __init__(self, script: list[tuple[bool, ...]]) -> None:
        self._script = script
        self.calls = 0

    async def probe(self, targets: Any) -> tuple[ProbeOutcome, ...]:
        entry = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return tuple(
            ProbeOutcome(
                target=target.name,
                reachable=bool(entry[index]) if index < len(entry) else True,
                elapsed_ms=12.0 if (index < len(entry) and entry[index]) else 2000.0,
            )
            for index, target in enumerate(targets)
        )


class OverlapProbe:
    """Records how many probes were ever in flight at the same moment.

    This is the instrument that makes the serialization observable. Without the
    lock, two concurrent ``check_once()`` calls overlap and this counter reaches
    2; with it, it cannot.
    """

    def __init__(self, *, hold: float = 0.02) -> None:
        self.hold = hold
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def probe(self, targets: Any) -> tuple[ProbeOutcome, ...]:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.hold)
            self.calls += 1
            return tuple(ProbeOutcome(target=target.name, reachable=True, elapsed_ms=5.0) for target in targets)
        finally:
            self.in_flight -= 1


def _monitor(script: list[tuple[bool, ...]], **overrides: Any) -> tuple[NetworkMonitor, ScriptedReachableProbe]:
    from alpha.runtime.network.probe import ProbeTarget

    probe = ScriptedReachableProbe(script)
    config = NetworkMonitorConfig(poll_interval_seconds=0.01, backoff_initial_seconds=0.01, backoff_max_seconds=0.02, **overrides)
    monitor = NetworkMonitor(
        config,
        probe=probe,
        targets=tuple(ProbeTarget(name=name, host=f"{name}.test", port=443, timeout_seconds=0.5) for name in TARGETS),
        clock=ManualClock(),
    )
    return monitor, probe


class _Registry:
    """A parked-session registry double. ``None`` counts are still counts."""

    def __init__(self, *, open_waits: int = 0, fail: bool = False) -> None:
        self._open = open_waits
        self._fail = fail

    async def status(self) -> Any:
        if self._fail:
            raise RuntimeError("network_waits is unreachable")
        return type("S", (), {"open_waits": self._open, "claimed": 0, "resumed": 0, "gave_up": 0})()


# ══════════════════════════════════════════════════════════════════════════
# 1. The projection: latency, targets, and the attempt permission
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_latency_is_the_mean_over_the_reachable_endpoints_only() -> None:
    monitor, _ = _monitor([(True, True)])
    await monitor.check_once()

    snapshot = network_snapshot(monitor, None)
    assert snapshot["reported"] is True
    assert snapshot["state"] == "online"
    assert snapshot["latency_ms"] == 12.0, "both endpoints answered at 12 ms, so the mean is 12 ms"
    assert [t["name"] for t in snapshot["targets"]] == list(TARGETS)
    assert all(t["reachable"] for t in snapshot["targets"])


@pytest.mark.asyncio
async def test_an_unreachable_endpoint_never_enters_the_latency_mean() -> None:
    """A partial link has no honest single latency figure.

    Averaging 12 ms with a 2000 ms timeout would report ~1006 ms, which
    describes neither the reachable endpoint nor the dead one.
    """

    monitor, _ = _monitor([(True, False)])
    await monitor.check_once()

    snapshot = network_snapshot(monitor, None)
    assert snapshot["state"] == "degraded", "one of two answers is DEGRADED, not OFFLINE"
    assert snapshot["latency_ms"] == 12.0
    # …and the failed target still reports the time it took to fail, so an
    # operator can tell a fast refusal from a black-holed route.
    unreachable = [t for t in snapshot["targets"] if not t["reachable"]]
    assert unreachable[0]["latency_ms"] == 2000.0


@pytest.mark.asyncio
async def test_no_reachable_endpoint_reports_no_latency_rather_than_zero() -> None:
    monitor, _ = _monitor([(False, False)])
    await monitor.check_once()

    snapshot = network_snapshot(monitor, None)
    assert snapshot["state"] == "offline"
    assert snapshot["latency_ms"] is None, "a null reading must never be rounded to a measured 0 ms"
    assert snapshot["latency_ms"] != 0


@pytest.mark.asyncio
async def test_unknown_still_permits_a_network_attempt() -> None:
    """The asymmetry the whole state vocabulary exists for.

    A probe that could not run is a *broken probe*. Announcing an outage on one
    parks every session on a lie, so UNKNOWN keeps admitting network work.
    """

    class BrokenProbe:
        async def probe(self, targets: Any) -> tuple[ProbeOutcome, ...]:
            raise RuntimeError("the probe subsystem is wedged")

    from alpha.runtime.network.probe import ProbeTarget

    monitor = NetworkMonitor(
        NetworkMonitorConfig(unknown_after_consecutive=1),
        probe=BrokenProbe(),
        targets=tuple(ProbeTarget(name=name, host=f"{name}.test", port=443, timeout_seconds=0.5) for name in TARGETS),
        clock=ManualClock(),
    )
    for _ in range(3):
        await monitor.check_once()

    snapshot = network_snapshot(monitor, None)
    assert snapshot["state"] == "unknown"
    assert snapshot["state"] != "offline", "UNKNOWN is never rounded to OFFLINE"
    assert snapshot["allows_network_attempt"] is True, "not knowing is not knowing the link is down"


@pytest.mark.asyncio
async def test_a_confirmed_outage_refuses_an_attempt() -> None:
    monitor, _ = _monitor([(False, False)])
    await monitor.check_once()
    assert network_snapshot(monitor, None)["allows_network_attempt"] is False


# ══════════════════════════════════════════════════════════════════════════
# 2. The retry schedule: who keeps trying, and how often
# ══════════════════════════════════════════════════════════════════════════


def test_a_stopped_loop_never_claims_a_retry_that_will_not_happen() -> None:
    """`retrying` must be false when nothing is scheduled to run.

    Without this, a process whose poll loop was never started would tell an
    operator it was "retrying" — the most reassuring possible lie.
    """

    class Stopped:
        running = False

        @property
        def state(self) -> Any:
            return type("S", (), {"value": "offline", "detail": ""})()

        def last_observation(self) -> Any:
            return None

        def wait_decision(self) -> Any:
            return type("D", (), {"admit_network_work": False, "next_poll_seconds": 240.0})()

    snapshot = network_snapshot(Stopped(), None)
    assert snapshot["retry"]["automatic"] is False
    assert snapshot["retry"]["retrying"] is False, "a stopped loop cannot be retrying"
    assert snapshot["retry"]["next_probe_seconds"] == 240.0, "the drawn delay is still reported, so the cadence is visible"


@pytest.mark.asyncio
async def test_an_offline_link_with_a_running_loop_reports_the_real_retry_schedule() -> None:
    monitor, _ = _monitor([(False, False)])
    monitor.start()
    try:
        await monitor.check_once()
        snapshot = network_snapshot(monitor, None)
        retry = snapshot["retry"]
        assert retry["automatic"] is True
        assert retry["retrying"] is True, "the loop keeps re-probing a link that is not up"
        assert retry["next_probe_seconds"] is not None
        assert retry["poll_interval_seconds"] == 0.01
        assert retry["backoff_max_seconds"] == 0.02
    finally:
        await monitor.stop()


@pytest.mark.asyncio
async def test_a_healthy_link_does_not_claim_to_be_retrying() -> None:
    monitor, _ = _monitor([(True, True)])
    await monitor.check_once()
    retry = network_snapshot(monitor, None)["retry"]
    assert retry["retrying"] is False, "an online host is not retrying anything"


@pytest.mark.asyncio
async def test_the_loop_keeps_polling_after_the_link_is_lost() -> None:
    """The bug this whole layer was written to prevent, in one assertion.

    An earlier wiring read ``if monitor.state is not OFFLINE: monitor.start()``,
    reasoning that a host which had just proved the link was down should not
    probe it. That is exactly backwards: the poll loop is the only thing that
    can ever notice the link returning, so such a host sat there forever.
    """

    monitor, probe = _monitor([(False, False), (False, False), (True, True), (True, True)])
    await monitor.check_once()
    assert monitor.state.value == "offline"

    monitor.start()
    try:
        # The script recovers on the third and fourth poll; without a loop that
        # kept running while offline, `probe.calls` would stay at 1 forever.
        for _ in range(60):
            if monitor.state.value == "online":
                break
            await asyncio.sleep(0.02)
        assert monitor.state.value == "online", "the loop must notice a recovery it was not asked about"
        assert probe.calls > 2
    finally:
        await monitor.stop()


# ══════════════════════════════════════════════════════════════════════════
# 3. Serialization: a manual retry must not race the poll loop
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_concurrent_measurements_never_overlap() -> None:
    """The reason the lock exists.

    ``check_once`` is the entire state transition — probe, fold, hysteresis,
    backoff, publish. Two overlapping calls interleave those steps and
    double-count corroboration, which can publish a state the ladder has not
    seen twice.
    """

    probe = OverlapProbe()
    monitor = NetworkMonitor(probe=probe, clock=ManualClock())

    await asyncio.gather(monitor.check_once(), monitor.recheck())

    assert probe.max_in_flight == 1, "a manual retry must not run beside a poll"
    assert probe.calls == 2, "both readings still happened; they were serialized, not dropped"


@pytest.mark.asyncio
async def test_a_manual_retry_cannot_publish_a_state_the_ladder_has_not_seen_twice() -> None:
    """The user-visible consequence of the race, stated directly.

    ``offline_after_consecutive``/``online_after_consecutive`` exist to stop a
    flapping link from parking and un-parking the fleet. A recheck that
    double-counted would collapse that gate to a single sample — which is the
    entire reason hysteresis is there.
    """

    # Three concurrent confirmations. Even fully serialized, a recovery needs
    # `online_after_consecutive` agreeing observations, and the third is the one
    # that publishes. The script goes offline first and reachable from then on,
    # because a probe that keeps failing would confirm the *outage* and the gate
    # under test would never be reached.
    monitor, _ = _monitor([(False, False), (True, True), (True, True), (True, True)], offline_after_consecutive=1, online_after_consecutive=3)
    await monitor.check_once()
    assert monitor.state.value == "offline", "the first observation always publishes"

    await asyncio.gather(monitor.recheck(), monitor.recheck(), monitor.recheck())
    assert monitor.state.value == "online", "three agreeing observations are required, and three ran"

    # One observation, from a fresh monitor, must NOT publish a recovery.
    fresh, _ = _monitor([(False, False), (True, True)], offline_after_consecutive=1, online_after_consecutive=3)
    await fresh.check_once()
    observation = await fresh.recheck()
    assert observation.state.value == "offline"
    assert observation.changed is False
    # The outstanding confirmations come from the monitor's live counter, NOT from
    # the observation: `check_once` reports the branch it took, which is 0 whenever
    # the probe itself ran, so an assertion against
    # `observation.consecutive_agreeing` here would compare against 0 and prove
    # nothing about the gate.
    assert fresh.pending_confirmations == 1


@pytest.mark.asyncio
async def test_the_first_probe_after_a_cold_start_publishes_immediately() -> None:
    """Hysteresis exists to stop a *flapping* link, and there is nothing to flap
    against before the first measurement — so a Gateway booting on a dead network
    must say ``offline`` now, not ``unknown`` one poll later."""

    monitor, _ = _monitor([(False, False)], offline_after_consecutive=5)
    observation = await monitor.check_once()
    assert observation.state.value == "offline"
    assert observation.changed is True


# ══════════════════════════════════════════════════════════════════════════
# 4. The route: GET
# ══════════════════════════════════════════════════════════════════════════


def test_both_routes_are_declared_on_the_ops_router() -> None:
    paths = {getattr(route, "path", "") for route in ops_router.router.routes}
    assert "/api/ops/network" in paths
    assert "/api/ops/network/recheck" in paths


@pytest.mark.asyncio
async def test_get_reports_the_live_reading_with_its_targets() -> None:
    monitor, _ = _monitor([(True, True)])
    await monitor.check_once()

    app = _app()
    app.state.network_monitor = monitor
    app.state.network_waits = _Registry(open_waits=2)
    app.state.network_configured = True

    body = await _get(app, "/api/ops/network")

    assert body["reported"] is True
    assert body["state"] == "online"
    assert body["latency_ms"] == 12.0
    assert body["allows_network_attempt"] is True
    assert [t["name"] for t in body["targets"]] == list(TARGETS)
    assert body["retry"]["automatic"] is False, "the loop was never started in this test"
    assert body["parked_sessions"]["open_waits"] == 2
    assert body["recheck"] is None, "a read never claims a recheck happened"
    assert isinstance(body["observed_age_seconds"], float), "the age is asked of the monitor's own clock"


@pytest.mark.asyncio
async def test_an_absent_reading_is_reported_as_absent_not_as_a_healthy_link() -> None:
    app = _app()
    app.state.network_configured = True  # enabled, but nothing installed

    body = await _get(app, "/api/ops/network")

    assert body["reported"] is False
    assert body["reason"] == ops_runtime.REASON_MONITOR_ABSENT
    assert body["state"] is None
    assert body["latency_ms"] is None
    assert any("not the same as connectivity being fine" in note for note in body["notes"])


@pytest.mark.asyncio
async def test_disabled_by_configuration_is_distinguished_from_absent() -> None:
    """Two different operator problems that a missing attribute cannot tell apart."""

    app = _app()
    app.state.network_configured = False
    body = await _get(app, "/api/ops/network")
    assert body["reported"] is False
    assert body["reason"] == ops_runtime.REASON_MONITOR_DISABLED


@pytest.mark.asyncio
async def test_the_unreported_payload_has_the_same_keys_as_the_reported_one() -> None:
    """A consumer must not have to branch on which keys exist to learn that
    nothing was measured."""

    app = _app()
    app.state.network_configured = False
    unreported = await _get(app, "/api/ops/network")

    monitor, _ = _monitor([(True, True)])
    await monitor.check_once()
    live = _app()
    live.state.network_monitor = monitor
    live.state.network_configured = True
    reported = await _get(live, "/api/ops/network")

    assert set(unreported) == set(reported)


@pytest.mark.asyncio
async def test_a_parked_session_store_outage_does_not_fail_the_reading() -> None:
    """A degraded read is disclosed, not turned into a 500 that hides the link."""

    monitor, _ = _monitor([(True, True)])
    await monitor.check_once()
    app = _app()
    app.state.network_monitor = monitor
    app.state.network_waits = _Registry(fail=True)
    app.state.network_configured = True

    body = await _get(app, "/api/ops/network")

    assert body["reported"] is True
    assert body["state"] == "online", "the connectivity reading is still good and must survive"
    assert body["parked_sessions"]["reported"] is False
    assert "RuntimeError" in body["parked_sessions"]["detail"]


@pytest.mark.asyncio
async def test_a_null_latency_is_disclosed_rather_than_left_for_a_reader_to_guess() -> None:
    monitor, _ = _monitor([(False, False)])
    await monitor.check_once()
    app = _app()
    app.state.network_monitor = monitor
    app.state.network_configured = True

    body = await _get(app, "/api/ops/network")

    assert body["latency_ms"] is None
    assert any("NOT 0 ms" in note for note in body["notes"]), "a null latency must say it is not zero"
    assert any("never stops re-probing" in note for note in body["notes"]) or any("resumes automatically" in note for note in body["notes"])


# ══════════════════════════════════════════════════════════════════════════
# 5. The route: POST /recheck
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_recheck_runs_a_real_probe_and_returns_the_new_reading() -> None:
    # Offline first, reachable from the second call on: the recheck is call 1, so
    # a script that stayed unreachable would confirm the outage instead of
    # recovering, and `changed` would be False for reasons unrelated to recheck.
    monitor, probe = _monitor([(False, False), (True, True)], offline_after_consecutive=1, online_after_consecutive=1)
    await monitor.check_once()
    assert monitor.state.value == "offline"

    app = _app()
    app.state.network_monitor = monitor
    app.state.network_configured = True

    body = await _post(app, "/api/ops/network/recheck")

    assert probe.calls == 2, "the recheck actually probed"
    assert body["recheck"]["performed"] is True
    assert body["recheck"]["changed"] is True
    assert body["state"] == "online", "the response is the new reading, not the previous one"
    assert body["latency_ms"] == 12.0


@pytest.mark.asyncio
async def test_a_recheck_that_hysteresis_declines_says_so_with_the_outstanding_count() -> None:
    """Silently doing nothing is indistinguishable from a broken button."""

    monitor, _ = _monitor([(False, False), (True, True)], offline_after_consecutive=1, online_after_consecutive=3)
    await monitor.check_once()
    assert monitor.state.value == "offline"

    app = _app()
    app.state.network_monitor = monitor
    app.state.network_configured = True

    body = await _post(app, "/api/ops/network/recheck")

    assert body["recheck"]["performed"] is True
    assert body["recheck"]["changed"] is False
    assert body["recheck"]["consecutive_agreeing"] == 1
    assert body["recheck"]["confirmations_required"] == 3
    assert body["state"] == "offline", "a recheck must not publish a recovery the ladder has not seen twice"
    assert any("working as designed, not stuck" in note for note in body["notes"])


@pytest.mark.asyncio
async def test_a_recheck_with_no_probe_is_reported_as_not_run() -> None:
    """Never a quiet success, and never a 500."""

    app = _app()
    app.state.network_configured = False

    body = await _post(app, "/api/ops/network/recheck")

    assert body["recheck"]["performed"] is False
    assert body["recheck"]["reason"] == ops_runtime.REASON_MONITOR_DISABLED
    assert "network.enabled" in body["recheck"]["detail"], "the operator is told what would fix it"
    assert body["reported"] is False
    assert any("no probe ran" in note for note in body["notes"])


@pytest.mark.asyncio
async def test_a_recheck_that_explodes_is_reported_as_not_run() -> None:
    class Exploding:
        running = True

        @property
        def state(self) -> Any:
            return type("S", (), {"value": "unknown", "detail": ""})()

        def last_observation(self) -> Any:
            return None

        def observation_age_seconds(self) -> float | None:
            return None

        def wait_decision(self) -> Any:
            return type("D", (), {"admit_network_work": True, "next_poll_seconds": 15.0})()

        @property
        def config(self) -> Any:
            return type("C", (), {"poll_interval_seconds": 15.0, "backoff_max_seconds": 300.0})()

        async def recheck(self) -> Any:
            raise RuntimeError("the socket layer is gone")

    app = _app()
    app.state.network_monitor = Exploding()
    app.state.network_configured = True

    body = await _post(app, "/api/ops/network/recheck")

    assert body["recheck"]["performed"] is False
    assert body["recheck"]["reason"] == "the connectivity probe could not be run"
    assert "RuntimeError" in body["recheck"]["detail"]
    assert body["state"] == "unknown", "the existing reading survives a failed recheck"


@pytest.mark.asyncio
async def test_a_monitor_with_no_measurement_api_is_refused_rather_than_called() -> None:
    class Bare:
        running = False

        @property
        def state(self) -> Any:
            return type("S", (), {"value": "offline", "detail": ""})()

        def last_observation(self) -> Any:
            return None

        def wait_decision(self) -> Any:
            return type("D", (), {"admit_network_work": False, "next_poll_seconds": 1.0})()

    app = _app()
    app.state.network_monitor = Bare()
    app.state.network_configured = True

    body = await _post(app, "/api/ops/network/recheck")
    assert body["recheck"]["performed"] is False
    assert body["recheck"]["reason"] == "the installed monitor exposes no measurement API"


# ══════════════════════════════════════════════════════════════════════════
# 6. WIRING — a capability nothing reaches ships green and does nothing
# ══════════════════════════════════════════════════════════════════════════


def test_wiring_the_recheck_route_reaches_the_monitor_api() -> None:
    source = _read("app/gateway/routers/ops.py")
    assert 'getattr(monitor, "recheck", None)' in source, "the route must call the monitor's own measurement entry point"
    assert "await asyncio.wait_for(measure(), timeout=_RECHECK_TIMEOUT_SECONDS)" in source, "an operator-triggered probe must be bounded"


def test_wiring_the_retry_schedule_is_read_from_the_monitor() -> None:
    source = _read("app/gateway/ops_runtime.py")
    assert "monitor.wait_decision()" in source, "the schedule must come from the process that owns it"
    assert "monitor.observation_age_seconds()" in source, "the age must come from the clock that stamped the reading"


def test_wiring_the_monitor_serializes_its_measurements() -> None:
    source = _read("packages/harness/alpha/runtime/network/monitor.py")
    assert "self._probe_lock = asyncio.Lock()" in source
    assert "async with self._probe_lock:" in source
    assert "async def recheck(self)" in source


def test_wiring_the_lifespan_records_whether_connectivity_is_configured() -> None:
    """Without this the route cannot tell "switched off" from "never started"."""

    source = _read("app/gateway/deps.py")
    assert "app.state.network_configured = bool(startup_network is not None and startup_network.enabled)" in source
    # …and it is recorded *before* the branch, so a startup failure still leaves
    # the true configuration answer behind.
    assert source.index("app.state.network_configured") < source.index("if startup_network is not None and startup_network.enabled:")


def test_wiring_no_absolute_probe_timestamp_is_published() -> None:
    """`observed_at` is stamped monotonic; publishing it as a wall-clock time
    would be a confident wrong number, and clamping the subtraction would render
    that wrongness as "measured just now"."""

    router = _read("app/gateway/routers/ops.py")
    assert "observed_at: float" not in router
    assert "observed_at=" not in router
    projection = _read("app/gateway/ops_runtime.py")
    assert '"observed_at": ' not in projection
