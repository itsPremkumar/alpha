"""The durable RRSI round store: ``S*``, the incumbent measurement, and the score series.

The store is the only thing Algorithm 1 carries across a process restart, so
every honesty rule about it is about what a *later* process will believe:

* an unreadable store reports **unknown**, never an empty search — ``S*`` of
  ``None`` floors at ``S* − δ``; ``S*`` of ``0.0`` would floor at ``−δ`` and
  admit every candidate thereafter;
* ``S*`` only ever rises, and a record that cannot be persisted is reported as
  not persisted rather than folded into the caller's memory as if it had been;
* a round with **no** measurement advances the round counter (it happened) but
  contributes no point to the stall series — and it cannot erase the last real
  ``(Ŝ_t, Ĉ_t)`` by writing ``None`` over it.

These tests take an explicit ``path`` so they exercise the real code path
without depending on process-global ``ALPHA_HOME``; only the path-resolution
test points the environment at a temp dir.
"""

from __future__ import annotations

import json

import pytest

from alpha.rsi.rrsi import DurableWrite, RoundState, RrsiRoundStore, round_store_path
from alpha.rsi.rrsi.store import MAX_ROUND_SCORES, STORE_VERSION


def _store(tmp_path) -> RrsiRoundStore:
    return RrsiRoundStore(tmp_path / "rsi" / "rrsi_rounds.json")


def _record(store: RrsiRoundStore, round_index: int, *, score, candidate_id="cand", incumbent_score=0.5, incumbent_cost=100.0):
    return store.record_round(
        round_index=round_index,
        score=score,
        candidate_id=candidate_id,
        incumbent_score=incumbent_score,
        incumbent_cost=incumbent_cost,
        incumbent_candidate_id=candidate_id,
    )


# --- resolution ---------------------------------------------------------------


def test_the_store_path_sits_under_runtime_home_and_reads_ALPHA_HOME(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    resolved = round_store_path()
    assert resolved == tmp_path / "rsi" / "rrsi_rounds.json"
    assert resolved.parent.is_dir() is False  # resolution creates nothing by itself


# --- first run ----------------------------------------------------------------


def test_a_first_run_reports_no_scores_and_never_zero_of_them(tmp_path):
    store = _store(tmp_path)
    assert store.readable is True
    assert store.fresh is True
    assert store.load_error is None
    assert store.round_index == 0
    assert store.next_round == 1, "rounds are 1-based; the first round is 1"
    assert store.best_score is None, "no score has been measured yet — 0.0 would make the floor −δ"
    assert store.round_scores == ()

    status = store.status()
    assert status["best_score"] is None and status["best_score"] != 0
    assert status["incumbent_score"] is None
    assert status["round_score_count"] == 0
    assert status["fresh"] is True


# --- recording a measured round ----------------------------------------------


def test_a_measured_round_is_durable_and_survives_a_reload(tmp_path):
    store = _store(tmp_path)
    write = _record(store, 1, score=0.5)
    assert write.ok is True and write.path.endswith("rrsi_rounds.json")

    reloaded = _store(tmp_path)
    assert reloaded.fresh is False
    assert reloaded.round_index == 1
    assert reloaded.next_round == 2
    assert reloaded.best_score == 0.5
    assert reloaded.round_scores == (0.5,)

    state = reloaded.state
    assert state.incumbent_score == 0.5
    assert state.incumbent_cost == 100.0
    assert state.incumbent_candidate_id == "cand"


def test_S_star_only_ever_rises(tmp_path):
    store = _store(tmp_path)
    assert _record(store, 1, score=0.8, candidate_id="best").ok is True
    assert _record(store, 2, score=0.3, candidate_id="worse").ok is True

    assert store.best_score == 0.8, "S* ← max(S*, Ŝ'); a worse round must not lower it"
    assert store.state.best_candidate_id == "best"
    assert store.round_scores == (0.8, 0.3), "the series records what each round measured, whatever the direction"
    assert store.round_index == 2


def test_an_unmeasured_round_advances_the_counter_without_inventing_a_point(tmp_path):
    store = _store(tmp_path)
    assert store.record_round(round_index=1, score=0.8, candidate_id="best", incumbent_score=0.75, incumbent_cost=42.0, incumbent_candidate_id="best").ok is True

    write = store.record_round(round_index=2, score=None, candidate_id=None, incumbent_score=None, incumbent_cost=None, incumbent_candidate_id=None)
    assert write.ok is True

    assert store.round_index == 2, "the round happened; its index must advance"
    assert store.next_round == 3
    assert store.round_scores == (0.8,), "a round with no measurement contributes no point to the stall series"
    assert store.best_score == 0.8
    assert store.state.incumbent_score == 0.75, "an unmeasured round must not overwrite the last real (Ŝ_t, Ĉ_t) with None"
    assert store.state.incumbent_cost == 42.0, "same for Ĉ_t — a preview cannot zero the cost history by measuring nothing"
    assert store.state.incumbent_candidate_id == "best"


def test_the_score_series_is_bounded_at_the_declared_cap():
    seeded = RoundState(round=MAX_ROUND_SCORES, best_score=0.5, round_scores=tuple(float(index) for index in range(MAX_ROUND_SCORES)))
    advanced = seeded.with_round(round_index=MAX_ROUND_SCORES + 1, score=-1.0, candidate_id="late", incumbent_score=0.0, incumbent_cost=1.0, incumbent_candidate_id="late")
    assert len(advanced.round_scores) == MAX_ROUND_SCORES, "the series is a bounded window, never an unbounded log"
    assert advanced.round_scores[-1] == -1.0
    assert advanced.round_scores[0] == 1.0, "the oldest point is the one dropped"
    assert advanced.best_score == 0.5


# --- unreadable: unknown, never empty ----------------------------------------


def test_an_unreadable_store_reports_unknown_and_refuses_to_overwrite(tmp_path):
    blocked = tmp_path / "rsi" / "rrsi_rounds.json"
    blocked.mkdir(parents=True)  # a directory where the file should be: exists, unreadable as text
    store = RrsiRoundStore(blocked)

    assert store.readable is False
    assert store.load_error
    assert store.state is None
    assert store.fresh is False, "a store that could not be read is not a first run"
    assert store.best_score is None
    assert store.round_scores == ()
    assert store.round_index == 0
    assert store.next_round == 1, "unreadable reports 1 — never 0, which is a round number that cannot occur"

    status = store.status()
    assert status["readable"] is False
    assert status["best_score"] is None and status["best_score"] != 0
    assert status["round"] is None
    assert status["round_score_count"] is None, "an unreadable source has no count; it does not have a count of 0"

    write = store.record_round(round_index=1, score=0.5, candidate_id="c", incumbent_score=0.5, incumbent_cost=1.0, incumbent_candidate_id="c")
    assert write.ok is False
    assert "refusing to overwrite state that could not be read" in (write.error or "")


def test_a_malformed_store_is_unknown_and_names_the_real_error(tmp_path):
    path = tmp_path / "rrsi_rounds.json"
    path.write_text("{not json", encoding="utf-8")
    store = RrsiRoundStore(path)
    assert store.readable is False
    assert store.load_error
    assert store.best_score is None
    assert store.record_round(round_index=1, score=0.5, candidate_id="c", incumbent_score=0.5, incumbent_cost=1.0, incumbent_candidate_id="c").ok is False


def test_an_unsupported_store_version_is_refused_rather_than_partially_read(tmp_path):
    path = tmp_path / "rrsi_rounds.json"
    path.write_text(json.dumps({"version": STORE_VERSION + 1, "round": 3, "round_scores": [0.9]}), encoding="utf-8")
    store = RrsiRoundStore(path)
    assert store.readable is False
    assert "not the supported version" in (store.load_error or "")
    assert store.round_index == 0 and store.best_score is None


def test_absent_is_not_corrupt(tmp_path):
    store = _store(tmp_path)
    assert store.readable is True, "a first run has no file, and that is not a fault"
    assert store.load_error is None


# --- write reporting ----------------------------------------------------------


def test_a_refused_save_is_reported_not_raised(tmp_path):
    store = _store(tmp_path)
    write = store.save({"round": 1})  # type: ignore[arg-type] - the refusal is the contract under test
    assert isinstance(write, DurableWrite)
    assert write.ok is False
    assert "takes a RoundState" in (write.error or "")
    assert store.round_index == 0, "a refused save leaves the loaded state exactly as it was"


def test_a_write_that_cannot_land_reports_failure_and_keeps_memory_and_disk_apart(tmp_path):
    blocked = tmp_path / "rrsi_rounds.json"
    blocked.mkdir(parents=True)
    store = RrsiRoundStore(blocked)
    store._ensure_loaded()  # noqa: SLF001 - loading is what makes the later write failure meaningful

    state = RoundState(round=1, best_score=0.5, round_scores=(0.5,))
    write = store.save(state)
    assert write.ok is False
    assert write.error

    # In-memory state was NOT adopted as though the write had landed.
    assert store.round_index == 0
    assert store.best_score is None


def test_disclosure_reports_store_health_and_the_series_without_claiming_a_result(tmp_path):
    store = _store(tmp_path)
    _record(store, 1, score=0.5)
    disclosure = store.disclosure()
    assert disclosure["store"]["readable"] is True
    assert disclosure["store"]["best_score"] == 0.5
    assert disclosure["round_scores"] == [0.5]
    assert "improvement" not in json.dumps(disclosure)


# --- payload validation -------------------------------------------------------


def test_a_round_index_that_cannot_occur_is_refused():
    with pytest.raises(ValueError, match="round must be an integer >= 0"):
        RoundState.from_dict({"version": STORE_VERSION, "round": -1})
    with pytest.raises(ValueError, match="round must be an integer >= 0"):
        RoundState.from_dict({"version": STORE_VERSION, "round": True})
    with pytest.raises(ValueError, match="round_scores entries must be a number or null"):
        RoundState.from_dict({"version": STORE_VERSION, "round": 1, "round_scores": ["0.5"]})
    with pytest.raises(ValueError, match="round_scores must be a list"):
        RoundState.from_dict({"version": STORE_VERSION, "round": 1, "round_scores": "0.5"})
    with pytest.raises(ValueError, match="payload must be an object"):
        RoundState.from_dict([])  # type: ignore[arg-type]
