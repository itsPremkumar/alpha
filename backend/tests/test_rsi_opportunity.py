"""WP-D1 tests for the observe-only RSI opportunity miner (feature #19).

Honesty pins (implementation plan sections 2.4-#5, 5.6 and 3 WP-D1):
- identical signatures cluster once with the right frequency;
- clusters without concrete evidence references are never recorded;
- confidence is ``measured`` only with a real denominator recomputed in the
  test, otherwise ``0.5`` neutral + ``unverified`` (the spec's fixed
  confidence-number style is rejected - the literal pattern must not even
  appear in the module source);
- ``heuristic`` string present iff ``confidence_kind == "heuristic"``;
- priority is deterministic and discloses its heuristic inputs and neutral
  unmeasured penalties;
- observe-only: no work dispatch, no company-state mutation, no RSI cycle
  trigger, and ``mine()`` performs no filesystem writes.
"""

import json
import logging
from dataclasses import fields, replace
from pathlib import Path

import pytest

import alpha.rsi.engine as rsi_engine
import alpha.rsi.opportunity as opportunity_module
from alpha.benchmarks.runner import BenchmarkResult
from alpha.company import discovery, kpi, strategy
from alpha.company.models import DiscoveredWorkItem
from alpha.rsi.opportunity import (
    Opportunity,
    benchmark_regressions,
    load_opportunities,
    mine,
    opportunities_ledger_path,
    prioritization,
    record_opportunities,
    signals_from_watchdog,
)
from alpha.supervision.models import AnomalyReport, AnomalySeverity, AnomalyType


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Pin runtime_home() to a per-test temp dir (the environment does not isolate it)."""
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    return home


def _event(message, **extra):
    event = {"message": message}
    event.update(extra)
    return event


def _mine(errors=(), feedback=(), benchmarks=()):
    return mine(error_events=list(errors), feedback=list(feedback), benchmark_regressions=list(benchmarks))


# --- clustering contract -----------------------------------------------------


def test_identical_signatures_cluster_once_with_frequency():
    events = [
        _event("Gateway timeout after 30s", ref="trace:a1"),
        _event("gateway timeout after 30s", ref="trace:a2"),
        _event("GATEWAY  TIMEOUT AFTER 30S", ref="trace:a3"),  # case + whitespace normalize
        _event("disk fsck failed", ref="trace:b1"),
    ]
    opportunities = _mine(errors=events)
    assert len(opportunities) == 2
    by_frequency = {record.frequency: record for record in opportunities}
    cluster = by_frequency[3]
    assert cluster.category == "reliability"
    assert cluster.evidence_refs == ["trace:a1", "trace:a2", "trace:a3"]
    assert cluster.frequency == 3
    assert by_frequency[1].evidence_refs == ["trace:b1"]
    # deterministic: re-mining the same inputs yields the same content ids
    again = _mine(errors=events)
    assert [record.id for record in again] == [record.id for record in opportunities]


def test_cluster_without_evidence_refs_not_recorded(caplog):
    events = [
        _event("null pointer in worker"),  # no concrete evidence ref -> cluster discarded
        _event("null pointer in worker"),
        _event("separate issue", ref="trace:other"),
    ]
    with caplog.at_level(logging.WARNING):
        opportunities = _mine(errors=events)
    assert [record.evidence_refs for record in opportunities] == [["trace:other"]]
    assert "insufficient evidence — not recorded" in caplog.text
    assert "null pointer in worker" in caplog.text  # the log names the discarded cluster


def test_feedback_signals_cluster_and_categorize_deterministically():
    feedback = [
        {"text": "The summarizer is too slow on long files", "evidence": ["fb:1"]},
        {"text": "the summarizer is  too slow on long files", "evidence": ["fb:2"]},
        {"text": "please investigate the nightly drift", "category": "maintenance", "ref": "fb:3"},
    ]
    opportunities = _mine(feedback=feedback)
    assert len(opportunities) == 2
    slow = next(record for record in opportunities if record.frequency == 2)
    assert slow.category == "performance"  # deterministic keyword inference ("slow")
    assert slow.evidence_refs == ["fb:1", "fb:2"]
    assert slow.confidence_kind == "measured"
    assert slow.confidence == round(2 / 3, 6)  # hits / total feedback inputs, recomputed
    other = next(record for record in opportunities if record.frequency == 1)
    assert other.category == "maintenance"  # explicit category wins over keyword inference


# --- confidence honesty ------------------------------------------------------


def test_confidence_math_matches_recorded_inputs():
    events = [
        _event("flaky test timeout", ref="trace:1"),
        _event("flaky test timeout", ref="trace:2"),
        _event("unique failure a", ref="trace:3"),
        _event("unique failure b", ref="trace:4"),
        _event("unique failure c", ref="trace:5"),
        _event("unique failure d", ref="trace:6"),
    ]
    opportunities = _mine(errors=events)
    cluster = next(record for record in opportunities if record.frequency == 2)
    expected = round(cluster.frequency / len(events), 6)  # cluster_hits / total_events, recomputed
    assert cluster.confidence == expected == round(2 / 6, 6)
    assert cluster.confidence_kind == "measured"
    assert cluster.heuristic is None
    assert "[confidence=measured: 2/6 from recorded inputs]" in cluster.description


def test_no_denominator_confidence_is_neutral_unverified():
    # aggregate count with no stated population: list length is not an honest denominator
    events = [_event("disk pressure high", ref="trace:p1", count=40)]
    [opportunity] = _mine(errors=events)
    assert opportunity.frequency == 40
    assert opportunity.confidence == 0.5
    assert opportunity.confidence_kind == "unverified"
    assert opportunity.heuristic is None
    assert "no honest denominator" in opportunity.description
    assert "0.5 neutral" in opportunity.description


def test_inconsistent_population_totals_are_unverified():
    # stated total smaller than the cluster hits -> no honest denominator
    undersized = [_event("split brain", ref="trace:s1", count=5, total=2)]
    [opportunity] = _mine(errors=undersized)
    assert (opportunity.confidence, opportunity.confidence_kind) == (0.5, "unverified")
    # conflicting stated populations for one signature -> no honest denominator
    conflicting = [
        _event("split brain", ref="trace:s2", count=2, total=100),
        _event("split brain", ref="trace:s3", count=2, total=50),
    ]
    [opportunity] = _mine(errors=conflicting)
    assert (opportunity.confidence, opportunity.confidence_kind) == (0.5, "unverified")


def test_aggregate_with_consistent_population_total_is_measured():
    events = [_event("repeated search", ref="trace:r1", count=37, total=100)]
    [opportunity] = _mine(errors=events)
    assert opportunity.frequency == 37
    assert opportunity.confidence == round(37 / 100, 6)
    assert opportunity.confidence_kind == "measured"
    assert "[confidence=measured: 37/100 from recorded inputs]" in opportunity.description


def test_heuristic_string_present_iff_kind_is_heuristic():
    [mined] = _mine(errors=[_event("provider timeout", ref="trace:1")])
    heuristic = replace(mined, confidence_kind="heuristic", heuristic="score = frequency / 10 (demo formula)")
    unverified = replace(mined, confidence_kind="unverified", confidence=0.5, heuristic=None)
    measured = replace(mined, confidence_kind="measured", heuristic=None)
    for record in [mined, heuristic, unverified, measured]:
        assert (record.heuristic is not None) == (record.confidence_kind == "heuristic")
    # the miner itself only ever measures or discloses unverified - never an unlabeled heuristic
    assert all(record.confidence_kind in ("measured", "unverified") for record in [mined])


# --- prioritization ----------------------------------------------------------


def test_priority_deterministic_and_discloses_neutral_inputs():
    events = [_event("gateway timeout", ref="trace:1") for _ in range(3)]
    [opportunity] = _mine(errors=events)
    first = prioritization(opportunity)
    second = prioritization(opportunity)
    assert first == second
    score, disclosure = first
    severity_impact = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}[opportunity.severity]
    expected = round((severity_impact * opportunity.frequency * opportunity.confidence * 1.0) / (1.0 + 1.0), 6)
    assert score == expected
    assert "score_method=heuristic" in disclosure
    assert "spec section 8" in disclosure
    assert "resource_cost=1.0" in disclosure
    assert "risk_penalty=1.0" in disclosure
    assert "reproducibility=1.0" in disclosure
    assert "unmeasured -> neutral" in disclosure
    assert "not a measurement" in disclosure
    # unknown severity maps to the disclosed neutral impact, not an invented one
    odd = replace(opportunity, severity="urgent")
    odd_score, odd_disclosure = prioritization(odd)
    assert odd_score == round((2.0 * odd.frequency * odd.confidence * 1.0) / 2.0, 6)
    assert "unknown severity -> neutral 2.0" in odd_disclosure


# --- schema + honesty whitelist ----------------------------------------------


def test_opportunity_schema_matches_wp_d1_contract():
    names = {field.name for field in fields(Opportunity)}
    assert names == {
        "id",
        "category",
        "title",
        "description",
        "evidence_refs",
        "affected_components",
        "severity",
        "frequency",
        "confidence",
        "confidence_kind",
        "heuristic",
        "objective",
        "created_at",
    }


def test_honesty_whitelist_over_mixed_signals():
    opportunities = _mine(
        errors=[
            _event("gateway timeout", ref="trace:1"),
            _event("gateway timeout", ref="trace:2"),
            _event("disk pressure", ref="trace:3", count=7),
        ],
        feedback=[{"text": "the summarizer is too slow", "evidence": ["fb:1"]}],
        benchmarks=[
            {"detail": "p95 latency regressed", "suite": "gateway", "ref": "benchmark:bm-1"},
        ],
    )
    assert opportunities
    for record in opportunities:
        assert record.category in ("reliability", "performance", "capability", "maintenance", "research")
        assert record.confidence_kind in ("measured", "heuristic", "unverified")
        assert (record.heuristic is not None) == (record.confidence_kind == "heuristic")
        assert 0.0 <= record.confidence <= 1.0
        if record.confidence_kind == "unverified":
            assert record.confidence == 0.5  # neutral, never a fabricated pass
        assert record.frequency >= 1
        assert record.evidence_refs  # recorded clusters always carry concrete evidence
        assert record.objective  # fixed per-category default, never invented per run


def test_empty_signals_honest_empty_list(caplog):
    with caplog.at_level(logging.WARNING):
        assert _mine() == []
    assert "honest empty opportunity list" in caplog.text


# --- persistence (append-only ledger) ----------------------------------------


def test_ledger_append_dedupe_roundtrip_and_scope(isolated_home):
    home = Path(isolated_home)
    signals = [_event("gateway timeout", ref="trace:1"), _event("gateway timeout", ref="trace:2")]
    opportunities = _mine(errors=signals)
    first = record_opportunities(opportunities)
    ledger = opportunities_ledger_path()
    assert ledger.is_file()
    assert home.resolve() in ledger.parents
    assert first == {"appended": len(opportunities), "skipped": 0, "failed": 0}
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(opportunities)
    recorded = json.loads(lines[0])
    assert recorded["id"] == opportunities[0].id

    # re-recording the same ids must not duplicate rows (append-only + dedupe)
    second = record_opportunities(opportunities)
    assert second == {"appended": 0, "skipped": len(opportunities), "failed": 0}
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == len(opportunities)

    # a freshly mined copy (new created_at) still dedupes on the content id
    remine = _mine(errors=signals)
    assert record_opportunities(remine) == {"appended": 0, "skipped": 1, "failed": 0}

    loaded = load_opportunities()
    assert [record.id for record in loaded] == [record.id for record in opportunities]
    assert loaded[0].confidence == opportunities[0].confidence
    assert loaded[0].created_at == opportunities[0].created_at
    assert loaded[0].evidence_refs == opportunities[0].evidence_refs

    # the ONLY file under the runtime home is the ledger itself
    relative_files = {path.relative_to(home) for path in home.rglob("*") if path.is_file()}
    assert relative_files == {Path("rsi") / "opportunities.jsonl"}


def test_corrupt_ledger_lines_skipped_with_honest_warning(caplog):
    [opportunity] = _mine(errors=[_event("timeout", ref="trace:1")])
    record_opportunities([opportunity])
    ledger = opportunities_ledger_path()
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")
    with caplog.at_level(logging.WARNING):
        loaded = load_opportunities()
    assert [record.id for record in loaded] == [opportunity.id]
    assert "Skipped 1 corrupt/unrecognized" in caplog.text


def test_record_persistence_failure_reported_never_raises(isolated_home):
    home = Path(isolated_home)
    home.mkdir(parents=True, exist_ok=True)
    blocker = home / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    [opportunity] = _mine(errors=[_event("timeout", ref="trace:1")])
    summary = record_opportunities([opportunity], path=blocker / "opportunities.jsonl")
    assert summary == {"appended": 0, "skipped": 0, "failed": 1}
    assert not (blocker / "opportunities.jsonl").exists()


def test_mine_performs_no_filesystem_writes(isolated_home):
    home = Path(isolated_home)
    _mine(errors=[_event("timeout", ref="trace:1")])
    assert not home.exists() or not any(home.rglob("*"))


# --- observe-only enforcement ------------------------------------------------


def _forbidden(*_args, **_kwargs):
    raise AssertionError("observe-only violation: WP-D1 must stay read-and-record only")


def test_observe_only_no_dispatch_no_mutation_no_cycle(isolated_home, monkeypatch):
    # Any wiring into the company KPI subsystem, work discovery, or the RSI
    # cycle would raise here; mine/record/load must complete without that.
    monkeypatch.setattr(discovery.ContinuousWorkDiscoveryEngine, "discover_from_sources", _forbidden)
    monkeypatch.setattr(kpi.KPIEngine, "update_metric", _forbidden)
    monkeypatch.setattr(kpi.KPIEngine, "list_kpis", _forbidden)
    monkeypatch.setattr(strategy.StrategicPlanningEngine, "replan_strategy", _forbidden)
    monkeypatch.setattr(DiscoveredWorkItem, "__init__", _forbidden)
    monkeypatch.setattr(rsi_engine.RSIEngine, "run_rsi_cycle", _forbidden)

    opportunities = _mine(errors=[_event("gateway timeout", ref="trace:1")])
    summary = record_opportunities(opportunities)
    assert summary["appended"] == len(opportunities) >= 1
    assert load_opportunities()


def test_module_source_wiring_bans_and_fabrication_ban():
    source = Path(opportunity_module.__file__).read_text(encoding="utf-8")
    forbidden_tokens = (
        "alpha.company",
        "DiscoveredWorkItem",
        "WorkCategory",
        "discover_from_sources",
        "update_metric",
        "replan_strategy",
        "KPIEngine",
        "StrategicPlanningEngine",
        "run_rsi_cycle",
        "rsi.engine",
        "get_rsi_engine",
        "0.91",  # spec section 2.4-#5 fabricated-confidence pattern must never appear
    )
    for token in forbidden_tokens:
        assert token not in source, f"observe-only/honesty ban: {token!r} found in opportunity.py"


# --- integration adapters (read-only bridges) --------------------------------


def test_watchdog_adapter_reads_inspect_anomalies_only():
    thrash = AnomalyReport(
        worker_id="worker-1",
        anomaly_type=AnomalyType.TOOL_THRASHING,
        severity=AnomalySeverity.HIGH,
        description="Tool thrashing detected for worker-1",
    )
    latency = AnomalyReport(
        worker_id="worker-2",
        anomaly_type=AnomalyType.HIGH_LATENCY,
        severity=AnomalySeverity.MEDIUM,
        description="High latency observed on gateway calls",
    )

    class _Watchdog:
        def __init__(self):
            self.calls = 0

        def inspect_anomalies(self):
            self.calls += 1
            return [thrash, latency]

    watchdog = _Watchdog()
    signals = signals_from_watchdog(watchdog)
    assert watchdog.calls == 1  # the single read the observe-only bridge is allowed
    assert [signal["ref"] for signal in signals] == [f"anomaly:{thrash.anomaly_id}", f"anomaly:{latency.anomaly_id}"]
    assert signals[0]["severity"] == "high"
    assert signals[0]["component"] == "worker-1"
    assert [signal["category"] for signal in signals] == ["reliability", "performance"]

    opportunities = _mine(errors=signals)
    assert len(opportunities) == 2
    assert all(record.evidence_refs[0].startswith("anomaly:") for record in opportunities)


def test_benchmark_regression_filter_drops_unconfirmed_results():
    regression = BenchmarkResult(result_id="bm-1", suite="gateway", case_id="case-1", passed=False, detail="p95 latency regressed").to_dict()
    passing = BenchmarkResult(result_id="bm-2", suite="gateway", case_id="case-2", passed=True).to_dict()
    unstated = {"result_id": "bm-3", "suite": "gateway", "case_id": "case-3", "detail": "no verdict"}
    regressions = benchmark_regressions([regression, passing, unstated])
    assert [entry["ref"] for entry in regressions] == ["benchmark:bm-1"]

    [opportunity] = _mine(benchmarks=regressions)
    assert opportunity.category == "performance"
    assert opportunity.evidence_refs == ["benchmark:bm-1"]
    assert "gateway" in opportunity.affected_components
    assert opportunity.confidence_kind == "measured"  # single-occurrence source list: 1/1
