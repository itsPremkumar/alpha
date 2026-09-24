"""Tests for Epistemic Belief Engine and Bayesian Calibration."""

import json

from alpha.epistemics.engine import EpistemicBeliefEngine, get_epistemic_engine
from alpha.epistemics.models import EpistemicStatus
from alpha.tools.builtins.epistemic_belief_tool import evaluate_epistemic_claim


def test_seeded_baseline_claims_are_unverified_hypotheses():
    """Seeds must never ship as FACT with fabricated evidence receipts.

    Prose registered at startup has no evidence behind it, so every seeded
    claim starts as HYPOTHESIS at the neutral 0.5 prior with an EMPTY
    evidence ledger. FACT badges and above-neutral confidence may only
    follow real evidence registered through ``update_with_evidence()`` —
    the old seeds carried invented receipts ("All 29 unit and integration
    tests passed in 27.21s", "CI green receipt verified in workspace")
    that backed FACT@0.98/0.92 badges in render_belief_summary() and the
    War Room ``epistemic_claims`` payload.
    """
    engine = get_epistemic_engine("test_seed_honesty")
    claims = engine.list_all()
    assert len(claims) == 4

    for claim in claims:
        assert claim.status == EpistemicStatus.HYPOTHESIS, claim.text
        assert claim.confidence == 0.5, claim.text
        assert claim.bayesian_prior == 0.5, claim.text
        assert claim.bayesian_posterior == 0.5, claim.text
        assert claim.supporting_evidence == [], claim.text
        assert claim.contradicting_evidence == [], claim.text
        assert claim.is_verified is False, claim.text

    # The fabricated CI receipts are gone for good: no evidence of any kind
    # is registered with the seeds.
    all_evidence = [e for c in claims for e in (c.supporting_evidence + c.contradicting_evidence)]
    assert all_evidence == []
    assert not any("29 unit" in e for e in all_evidence)
    assert not any("CI green" in e for e in all_evidence)

    # A verification METHOD naming how the claim COULD be checked is fine
    # on a hypothesis; receipts are not.
    core_test = next(c for c in claims if c.text.startswith("Core test suite"))
    assert core_test.verification_method == "pytest backend/tests -v"

    # Every seed is disclosed as unverified, and the rendered ledger shows
    # hypothesis badges instead of FACT badges.
    unverified = engine.get_unverified_assumptions()
    assert {c.claim_id for c in unverified} == {c.claim_id for c in claims}
    summary = engine.render_belief_summary()
    assert "✅ FACT" not in summary
    assert summary.count("[HYPOTHESIS]") == 4
    assert summary.count("(Conf: 50%)") == 4


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
