"""GRO evaluation: hard gates decide eligibility; relative rank only ranks.

The whole point of the engine is what it refuses to do: a candidate that
fails or cannot prove a required gate is never eligible, no matter how high
its score or how confident its prose; and a group where nothing passes ends
as an explicit no-winner, never as "the best of a bad lot, verified".
"""

from __future__ import annotations

import pytest

from alpha.gro.evaluation import (
    GATE_CRITERIA,
    GATE_EVIDENCE,
    GATE_EXECUTION,
    absolute_score,
    evaluate_candidate,
    evidence_units,
    hard_gates,
    is_eligible,
    rank_group,
    select_candidate,
)
from alpha.gro.models import (
    EVALUATOR_VERSION,
    CandidateEvaluation,
    CandidateOutcome,
    CandidateStatus,
    GateResult,
    GateStatus,
    RiskTier,
    TaskContract,
    TaskType,
)

CRITERION = "tests_passed: pytest"


def _contract(task_type: TaskType = TaskType.CODING, criteria: tuple[str, ...] = (CRITERION,)) -> TaskContract:
    return TaskContract(run_id="gro_x", goal="fix the bug", task_type=task_type, risk_tier=RiskTier.ORDINARY, acceptance_criteria=criteria)


def _completed(candidate_id: str = "cand_01", *, receipts: int = 0, artifacts: tuple[str, ...] = (), bash: tuple[dict, ...] = (), duration_ms: float = 1000.0) -> CandidateOutcome:
    return CandidateOutcome(
        candidate_id=candidate_id,
        status=CandidateStatus.COMPLETED,
        output_text="done",
        tool_receipt_count=receipts,
        artifacts=list(artifacts),
        bash_executions=list(bash),
        duration_ms=duration_ms,
    )


def _verdict(*, holds: int = 0, unchecked: int = 0, failing: int = 0):
    leaves = []
    for index in range(holds):
        leaves.append({"criterion": f"c{index}", "checked": True, "holds": True})
    for index in range(failing):
        leaves.append({"criterion": f"f{index}", "checked": True, "holds": False})
    for index in range(unchecked):
        leaves.append({"criterion": f"u{index}", "checked": False, "holds": False})
    return {"leaves": leaves, "all_hold": holds > 0 and not unchecked and not failing}


class TestHardGates:
    def test_completed_candidate_passes_execution_gate(self):
        gates = hard_gates(_contract(), _completed(), None)
        execution = next(g for g in gates if g.name == GATE_EXECUTION)
        assert execution.status is GateStatus.PASS

    def test_incomplete_candidate_fails_execution_gate(self):
        gates = hard_gates(_contract(), CandidateOutcome(candidate_id="c", status=CandidateStatus.FAILED, error="boom"), None)
        execution = next(g for g in gates if g.name == GATE_EXECUTION)
        assert execution.status is GateStatus.FAIL
        assert "boom" in execution.detail

    def test_declared_criteria_without_verdict_is_unknown_not_pass(self):
        gates = hard_gates(_contract(), _completed(), None)
        criteria = next(g for g in gates if g.name == GATE_CRITERIA)
        assert criteria.status is GateStatus.UNKNOWN
        assert criteria.required is True
        assert is_eligible(gates) is False

    def test_failing_criterion_leaf_fails_the_gate(self):
        gates = hard_gates(_contract(), _completed(), _verdict(failing=1))
        criteria = next(g for g in gates if g.name == GATE_CRITERIA)
        assert criteria.status is GateStatus.FAIL
        assert is_eligible(gates) is False

    def test_unchecked_criterion_keeps_gate_unknown(self):
        gates = hard_gates(_contract(), _completed(), _verdict(unchecked=1))
        criteria = next(g for g in gates if g.name == GATE_CRITERIA)
        assert criteria.status is GateStatus.UNKNOWN
        assert is_eligible(gates) is False

    def test_all_holding_criteria_pass_the_gate(self):
        # Evidence must be harness-measured too: with criteria declared, prose alone
        # fails the evidence gate, so this candidate carries a recorded execution.
        gates = hard_gates(_contract(), _completed(bash=({"command": "pytest"},)), _verdict(holds=2))
        criteria = next(g for g in gates if g.name == GATE_CRITERIA)
        assert criteria.status is GateStatus.PASS
        assert is_eligible(gates) is True

    def test_no_criteria_declared_means_criterion_gate_not_required(self):
        gates = hard_gates(_contract(criteria=()), _completed(), None)
        criteria = next(g for g in gates if g.name == GATE_CRITERIA)
        assert criteria.status is GateStatus.UNKNOWN
        assert criteria.required is False
        assert is_eligible(gates) is True

    def test_bare_claim_cannot_verify_a_declared_criterion(self):
        bare = CandidateOutcome(candidate_id="c", status=CandidateStatus.COMPLETED, output_text="I fixed it, tests pass")
        gates = hard_gates(_contract(), bare, None)
        evidence = next(g for g in gates if g.name == GATE_EVIDENCE)
        assert evidence.status is GateStatus.FAIL
        assert "artifacts" in evidence.detail

    def test_output_alone_is_enough_when_no_criteria_declared(self):
        gates = hard_gates(_contract(criteria=()), _completed(), None)
        evidence = next(g for g in gates if g.name == GATE_EVIDENCE)
        assert evidence.status is GateStatus.PASS


class TestEvidenceUnits:
    def test_units_count_harness_measured_evidence(self):
        outcome = _completed(receipts=3, artifacts=("a.pdf",), bash=({"command": "pytest"},))
        assert evidence_units(outcome) == 5

    def test_prose_is_not_evidence(self):
        assert evidence_units(_completed()) == 0

    def test_negative_receipts_cannot_inflate(self):
        assert evidence_units(_completed(receipts=-5)) == 0


class TestAbsoluteScore:
    def test_ineligible_candidate_has_no_score(self):
        evaluation = evaluate_candidate(_contract(), CandidateOutcome(candidate_id="c", status=CandidateStatus.FAILED), None)
        assert evaluation.eligible is False
        assert evaluation.absolute_score is None

    def test_score_uses_versioned_task_type_weights(self):
        evaluation = evaluate_candidate(_contract(task_type=TaskType.CODING), _completed(bash=({"command": "pytest"},)), _verdict(holds=1))
        assert evaluation.eligible is True
        assert evaluation.absolute_score is not None
        assert 0.0 <= evaluation.absolute_score <= 1.0
        # coverage=1.0 (weight .5), evidence=1/6 (weight .3), efficiency ~0.58 (weight .2)
        assert evaluation.metric_breakdown["weight_normalizer"] == pytest.approx(1.0)

    def test_missing_measurement_drops_out_and_renormalizes(self):
        outcome = _completed(duration_ms=0.0, bash=({"command": "pytest"},))  # no duration -> no efficiency component
        evaluation = evaluate_candidate(_contract(), outcome, _verdict(holds=1))
        assert "efficiency" not in evaluation.metric_breakdown or evaluation.metric_breakdown.get("efficiency") is None
        assert evaluation.metric_breakdown["weight_normalizer"] == pytest.approx(0.8)  # 0.5 coverage + 0.3 evidence

    def test_tokens_never_enter_the_score(self):
        lean = evaluate_candidate(_contract(), _completed(bash=({"command": "pytest"},)), _verdict(holds=1))
        rich_outcome = _completed(bash=({"command": "pytest"},))
        rich_outcome.usage = {"total_tokens": 1_000_000}
        rich = evaluate_candidate(_contract(), rich_outcome, _verdict(holds=1))
        # Tokens are recorded for operators to compare...
        assert rich.metric_breakdown["total_tokens"] == 1_000_000
        assert "total_tokens" not in lean.metric_breakdown
        # ...but the score is identical: no per-task token scale exists, so a
        # token term would be a fabricated metric.
        assert rich.absolute_score == lean.absolute_score
        assert rich.metric_breakdown["weight_normalizer"] == pytest.approx(1.0)


class TestEvaluateCandidate:
    def test_no_criteria_winner_is_labelled_unverified(self):
        evaluation = evaluate_candidate(_contract(criteria=()), _completed(bash=({"command": "pytest"},)), None)
        assert evaluation.eligible is True
        assert "unverified_no_criteria_declared" in evaluation.reason_codes
        assert "eligibility_rests_on_execution_evidence_only" in evaluation.reason_codes

    def test_unreported_usage_is_disclosed(self):
        evaluation = evaluate_candidate(_contract(criteria=()), _completed(), None)
        assert "provider_usage_unreported" in evaluation.reason_codes

    def test_completed_but_ineligible_is_disclosed(self):
        evaluation = evaluate_candidate(_contract(), _completed(), _verdict(failing=1))
        assert "completed_but_ineligible" in evaluation.reason_codes
        assert any(code.startswith("gate_") for code in evaluation.reason_codes)

    def test_evaluation_version_travels(self):
        assert evaluate_candidate(_contract(), _completed(), None).evaluator_version == EVALUATOR_VERSION


class TestRelativeRank:
    def test_rank_orders_only_the_eligible(self):
        eligible_a = evaluate_candidate(_contract(criteria=()), _completed("cand_a", bash=({"command": "pytest"},)), None)
        eligible_b = evaluate_candidate(_contract(criteria=()), _completed("cand_b", bash=({"command": "pytest"}, {"command": "make"})), None)
        ineligible = evaluate_candidate(_contract(), CandidateOutcome(candidate_id="cand_c", status=CandidateStatus.FAILED), None)
        rank_group([eligible_a, eligible_b, ineligible])
        assert ineligible.relative_rank is None
        assert {eligible_a.relative_rank, eligible_b.relative_rank} == {1, 2}

    def test_margin_is_vs_median(self):
        evaluations = [evaluate_candidate(_contract(criteria=()), _completed(f"cand_{i:02d}", bash=tuple({"command": "x"} for _ in range(units)), duration_ms=100.0), None) for i, units in enumerate((1, 3, 5))]
        rank_group(evaluations)
        median = sorted(e.absolute_score for e in evaluations)[1]
        for evaluation in evaluations:
            assert evaluation.relative_margin == pytest.approx(evaluation.absolute_score - median, abs=1e-5)


class TestSelection:
    def _eligible(self, candidate_id: str, units: int, duration_ms: float) -> CandidateEvaluation:
        return evaluate_candidate(
            _contract(criteria=()),
            _completed(candidate_id, bash=tuple({"command": "x"} for _ in range(units)), duration_ms=duration_ms),
            None,
        )

    def test_best_eligible_candidate_wins(self):
        weak = self._eligible("cand_weak", 1, 900.0)
        strong = self._eligible("cand_strong", 6, 100.0)
        result = select_candidate([weak, strong])
        assert result.outcome == "selected"
        assert result.selected_candidate_id == "cand_strong"
        assert result.score_margin_over_runner_up is not None

    def test_all_fail_is_an_explicit_no_winner(self):
        failed = evaluate_candidate(_contract(), CandidateOutcome(candidate_id="cand_01", status=CandidateStatus.FAILED, error="boom"), None)
        timed_out = evaluate_candidate(_contract(), CandidateOutcome(candidate_id="cand_02", status=CandidateStatus.TIMEOUT, error="late"), None)
        result = select_candidate([failed, timed_out])
        assert result.outcome == "no_acceptable_candidate"
        assert result.selected_candidate_id is None
        assert "promoting the highest-scoring failure would fabricate" in result.reason
        assert result.repairable is False  # nothing completed: nothing to repair from

    def test_all_fail_with_a_completed_attempt_is_repairable(self):
        failed = evaluate_candidate(_contract(), CandidateOutcome(candidate_id="cand_01", status=CandidateStatus.FAILED, error="boom"), None)
        completed_unproven = evaluate_candidate(_contract(), _completed("cand_02"), _verdict(failing=1))
        result = select_candidate([failed, completed_unproven])
        assert result.outcome == "no_acceptable_candidate"
        assert result.repairable is True

    def test_ties_break_deterministically_by_evidence_then_latency_then_id(self):
        first = self._eligible("cand_aaa", 2, 100.0)
        second = self._eligible("cand_bbb", 2, 100.0)
        result = select_candidate([first, second])
        assert result.selected_candidate_id == "cand_aaa"  # identical evidence/latency -> stable id order
        faster = self._eligible("cand_ccc", 2, 50.0)
        assert select_candidate([first, faster]).selected_candidate_id == "cand_ccc"

    def test_no_criteria_selection_is_marked_unverified_in_reason(self):
        winner = self._eligible("cand_01", 3, 100.0)
        result = select_candidate([winner])
        assert result.outcome == "selected"
        assert "UNVERIFIED" in result.reason

    def test_ranking_covers_every_candidate(self):
        winner = self._eligible("cand_01", 3, 100.0)
        loser = evaluate_candidate(_contract(), CandidateOutcome(candidate_id="cand_02", status=CandidateStatus.FAILED), None)
        result = select_candidate([loser, winner])
        assert set(result.ranking) == {"cand_01", "cand_02"}


def test_is_eligible_empty_gates_means_no_demands():
    assert is_eligible([]) is True


def test_score_of_ineligible_is_none_even_with_perfect_evidence():
    gates = [GateResult(GATE_CRITERIA, GateStatus.FAIL, "criterion failed")]
    score, _ = absolute_score(_contract(), gates, {"coverage": 1.0, "evidence": 1.0}, {})
    assert score is None
