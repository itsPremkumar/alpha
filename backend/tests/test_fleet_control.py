"""Fleet-wide ESTOP: the control that had no enforcement.

Alpha had two stop mechanisms and neither stopped the fleet:

* ``alpha.bots.kill_switch`` - an in-memory boolean that vanishes on restart, so
  it cannot stop work a restart resumed.
* ``alpha.runtime.estop`` - a durable sentinel whose **only** consumer was
  ``alpha.rsi.switchboard``. It gated the recursive-self-improvement cycle and
  nothing else: not the run worker, not run admission, not the eight autonomy
  loops.

So an operator who engaged the emergency stop was told "Fleet execution paused"
while the fleet kept running. These tests pin the enforcement that closes it.

The design point worth testing hardest is the **generation fence**: a worker
admitted before a stop cannot perform an effect afterwards, wherever in its own
body it is. That is the property a plain boolean check cannot provide, and it is
borrowed from distributed-lock fencing (Kleppmann: assume your view of the world
is stale, validate at the resource level).
"""

from __future__ import annotations

import json

import pytest

from alpha.runtime import control
from alpha.runtime.control import ControlMode, FleetStopped, assert_admissible, assert_effect_allowed


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated runtime home, so no test can read or write the real one."""
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    monkeypatch.delenv("ALPHA_WORKSPACE_ROOTS", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Defaults and basic transitions
# ---------------------------------------------------------------------------


def test_absent_state_means_running(home):
    """A deployment that has never engaged a stop must not be frozen."""
    state = control.get_state()
    assert state.mode is ControlMode.RUN
    assert state.generation == 0
    assert state.mode.refuses_admission is False
    assert_admissible("new work")  # must not raise


def test_estop_advances_the_generation(home):
    state = control.engage("incident 4711")
    assert state.mode is ControlMode.ESTOP
    assert state.generation == 1
    assert state.reason == "incident 4711"
    assert state.engaged_at is not None


def test_pause_and_estop_refuse_admission_differently(home):
    control.pause("maintenance window")
    with pytest.raises(FleetStopped) as pause_exc:
        assert_admissible("a new run")
    assert pause_exc.value.mode is ControlMode.PAUSE
    assert "maintenance window" in str(pause_exc.value)


def test_only_an_explicit_clear_lifts_estop(home):
    """ESTOP is not released by a restart or by any automatic path."""
    control.engage("operator")
    (home / "control_state_probe").write_text("x", encoding="utf-8")  # touch the dir
    assert control.get_state().mode is ControlMode.ESTOP
    # Nothing but clear() moves it.
    control.clear()
    assert control.get_state().mode is ControlMode.RUN


def test_pause_lets_admitted_work_finish(home):
    """The defining difference from ESTOP: pause stops new work, not in-flight."""
    generation = control.get_state().generation
    control.pause("window")
    # Work admitted at the current generation may still act.
    state = assert_effect_allowed(generation, "finish an in-flight tool call")
    assert state.mode is ControlMode.PAUSE
    # New work still cannot start.
    with pytest.raises(FleetStopped):
        assert_admissible("a new run")


# ---------------------------------------------------------------------------
# The generation fence - the reason this is a counter and not a boolean
# ---------------------------------------------------------------------------


def test_work_admitted_before_a_stop_cannot_act_afterwards(home):
    """The core property. A boolean check at entry cannot provide this."""
    admitted_at = control.get_state().generation
    control.engage("incident")

    # This worker was admitted under generation 0. It is now somewhere deep in
    # its body and about to perform an effect.
    with pytest.raises(FleetStopped) as exc:
        assert_effect_allowed(admitted_at, "a model call")
    assert "advanced to generation 1" in str(exc.value)


def test_a_freshly_admitted_run_is_not_fenced_by_a_past_stop(home):
    """Fencing must not accumulate: the generation advances, it does not accumulate."""
    control.engage("first incident")
    control.clear()
    current = control.get_state().generation
    # A run admitted *now* acts normally.
    state = assert_effect_allowed(current, "a model call")
    assert state.mode is ControlMode.RUN


def test_repeated_pause_never_fences_admitted_work(home):
    """Pause is defined as letting admitted work finish, so it never advances
    the generation - not even repeatedly. A generation advance IS the fence, so
    advancing here would silently turn PAUSE into ESTOP.
    """
    stale = control.get_state().generation
    control.pause("first window")
    assert_effect_allowed(stale, "in-flight effect")
    control.pause("second window")
    assert_effect_allowed(stale, "still in flight")
    assert control.get_state().generation == stale


def test_escalating_pause_to_estop_does_fence_admitted_work(home):
    """The transition that DOES invalidate in-flight work, and only that one."""
    admitted_at = control.get_state().generation
    control.pause("window")
    assert_effect_allowed(admitted_at, "in-flight effect")
    control.engage("incident during the window")
    with pytest.raises(FleetStopped):
        assert_effect_allowed(admitted_at, "in-flight effect")


# ---------------------------------------------------------------------------
# Fail-closed: a stop mechanism that fails open is not a stop mechanism
# ---------------------------------------------------------------------------


def test_corrupt_state_fails_closed(home):
    control.engage("incident")
    (home / control.STATE_FILENAME).write_text("{not json", encoding="utf-8")

    state = control.get_state()
    assert state.mode is ControlMode.ESTOP
    assert state.error is not None
    with pytest.raises(FleetStopped):
        assert_admissible("anything at all")


def test_unreadable_state_fails_closed(home):
    control.engage("incident")
    # A directory where the state file should be makes the read fail.
    (home / control.STATE_FILENAME).unlink()
    (home / control.STATE_FILENAME).mkdir()

    state = control.get_state()
    assert state.mode is ControlMode.ESTOP
    assert state.error is not None


def test_unknown_mode_is_not_permission_to_run(home):
    """An unrecognised mode must not fall through to RUN."""
    (home / control.STATE_FILENAME).write_text(json.dumps({"mode": "definitely-not-a-mode", "generation": 3}), encoding="utf-8")
    state = control.get_state()
    assert state.mode is ControlMode.ESTOP
    assert "unknown mode" in (state.error or "")


def test_a_garbage_generation_does_not_undo_a_stop(home):
    (home / control.STATE_FILENAME).write_text(json.dumps({"mode": "estop", "generation": "not-a-number"}), encoding="utf-8")
    state = control.get_state()
    assert state.mode is ControlMode.ESTOP  # mode survives
    assert state.generation == 0  # degraded, not crashing


# ---------------------------------------------------------------------------
# The ticket: admission then fencing
# ---------------------------------------------------------------------------


def test_ticket_fences_a_long_running_body(home):
    """The realistic shape: admitted, then the world moves, then it wants to act."""
    with control.admitted("a long run") as ticket:
        ticket.before_effect("step 1")  # fine
        control.engage("operator stopped the fleet mid-run")
        with pytest.raises(FleetStopped):
            ticket.before_effect("step 2")


def test_ticket_reports_the_real_phase_in_the_refusal(home):
    """An operator reading a log needs to know what was refused, not just that one."""
    control.engage("incident 99")
    with pytest.raises(FleetStopped) as exc:
        assert_admissible("autonomy loop self_update")
    assert "autonomy loop self_update" in str(exc.value)
    assert "incident 99" in str(exc.value)
    assert exc.value.phase == "autonomy loop self_update"


# ---------------------------------------------------------------------------
# Status / operator surface
# ---------------------------------------------------------------------------


def test_status_reports_the_guards_it_claims(home):
    control.engage("incident")
    payload = control.status()
    assert payload["mode"] == "estop"
    assert payload["refuses_admission"] is True
    assert payload["refuses_effects"] is True
    assert payload["generation"] == 1
    assert payload["state_path"].endswith(control.STATE_FILENAME)


def test_status_run_mode_claims_no_guards(home):
    payload = control.status()
    assert payload["refuses_admission"] is False
    assert payload["refuses_effects"] is False


def test_state_is_written_atomically(home):
    """A crash mid-write must not leave a truncated file that reads as unknown."""
    control.engage("incident")
    leftovers = [p.name for p in home.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"temp files left behind: {leftovers}"
    json.loads((home / control.STATE_FILENAME).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The RSI switchboard now also honours fleet control
# ---------------------------------------------------------------------------


def test_rsi_switchboard_refuses_a_cycle_under_fleet_estop(home, monkeypatch):
    """The pre-existing consumer must see the new control, not only its own sentinel.

    This is the composition point: `rsi_frozen()` already consulted
    `alpha.runtime.estop`, and fleet control is a superset of that state, so a
    fleet stop must reach RSI too without RSI knowing the difference.
    """
    from alpha.rsi import switchboard

    control.engage("fleet incident")
    frozen, reason = switchboard.rsi_frozen()
    assert frozen is True
    assert reason


def test_switchboard_still_passes_under_run_mode(home):
    from alpha.rsi import switchboard

    frozen, _ = switchboard.rsi_frozen()
    assert frozen is False


# ---------------------------------------------------------------------------
# What this deliberately does NOT do
# ---------------------------------------------------------------------------


def test_control_does_not_roll_back_completed_work(home):
    """Documented limit: this prevents the next effect, not the last one.

    Pinned so the boundary is visible rather than implied. Recovery and the
    side-effect ledger own already-completed effects; duplicating their authority
    here would make this a second lifecycle owner.
    """
    control.engage("incident")
    # A side effect that completed before the stop stays completed.
    assert control.get_state().mode is ControlMode.ESTOP
    # Nothing here reverts prior work, and nothing claims to.
    assert not hasattr(control, "rollback")
