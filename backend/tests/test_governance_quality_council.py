
from alpha.governance.council import (
    QualityCouncil,
    RiskTier,
    VoteVerdict,
)


def test_council_approval_on_clean_artifact():
    council = QualityCouncil()
    clean_code = '''
def add(a: int, b: int) -> int:
    """Add two integers safely."""
    try:
        return a + b
    except Exception as e:
        raise ValueError(f"Addition failed: {e}")
'''
    verdict = council.deliberate(
        artifact_name="math_utils.py",
        content=clean_code,
        risk_tier=RiskTier.TIER_2_STANDARD,
        metadata={"test_passed": True, "exit_code": 0, "has_tests": True},
    )

    assert verdict.passed is True
    assert verdict.approvals_count >= 3
    assert verdict.rejections_count == 0

    # Honest behavior (replaces the old `weighted_score > 0.8` pin, which only
    # pinned a fabricated heuristic constant): the aggregate must be disclosed
    # as a heuristic with its formula/baseline, and stay within range.
    payload = verdict.to_dict()
    assert 0.0 <= payload["weighted_score"] <= 1.0
    assert payload["weighted_score_method"] == "heuristic"
    assert "approve confidences" in payload["weighted_score_formula"]
    assert payload["confidence_baseline_disclosed"] is True
    assert payload["confidence_note"]
    for vote in payload["votes"]:
        assert vote["confidence_method"] in {"evidence", "heuristic", "unverified"}
        assert vote["confidence_note"]
        assert 0.0 <= vote["confidence"] <= 1.0


def test_council_rejection_on_security_hazard():
    council = QualityCouncil()
    malicious_script = '''
import os
def wipe_system():
    os.system("rm -rf /tmp/test_dir")
'''
    verdict = council.deliberate(
        artifact_name="cleanup.py",
        content=malicious_script,
        risk_tier=RiskTier.TIER_2_STANDARD,
        metadata={"test_passed": True, "exit_code": 0},
    )

    assert verdict.passed is False
    assert any("Destructive recursive delete" in c for c in verdict.dissenting_concerns)
    # Security reviewer vetoed
    sec_vote = next(v for v in verdict.votes if v.role == "SecurityReviewer")
    assert sec_vote.verdict == VoteVerdict.REJECT
    # The vetoing confidence is a disclosed heuristic, not a measured probability.
    assert sec_vote.confidence_method == "heuristic"
    assert "not a" in sec_vote.confidence_note or "Not a" in sec_vote.confidence_note


def test_council_rejection_on_invariant_failure():
    council = QualityCouncil()
    code = 'def compute(): return 42'
    # Simulated failing test / non-zero exit code
    verdict = council.deliberate(
        artifact_name="compute.py",
        content=code,
        risk_tier=RiskTier.TIER_1_CRITICAL,
        metadata={"test_passed": False, "exit_code": 1},
    )

    assert verdict.passed is False
    inv_vote = next(v for v in verdict.votes if v.role == "InvariantVerifier")
    assert inv_vote.verdict == VoteVerdict.REJECT
    # Rejection is grounded in the evidence the caller actually supplied.
    assert inv_vote.confidence_method == "evidence"
    assert "test_passed=False" in inv_vote.confidence_note


def test_council_without_execution_evidence_reports_unverified_baseline():
    council = QualityCouncil()
    content = '''
def run() -> int:
    """Run the pipeline."""
    try:
        return 1
    except Exception:
        raise
'''
    # No test results / exit code were actually supplied: the Invariant
    # Verifier must hold the neutral unverified baseline and say so instead of
    # fabricating a "verified" confidence.
    verdict = council.deliberate(
        artifact_name="run.py",
        content=content,
        risk_tier=RiskTier.TIER_2_STANDARD,
        metadata={},
    )
    inv_vote = next(v for v in verdict.votes if v.role == "InvariantVerifier")
    assert inv_vote.confidence == 0.5
    assert inv_vote.confidence_method == "unverified"
    assert "baseline" in inv_vote.confidence_note

    # With real evidence supplied, the same vote discloses the evidence method
    # and names the actual signals it used.
    verdict_with_evidence = council.deliberate(
        artifact_name="run.py",
        content=content,
        risk_tier=RiskTier.TIER_2_STANDARD,
        metadata={"test_passed": True, "exit_code": 0},
    )
    inv_vote_evidence = next(
        v for v in verdict_with_evidence.votes if v.role == "InvariantVerifier"
    )
    assert inv_vote_evidence.confidence_method == "evidence"
    assert "test_passed=True" in inv_vote_evidence.confidence_note
    assert "exit_code=0" in inv_vote_evidence.confidence_note


def test_council_risk_tiers_quorum_thresholds():
    council = QualityCouncil()
    # Incomplete placeholder code (causes Critic to reject/conditional)
    draft_content = 'def work():\n    # TODO: implement later\n    # FIXME: broken'
    
    # Under Tier 1 Critical: requires 4/5 approvals -> must reject
    v_tier1 = council.deliberate(
        artifact_name="draft.py",
        content=draft_content,
        risk_tier=RiskTier.TIER_1_CRITICAL,
        metadata={"test_passed": True, "exit_code": 0},
    )
    assert v_tier1.passed is False

    # Under Tier 3 Low: requires only 2/5 approvals
    v_tier3 = council.deliberate(
        artifact_name="draft.py",
        content=draft_content,
        risk_tier=RiskTier.TIER_3_LOW,
        metadata={"test_passed": True, "exit_code": 0},
    )
    assert v_tier3.quorum_required == 2
