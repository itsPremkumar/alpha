"""Tests for the real hidden holdout evaluation (RSI WP-A3, feature #3).

Pins, per plan section 3 WP-A3:
- the hidden suite executes for real and yields measured evidence whose
  values come from evaluator outputs, not constants;
- hidden case IDs never appear in candidate-facing payloads;
- evaluator exceptions fail closed with the real message and cannot gate;
- holdout-not-run is 0.5/unverified and cannot gate;
- simulated results (the existing 0.89 preview / 96.4 family) are rejected
  as gate inputs — simulated never gates.
"""

import json
from dataclasses import asdict
from datetime import datetime

import pytest

from alpha.benchmarks.runner import BenchmarkCase, BenchmarkRunner, BenchmarkSuite
from alpha.rsi import holdout
from alpha.rsi.engine import RSIEngine
from alpha.rsi.holdout import (
    HOLDOUT_SUITE_NAME,
    HoldoutCase,
    HoldoutResultRecord,
    hidden_case_ids,
    holdout_gate,
    register_hidden_suite,
    run_holdout,
)
from alpha.tools.builtins.rsi_engine_tool import run_rsi_cycle

EVIDENCE_WHITELIST = {"measured", "simulated", "heuristic", "unverified"}
UNVERIFIED_REASON = "holdout unverified — cannot gate on unverified evidence"


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Keep runtime_home() inside a temp dir (the env does not isolate it)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def healthy_view() -> dict:
    return {
        "compaction": {"max_budget_chars": 100_000, "keep_last_observations": 4},
        "tool_router": {"retry_limit": 3, "timeout_seconds": 30},
        "context_pruner": {"strip_threshold": 500},
    }


def degenerate_view() -> dict:
    """Every hidden range violated — failures must come from real evaluation."""
    return {
        "compaction": {"max_budget_chars": 0, "keep_last_observations": 0},
        "tool_router": {"retry_limit": 0, "timeout_seconds": 0},
        "context_pruner": {"strip_threshold": 0},
    }


def test_register_hidden_suite_returns_unique_hidden_case_ids():
    ids = register_hidden_suite()
    assert ids
    assert len(set(ids)) == len(ids)
    assert register_hidden_suite() == ids  # idempotent
    assert hidden_case_ids() == ids
    assert HoldoutCase(case_id="x", fixture={}, expected={}).hidden is True


def test_holdout_case_and_record_shapes_match_contract():
    assert set(asdict(HoldoutCase(case_id="c", fixture={"a": 1}, expected={"b": 2}))) == {"case_id", "fixture", "expected", "hidden"}
    record = HoldoutResultRecord(
        suite="s", suite_version="v1", case_id="c", passed=True, score=1.0, detail="d", evidence_kind="measured", ran_at="t"
    )
    assert set(asdict(record)) == {"suite", "suite_version", "case_id", "passed", "score", "detail", "evidence_kind", "ran_at"}


def test_hidden_suite_runs_measured_from_real_evaluator_outputs():
    ids = register_hidden_suite()
    result = run_holdout(healthy_view())
    assert result["error"] is None
    assert result["evidence_kind"] == "measured"
    assert result["evidence_kind"] in EVIDENCE_WHITELIST
    assert result["results"]
    assert len(result["results"]) == len(ids)
    assert {r["case_id"] for r in result["results"]} == set(ids)
    assert result["passed"] + result["failed"] == len(result["results"])
    # Score is derived from the executed run, never a constant.
    assert result["score"] == result["passed"] / len(result["results"])
    for record in result["results"]:
        assert record["suite"] == HOLDOUT_SUITE_NAME
        assert record["evidence_kind"] in EVIDENCE_WHITELIST
        datetime.fromisoformat(record["ran_at"])  # real execution timestamp
    # Deterministic: a second executed run reproduces the same outcomes.
    again = run_holdout(healthy_view())
    assert (again["passed"], again["failed"], again["score"]) == (result["passed"], result["failed"], result["score"])
    assert {r["case_id"]: r["passed"] for r in again["results"]} == {r["case_id"]: r["passed"] for r in result["results"]}


def test_case_ids_subset_runs_only_requested_cases():
    ids = register_hidden_suite()
    result = run_holdout(healthy_view(), case_ids=[ids[0]])
    assert result["evidence_kind"] == "measured"
    assert [r["case_id"] for r in result["results"]] == [ids[0]]
    assert result["passed"] + result["failed"] == 1


def test_failing_view_yields_real_failures_and_gate_regressions():
    register_hidden_suite()
    healthy = run_holdout(healthy_view())
    bad = run_holdout(degenerate_view())
    # Same suite, same cases: outcomes differ because they were really evaluated.
    assert bad["evidence_kind"] == "measured"
    assert bad["failed"] > 0
    assert bad["score"] < healthy["score"]
    assert bad["score"] == bad["passed"] / len(bad["results"])
    healthy_by_id = {r["case_id"]: r for r in healthy["results"]}
    budget = next(r for r in bad["results"] if r["case_id"] == "holdout-compaction-budget")
    assert budget["passed"] is False
    assert healthy_by_id["holdout-compaction-budget"]["passed"] is True
    assert budget["detail"].startswith("compaction.max_budget_chars=")
    assert "0" in budget["detail"]  # the real measured value appears in the detail
    assert holdout_gate(bad) == (False, "holdout regressions")


def test_gate_passes_only_on_executed_measured_run():
    register_hidden_suite()
    result = run_holdout(healthy_view())
    assert holdout_gate(result) == (True, "holdout passed")


def test_candidate_view_missing_is_not_run_and_cannot_gate():
    register_hidden_suite()
    result = run_holdout()  # no candidate view -> nothing to evaluate
    assert result["score"] == 0.5
    assert result["evidence_kind"] == "unverified"
    assert result["results"] == []
    assert "candidate_view was not provided" in result["error"]
    assert holdout_gate(result) == (False, UNVERIFIED_REASON)


def test_unregistered_suite_fails_closed_with_real_error(monkeypatch):
    monkeypatch.setattr(holdout, "_RUNNER", BenchmarkRunner())  # bare runner, suite never registered
    result = run_holdout(healthy_view())
    assert result["score"] == 0.5
    assert result["evidence_kind"] == "unverified"
    assert "not registered" in result["error"]
    assert HOLDOUT_SUITE_NAME in result["error"]  # real error text, not a substitute
    assert holdout_gate(result) == (False, UNVERIFIED_REASON)


def test_evaluator_exception_fails_closed_with_real_message(monkeypatch):
    runner = BenchmarkRunner()
    suite = BenchmarkSuite(
        name=HOLDOUT_SUITE_NAME,
        version="v1",
        cases=[BenchmarkCase(case_id="boom-case", title="boom", fixture={"kind": "range", "path": ["x"]}, expected={"min": 0, "max": 1})],
    )

    def raiser(case: BenchmarkCase) -> tuple[bool, float, str]:
        raise RuntimeError("holdout evaluator exploded (real error)")

    runner.register_suite(suite, raiser)
    monkeypatch.setattr(holdout, "_RUNNER", runner)
    result = run_holdout(healthy_view())
    assert result["evidence_kind"] == "measured"  # the run was executed
    assert result["failed"] >= 1
    failed_record = next(r for r in result["results"] if not r["passed"])
    assert "evaluator raised:" in failed_record["detail"]
    assert "holdout evaluator exploded (real error)" in failed_record["detail"]
    assert holdout_gate(result) == (False, "holdout regressions")


def test_unknown_case_ids_fail_closed():
    register_hidden_suite()
    result = run_holdout(healthy_view(), case_ids=["holdout-does-not-exist"])
    assert result["evidence_kind"] == "unverified"
    assert result["score"] == 0.5
    assert "holdout-does-not-exist" in result["error"]
    assert holdout_gate(result) == (False, UNVERIFIED_REASON)


def test_simulated_results_are_rejected_as_gate_inputs():
    # The existing simulated preview score (engine 0.89 / enterprise 96.4 family).
    for simulated_score in (0.89, 0.964):
        payload = {"results": [], "passed": 45, "failed": 0, "score": simulated_score, "evidence_kind": "simulated", "error": None}
        assert holdout_gate(payload) == (False, UNVERIFIED_REASON)
    for other_kind in ("heuristic", "unknown", "unverified"):
        payload = {"results": [], "passed": 1, "failed": 0, "score": 1.0, "evidence_kind": other_kind, "error": None}
        assert holdout_gate(payload) == (False, UNVERIFIED_REASON)
    not_run = holdout.holdout_not_run("holdout not run: operator never invoked it")
    assert not_run["score"] == 0.5
    assert not_run["evidence_kind"] == "unverified"
    assert holdout_gate(not_run) == (False, UNVERIFIED_REASON)


def test_gate_fails_closed_on_malformed_or_empty_payloads():
    assert holdout_gate({}) == (False, UNVERIFIED_REASON)
    assert holdout_gate(None) == (False, UNVERIFIED_REASON)  # type: ignore[arg-type]
    assert holdout_gate({"evidence_kind": "measured"}) == (False, UNVERIFIED_REASON)  # missing counts
    assert holdout_gate({"evidence_kind": "measured", "passed": 0, "failed": 0}) == (False, UNVERIFIED_REASON)  # empty run
    assert holdout_gate({"evidence_kind": "measured", "passed": -1, "failed": 0}) == (False, UNVERIFIED_REASON)
    assert holdout_gate({"evidence_kind": "measured", "passed": True, "failed": False}) == (False, UNVERIFIED_REASON)  # bools


def test_hidden_case_ids_absent_from_candidate_facing_payloads_and_engine_simulated_never_gates():
    ids = register_hidden_suite()
    engine = RSIEngine()
    result = engine.run_rsi_cycle(
        bottleneck="Excessive token bloat due to uncompacted directory listings",
        target_component="compaction",
        force_promote=False,
    )
    payload = json.dumps(result.to_dict(), default=str)
    tool_payload = run_rsi_cycle.invoke({"bottleneck": "Tool retry rate exceeded", "target_component": "tool_router"})
    assert isinstance(tool_payload, str)
    for hidden_id in ids:
        assert hidden_id not in payload
        assert hidden_id not in tool_payload
    assert HOLDOUT_SUITE_NAME not in payload
    assert HOLDOUT_SUITE_NAME not in tool_payload
    # The existing simulated preview stays simulated and can never gate.
    engine_holdout = result.to_dict()["holdout"]
    assert engine_holdout["evidence_kind"] == "simulated"
    gate_input = {
        "results": [],
        "passed": 45,
        "failed": 0,
        "score": engine_holdout["score"],
        "evidence_kind": engine_holdout["evidence_kind"],
        "error": None,
    }
    assert holdout_gate(gate_input) == (False, UNVERIFIED_REASON)
