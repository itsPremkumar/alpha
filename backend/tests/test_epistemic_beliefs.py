"""Tests for Epistemic Belief Engine and Bayesian Calibration."""

import json

from agent_workspace.epistemics.engine import EpistemicBeliefEngine
from agent_workspace.epistemics.models import EpistemicStatus
from agent_workspace.tools.builtins.epistemic_belief_tool import evaluate_epistemic_claim


def test_epistemic_claim_registration():
    engine = EpistemicBeliefEngine()
    claim = engine.register_claim(
        text="The test failure is caused by an unhandled KeyError in config parser",
        status=EpistemicStatus.HYPOTHESIS,
        prior_confidence=0.5,
        falsification_test="Running parser with empty dictionary raises KeyError",
    )
    assert claim.claim_id is not None
    assert claim.confidence == 0.5
    assert claim.bayesian_posterior == 0.5
    assert not claim.is_verified


def test_bayesian_evidence_updating():
    engine = EpistemicBeliefEngine()
    claim = engine.register_claim(
        text="Memory leak is caused by unclosed HTTP client sessions",
        prior_confidence=0.5,
    )

    # 1. Provide first supporting evidence
    claim = engine.update_with_evidence(
        claim_id=claim.claim_id,
        evidence="Found 45 unclosed ClientSession instances in heap dump",
        is_supporting=True,
    )
    assert claim.bayesian_posterior > 0.5

    # 2. Provide second supporting evidence (should cross 0.90 and promote to FACT)
    claim = engine.update_with_evidence(
        claim_id=claim.claim_id,
        evidence="Closing sessions in finally block eliminated memory growth",
        is_supporting=True,
    )
    assert claim.bayesian_posterior >= 0.90
    assert claim.status == EpistemicStatus.FACT
    assert claim.is_verified


def test_bayesian_refutation():
    engine = EpistemicBeliefEngine()
    claim = engine.register_claim(
        text="Port 8000 is occupied by a runaway daemon",
        prior_confidence=0.5,
    )

    # Contradicting evidence: lsof shows port 8000 is completely free
    claim = engine.update_with_evidence(
        claim_id=claim.claim_id,
        evidence="netstat -tuln shows port 8000 is unused",
        is_supporting=False,
    )
    assert claim.bayesian_posterior < 0.5


def test_evaluate_epistemic_claim_tool():
    # 1. Register claim
    reg_str = evaluate_epistemic_claim.invoke({
        "action": "register",
        "claim_text": "Disk space exhaustion caused build failure",
        "falsification_test": "df -h shows free space > 10GB",
    })
    reg_data = json.loads(reg_str)
    assert reg_data["status"] == "registered"
    claim_id = reg_data["claim_id"]

    # 2. Update claim with evidence
    upd_str = evaluate_epistemic_claim.invoke({
        "action": "update",
        "claim_id": claim_id,
        "evidence": "df -h shows 0% available on root mount",
        "is_supporting": True,
    })
    upd_data = json.loads(upd_str)
    assert upd_data["status"] == "updated"
    assert upd_data["posterior_confidence"] > 0.5

    # 3. Summary
    sum_str = evaluate_epistemic_claim.invoke({"action": "summary"})
    sum_data = json.loads(sum_str)
    assert sum_data["active_claims_count"] > 0
    assert "Epistemic Belief" in sum_data["markdown_summary"]
