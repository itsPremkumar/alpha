"""Tests for the RSI kill-switch/incident freeze switchboard and the additive `run_holdout_gate` seam (plan WP-A4, features #20 + A3-wiring)."""

from __future__ import annotations

import pytest

from alpha.bots.kill_switch import set_global_kill_switch
from alpha.rsi import switchboard
from alpha.rsi.engine import RSIEngine
from alpha.rsi.models import RSIStage
from alpha.rsi.switchboard import guard_cycle_start, rsi_frozen


@pytest.fixture()
def runtime_home(tmp_path, monkeypatch):
    """Isolate runtime_home() (STOP sentinel + kill-switch event log) and guarantee a clean switch state."""
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    set_global_kill_switch(False, reason="test setup")
    yield home.resolve()
    set_global_kill_switch(False, reason="test cleanup")


def test_kill_switch_refuses_cycle_with_real_reason(runtime_home):
    engine = RSIEngine()
    reason_text = "incident INC-1234: operator requested RSI freeze"
    set_global_kill_switch(True, reason=reason_text)

    frozen, reason = rsi_frozen()
    assert frozen is True
    assert reason_text in reason  # verbatim engaged reason

    with pytest.raises(RuntimeError) as excinfo:
        engine.run_rsi_cycle(bottleneck="Excessive token bloat")
    message = str(excinfo.value)
    assert reason_text in message  # refusal carries the exact engaged reason

    # Honesty: the refusal payload contains no fabricated evidence at all.
    assert "score" not in message.lower()
    assert "confidence" not in message.lower()
    assert excinfo.value.args == (message,)  # a single real reason, nothing smuggled in
    assert engine.stage == RSIStage.IDLE  # no cycle record fabricated on refusal
    assert engine.get_status()["stage"] == "idle"
    assert getattr(engine, "_last_summary", None) is None

    # Disengage -> the existing simulated-preview cycle runs again, unchanged.
    set_global_kill_switch(False)
    result = engine.run_rsi_cycle(bottleneck="Excessive token bloat")
    assert result.stage == RSIStage.PREVIEW
    assert result.evidence_kind == "simulated"
    assert result.promoted is False


def test_estop_engagement_refuses_cycle(runtime_home, monkeypatch):
    from alpha.runtime.estop import EmergencyStopManager

    manager = EmergencyStopManager(root_dir=runtime_home / "estop")
    monkeypatch.setattr(switchboard, "_estop_manager", lambda: manager)

    manager.engage(reason="estop INC-777: physical motion fault")
    frozen, reason = rsi_frozen()
    assert frozen is True
    assert "physical motion fault" in reason  # real sentinel reason surfaced

    engine = RSIEngine()
    with pytest.raises(RuntimeError) as excinfo:
        engine.run_rsi_cycle(bottleneck="slow tool router")
    assert "physical motion fault" in str(excinfo.value)
    assert engine.stage == RSIStage.IDLE

    manager.disengage()
    assert rsi_frozen() == (False, "")
    assert engine.run_rsi_cycle(bottleneck="slow tool router").stage == RSIStage.PREVIEW


def test_local_stop_file_refuses_cycle(runtime_home):
    stop = switchboard.stop_file_path()
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("operator incident freeze", encoding="utf-8")

    frozen, reason = rsi_frozen()
    assert frozen is True
    assert "STOP" in reason and str(stop) in reason

    engine = RSIEngine()
    with pytest.raises(RuntimeError) as excinfo:
        engine.run_rsi_cycle(bottleneck="slow tool router")
    message = str(excinfo.value)
    assert str(stop) in message
    assert "score" not in message.lower()  # refusal honesty: no fabricated evidence fields
    assert engine.stage == RSIStage.IDLE

    stop.unlink()
    assert rsi_frozen() == (False, "")
    assert engine.run_rsi_cycle(bottleneck="slow tool router").stage == RSIStage.PREVIEW


def test_switchboard_probe_error_engages_freeze_with_real_error(runtime_home, monkeypatch):
    """§5.6/§5.9: a broken switchboard refuses RSI cycles (with the real error) and never raises."""
    def _broken_probe():
        raise OSError("kill-switch state file unreadable (simulated)")

    monkeypatch.setattr(switchboard, "_kill_switch_state", _broken_probe)

    frozen, reason = rsi_frozen()  # rsi_frozen itself must never raise
    assert frozen is True
    assert "rsi switchboard failure" in reason
    assert "kill-switch state file unreadable (simulated)" in reason

    engine = RSIEngine()
    with pytest.raises(RuntimeError) as excinfo:
        engine.run_rsi_cycle(bottleneck="slow tool router")
    assert "kill-switch state file unreadable (simulated)" in str(excinfo.value)
    assert engine.stage == RSIStage.IDLE  # refused, not silently run

    # Switchboard degrades back to clear once the probe recovers — guard becomes a no-op again.
    monkeypatch.setattr(switchboard, "_kill_switch_state", lambda: (False, ""))
    assert rsi_frozen() == (False, "")
    assert guard_cycle_start() is None


def test_guard_is_noop_when_clear(runtime_home):
    assert rsi_frozen() == (False, "")
    assert guard_cycle_start() is None
    result = RSIEngine().run_rsi_cycle(bottleneck="slow tool router")
    assert result.stage == RSIStage.PREVIEW
    assert result.evidence_kind == "simulated"


def test_run_holdout_gate_absent_module_is_honest_not_available(runtime_home, monkeypatch):
    """Parallel-sequencing seam stubbed as absent: honest `not_available`, never a fabricated score."""
    def _absent():
        return None, None, "No module named 'alpha.rsi.holdout'"

    monkeypatch.setattr("alpha.rsi.engine._resolve_holdout_api", _absent)
    result = RSIEngine().run_holdout_gate()
    assert result["status"] == "not_available"
    assert result["gate_passed"] is False
    assert result["holdout"] is None
    assert "No module named 'alpha.rsi.holdout'" in result["gate_reason"]  # real error verbatim
    assert result["evidence_kind"] == "unverified"
    # Honesty: no score/pass-rate fields exist anywhere on the unavailable path.
    assert "score" not in result
    assert "confidence" not in result


def test_run_holdout_gate_composes_stubbed_a3_api(runtime_home, monkeypatch):
    """Composition against A3's specified API: run_holdout() -> dict, holdout_gate(dict) -> (bool, str)."""
    def _fake_run_holdout():
        return {
            "results": [{"case_id": "hidden-1", "passed": True}],
            "passed": 1,
            "failed": 0,
            "score": 1.0,
            "evidence_kind": "measured",
            "error": None,
        }

    def _fake_holdout_gate(holdout_result):
        if holdout_result.get("evidence_kind") != "measured":
            return False, "holdout unverified — cannot gate on unverified evidence"
        if holdout_result.get("failed", 0) > 0:
            return False, "holdout regressions"
        return True, "holdout passed"

    monkeypatch.setattr("alpha.rsi.engine._resolve_holdout_api", lambda: (_fake_run_holdout, _fake_holdout_gate, None))
    result = RSIEngine().run_holdout_gate()
    assert result["status"] == "ok"
    assert result["gate_passed"] is True
    assert result["gate_reason"] == "holdout passed"
    assert result["evidence_kind"] == "measured"
    assert result["holdout"]["passed"] == 1

    # Unverified evidence is passed through and rejected by the gate — simulated never gates.
    def _unverified_run():
        return {"score": 0.5, "evidence_kind": "unverified", "failed": 0, "passed": 0, "error": "holdout not run"}

    monkeypatch.setattr("alpha.rsi.engine._resolve_holdout_api", lambda: (_unverified_run, _fake_holdout_gate, None))
    rejected = RSIEngine().run_holdout_gate()
    assert rejected["status"] == "ok"
    assert rejected["gate_passed"] is False
    assert "cannot gate on unverified evidence" in rejected["gate_reason"]
    assert rejected["evidence_kind"] == "unverified"


def test_run_holdout_gate_run_failure_fails_closed(runtime_home, monkeypatch):
    """A holdout that raises surfaces the real error with gate_passed=False (fail-closed)."""
    def _explodes():
        raise RuntimeError("holdout evaluator exploded (simulated)")

    def _gate(holdout_result):
        return True, "must not be reached"

    monkeypatch.setattr("alpha.rsi.engine._resolve_holdout_api", lambda: (_explodes, _gate, None))
    result = RSIEngine().run_holdout_gate()
    assert result["status"] == "error"
    assert result["gate_passed"] is False
    assert result["holdout"] is None
    assert "holdout evaluator exploded (simulated)" in result["gate_reason"]
