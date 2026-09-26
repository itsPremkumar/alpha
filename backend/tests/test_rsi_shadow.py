"""Tests for WP-C1: shadow evaluation harness (rsi/shadow.py).

Pins (plan section 3 WP-C1 + honesty guardrails section 5):

- only the three plan states are ever emitted (``improved|regressed|
  inconclusive``); ``improved`` requires zero regressions AND >=1 strict
  improvement; one regressing fixture -> ``regressed`` with that fixture
  named in ``deltas`` (never averaged away, no numeric "improvement %"
  anywhere);
- any per-fixture failure -> ``inconclusive`` with the REAL exception text
  in ``notes`` and no numeric score in the payload (fail-closed: never a
  win for either side, even when other fixtures improve or regress);
- a delta exists only for metrics present on BOTH sides: a missing metric
  marks that fixture inconclusive and never produces a fabricated delta;
- ``evidence_kind`` stays inside the section 5.6 whitelist and is
  ``measured`` only when every fixture actually executed;
- every record carries ``channel="shadow"`` + the offline-fixture-not-live
  disclosure (constructor-pinned, cannot be emptied);
- purity: the fixture list and the evaluator metric dicts are never
  mutated, and the record holds its own copies;
- persistence is atomic (unique tmp + ``os.replace``, no leftover
  ``*.tmp``), content-addressed/deterministic (no wall clock), and
  round-trips through ``load_shadow_record`` with an integrity check that
  fails closed on tampering.
"""

import json

import pytest

from alpha.benchmarks.runner import BenchmarkCase, BenchmarkSuite
from alpha.config.runtime_paths import runtime_home
from alpha.rsi import shadow as shadow_mod
from alpha.rsi.shadow import (
    DISCLOSURE_NOTE,
    EVIDENCE_KINDS,
    SHADOW_STATES,
    ShadowComparison,
    fixture_ids_from_suite,
    load_shadow_record,
    run_shadow,
    shadow_record_path,
)


@pytest.fixture(autouse=True)
def _isolate_runtime_home(tmp_path, monkeypatch):
    """Every test gets its own state dir: AGENT_WORKSPACE_HOME -> tmp_path (task-mandated)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_WORKSPACE_PROJECT_ROOT", raising=False)
    yield


def _suite(*ids):
    """Fixture source: a real BenchmarkSuite from alpha.benchmarks.runner."""
    return BenchmarkSuite(name="shadow-fixture-source", version="v1", cases=[BenchmarkCase(case_id=case_id, title=case_id) for case_id in ids])


def _evaluator(metrics_by_fixture):
    """Evaluator handing back the table's dict BY REFERENCE (purity tests watch for mutation)."""

    def evaluate(fixture_id):
        return metrics_by_fixture[fixture_id]

    return evaluate


def _run(baseline_table, candidate_table, fixtures, fixture_set):
    return run_shadow(baseline_eval=_evaluator(baseline_table), candidate_eval=_evaluator(candidate_table), fixtures=fixtures, fixture_set=fixture_set)


# ── fixture source (integration: alpha.benchmarks.runner) ─────────────────


def test_fixture_source_is_benchmark_runner_suite_case_ids():
    suite = _suite("fx-1", "fx-2", "fx-3")
    ids = fixture_ids_from_suite(suite)
    assert ids == ["fx-1", "fx-2", "fx-3"]
    assert ids == [case.case_id for case in suite.cases]


# ── state machine: improved / regressed / inconclusive ────────────────────


def test_candidate_better_everywhere_improved_hand_computed_deltas():
    baseline_table = {"fx-parse": {"errors": 4, "latency_ms": 120}, "fx-route": {"errors": 2, "latency_ms": 80}}
    candidate_table = {"fx-parse": {"errors": 1, "latency_ms": 90}, "fx-route": {"errors": 1, "latency_ms": 80}}
    suite = _suite("fx-parse", "fx-route")
    comp = _run(baseline_table, candidate_table, fixture_ids_from_suite(suite), "unit-improved")
    # zero regressions + at least one strict improvement -> improved, measured
    assert comp.state == "improved"
    assert comp.state in SHADOW_STATES
    assert comp.evidence_kind == "measured"
    assert comp.channel == "shadow"
    # hand-computed deltas: candidate - baseline (lower is better)
    assert comp.deltas == {"fx-parse": {"errors": -3, "latency_ms": -30}, "fx-route": {"errors": -1, "latency_ms": 0}}
    assert comp.deltas["fx-parse"]["errors"] == -3
    assert comp.deltas["fx-parse"]["latency_ms"] == -30
    assert comp.deltas["fx-route"] == {"errors": -1, "latency_ms": 0}
    assert comp.baseline == baseline_table
    assert comp.candidate == candidate_table
    assert comp.fixture_set == "unit-improved"
    assert comp.disclosure == DISCLOSURE_NOTE


def test_single_regressing_fixture_is_regressed_never_averaged():
    baseline_table = {"fx-a": {"errors": 5}, "fx-b": {"errors": 4}, "fx-regress": {"errors": 1}}
    candidate_table = {"fx-a": {"errors": 0}, "fx-b": {"errors": 1}, "fx-regress": {"errors": 3}}
    comp = _run(baseline_table, candidate_table, ["fx-a", "fx-b", "fx-regress"], "unit-regressed")
    # two big improvements cannot average away the one regression
    assert comp.state == "regressed"
    assert comp.state in SHADOW_STATES
    assert comp.evidence_kind == "measured"
    assert "fx-regress" in comp.deltas  # the regression fixture is named in deltas
    assert comp.deltas["fx-regress"]["errors"] == 2  # 3 - 1, positive = regression
    assert comp.deltas["fx-a"] == {"errors": -5}
    assert comp.deltas["fx-b"] == {"errors": -3}
    assert any("fx-regress" in note for note in comp.notes)
    # no averaged score / pass rate is emitted anywhere in the payload
    dumped = json.dumps(comp.to_dict())
    assert "score" not in dumped
    assert "average" not in dumped


def test_fixture_error_outweighs_improvements_inconclusive_real_text():
    baseline_table = {"fx-gain": {"errors": 9, "latency_ms": 200}}
    candidate_table = {"fx-gain": {"errors": 1, "latency_ms": 50}, "fx-boom": {"errors": 4, "latency_ms": 10}}

    def baseline_eval(fixture_id):
        if fixture_id == "fx-boom":
            raise ValueError("baseline exploded")
        return baseline_table[fixture_id]

    comp = run_shadow(
        baseline_eval=baseline_eval,
        candidate_eval=_evaluator(candidate_table),
        fixtures=["fx-gain", "fx-boom"],
        fixture_set="unit-error-vs-improvement",
    )
    assert comp.state == "inconclusive"  # NOT improved, despite the huge fx-gain win
    assert comp.state in SHADOW_STATES
    assert comp.evidence_kind == "unverified"
    # the real exception text, verbatim, naming the failing side
    assert "fixture fx-boom raised: baseline evaluator ValueError: baseline exploded" in comp.notes
    # both sides always run: the candidate's fx-boom result is still disclosed
    assert "fx-boom" not in comp.baseline
    assert "fx-boom" in comp.candidate
    # real measured deltas for the completed fixture are kept (they are facts, not a verdict)
    assert comp.deltas == {"fx-gain": {"errors": -8, "latency_ms": -150}}


def test_fixture_error_outweighs_regressions_inconclusive_never_a_win():
    baseline_table = {"fx-slip": {"errors": 2}}
    candidate_table = {"fx-slip": {"errors": 9}}

    def baseline_eval(fixture_id):
        if fixture_id == "fx-boom":
            raise RuntimeError("fixture evaluation unavailable")
        return baseline_table[fixture_id]

    comp = run_shadow(
        baseline_eval=baseline_eval,
        candidate_eval=_evaluator(candidate_table),
        fixtures=["fx-slip", "fx-boom"],
        fixture_set="unit-error-vs-regression",
    )
    assert comp.state == "inconclusive"  # NOT regressed either: never a win for either side
    assert comp.evidence_kind == "unverified"
    assert "fixture fx-boom raised: baseline evaluator RuntimeError: fixture evaluation unavailable" in comp.notes
    assert comp.deltas == {"fx-slip": {"errors": 7}}  # the real positive delta stays visible


def test_all_fixture_exceptions_leave_no_numeric_score_in_payload():
    def exploding(fixture_id):
        raise RuntimeError("fixture evaluation unavailable")

    comp = run_shadow(baseline_eval=exploding, candidate_eval=exploding, fixtures=["fx-1", "fx-2"], fixture_set="unit-all-raise")
    assert comp.state == "inconclusive"
    assert comp.evidence_kind == "unverified"
    assert comp.baseline == {}
    assert comp.candidate == {}
    assert comp.deltas == {}
    assert "fixture fx-1 raised: baseline evaluator RuntimeError: fixture evaluation unavailable" in comp.notes
    assert "fixture fx-1 raised: candidate evaluator RuntimeError: fixture evaluation unavailable" in comp.notes
    assert "fixture fx-2 raised: baseline evaluator RuntimeError: fixture evaluation unavailable" in comp.notes
    assert "fixture fx-2 raised: candidate evaluator RuntimeError: fixture evaluation unavailable" in comp.notes
    # assert no numeric score appears in that payload: no score-like keys or
    # metric values at all (hex digits in run_id are an id, not a score)
    dumped = json.dumps(comp.to_dict())
    for token in ("score", "percent", "pct", "%", "confidence", "pass_rate"):
        assert token not in dumped
    assert comp.to_dict()["deltas"] == {}


def test_missing_metric_marks_fixture_inconclusive_without_fabricated_delta():
    baseline_table = {"fx-a": {"errors": 2, "latency_ms": 50}, "fx-b": {"errors": 3}}
    candidate_table = {"fx-a": {"errors": 2}, "fx-b": {"errors": 1}}  # candidate side lacks latency_ms for fx-a
    comp = _run(baseline_table, candidate_table, ["fx-a", "fx-b"], "unit-missing-metric")
    assert comp.state == "inconclusive"
    assert comp.evidence_kind == "unverified"
    # the fixture with the missing metric is excluded from deltas entirely
    assert "fx-a" not in comp.deltas
    assert comp.deltas == {"fx-b": {"errors": -2}}
    assert "latency_ms" not in json.dumps(comp.deltas)  # no fabricated delta for the missing metric
    notes = " | ".join(comp.notes)
    assert "fixture fx-a inconclusive" in notes
    assert "present only on baseline: ['latency_ms']" in notes
    assert "present only on candidate: []" in notes
    # the raw executed results remain visible as facts
    assert comp.baseline["fx-a"] == {"errors": 2, "latency_ms": 50}
    assert comp.candidate["fx-a"] == {"errors": 2}


def test_empty_metric_dicts_are_inconclusive_nothing_compared():
    comp = run_shadow(baseline_eval=lambda fixture_id: {}, candidate_eval=lambda fixture_id: {}, fixtures=["fx-empty"], fixture_set="unit-empty-metrics")
    assert comp.state == "inconclusive"
    assert comp.evidence_kind == "unverified"
    assert comp.deltas == {}
    assert any("empty metric dict" in note and "no delta fabricated" in note for note in comp.notes)


def test_non_dict_evaluator_return_fails_closed():
    comp = run_shadow(baseline_eval=lambda fixture_id: "not a dict", candidate_eval=lambda fixture_id: {"errors": 1}, fixtures=["fx-1"], fixture_set="unit-nondict")
    assert comp.state == "inconclusive"
    assert comp.evidence_kind == "unverified"
    assert "fixture fx-1 inconclusive: baseline evaluator returned str, expected dict" in comp.notes
    assert comp.deltas == {}
    assert "fx-1" not in comp.deltas


def test_tie_is_inconclusive_not_improved_evidence_still_measured():
    table = {"fx-x": {"errors": 2, "latency_ms": 40}}
    comp = _run(table, table, ["fx-x"], "unit-tie")
    # a tie is NOT an improvement: improved strictly requires >=1 strict improvement
    assert comp.state == "inconclusive"
    assert comp.evidence_kind == "measured"  # every fixture executed and compared: zero deltas are real measurements
    assert comp.deltas == {"fx-x": {"errors": 0, "latency_ms": 0}}
    assert any("tied" in note and "not improved" in note for note in comp.notes)


def test_empty_fixture_list_fails_closed():
    comp = run_shadow(
        baseline_eval=lambda fixture_id: {"errors": 0},
        candidate_eval=lambda fixture_id: {"errors": 0},
        fixtures=[],
        fixture_set="unit-no-fixtures",
    )
    assert comp.state == "inconclusive"
    assert comp.evidence_kind == "unverified"
    assert comp.deltas == {}
    assert any("no fixtures supplied" in note and "fail-closed" in note for note in comp.notes)
    # the inconclusive record is still persisted honestly
    assert shadow_record_path(comp.run_id).is_file()


def test_duplicate_fixture_ids_evaluated_once():
    calls: list[str] = []

    def counting(fixture_id):
        calls.append(fixture_id)
        return {"errors": 1}

    comp = run_shadow(baseline_eval=counting, candidate_eval=counting, fixtures=["fx-d", "fx-d"], fixture_set="unit-dup")
    # snapshot + de-dup: one pass over the fixture, one call per side
    assert calls == ["fx-d", "fx-d"]
    assert any("duplicate fixture id(s) evaluated once" in note for note in comp.notes)
    assert comp.state == "inconclusive"  # identical metrics tie; a tie is not improved


# ── whitelist / state pins (section 5.6 + "only 3 states ever") ──────────


def test_only_three_plan_states_and_section5_whitelist_constants():
    assert SHADOW_STATES == {"improved", "regressed", "inconclusive"}
    assert EVIDENCE_KINDS == {"measured", "simulated", "heuristic", "unverified"}


def test_constructing_invalid_state_evidence_or_channel_raises():
    base = {"run_id": "shadow-0123456789abcdef", "fixture_set": "s", "baseline": {}, "candidate": {}, "deltas": {}, "notes": []}
    ok = ShadowComparison(**base, state="inconclusive", evidence_kind="unverified")
    assert ok.state in SHADOW_STATES
    assert ok.evidence_kind in EVIDENCE_KINDS
    with pytest.raises(ValueError, match="invalid shadow state"):
        ShadowComparison(**base, state="promoted", evidence_kind="unverified")
    with pytest.raises(ValueError, match="invalid evidence_kind"):
        ShadowComparison(**base, state="inconclusive", evidence_kind="magic")
    with pytest.raises(ValueError, match="invalid channel"):
        ShadowComparison(**base, state="inconclusive", evidence_kind="unverified", channel="canary")
    with pytest.raises(ValueError, match="disclosure"):
        ShadowComparison(**base, state="inconclusive", evidence_kind="unverified", disclosure="")


def test_evidence_kind_measured_only_when_actually_computed():
    # fully executed comparison -> measured
    improved = _run({"fx": {"errors": 2}}, {"fx": {"errors": 1}}, ["fx"], "unit-ek-ok")
    assert improved.evidence_kind == "measured"
    # failing execution -> unverified (fail-closed, disclosed, never a pass)
    failing = run_shadow(baseline_eval=lambda fixture_id: 1 / 0, candidate_eval=lambda fixture_id: {"errors": 1}, fixtures=["fx"], fixture_set="unit-ek-fail")
    assert failing.evidence_kind == "unverified"
    # run_shadow never emits simulated/heuristic from this path
    assert {improved.evidence_kind, failing.evidence_kind} <= {"measured", "unverified"}
    for comp in (improved, failing):
        assert comp.evidence_kind in EVIDENCE_KINDS
        assert comp.state in SHADOW_STATES


def test_disclosure_and_channel_present_in_every_record_state():
    runs = [
        _run({"fx": {"errors": 2}}, {"fx": {"errors": 1}}, ["fx"], "unit-disc-improved"),
        _run({"fx": {"errors": 1}}, {"fx": {"errors": 2}}, ["fx"], "unit-disc-regressed"),
        run_shadow(baseline_eval=lambda fixture_id: (_ for _ in ()).throw(KeyError("boom")), candidate_eval=lambda fixture_id: {"errors": 1}, fixtures=["fx"], fixture_set="unit-disc-error"),
        run_shadow(baseline_eval=lambda fixture_id: {"errors": 1}, candidate_eval=lambda fixture_id: {"errors": 1}, fixtures=[], fixture_set="unit-disc-empty"),
    ]
    for comp in runs:
        assert comp.channel == "shadow"
        assert comp.disclosure == DISCLOSURE_NOTE
        assert "offline fixture shadow" in comp.disclosure
        assert "not live-traffic shadow" in comp.disclosure
        # the persisted record carries both, not just the in-memory object
        raw = json.loads(shadow_record_path(comp.run_id).read_text(encoding="utf-8"))
        assert raw["channel"] == "shadow"
        assert "not live-traffic shadow" in raw["disclosure"]


def test_no_numeric_improvement_percent_or_score_keys_anywhere():
    baseline_table = {"fx-a": {"errors": 4, "latency_ms": 120}, "fx-b": {"errors": 6}}
    candidate_table = {"fx-a": {"errors": 1, "latency_ms": 90}, "fx-b": {"errors": 2}}
    comp = _run(baseline_table, candidate_table, ["fx-a", "fx-b"], "unit-no-percent")
    payload = comp.to_dict()
    keys: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                keys.append(str(key))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    forbidden = ("score", "percent", "pct", "%", "improvement", "confidence")
    for key in keys:
        low = key.lower()
        assert not any(token in low for token in forbidden), f"forbidden metric-like key: {key!r}"
    # deltas are raw metric differences (candidate - baseline), never ratios/percentages
    for fixture_id, entry in payload["deltas"].items():
        for metric, delta in entry.items():
            assert delta == payload["candidate"][fixture_id][metric] - payload["baseline"][fixture_id][metric]


# ── purity: inputs are never mutated ──────────────────────────────────────


def test_inputs_not_mutated_and_record_holds_copies():
    baseline_table = {"fx-a": {"errors": 4, "latency_ms": 120}}
    candidate_table = {"fx-a": {"errors": 2, "latency_ms": 60}}
    fixtures = ["fx-a"]
    baseline_before = json.loads(json.dumps(baseline_table))
    candidate_before = json.loads(json.dumps(candidate_table))
    fixtures_before = list(fixtures)
    comp = _run(baseline_table, candidate_table, fixtures, "unit-purity")
    assert baseline_table == baseline_before  # evaluator dicts untouched
    assert candidate_table == candidate_before
    assert fixtures == fixtures_before  # fixture list untouched (snapshot + de-dup only reads it)
    # the record holds its own copies, not the evaluator's dict objects
    assert comp.baseline["fx-a"] is not baseline_table["fx-a"]
    assert comp.candidate["fx-a"] is not candidate_table["fx-a"]
    comp.baseline["fx-a"]["errors"] = 999
    assert baseline_table["fx-a"]["errors"] == 4


# ── persistence: atomic, round-trip, integrity, determinism ──────────────


def test_persisted_record_is_atomic_and_round_trips():
    comp = _run({"fx": {"errors": 3}}, {"fx": {"errors": 1}}, ["fx"], "unit-roundtrip")
    path = shadow_record_path(comp.run_id)
    assert path == runtime_home() / "rsi" / "shadow" / f"{comp.run_id}.json"
    assert path.is_file()
    assert list(path.parent.glob("*.tmp")) == []  # unique-tmp + os.replace left no staging file behind
    raw = json.loads(path.read_text(encoding="utf-8"))  # complete, parseable write (atomicity observable end state)
    assert raw == comp.to_dict()
    assert ShadowComparison.from_dict(raw) == comp  # round-trip through the dataclass
    assert load_shadow_record(comp.run_id) == comp  # round-trip through the integrity-checked loader


def test_tampered_record_fails_integrity_check_closed():
    comp = _run({"fx": {"errors": 3}}, {"fx": {"errors": 1}}, ["fx"], "unit-tamper")
    path = shadow_record_path(comp.run_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["baseline"]["fx"]["errors"] = 999  # forge a different measurement
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        load_shadow_record(comp.run_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["run_id"] = "shadow-deadbeefdeadbeef"  # forge the id itself
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        load_shadow_record(comp.run_id)


def test_missing_record_surfaces_real_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_shadow_record("shadow-0123456789abcdef")


def test_run_id_content_addressed_deterministic_no_wall_clock():
    first = _run({"fx": {"errors": 3}}, {"fx": {"errors": 1}}, ["fx"], "unit-determinism")
    second = _run({"fx": {"errors": 3}}, {"fx": {"errors": 1}}, ["fx"], "unit-determinism")
    assert first == second
    assert first.run_id == second.run_id  # identical inputs -> identical id (no uuid, no timestamp)
    assert first.run_id.startswith("shadow-")
    assert len(first.run_id) == len("shadow-") + 16
    other = _run({"fx": {"errors": 4}}, {"fx": {"errors": 1}}, ["fx"], "unit-determinism")
    assert other.run_id != first.run_id  # different content -> different id


def test_run_shadow_side_effects_limited_to_its_own_record(tmp_path):
    comp = _run({"fx": {"errors": 3}}, {"fx": {"errors": 1}}, ["fx"], "unit-side-effects")
    produced = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert produced == [f"rsi/shadow/{comp.run_id}.json"]  # nothing else on disk is created or touched


def test_persistence_failure_surfaces_real_error(monkeypatch):
    def fail(path, payload):
        raise OSError("runtime disk full - test injected")

    monkeypatch.setattr(shadow_mod, "atomic_write_json", fail)
    with pytest.raises(OSError, match="runtime disk full"):
        _run({"fx": {"errors": 3}}, {"fx": {"errors": 1}}, ["fx"], "unit-persist-fail")
    # a comparison that failed to persist is never silently returned


def test_run_id_path_safety():
    for bad in ("../evil", "a/b", "a\\b", "", "..", "a b", None):
        with pytest.raises(ValueError):
            shadow_record_path(bad)
    safe = shadow_record_path("shadow-0123456789abcdef")
    assert safe == runtime_home() / "rsi" / "shadow" / "shadow-0123456789abcdef.json"
