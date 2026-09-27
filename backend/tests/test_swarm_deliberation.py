"""Adaptive deliberation: sequential stopping and the blocking guards.

Three properties are pinned here because each is a way a consensus system can
look healthy while being wrong:

1. **The sequential test must not manufacture a verdict.**  A small, undecided
   panel reports ``unresolved``, never ``approved`` — the honest answer when
   the sample is thin is "not enough signal", not a confident yes.
2. **Guards may only block.**  Domination, sycophancy and a flat disagreement
   between the sequential test and the explicit policy can downgrade an
   approval to ``manual_review``; nothing can raise a refusal to an approval.
3. **Round savings are a count, not a token estimate.**  No round-level token
   meter exists, so the report carries rounds and votes, never converted cost.

The log-likelihood arithmetic is asserted against Wald's bounds computed from
the default policy (``p0=0.65``, ``p1=0.85``, ``A=ln(0.9/0.05)``,
``B=ln(0.1/0.95)``), so a regression in the test itself would fail the
expected verdict rather than quietly move the numbers.
"""

from __future__ import annotations

import math

import pytest

from alpha.swarm.aggregator import SwarmAggregator
from alpha.swarm.consensus import evaluate_consensus, votes_from_evidence
from alpha.swarm.deliberation import (
    DeliberationPolicy,
    rounds_from_evidence,
    run_deliberation,
)


def _vote(voter: str, stance: str, *, weight: float = 1.0, evidence: tuple[str, ...] = ("ref",)) -> dict:
    return {"voter": voter, "stance": stance, "weight": weight, "evidence": list(evidence)}


def _approvals(count: int) -> list[dict]:
    return [_vote(f"v{i}", "approve") for i in range(count)]


def _rejections(count: int) -> list[dict]:
    return [_vote(f"v{i}", "reject") for i in range(count)]


# --- sequential test -------------------------------------------------------


def test_unanimous_approvals_cross_the_upper_boundary():
    policy = DeliberationPolicy()
    # 11 x ln(0.85/0.65) = 2.951 >= ln(0.9/0.05) = 2.890
    report = run_deliberation([_approvals(11)], policy)
    assert report.status == "approved"
    assert report.approved is True
    assert report.stop_reason == "sprt_approved"
    assert report.stopped_early is True
    assert report.llr >= policy.upper_bound
    assert report.rounds_used == 1
    assert report.guards == ()


def test_ten_approvals_are_not_yet_decisive():
    policy = DeliberationPolicy()
    # 10 x 0.268 = 2.683 < 2.890: one more vote, not a verdict.
    report = run_deliberation([_approvals(10)], policy)
    assert report.status == "unresolved"
    assert report.approved is False
    assert report.stop_reason == "max_rounds"
    assert report.llr < policy.upper_bound


def test_decisive_rejection_crosses_the_lower_boundary():
    policy = DeliberationPolicy()
    report = run_deliberation([_rejections(4)], policy)
    assert report.status == "rejected"
    assert report.approved is False
    assert report.stop_reason == "sprt_rejected"
    assert report.llr <= policy.lower_bound


def test_thin_unanimous_panel_reports_unresolved_not_approval():
    """Three approvals is not evidence of a 0.75 approval rate; say so."""
    report = run_deliberation([_approvals(3)])
    assert report.status == "unresolved"
    assert report.approved is False
    assert report.rounds_used == 1
    assert "never reached a boundary" in report.explanation


def test_mixed_panel_stays_inside_both_boundaries():
    report = run_deliberation([_approvals(5) + _rejections(1)])
    policy = DeliberationPolicy()
    assert report.status == "unresolved"
    assert policy.lower_bound < report.llr < policy.upper_bound


def test_early_stop_saves_later_rounds():
    rounds = [[_vote(f"v{i}", "approve") for i in range(3)] for _ in range(5)]
    report = run_deliberation(rounds, DeliberationPolicy(max_rounds=5))
    # Cumulative 12 approvals at round 4; round 5 is never consumed.
    assert report.stop_reason == "sprt_approved"
    assert report.rounds_used == 4
    assert len(report.rounds) == 4
    assert report.to_dict()["stopped_early"] is True
    assert 5 - report.rounds_used == 1


def test_round_cap_is_enforced_even_when_the_caller_supplies_more():
    rounds = [[_vote("shared", "approve")] for _ in range(10)]
    report = run_deliberation(rounds, DeliberationPolicy(max_rounds=5))
    assert report.rounds_used == 5
    assert report.status == "unresolved"
    # Extra polls beyond the cap must not buy extra deliberation.
    assert report.votes_used == 5


def test_single_vote_cannot_meet_the_minimum():
    report = run_deliberation([[_vote("only", "approve")]])
    assert report.status == "unresolved"
    assert report.stop_reason == "insufficient_votes"
    assert "insufficient_votes" in report.guards


def test_no_votes_yields_no_verdict():
    report = run_deliberation([])
    assert report.status == "unresolved"
    assert report.approved is False
    assert report.stop_reason == "no_votes"
    assert "insufficient_votes" in report.guards
    assert "cannot produce a verdict" in report.explanation


# --- guards only block -----------------------------------------------------


def test_uneven_weight_is_detected_as_domination():
    votes = [_vote("heavy", "approve", weight=10.0), _vote("a", "reject"), _vote("b", "reject")]
    report = run_deliberation([votes])
    # (10/12) * 3 = 2.5x a fair share, above the 2.0 threshold.
    assert report.domination_ratio > 2.0
    assert "domination" in report.guards


def test_an_even_two_voter_panel_is_not_flagged_as_domination():
    """Two equal voters always hold 50% each; a raw share would flag every healthy panel."""
    report = run_deliberation([[_vote("a", "approve"), _vote("b", "approve")]])
    assert report.domination_ratio == 1.0
    assert "domination" not in report.guards


def test_sycophancy_is_detected_when_voters_flip_without_new_evidence():
    first = [_vote("a", "approve", evidence=("r1",)), _vote("b", "reject", evidence=("r1",)), _vote("c", "reject", evidence=("r1",))]
    second = [_vote("a", "reject", evidence=("r1",)), _vote("b", "reject", evidence=("r1",)), _vote("c", "reject", evidence=("r1",))]
    report = run_deliberation([first, second])
    assert report.sycophancy_rate == 1.0
    assert "sycophancy" in report.guards


def test_a_flip_supported_by_new_evidence_is_not_sycophancy():
    first = [_vote("a", "approve", evidence=("r1",)), _vote("b", "reject", evidence=("r1",)), _vote("c", "reject", evidence=("r1",))]
    second = [_vote("a", "reject", evidence=("r1", "r2")), _vote("b", "reject", evidence=("r1",)), _vote("c", "reject", evidence=("r1",))]
    report = run_deliberation([first, second])
    assert report.sycophancy_rate == 0.0
    assert "sycophancy" not in report.guards


def test_evidence_free_approvals_block_when_the_policy_demands_evidence():
    votes = [_vote("a", "approve", evidence=()), _vote("b", "approve", evidence=()), _vote("c", "approve", evidence=())]
    report = run_deliberation([votes], DeliberationPolicy(require_evidence=True))
    assert "evidence_free_approval" in report.guards


# --- aggregator: downgrade only -------------------------------------------


def test_deliberation_never_rescues_an_explicit_refusal():
    evidence = _approvals(11)
    result = {"approved": False, "status": "rejected", "reason": "explicit policy refused"}
    merged = SwarmAggregator._apply_deliberation(result, evidence)
    assert merged["approved"] is False
    assert merged["status"] == "rejected"
    # The full report is still attached so the disagreement is visible.
    assert merged["deliberation"]["status"] == "approved"


def test_deliberation_leaves_a_clean_approval_alone():
    evidence = [_vote("a", "approve"), _vote("b", "approve")]
    result = {"approved": True, "status": "approved", "reason": "quorum and weighted threshold met"}
    merged = SwarmAggregator._apply_deliberation(result, evidence)
    assert merged["approved"] is True
    assert merged["status"] == "approved"
    assert merged["deliberation"]["status"] == "unresolved"
    assert merged["deliberation"]["guards"] == []
    assert "blocked_by" not in merged


def test_domination_guard_downgrades_a_clean_approval():
    evidence = [_vote("heavy", "approve", weight=10.0), _vote("a", "reject"), _vote("b", "reject")]
    votes = votes_from_evidence(evidence)
    assert evaluate_consensus(votes).approved is True

    merged = SwarmAggregator._apply_deliberation({"approved": True, "status": "approved", "reason": "threshold met"}, evidence)
    assert merged["approved"] is False
    assert merged["status"] == "manual_review"
    assert merged["blocked_by"] == ["domination"]
    assert "blocked it" in merged["reason"]


def test_sequential_test_disagreement_blocks_defensively():
    """Invariant test: when the two methods disagree, the answer reaches a human.

    The explicit policy and the sequential test agree in practice, so this
    feeds an inconsistent pair deliberately to pin the direction of the merge —
    a disagreement must never resolve silently in either direction.
    """
    evidence = _rejections(4)
    merged = SwarmAggregator._apply_deliberation({"approved": True, "status": "approved", "reason": "threshold met"}, evidence)
    assert merged["approved"] is False
    assert "sequential_test_disagreement" in merged["blocked_by"]
    assert merged["deliberation"]["status"] == "rejected"


def test_malformed_deliberation_discloses_instead_of_raising():
    merged = SwarmAggregator._apply_deliberation({"approved": True, "status": "approved"}, [{"stance": "approve", "weight": "not-a-number"}])
    assert merged["approved"] is True
    assert merged["deliberation"]["status"] == "error"
    assert merged["deliberation"]["reason"]


# --- evidence -> rounds ----------------------------------------------------


def test_missing_round_structure_is_never_fabricated():
    evidence = _approvals(4)
    rounds = rounds_from_evidence(evidence)
    assert len(rounds) == 1
    assert len(rounds[0]) == 4


def test_explicit_round_keys_group_in_ascending_order():
    evidence = [{"round": 2, "voter": "b", "stance": "approve"}, {"round": 1, "voter": "a", "stance": "approve"}]
    rounds = rounds_from_evidence(evidence)
    assert len(rounds) == 2
    assert rounds[0][0].voter == "a"
    assert rounds[1][0].voter == "b"


def test_round_extraction_sees_exactly_the_votes_the_explicit_policy_saw():
    evidence = [
        {"kind": "consensus_vote", "voter": "a", "stance": "approve", "evidence": ["r1"]},
        {"kind": "note", "text": "not a vote"},
        {"voter": "b", "stance": "reject", "evidence": ["r2"]},
    ]
    rounds = rounds_from_evidence(evidence)
    assert len(rounds) == 1
    assert len(rounds[0]) == len(votes_from_evidence(evidence)) == 2


def test_anonymous_votes_are_counted_but_collapse_nothing():
    """Unknown identity must not silently merge two distinct voters into one."""
    evidence = [{"stance": "approve", "evidence": ["r1"]}, {"stance": "approve", "evidence": ["r2"]}]
    rounds = rounds_from_evidence(evidence)
    voters = {vote.voter for vote in rounds[0]}
    assert len(voters) == 2
    assert run_deliberation(rounds).votes_used == 2


def test_non_mapping_evidence_rows_are_ignored():
    rounds = rounds_from_evidence(["garbage", None, 42])  # type: ignore[list-item]
    assert rounds == []


# --- policy validation -----------------------------------------------------


def test_policy_rejects_impossible_parameters():
    with pytest.raises(ValueError):
        DeliberationPolicy(target_approve_rate=1.5)
    with pytest.raises(ValueError):
        DeliberationPolicy(target_approve_rate=0.95, indifference=0.10)  # p1 >= 1.0
    with pytest.raises(ValueError):
        DeliberationPolicy(alpha=0.0)
    with pytest.raises(ValueError):
        DeliberationPolicy(max_rounds=0)
    with pytest.raises(ValueError):
        DeliberationPolicy(min_votes=0)
    with pytest.raises(ValueError):
        DeliberationPolicy(domination_threshold=0.5)  # below a fair share
    with pytest.raises(ValueError):
        DeliberationPolicy(sycophancy_threshold=1.5)


def test_report_serialises_bounds_and_carries_no_cost_conversion():
    payload = run_deliberation([_approvals(11)]).to_dict()
    policy = DeliberationPolicy()
    assert payload["policy"]["upper_bound"] == pytest.approx(policy.upper_bound, abs=1e-6)
    assert payload["policy"]["lower_bound"] == pytest.approx(policy.lower_bound, abs=1e-6)
    assert math.isclose(payload["llr"], 11 * math.log(0.85 / 0.65), rel_tol=1e-6)
    assert not any("token" in key.lower() or "cost" in key.lower() for key in payload)
