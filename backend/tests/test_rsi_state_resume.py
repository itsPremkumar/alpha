"""Tests for durable RSI cycle state + resume quarantine (plan WP-A4, feature #17)."""

from __future__ import annotations

import json

import pytest

from alpha.rsi import state as state_module
from alpha.rsi.models import RSIStage
from alpha.rsi.state import (
    RsiCycleState,
    load_state,
    new_state,
    resume,
    save_state,
    state_path,
)


@pytest.fixture()
def runtime_home(tmp_path, monkeypatch):
    """Isolate runtime_home() so state never touches the real workspace (env does not isolate it)."""
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    return home.resolve()


def test_state_save_load_round_trip(runtime_home):
    state = RsiCycleState(
        cycle_id="cycle-abc123",
        stage=RSIStage.CANDIDATE_CREATED.value,
        repo_commit="deadbeef",
        evaluator_manifest_sha="sha256:cafe",
        remaining=["holdout_evaluation", "preview"],
        updated_at=0.0,
    )
    path = save_state(state)
    assert path == state_path()
    assert path == runtime_home / "rsi" / "state" / "cycle.json"
    assert path.is_file()
    # Atomic write: the unique staging tmp file never survives (tmp + os.replace).
    assert not list(path.parent.glob("*.tmp"))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["cycle_id"] == "cycle-abc123"
    assert on_disk["evaluator_manifest_sha"] == "sha256:cafe"
    assert on_disk["remaining"] == ["holdout_evaluation", "preview"]
    loaded, err = load_state()
    assert err is None
    assert loaded == state
    assert loaded is not None
    assert loaded.updated_at > 0.0
    # A second save also stays atomic.
    save_state(loaded)
    assert not list(path.parent.glob("*.tmp"))


def test_corrupt_state_returns_real_error_and_resume_quarantines(runtime_home):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json{{", encoding="utf-8")
    loaded, err = load_state()
    assert loaded is None
    assert err is not None
    assert "corrupt" in err
    assert "Expecting" in err  # the real JSON parser message passes through — never "pretend clean"
    ok, reason = resume()
    assert ok is False
    assert reason.startswith("quarantine:")
    assert "Expecting" in reason  # the real cause travels with the refusal


def test_missing_state_honest_note_and_resume_refuses(runtime_home):
    loaded, err = load_state()
    assert loaded is None
    assert err is not None
    assert "no saved RSI cycle state" in err
    ok, reason = resume()
    assert ok is False
    assert "quarantine" in reason
    assert "no saved RSI cycle state" in reason


def test_persisted_unknown_stage_is_corrupt_not_silent_clean(runtime_home):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cycle_id": "cycle-x",
        "stage": "teleport",
        "repo_commit": "abc",
        "evaluator_manifest_sha": None,
        "remaining": [],
        "updated_at": 1.0,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded, err = load_state()
    assert loaded is None
    assert err is not None
    assert "unknown RSI stage" in err


def test_resume_quarantines_on_repo_commit_change(runtime_home, monkeypatch):
    saved = RsiCycleState(
        cycle_id="cycle-stale",
        stage=RSIStage.HOLDOUT_EVALUATION.value,
        repo_commit="stale-commit-0000",
        evaluator_manifest_sha=None,
        remaining=["preview"],
        updated_at=1.0,
    )
    save_state(saved)
    monkeypatch.setattr(state_module, "fresh_manifest_sha", lambda: (None, "evaluator manifest builder unavailable: sim"))
    ok, reason = resume()
    assert ok is False
    assert reason.startswith("quarantine:")
    assert "repo commit changed" in reason
    assert "stale-commit-0000" in reason


def test_resume_quarantines_on_evaluator_manifest_tamper(runtime_home, monkeypatch):
    saved = RsiCycleState(
        cycle_id="cycle-tamper",
        stage=RSIStage.PREVIEW.value,
        repo_commit=state_module.current_repo_commit(),
        evaluator_manifest_sha="sha256:baseline",
        remaining=[],
        updated_at=1.0,
    )
    save_state(saved)
    monkeypatch.setattr(state_module, "fresh_manifest_sha", lambda: ("sha256:tampered", None))
    ok, reason = resume()
    assert ok is False
    assert reason.startswith("quarantine:")
    assert "evaluator manifest changed" in reason
    assert "sha256:baseline" in reason and "sha256:tampered" in reason


def test_resume_ok_when_commit_and_manifest_match(runtime_home, monkeypatch):
    saved = RsiCycleState(
        cycle_id="cycle-good",
        stage=RSIStage.PREVIEW.value,
        repo_commit=state_module.current_repo_commit(),
        evaluator_manifest_sha="sha256:same",
        remaining=[],
        updated_at=1.0,
    )
    save_state(saved)
    monkeypatch.setattr(state_module, "fresh_manifest_sha", lambda: ("sha256:same", None))
    ok, reason = resume()
    assert ok is True
    assert "resume ok" in reason
    assert "evaluator manifest match" in reason


def test_resume_quarantines_when_recorded_manifest_cannot_be_reverified(runtime_home, monkeypatch):
    saved = RsiCycleState(
        cycle_id="cycle-gone",
        stage=RSIStage.PREVIEW.value,
        repo_commit=state_module.current_repo_commit(),
        evaluator_manifest_sha="sha256:was-recorded",
        remaining=[],
        updated_at=1.0,
    )
    save_state(saved)
    monkeypatch.setattr(state_module, "fresh_manifest_sha", lambda: (None, "evaluator manifest builder unavailable: builder gone"))
    ok, reason = resume()
    assert ok is False  # a recorded baseline that cannot be re-verified fails closed
    assert "could not be re-verified" in reason
    assert "builder gone" in reason


def test_resume_discloses_unverified_manifest_when_never_recorded(runtime_home, monkeypatch):
    saved = RsiCycleState(
        cycle_id="cycle-unver",
        stage=RSIStage.PREVIEW.value,
        repo_commit=state_module.current_repo_commit(),
        evaluator_manifest_sha=None,
        remaining=[],
        updated_at=1.0,
    )
    save_state(saved)
    unavailable = "evaluator manifest builder unavailable: No module named 'alpha.rsi.evaluator_manifest'"
    monkeypatch.setattr(state_module, "fresh_manifest_sha", lambda: (None, unavailable))
    ok, reason = resume()
    assert ok is True  # the commit check genuinely passed...
    assert "UNVERIFIED" in reason  # ...but manifest integrity is disclosed, never claimed
    assert unavailable in reason


def test_advance_rejects_unknown_stage():
    st = RsiCycleState()
    with pytest.raises(ValueError, match="unknown RSI stage"):
        st.advance("teleport")
    assert st.stage == RSIStage.IDLE.value


@pytest.mark.parametrize("gated", [RSIStage.PROMOTED.value, RSIStage.ROLLED_BACK.value])
def test_advance_gated_stages_require_measured_evidence(gated):
    st = RsiCycleState()
    with pytest.raises(ValueError, match="requires evidence_kind='measured'"):
        st.advance(gated)
    with pytest.raises(ValueError, match="requires evidence_kind='measured'"):
        st.advance(gated, evidence_kind="simulated")
    with pytest.raises(ValueError, match="requires evidence_kind='measured'"):
        st.advance(gated, evidence_kind="unverified")
    assert st.stage == RSIStage.IDLE.value  # refused attempts leave the stage untouched
    st.advance(gated, evidence_kind="measured")
    assert st.stage == gated


def test_advance_regular_stages_need_no_marker():
    st = RsiCycleState()
    assert st.updated_at == 0.0
    st.advance(RSIStage.PREVIEW.value)
    assert st.stage == RSIStage.PREVIEW.value
    assert st.updated_at > 0.0
    st.advance(RSIStage.IDLE.value)
    assert st.stage == RSIStage.IDLE.value


def test_new_state_records_fresh_manifest_sha(runtime_home, monkeypatch):
    monkeypatch.setattr(state_module, "fresh_manifest_sha", lambda: ("sha256:fresh", None))
    st = new_state(remaining=["holdout_evaluation"])
    assert st.stage == RSIStage.IDLE.value
    assert st.evaluator_manifest_sha == "sha256:fresh"
    assert st.remaining == ["holdout_evaluation"]
    assert st.cycle_id.startswith("cycle-")


def test_new_state_keeps_manifest_none_when_builder_absent(runtime_home, monkeypatch):
    monkeypatch.setattr(state_module, "fresh_manifest_sha", lambda: (None, "evaluator manifest builder unavailable: sim"))
    st = new_state()
    assert st.evaluator_manifest_sha is None  # honest None — never a fabricated digest


def test_repo_commit_failure_records_honest_unknown(runtime_home, monkeypatch):
    import alpha.evolution.identity as identity_module

    def _boom():
        raise RuntimeError("identity unavailable (simulated)")

    monkeypatch.setattr(identity_module, "get_runtime_identity", _boom)
    assert state_module.current_repo_commit() == "unknown"
