"""Prove the fleet control guards are actually *reached*, not merely present.

`tests/test_fleet_control.py` pins the control layer's semantics. This file pins
the enforcement: that a stopped fleet actually stops the autonomy loops and
refuses run admission. Those are the two findings that motivated the work - the
supervisor had no stop mechanism at all, and `run_agent` never consulted one.

If someone removes a guard, these fail. That is the point.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.runtime import control
from app.gateway.autonomy.supervisor import _fleet_admits_tick


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------------------
# The autonomy loops
# ---------------------------------------------------------------------------


def test_engaged_estop_stops_an_autonomy_tick():
    """The finding: `autonomy.loops.*` decided whether a loop was registered,
    not whether it was allowed to run. An engaged stop previously had no effect
    on any of the eight loops."""
    control.engage("incident: runaway self-update loop")
    assert _fleet_admits_tick("self_update") is False


def test_a_loop_runs_normally_under_run_mode():
    assert _fleet_admits_tick("self_update") is True


def test_pause_also_stops_new_ticks():
    """Pause means "no new work", and a loop tick is new work."""
    control.pause("maintenance")
    assert _fleet_admits_tick("self_update") is False


def test_the_preexisting_estop_sentinel_still_stops_ticks():
    """The older `EmergencyStopManager` sentinel is honoured too.

    Both mechanisms are consulted rather than one subsuming the other, so an
    operator who engaged either one gets the same fleet-wide effect. The sentinel
    now resolves through `runtime_home()`, so this is the same directory the
    fleet-control state lives in - previously it followed `Path.cwd()`, which
    meant a process whose cwd was not the project root wrote and read different
    files.
    """
    from alpha.runtime.estop import get_estop_manager

    get_estop_manager().engage("legacy operator stop")
    assert _fleet_admits_tick("self_update") is False


def test_corrupt_control_state_stops_ticks_fail_closed(isolated_home):
    """A stop mechanism that fails open is not a stop mechanism."""
    (isolated_home / control.STATE_FILENAME).write_text("{corrupt", encoding="utf-8")
    assert _fleet_admits_tick("self_update") is False


def test_the_guard_never_raises():
    """An exception here would kill the supervisor's loop scheduler."""
    assert _fleet_admits_tick("self_update") is True


# ---------------------------------------------------------------------------
# Run admission
# ---------------------------------------------------------------------------


def test_a_stopped_fleet_refuses_run_admission():
    """`run_agent` is the single entry every lead run passes through."""
    from alpha.runtime.runs.worker import _admit_run_to_fleet

    class _Record:
        run_id = "run-1"
        status = "pending"
        error = None

    control.engage("incident")
    record = _Record()
    assert _admit_run_to_fleet(record) is None
    # And the refusal is recorded, so a client polling for completion sees a
    # reason rather than a run that will never start.
    assert record.status == "error"
    assert "ESTOP" in record.error


def test_an_admitted_run_gets_a_fencing_ticket():
    from alpha.runtime.runs.worker import _admit_run_to_fleet

    class _Record:
        run_id = "run-2"
        status = "pending"
        error = None

    ticket = _admit_run_to_fleet(_Record())
    assert ticket is not None
    assert isinstance(ticket.generation, int)


def test_a_ticket_from_before_a_stop_cannot_act_after():
    """The realistic incident: a run is mid-flight when the operator stops it."""
    from alpha.runtime.runs.worker import _admit_run_to_fleet

    class _Record:
        run_id = "run-3"
        status = "running"
        error = None

    ticket = _admit_run_to_fleet(_Record())
    ticket.before_effect("step 1")
    control.engage("operator stopped the fleet mid-run")
    with pytest.raises(control.FleetStopped):
        ticket.before_effect("step 2")


# ---------------------------------------------------------------------------
# Real loop dispatch, not just the predicate
# ---------------------------------------------------------------------------


def test_a_real_tick_is_skipped_while_stopped(monkeypatch, tmp_path):
    """End-to-end through `_tick_once`, so the guard's placement is proven."""
    from alpha.config.autonomy_config import AutonomyLoopConfig
    from app.gateway.autonomy.supervisor import AutonomySupervisor, _LoopState

    ran: list[int] = []
    supervisor = AutonomySupervisor()
    loop_id = "probe_loop"
    state = _LoopState()
    cfg = AutonomyLoopConfig(interval_seconds=1)

    async def _tick():
        ran.append(1)
        return "did work"

    monkeypatch.setitem(supervisor._semaphores, loop_id, asyncio.Semaphore(1))

    async def _drive():
        control.engage("incident")
        await supervisor._tick_once(loop_id, cfg, _tick, state)

    asyncio.run(_drive())
    assert ran == [], "the loop performed an effect while the fleet was stopped"
    assert state.runs == 0

    async def _drive_after_clear():
        control.clear()
        await supervisor._tick_once(loop_id, cfg, _tick, state)

    asyncio.run(_drive_after_clear())
    assert ran == [1], "the loop did not resume after the stop was cleared"
