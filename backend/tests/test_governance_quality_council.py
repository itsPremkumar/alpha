
from agent_workspace.governance.council import (
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
    assert verdict.weighted_score > 0.8


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
