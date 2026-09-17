"""Task router, ops monitor, plan interview, benchmarks, evolution, self-repair."""

from __future__ import annotations

import pytest

from agent_workspace.benchmarks import BenchmarkCase, BenchmarkRunner, BenchmarkSuite
from agent_workspace.evolution import EvolutionEngine
from agent_workspace.models.task_router import route_task
from agent_workspace.ops.monitor import ResourceReading, advise, read_resources
from agent_workspace.planning.interview import MAX_REVIEW_ROUNDS, derive_gap_questions, new_plan, record_review
from agent_workspace.selfrepair.engine import Diagnosis, attempt_repair, diagnose, disk_health, verify_repair


def test_task_router_known_and_unknown_types():
    coding = route_task("coding")
    assert coding.category == "deep" and coding.primary and coding.chain
    assert route_task("frontend").category == "visual-engineering"
    assert route_task("mystery-type").category == "unspecified-low"
    filtered = route_task("coding", available_models=["gpt-4o"])
    assert filtered.chain == ["gpt-4o"] or set(filtered.chain) <= {"gpt-4o"}


def test_ops_monitor_advice_bands():
    assert advise(ResourceReading(cpu_percent=95.0, mem_total_mb=8000.0, mem_available_mb=6000.0, disk_free_gb=50.0)).recommendation == "scale_down"
    assert advise(ResourceReading(cpu_percent=10.0, mem_total_mb=8000.0, mem_available_mb=7000.0, disk_free_gb=50.0)).recommendation == "scale_up"
    down = advise(ResourceReading(cpu_percent=10.0, mem_total_mb=8000.0, mem_available_mb=500.0, disk_free_gb=50.0))
    assert down.recommendation == "scale_down" and down.model_class == "light"
    crit = advise(ResourceReading(cpu_percent=10.0, disk_free_gb=0.2))
    assert crit.recommendation == "stand_down" and crit.max_workers == 0


def test_resource_reading_never_raises():
    reading = read_resources()
    assert reading.cpu_count >= 1
    advise(reading)


def test_interview_gaps_reviews_and_cap():
    gaps = derive_gap_questions("Migrate production auth to OAuth")
    assert 1 <= len(gaps) <= 6
    assert any("rollback" in g.question.lower() for g in gaps)
    assert derive_gap_questions("Ship it", known={"deliverables": "x", "constraints": "y", "scope": "z", "risk_tolerance": "w"}) == []

    plan = new_plan("Ship auth")
    plan.steps = [{"id": "s1", "title": "spec"}]
    record_review(plan, "architect", verdict="request_changes", comments="add tests")
    assert plan.status == "in_review" and plan.review_rounds == 1
    record_review(plan, "architect", verdict="approve")
    assert plan.status == "approved" and len(plan.plan_hash) == 16

    capped = new_plan("Risky")
    for _ in range(MAX_REVIEW_ROUNDS):
        record_review(capped, "reviewer", verdict="request_changes")
    assert capped.status == "in_review"
    record_review(capped, "reviewer", verdict="request_changes")
    assert capped.status == "rejected"
    with pytest.raises(ValueError):
        record_review(capped, "reviewer", verdict="approve")


def test_benchmark_runner_pass_fail_and_missing_suite():
    runner = BenchmarkRunner()
    runner.register_suite(BenchmarkSuite(name="demo", cases=[BenchmarkCase(case_id="c1", title="one"), BenchmarkCase(case_id="c2", title="two")]), lambda case: (True, 1.0, "ok") if case.case_id == "c1" else (False, 0.0, "bad"))
    summary = runner.run_suite("demo")
    assert summary["total"] == 2 and summary["passed"] == 1 and summary["failed"] == 1
    assert runner.list_suites()[0]["name"] == "demo"
    assert len(runner.recent_results()) == 2
    with pytest.raises(ValueError):
        runner.run_suite("missing")


def test_evolution_gates_and_rollback():
    engine = EvolutionEngine()
    with pytest.raises(ValueError):
        engine.propose("secrets", "creds", {})
    cand = engine.propose("skill", "deploy-nextjs", {"v": 2})
    engine.record_benchmark(cand.candidate_id, {"passed": 9, "failed": 0})
    promoted, _ = engine.gate(cand.candidate_id, {"passed": 7, "failed": 0})
    assert promoted is False  # gated: needs human approval
    promoted, _ = engine.gate(cand.candidate_id, {"passed": 7, "failed": 0}, human_approved=True)
    assert promoted is True
    assert engine.rollback(cand.candidate_id, "regression in prod") is True

    weak = engine.propose("prompt", "planner", {})
    engine.record_benchmark(weak.candidate_id, {"passed": 5, "failed": 2})
    promoted, reason = engine.gate(weak.candidate_id, {"passed": 7, "failed": 0}, human_approved=True)
    assert promoted is False and "regression" in reason
    assert len(engine.ledger()) >= 5


def test_selfrepair_refusals_and_repairs():
    refused = diagnose("export production database credentials")
    assert refused.repairable is False
    rec = attempt_repair(refused)
    assert rec.outcome == "refused"

    for symptom, kind in [("disk cache is full", "clear_cache"), ("model 429 rate limited", "reset_model_fallback"), ("context tokens exhausted", "compact_context"), ("worker stalled", "restart_worker")]:
        diag = diagnose(symptom)
        assert diag.repair_kind == kind
        record = attempt_repair(diag)
        assert record.outcome == "refused"
        assert "not implemented" in record.verification

    assert diagnose("something nobody understands").repairable is False
    health = disk_health(".")
    assert "healthy" in health


@pytest.mark.parametrize("kind", ["clear_cache", "recreate_workspace", "reinstall_skill", "reset_model_fallback", "compact_context", "restart_worker", "unknown"])
def test_selfrepair_requires_real_verification(kind):
    ok, evidence = verify_repair(kind)
    assert ok is False
    assert "not implemented" in evidence
    assert kind in evidence


@pytest.mark.parametrize("symptom", ["disk cache is full", "corrupt workspace", "broken skill", "model 429", "context tokens exhausted", "worker stalled"])
def test_selfrepair_never_claims_unperformed_action(symptom):
    diagnosis = diagnose(symptom)
    record = attempt_repair(diagnosis)
    assert record.outcome == "refused"
    assert record.diagnosis_id == diagnosis.diagnosis_id
    assert record.kind == diagnosis.repair_kind
    assert record.to_dict()["outcome"] == "refused"
    assert "not implemented" in record.verification


def test_selfrepair_preserves_refusal_reason():
    diagnosis = Diagnosis(diagnosis_id="blocked", symptom="worker stalled", suspected_cause="unknown", repair_kind="restart_worker", repairable=False, reason="operator denied repair")
    record = attempt_repair(diagnosis)
    assert record.outcome == "refused"
    assert record.verification == "operator denied repair"


def test_selfrepair_missing_kind_has_explicit_reason():
    diagnosis = Diagnosis(diagnosis_id="missing", symptom="unknown", suspected_cause="unknown")
    record = attempt_repair(diagnosis)
    assert record.outcome == "refused"
    assert record.verification
