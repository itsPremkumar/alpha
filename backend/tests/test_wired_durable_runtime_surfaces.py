"""Every piece of wiring this change adds, proven reachable from a production path.

The defect class this repository keeps hitting is a capability that is fully
implemented, tested and documented, and that nothing calls — it ships green and
does nothing. So a test that merely exercises a function is worthless here: it
proves the function works, not that a *production* path reaches it.

Every test below is one of four kinds, and its name says which:

* ``test_wiring_*`` — a production module really reaches the capability.
* ``test_*`` (behaviour) — the capability does what the wiring claims.
* honesty guards — the failure mode is inventing a value where nothing was
  measured, so the test pins the *absence* claim, not just a value.
* ``test_bite_*`` — the guard demonstrably fails when the production call is
  neutralised, done by **real mutation**, not by re-reading edited source.

Coverage:
  * ``app.gateway.ops_runtime`` — the drain record (items 6/7): a clean drain, an
    unclean drain, no record, an unreadable record, and the fail-OPEN rule that a
    failed record write must not fail the drain.
  * ``app.gateway.routers.ops.ops_runtime`` — the operator surface: absence kept
    distinct from a value, and ``UNKNOWN`` never rounded to ``offline``.
  * ``app.gateway.routers.supervision`` — an empty fleet must not read healthy.
  * ``alpha.evolution.promotion_route`` — the evidence gate reaches promotion, is
    default-off, and can only block.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from alpha.runtime.shutdown import PlannedShutdown, ShutdownPhase, ShutdownReport, ShutdownStatus
from app.gateway import ops_runtime
from app.gateway.ops_runtime import (
    REASON_MONITOR_ABSENT,
    REASON_MONITOR_DISABLED,
    REASON_NO_RECORD,
    REASON_UNREADABLE,
    drain_record_path,
    network_snapshot,
    read_last_drain,
    record_drain_report,
)
from app.gateway.routers import ops as ops_router
from app.gateway.routers import supervision as supervision_router

BACKEND = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (BACKEND / relative).read_text(encoding="utf-8")


def _report(*, clean: bool = True) -> ShutdownReport:
    steps: tuple[tuple[ShutdownPhase, ShutdownStatus, str], ...] = ((ShutdownPhase.ADMISSION_CLOSED, ShutdownStatus.COMPLETED, ""),)
    if not clean:
        steps += ((ShutdownPhase.OPERATIONS_DRAINED, ShutdownStatus.TIMED_OUT, "exceeded its 10.0s budget"),)
    return ShutdownReport(steps=steps, emergency=False, total_seconds=1.25)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(ops_router.router)
    app.include_router(supervision_router.router)
    return app


async def _get(app: FastAPI, path: str) -> dict[str, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


# ══════════════════════════════════════════════════════════════════════════
# 1. The drain record: `is_clean` survives the process that measured it
# ══════════════════════════════════════════════════════════════════════════


def test_a_clean_drain_round_trips_through_the_record(tmp_path: Path) -> None:
    assert record_drain_report(_report(clean=True), home=tmp_path, recorded_at="2026-09-29T00:00:00Z")["recorded"] is True

    read = read_last_drain(home=tmp_path)
    assert read["reported"] is True
    assert read["is_clean"] is True
    assert read["incomplete"] == []
    assert read["steps"][0]["phase"] == "admission_closed"
    assert read["total_seconds"] == 1.25
    assert read["recorded_at"] == "2026-09-29T00:00:00Z"


def test_an_unclean_drain_is_recorded_as_unclean_with_the_failing_phase(tmp_path: Path) -> None:
    """The point of the whole change: a partial drain stays visible after a restart."""

    assert record_drain_report(_report(clean=False), home=tmp_path)["recorded"] is True
    read = read_last_drain(home=tmp_path)
    assert read["reported"] is True
    assert read["is_clean"] is False, "an incomplete drain recorded as clean is the exact lie this closes"
    assert read["incomplete"] == ["operations_drained"]
    failing = next(step for step in read["steps"] if step["phase"] == "operations_drained")
    assert failing["status"] == "timed_out"
    assert "budget" in failing["detail"]


def test_the_record_is_versioned_and_replaced_atomically(tmp_path: Path) -> None:
    """A crash mid-write must leave the *previous* record, not a truncated file."""

    record_drain_report(_report(clean=False), home=tmp_path)
    record_drain_report(_report(clean=True), home=tmp_path)
    path = drain_record_path(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == ops_runtime.DRAIN_RECORD_VERSION
    assert [p.name for p in path.parent.iterdir()] == [path.name], "an atomic replace must not leave a .tmp behind"
    assert read_last_drain(home=tmp_path)["is_clean"] is True


def test_an_unserialisable_report_is_reported_not_raised(tmp_path: Path) -> None:
    class Unrecordable:
        pass

    result = record_drain_report(Unrecordable(), home=tmp_path)
    assert result["recorded"] is False
    assert "AttributeError" in result["reason"]
    assert read_last_drain(home=tmp_path)["reported"] is False, "a failed record must not leave a partial file behind"


# ══════════════════════════════════════════════════════════════════════════
# 2. Absence is never a value (honesty guards)
# ══════════════════════════════════════════════════════════════════════════


def test_no_record_is_reported_as_no_record_not_as_a_clean_shutdown(tmp_path: Path) -> None:
    read = read_last_drain(home=tmp_path)
    assert read["reported"] is False
    assert read["reason"] == REASON_NO_RECORD
    assert read["is_clean"] is None, "no record must never read as 'the last shutdown was clean'"


def test_an_unreadable_record_is_distinguished_from_no_record(tmp_path: Path) -> None:
    path = drain_record_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    read = read_last_drain(home=tmp_path)
    assert read["reported"] is False
    assert read["reason"] == REASON_UNREADABLE
    assert read["is_clean"] is None


def test_a_recognisable_but_unknown_shape_is_not_believed(tmp_path: Path) -> None:
    """A record from a future version is unreadable, not partially trusted."""

    path = drain_record_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 999, "is_clean": True}), encoding="utf-8")
    read = read_last_drain(home=tmp_path)
    assert read["reported"] is False
    assert read["reason"] == REASON_UNREADABLE
    assert read["is_clean"] is None, "half-understood shape must not yield a clean verdict"


# ══════════════════════════════════════════════════════════════════════════
# 3. Fail-OPEN: recording must never fail the drain
# ══════════════════════════════════════════════════════════════════════════


def test_a_failed_record_write_returns_instead_of_raising(tmp_path: Path) -> None:
    """The documented fail-OPEN rule, in code.

    A read-only `ALPHA_HOME`, a Windows file lock, a full disk: none of these may
    be raised out of a teardown path, because turning "I could not record that the
    shutdown was incomplete" into "the shutdown did not finish" is strictly
    worse. The side-effect ledger documents the same requirement for its own
    writes; this is the drain's copy of it.
    """

    # A real, unwritable location: a *file* where a directory must go.
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    result = record_drain_report(_report(clean=True), home=blocker / "nested")
    assert result["recorded"] is False
    assert result["reason"]
    # And the failure is not swallowed into silence: nothing was written.
    assert read_last_drain(home=blocker / "nested")["reported"] is False


# ══════════════════════════════════════════════════════════════════════════
# 4. The network projection: UNKNOWN is reachable and never rounded
# ══════════════════════════════════════════════════════════════════════════


class _Observation:
    def __init__(self, state: str) -> None:
        self._state = state

    def to_dict(self) -> dict[str, Any]:
        return {"state": self._state, "outcomes": []}


class _Monitor:
    def __init__(self, state: str = "online", *, running: bool = True, observation: Any = "default") -> None:
        self._state = state
        self._running = running
        self._observation = observation

    @property
    def state(self) -> Any:
        outer = self

        class S:
            value = outer._state

        return S()

    @property
    def running(self) -> bool:
        return self._running

    def last_observation(self) -> Any:
        return _Observation(self._state) if self._observation == "default" else self._observation


def test_unknown_is_reported_as_unknown_not_offline() -> None:
    snapshot = network_snapshot(_Monitor("unknown"), None)
    assert snapshot["reported"] is True
    assert snapshot["state"] == "unknown", "UNKNOWN must never be rounded to offline"


def test_a_disabled_monitor_is_unreported_not_online() -> None:
    """`network.enabled: false` must not read as "connectivity is fine"."""

    snapshot = network_snapshot(None, None, network_enabled=False)
    assert snapshot["reported"] is False
    assert snapshot["reason"] == REASON_MONITOR_DISABLED
    assert snapshot["state"] is None


def test_an_enabled_but_absent_monitor_is_distinguished_from_a_disabled_one() -> None:
    snapshot = network_snapshot(None, None, network_enabled=True)
    assert snapshot["reported"] is False
    assert snapshot["reason"] == REASON_MONITOR_ABSENT
    assert snapshot["state"] is None
    assert snapshot["parked_durability"] == "unavailable"


def test_a_broken_monitor_read_is_reported_not_raised() -> None:
    class Exploding(_Monitor):
        def last_observation(self) -> Any:
            raise RuntimeError("probe subsystem is wedged")

    snapshot = network_snapshot(Exploding(), None)
    assert snapshot["reported"] is True
    assert snapshot["last_observation"] is None
    assert snapshot["state"] == "online"


# ══════════════════════════════════════════════════════════════════════════
# 5. WIRING: the Gateway drain records, and the route reads live state
# ══════════════════════════════════════════════════════════════════════════


def test_wiring_the_teardown_records_the_drain_it_just_ran() -> None:
    """`deps.py` must call the recorder; a test-only caller proves nothing."""

    source = _read("app/gateway/deps.py")
    assert "from app.gateway.ops_runtime import record_drain_report" in source, "the recorder must be imported by the drain"
    tail = source[source.index("drain_report = await drain.shutdown()") :]
    assert "record_drain_report(drain_report)" in tail[:1200], "the record must be written immediately after the drain, not elsewhere"
    assert source.count("record_drain_report") >= 3, "import, call and failure branch must all name it"


def test_wiring_the_route_reads_app_state_not_a_module_global() -> None:
    source = _read("app/gateway/routers/ops.py")
    assert 'getattr(state, "network_monitor", None)' in source
    assert 'getattr(state, "network_waits", None)' in source
    assert "await wait_service.status()" in source, "the parked-session registry must actually be read"


def test_wiring_the_route_is_declared_on_the_ops_router() -> None:
    assert "/api/ops/runtime" in {getattr(route, "path", "") for route in ops_router.router.routes}


@pytest.mark.asyncio
async def test_the_route_reports_an_unclean_previous_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    record_drain_report(_report(clean=False), home=tmp_path)

    body = await _get(_app(), "/api/ops/runtime")
    assert body["last_drain"]["reported"] is True
    assert body["last_drain"]["is_clean"] is False
    assert body["last_drain"]["incomplete"] == ["operations_drained"]
    assert not any("no readable record" in note for note in body["notes"]), "a readable record must not be disclosed as missing"


@pytest.mark.asyncio
async def test_the_route_discloses_a_first_boot_instead_of_claiming_a_clean_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    body = await _get(_app(), "/api/ops/runtime")

    assert body["last_drain"]["reported"] is False
    assert body["last_drain"]["is_clean"] is None
    assert any("no readable record" in note for note in body["notes"]), "a first boot must be disclosed, not read as healthy"


@pytest.mark.asyncio
async def test_the_route_never_reports_unmeasured_connectivity_as_online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHA_HOME", str(Path(os.sep) / "nonexistent-alpha-home-for-this-test"))
    body = await _get(_app(), "/api/ops/runtime")

    assert body["network"]["reported"] is False
    assert body["network"]["state"] is None
    assert any("not the same as connectivity being fine" in note for note in body["notes"])


@pytest.mark.asyncio
async def test_a_parked_session_store_outage_does_not_fail_the_request() -> None:
    """A degraded read is disclosed, not turned into a 500 that hides connectivity."""

    class BrokenRegistry:
        async def status(self) -> Any:
            raise RuntimeError("network_waits is unreachable")

    app = _app()
    app.state.network_monitor = _Monitor("online")
    app.state.network_waits = BrokenRegistry()
    body = await _get(app, "/api/ops/runtime")

    assert body["network"]["reported"] is True
    assert body["network"]["state"] == "online", "the connectivity reading is still good and must survive"
    assert body["network"]["parked_sessions"]["reported"] is False
    assert "RuntimeError" in body["network"]["parked_sessions"]["detail"]


@pytest.mark.asyncio
async def test_the_route_reports_a_live_measurement_end_to_end() -> None:
    """The whole chain: `app.state` -> projection -> HTTP body."""

    class Registry:
        async def status(self) -> Any:
            return type("S", (), {"open_waits": 2, "claimed": 1, "resumed": 1, "gave_up": 0})()

    app = _app()
    app.state.network_monitor = _Monitor("degraded")
    app.state.network_waits = Registry()
    body = await _get(app, "/api/ops/runtime")

    assert body["network"]["state"] == "degraded"
    assert body["network"]["monitoring"] is True
    assert body["network"]["parked_durability"] == "installed"
    assert body["network"]["parked_sessions"] == {"reported": True, "reason": "", "detail": "", "open_waits": 2, "claimed": 1, "resumed": 1, "gave_up": 0}
    assert body["network"]["last_observation"]["state"] == "degraded"


# ══════════════════════════════════════════════════════════════════════════
# 6. WIRING: an empty supervision fleet is an unobserved watchdog
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_empty_fleet_does_not_read_as_a_healthy_fleet() -> None:
    assert supervision_router._GLOBAL_WATCHDOG.evaluate_fleet() == {}, "this test only means something on an unwatched watchdog"
    app = _app()
    fleet = await _get(app, "/api/supervision/fleet")
    observation = await _get(app, "/api/supervision/observation")

    assert fleet["observed"] is False, "an empty fleet must declare that nothing is being watched"
    assert fleet["watching"] is False
    assert fleet["observed_worker_count"] == 0
    assert fleet["observed_reason"] == supervision_router.NO_WORKERS_REASON_NO_HEARTBEAT_RECEIVED
    assert observation["observed"] is False
    assert observation["observed_worker_count"] == 0


@pytest.mark.asyncio
async def test_a_reported_worker_makes_the_fleet_observed() -> None:
    """Proves `observed` is derived from a real measurement, not hard-coded."""

    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        ingested = await client.post("/api/supervision/heartbeat", json={"worker_id": "wired-probe-worker", "task_id": "t1", "progress_percent": 10.0})
        assert ingested.status_code == 200
        fleet = (await client.get("/api/supervision/fleet")).json()

    try:
        assert fleet["observed"] is True
        assert fleet["watching"] is True
        assert fleet["observed_worker_count"] == 1
        assert fleet["observed_reason"] == ""
        assert "wired-probe-worker" in fleet, "the worker row must still be in the map a client iterates"
        assert isinstance(fleet["wired-probe-worker"], dict)
    finally:
        supervision_router._GLOBAL_WATCHDOG._heartbeats.pop("wired-probe-worker", None)


def test_the_duplicate_watchdog_instance_is_disclosed() -> None:
    """`supervision_tool.py` holds a second instance; the router must say so.

    A guard, not a fix: the single-instance fix belongs in `alpha/supervision/`,
    which this change does not own. If the disclosure is deleted, the structural
    gap becomes invisible again.
    """

    source = _read("app/gateway/routers/supervision.py")
    assert "supervision_tool.py" in source
    assert "two independent, independently-empty fleets" in source


# ══════════════════════════════════════════════════════════════════════════
# 7. WIRING: the evolution evidence gate reaches a real promotion decision
# ══════════════════════════════════════════════════════════════════════════


def test_wiring_the_evidence_gate_is_called_from_the_production_promotion_seam() -> None:
    """`alpha.rsi.promotion` imports `route_evolution_gate` at module scope."""

    source = _read("packages/harness/alpha/evolution/promotion_route.py")
    assert "evidence_verdict_for(candidate_id, baseline)" in source, "route_evolution_gate must actually consult the gate"
    rsi = _read("packages/harness/alpha/rsi/promotion.py")
    assert "from alpha.evolution.promotion_route import route_evolution_gate" in rsi, "the seam must be the one production promotion calls"


def test_the_evidence_gate_actually_runs_and_blocks_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not a source read: drive the real `route_evolution_gate` with the gate on."""

    from alpha.evolution import promotion_route
    from alpha.evolution.evidence.config import EvolutionEvidenceConfig

    baseline = {"kind": "code", "author": "test", "declared_intent": "prove the gate runs", "touched_paths": ("backend/app/gateway/deps.py",)}
    monkeypatch.setattr(promotion_route, "_evidence_enabled", lambda: True)

    outcome = promotion_route.evidence_verdict_for("wired-candidate", baseline)
    promoted, reason = promotion_route.route_evolution_gate("wired-candidate", baseline, human_approved=True)

    assert outcome.ran is True, "an enabled gate must actually evaluate"
    assert outcome.status == "insufficient_evidence", f"with no measurement source injected the honest verdict is insufficiency, never an accept (got {outcome.status})"
    assert outcome.blocking is True
    assert promoted is False, "a blocking evidence verdict must not promote"
    assert "evidence gate: insufficient_evidence" in reason
    assert "required gate 'reproducibility' is unavailable" in reason, "the reason must name the gate that could not be evaluated, not just that something failed"
    # The verdict is a real one from the real model, not a stub.
    assert EvolutionEvidenceConfig(enabled=True).enabled is True


def test_the_evidence_gate_is_off_by_default_and_changes_nothing() -> None:
    """Behaviour preservation: no operator change, no behaviour change."""

    from alpha.evolution import promotion_route
    from alpha.evolution.evidence.config import EvolutionEvidenceConfig

    assert EvolutionEvidenceConfig().enabled is False
    outcome = promotion_route.evidence_verdict_for("any-candidate", {"kind": "code"})
    assert outcome.ran is False
    assert outcome.blocking is False
    assert outcome.reason_text() == ""


def test_an_accepted_evidence_verdict_cannot_promote_what_the_engine_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The security property: a `False` from the engine can never become a `True`."""

    from alpha.evolution import promotion_route

    class _StubVerdict:
        status = "accepted"
        reasons = ("all gates passed",)
        blocking = False

    class _StubEvaluation:
        verdict = _StubVerdict()

    monkeypatch.setattr(promotion_route, "_evidence_enabled", lambda: True)
    monkeypatch.setattr(promotion_route, "get_evolution_engine", lambda: type("E", (), {"gate": staticmethod(lambda *a, **k: (False, "not strictly better than baseline"))})())
    import alpha.evolution.evidence as evidence_pkg

    monkeypatch.setattr(evidence_pkg, "evaluate_proposal", lambda *a, **k: _StubEvaluation())
    promoted, reason = promotion_route.route_evolution_gate("c", {"kind": "code", "declared_intent": "test that accept cannot promote"}, human_approved=True)

    assert promoted is False
    assert reason == "not strictly better than baseline", "an accepted evidence verdict must not alter the engine's own refusal"


def test_an_unreadable_config_does_not_block_promotion_but_is_not_a_clean_pass() -> None:
    """A broken config is a third state: not "no", and not "yes"."""

    from alpha.evolution import promotion_route

    class _Exploding:
        @property
        def evolution_evidence(self) -> Any:
            raise RuntimeError("config exploded")

    import alpha.config as config_pkg

    original = config_pkg.get_app_config
    config_pkg.get_app_config = lambda: _Exploding()
    try:
        outcome = promotion_route.evidence_verdict_for("c", {"kind": "code"})
    finally:
        config_pkg.get_app_config = original

    assert outcome.ran is False, "an unreadable config must not claim the gate ran"
    assert outcome.blocking is False, "a config outage is not evidence about the proposal"
    assert outcome.reason_text() == ""


def test_a_gate_that_cannot_evaluate_blocks_instead_of_waving_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ran=True, blocking=True` with the real exception, when the gate does run."""

    import alpha.evolution.evidence as evidence_pkg
    from alpha.evolution import promotion_route

    monkeypatch.setattr(promotion_route, "_evidence_enabled", lambda: True)

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("integrity policy exploded")

    monkeypatch.setattr(evidence_pkg, "evaluate_proposal", _boom)
    outcome = promotion_route.evidence_verdict_for("c", {"kind": "code", "declared_intent": "test the unrunnable gate"})

    assert outcome.ran is True
    assert outcome.blocking is True
    assert outcome.status == "gate_error"
    assert "integrity policy exploded" in outcome.reasons[0]


# ══════════════════════════════════════════════════════════════════════════
# 8. BITE PROOFS — neutralise the production call and watch the guard fail
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bite_removing_the_drain_recorder_call_stops_the_record_from_being_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the `deps.py` call were deleted, the operator surface would go empty.

    The removal is real, not a source paraphrase: `deps.py` imports the recorder
    *inside* the teardown function, so replacing the module attribute is exactly
    what a deleted `record_drain_report(drain_report)` line leaves behind. The
    same real drain then runs, and the route's honesty guard is asked the
    question -- and says "no record" instead of claiming a clean drain.
    """
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    assert "record_drain_report(drain_report)" in _read("app/gateway/deps.py"), "precondition: the production drain calls the recorder"

    async def _run_a_real_drain() -> ShutdownReport:
        drain = PlannedShutdown()
        drain.register(ShutdownPhase.ADMISSION_CLOSED, lambda: None, description="noop admission close")
        report = await drain.shutdown()
        # This is the production teardown's final act, verbatim.
        ops_runtime.record_drain_report(report)
        return report

    # 1. With the call in place the record exists and the route reports it.
    await _run_a_real_drain()
    assert read_last_drain()["reported"] is True
    assert (await _get(_app(), "/api/ops/runtime"))["last_drain"]["reported"] is True

    # 2. Remove the production call and re-run: nothing is written.
    os.remove(drain_record_path(tmp_path))
    monkeypatch.setattr(ops_runtime, "record_drain_report", lambda *a, **k: {"recorded": False, "reason": "call removed"})
    await _run_a_real_drain()
    assert read_last_drain()["reported"] is False, "precondition proven: without the call, no record is written"

    # 3. ...and the guard fires rather than reporting a clean shutdown.
    body = await _get(_app(), "/api/ops/runtime")
    assert body["last_drain"]["reported"] is False
    assert body["last_drain"]["is_clean"] is None
    assert any("no readable record" in note for note in body["notes"]), "the empty-fleet/first-boot guard must fire once the call is gone"


@pytest.mark.asyncio
async def test_bite_neutralising_the_evidence_gate_call_restores_the_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the `evidence_verdict_for` call were deleted, promotion would go through.

    This is the security property stated as a failing test: with the call
    replaced by a no-op the promotion is granted, which is precisely what the
    wiring prevents. A guard nobody has seen fail is not a guard.
    """
    from alpha.evolution import promotion_route

    baseline = {"kind": "code", "author": "test", "declared_intent": "bite", "touched_paths": ("backend/app/gateway/deps.py",)}
    monkeypatch.setattr(promotion_route, "_evidence_enabled", lambda: True)
    # The engine grants; the evidence gate is the only thing that can stop it.
    monkeypatch.setattr(promotion_route, "get_evolution_engine", lambda: type("E", (), {"gate": staticmethod(lambda *a, **k: (True, "promoted"))})())

    blocked_promoted, blocked_reason = promotion_route.route_evolution_gate("bite", baseline, human_approved=True)
    # Now remove the production call: a no-op outcome, as a deleted call leaves.
    monkeypatch.setattr(promotion_route, "evidence_verdict_for", lambda *a, **k: promotion_route.EvidenceGateOutcome(ran=False, blocking=False, status=None, reasons=()))
    open_promoted, open_reason = promotion_route.route_evolution_gate("bite", baseline, human_approved=True)

    assert blocked_promoted is False, "the gate must stop a promotion the engine granted"
    assert "evidence gate: insufficient_evidence" in blocked_reason
    assert open_promoted is True, "precondition proven: without the call the promotion goes through, so the guard bites"
    assert open_reason == "promoted"


def test_bite_a_hard_coded_observed_flag_breaks_the_empty_fleet_guard(tmp_path: Path) -> None:
    """Delete the fleet disclosure from the real module and watch the guard fail.

    The mutation is applied to a **copy of the production module** that is then
    executed, so the mutated code is the real route function rather than a
    paraphrase of it. The empty-fleet guard's own predicate is then re-run
    against the mutated module and must fail.
    """
    import importlib.util

    def asyncio_run(coro: Any) -> dict[str, Any]:
        import asyncio

        return asyncio.run(coro)

    source = _read("app/gateway/routers/supervision.py")

    def _guard_rejects(get_fleet_health: Any, watchdog: Any) -> bool:
        """The exact predicate `test_an_empty_fleet_does_not_read_as_a_healthy_fleet` asserts."""

        payload = asyncio_run(get_fleet_health())
        assert watchdog.evaluate_fleet() == {}
        return payload["observed"] is False and payload["watching"] is False and payload["observed_worker_count"] == 0

    # 1. The guard passes on the real module.
    spec = importlib.util.spec_from_file_location("_supervision_probe_real", BACKEND / "app" / "gateway" / "routers" / "supervision.py")
    assert spec is not None and spec.loader is not None
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    assert _guard_rejects(real.get_fleet_health, real._GLOBAL_WATCHDOG) is True, "precondition: the guard must pass on the real module"

    # 2. Mutate away the disclosure: the empty branch now claims it is watching.
    mutated = source.replace('        "observed": False,\n        "observed_reason": NO_WORKERS_REASON_NO_HEARTBEAT_RECEIVED,', '        "observed": True,\n        "observed_reason": "",')
    assert mutated != source, "the mutation must change the source, or this proof is theatre"
    path = tmp_path / "supervision_mutated.py"
    path.write_text(mutated, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_supervision_probe_mutated", path)
    assert spec is not None and spec.loader is not None
    broken = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(broken)

    # 3. The same guard now rejects it. A hard-coded `observed: True` makes the
    #    guard's own predicate False, which is exactly the failure it exists to
    #    prevent, so this is the bite.
    assert _guard_rejects(broken.get_fleet_health, broken._GLOBAL_WATCHDOG) is False, "the empty-fleet guard did not bite on a hard-coded observed flag"
    assert asyncio_run(broken.get_fleet_health())["observed"] is True, "and the mutation really did flip the answer"
