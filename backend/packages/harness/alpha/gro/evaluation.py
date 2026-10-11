"""GRO evaluation: hard gates first, absolute score second, relative rank third.

The order is the design (plan section 7). The candidate group might be
uniformly poor, so the winner is never chosen by z-score, majority vote,
confidence or verbosity: **absolute gates decide eligibility, relative
comparison only ranks the eligible.** A candidate that fails a required gate
is removed from promotion eligibility no matter how high its score; a group
where nothing passes ends as an explicit ``no_acceptable_candidate`` (with a
repair hint), never as "the best of a bad lot, verified".

Eligibility is exactly "every *required* gate passed" (see
``GateResult.required``): ``UNKNOWN`` is never collapsed into ``PASS``, so a
criterion the deterministic checker could not decide keeps its candidate
ineligible instead of silently promoting an unverified result.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from alpha.gro.models import EVALUATOR_VERSION, CandidateEvaluation, CandidateOutcome, CandidateStatus, GateResult, GateStatus, SelectionResult, TaskContract, TaskType

GATE_EXECUTION = "execution_completed"
GATE_CRITERIA = "acceptance_criteria_hold"
GATE_EVIDENCE = "evidence_present"

#: Evidence units needed to saturate the evidence component of the score.
_EVIDENCE_SATURATION = 6.0

#: Versioned per-task-category score weights (plan section 7.3: one hard-coded
#: global score across coding/research/planning is explicitly rejected). A
#: component whose input was not measured drops out and the remaining weights
#: are renormalized — an unmeasured component is never fabricated as 0.
_SCORE_WEIGHTS: dict[TaskType, dict[str, float]] = {
    TaskType.CODING: {"coverage": 0.50, "evidence": 0.30, "efficiency": 0.20},
    TaskType.RESEARCH: {"coverage": 0.50, "evidence": 0.35, "efficiency": 0.15},
    TaskType.PLANNING: {"coverage": 0.50, "evidence": 0.25, "efficiency": 0.25},
    TaskType.GENERAL: {"coverage": 0.50, "evidence": 0.25, "efficiency": 0.25},
}


def evidence_units(outcome: CandidateOutcome) -> int:
    """Harness-measured evidence: artifacts, tool receipts, recorded executions.

    Receipts and bash executions are harvested by the harness, not claimed by
    the candidate, so this count cannot be inflated by confident prose.
    """

    return len(outcome.artifacts) + max(0, outcome.tool_receipt_count) + len(outcome.bash_executions)


def is_eligible(gates: Sequence[GateResult]) -> bool:
    """Eligible == every required gate passed. Unknown required gates fail closed."""

    return all(gate.status is GateStatus.PASS for gate in gates if gate.required)


def hard_gates(contract: TaskContract, outcome: CandidateOutcome, verdict: Mapping[str, Any] | None) -> list[GateResult]:
    """Evaluate the hard gates for one candidate outcome."""

    return [_execution_gate(outcome), _criteria_gate(contract, verdict), _evidence_gate(contract, outcome)]


def _execution_gate(outcome: CandidateOutcome) -> GateResult:
    if outcome.status is CandidateStatus.COMPLETED:
        return GateResult(GATE_EXECUTION, GateStatus.PASS, f"candidate completed in {outcome.duration_ms / 1000.0:.1f}s")
    detail = {
        CandidateStatus.TIMEOUT: "candidate exceeded its wall-clock bound; a late result is fenced, never adopted",
        CandidateStatus.CANCELLED: "candidate was cancelled before completion",
        CandidateStatus.NOT_STARTED: f"candidate never started: {outcome.error or 'not dispatched'}",
        CandidateStatus.FAILED: outcome.error or "candidate execution failed",
    }.get(outcome.status, f"candidate status is {outcome.status.value}")
    return GateResult(GATE_EXECUTION, GateStatus.FAIL, detail)


def _criteria_gate(contract: TaskContract, verdict: Mapping[str, Any] | None) -> GateResult:
    if not contract.acceptance_criteria:
        # Not a required gate for this contract: there is nothing to check.
        return GateResult(GATE_CRITERIA, GateStatus.UNKNOWN, "no acceptance criteria were declared; the criterion gate is not required for this contract", required=False)
    if not verdict:
        return GateResult(GATE_CRITERIA, GateStatus.UNKNOWN, "no acceptance verdict could be produced (checker unavailable or returned nothing); unknown is never a pass")
    leaves = [leaf for leaf in verdict.get("leaves", []) or [] if isinstance(leaf, Mapping)]
    failing = [leaf for leaf in leaves if leaf.get("checked") and not leaf.get("holds")]
    unchecked = [leaf for leaf in leaves if not leaf.get("checked")]
    if failing:
        names = "; ".join(str(leaf.get("criterion", ""))[:120] for leaf in failing[:3])
        return GateResult(GATE_CRITERIA, GateStatus.FAIL, f"{len(failing)} required criterion/criteria did not hold: {names}")
    if unchecked:
        names = "; ".join(str(leaf.get("criterion", ""))[:120] for leaf in unchecked[:3])
        return GateResult(GATE_CRITERIA, GateStatus.UNKNOWN, f"{len(unchecked)} criterion/criteria could not be deterministically checked: {names}")
    return GateResult(GATE_CRITERIA, GateStatus.PASS, f"all {len(leaves)} required criteria hold")


def _evidence_gate(contract: TaskContract, outcome: CandidateOutcome) -> GateResult:
    if outcome.status is not CandidateStatus.COMPLETED:
        return GateResult(GATE_EVIDENCE, GateStatus.FAIL, "an incomplete candidate has no deliverable evidence")
    units = evidence_units(outcome)
    if units > 0:
        return GateResult(
            GATE_EVIDENCE,
            GateStatus.PASS,
            f"{units} harness-measured evidence units (artifacts={len(outcome.artifacts)}, receipts={outcome.tool_receipt_count}, executions={len(outcome.bash_executions)})",
        )
    if outcome.output_text.strip():
        if contract.acceptance_criteria:
            return GateResult(GATE_EVIDENCE, GateStatus.FAIL, "criteria were declared but the result carries no artifacts, tool receipts or recorded executions; a bare claim cannot verify a criterion")
        return GateResult(GATE_EVIDENCE, GateStatus.PASS, "non-empty completed output; no acceptance criteria were declared, so the output is the only available evidence")
    return GateResult(GATE_EVIDENCE, GateStatus.FAIL, "candidate completed with empty output")


def _criteria_holds(verdict: Mapping[str, Any] | None) -> int:
    """Criteria proven to hold (checked AND holds) in the recorded verdict."""

    if not verdict:
        return 0
    return sum(1 for leaf in verdict.get("leaves", []) or [] if isinstance(leaf, Mapping) and leaf.get("checked") and leaf.get("holds"))


def _score_components(contract: TaskContract, outcome: CandidateOutcome, verdict: Mapping[str, Any] | None, *, candidate_timeout_seconds: float) -> tuple[dict[str, float], dict[str, float]]:
    """Return (available components, full diagnostic breakdown)."""

    units = evidence_units(outcome)
    breakdown: dict[str, float] = {}
    components: dict[str, float] = {}

    total = len(contract.acceptance_criteria)
    holds = _criteria_holds(verdict)
    coverage = (holds / total) if total else 1.0
    breakdown["criteria_total"] = float(total)
    breakdown["criteria_hold"] = float(holds)
    breakdown["coverage"] = coverage
    components["coverage"] = coverage

    evidence = min(1.0, units / _EVIDENCE_SATURATION)
    breakdown["evidence_units"] = float(units)
    breakdown["evidence"] = evidence
    components["evidence"] = evidence

    if outcome.duration_ms > 0 and candidate_timeout_seconds > 0:
        efficiency = max(0.0, min(1.0, 1.0 - (outcome.duration_ms / 1000.0) / candidate_timeout_seconds))
        breakdown["duration_seconds"] = outcome.duration_ms / 1000.0
        breakdown["efficiency"] = efficiency
        components["efficiency"] = efficiency

    if outcome.usage:
        # Diagnostic only: no per-task absolute token scale exists, so tokens
        # never enter the score — they are recorded for operators to compare.
        breakdown["total_tokens"] = float(outcome.usage.get("total_tokens", 0))
    return components, breakdown


def absolute_score(contract: TaskContract, gates: Sequence[GateResult], components: dict[str, float], breakdown: dict[str, float]) -> tuple[float | None, dict[str, float]]:
    """Absolute quality score after gates; ``None`` when the candidate is ineligible.

    The score is a weighted mean over the *available* components, normalized by
    the weights actually used, so a missing measurement shrinks the evidence
    considered rather than silently scoring zero.
    """

    if not is_eligible(gates):
        return None, breakdown
    weights = _SCORE_WEIGHTS[contract.task_type]
    used = {name: weight for name, weight in weights.items() if name in components}
    total_weight = sum(used.values())
    if total_weight <= 0:
        return None, breakdown
    score = sum(used[name] * components[name] for name in used) / total_weight
    breakdown["weight_normalizer"] = total_weight
    return round(score, 6), breakdown


def evaluate_candidate(contract: TaskContract, outcome: CandidateOutcome, verdict: Mapping[str, Any] | None, *, candidate_timeout_seconds: float = 240.0) -> CandidateEvaluation:
    """One candidate's full evaluation record: gates, eligibility, score."""

    gates = hard_gates(contract, outcome, verdict)
    components, breakdown = _score_components(contract, outcome, verdict, candidate_timeout_seconds=candidate_timeout_seconds)
    score, breakdown = absolute_score(contract, gates, components, breakdown)
    eligible = is_eligible(gates)
    reasons: list[str] = [f"gate_{gate.name}_{gate.status.value}" for gate in gates if gate.required and gate.status is not GateStatus.PASS]
    if not contract.acceptance_criteria:
        reasons.append("unverified_no_criteria_declared")
    if eligible and not contract.acceptance_criteria:
        reasons.append("eligibility_rests_on_execution_evidence_only")
    if not eligible and outcome.status is CandidateStatus.COMPLETED:
        reasons.append("completed_but_ineligible")
    if outcome.usage is None and outcome.status is CandidateStatus.COMPLETED:
        reasons.append("provider_usage_unreported")
    return CandidateEvaluation(
        candidate_id=outcome.candidate_id,
        evaluator_version=EVALUATOR_VERSION,
        gates=gates,
        eligible=eligible,
        absolute_score=score,
        metric_breakdown=breakdown,
        reason_codes=reasons,
        evidence_units=evidence_units(outcome),
    )


def rank_group(evaluations: Sequence[CandidateEvaluation]) -> Sequence[CandidateEvaluation]:
    """Attach diagnostic relative rank and margin among ELIGIBLE candidates only.

    ``R_i = Q_i - median(Q)`` over the eligible set (plan section 7.4). The
    rank is written on the evaluation as a diagnostic; it never changes
    ``eligible`` and never touches an ineligible candidate's score.
    """

    eligible = [e for e in evaluations if e.eligible and e.absolute_score is not None]
    if not eligible:
        return evaluations
    scores = [e.absolute_score for e in eligible if e.absolute_score is not None]
    median = statistics.median(scores)
    ordered = sorted(eligible, key=_selection_key)
    for position, evaluation in enumerate(ordered, start=1):
        evaluation.relative_rank = position
        if evaluation.absolute_score is not None:
            evaluation.relative_margin = round(evaluation.absolute_score - median, 6)
    return evaluations


def _selection_key(evaluation: CandidateEvaluation) -> tuple[float, int, float, str]:
    """Deterministic order: absolute score, then evidence, then latency, then id.

    Mirrors the plan's tie handling (section 7.6): stronger external
    verification first, then lower cost/latency, then a stable id so the same
    evidence always selects the same winner.
    """

    score = evaluation.absolute_score if evaluation.absolute_score is not None else -1.0
    duration = evaluation.metric_breakdown.get("duration_seconds", float("inf"))
    return (-score, -evaluation.evidence_units, duration, evaluation.candidate_id)


def select_candidate(evaluations: Sequence[CandidateEvaluation]) -> SelectionResult:
    """Pick the winner among gate-eligible candidates, or say why there is none."""

    ranked = sorted(evaluations, key=_selection_key)
    eligible = [e for e in ranked if e.eligible]
    ranking = [e.candidate_id for e in ranked]
    if not eligible:
        repairable = any(_gate_status(e, GATE_EXECUTION) is GateStatus.PASS for e in evaluations)
        reason = "no candidate passed every required gate; promoting the highest-scoring failure would fabricate a verified result"
        return SelectionResult(outcome="no_acceptable_candidate", reason=reason, ranking=ranking, repairable=repairable)
    best = eligible[0]
    runner_up = eligible[1] if len(eligible) > 1 else None
    margin = None
    if runner_up is not None and best.absolute_score is not None and runner_up.absolute_score is not None:
        margin = round(best.absolute_score - runner_up.absolute_score, 6)
    reason = f"selected after hard gates on absolute score ({best.absolute_score}); relative rank {best.relative_rank or 1} is diagnostic only"
    if margin is not None:
        reason += f"; margin over runner-up {margin}"
    if not best.metric_breakdown.get("criteria_total"):
        reason += "; UNVERIFIED: no acceptance criteria were declared, so selection rests on execution evidence and cost only"
    return SelectionResult(outcome="selected", selected_candidate_id=best.candidate_id, reason=reason, ranking=ranking, score_margin_over_runner_up=margin)


def _gate_status(evaluation: CandidateEvaluation, name: str) -> GateStatus | None:
    for gate in evaluation.gates:
        if gate.name == name:
            return gate.status
    return None
