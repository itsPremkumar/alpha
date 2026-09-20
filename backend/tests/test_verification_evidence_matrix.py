
from alpha.verification.evidence import (
    ContradictionDetector,
    EvidenceMatrix,
    FinishFirstAuditor,
    ProofType,
    VerificationProof,
)


def test_evidence_matrix_record_and_attach():
    matrix = EvidenceMatrix()
    claim = matrix.record_claim(
        statement="Unit test suite passes with 100% green",
        target_path="backend/tests/test_math.py",
        claimed_success=True,
    )
    assert claim.claim_id.startswith("clm_")

    proof = VerificationProof(
        proof_type=ProofType.TEST_EXECUTION,
        command_run="pytest backend/tests/test_math.py",
        exit_code=0,
        output_snippet="5 passed in 0.4s",
        verified=True,
    )
    entry = matrix.attach_proof(claim.claim_id, proof)
    assert entry is not None
    assert entry.certified is True
    assert entry.proof.contradiction_detected is False

    summary = matrix.summary()
    assert summary["total_claims"] == 1
    assert summary["certified_claims"] == 1
    assert summary["completeness_ratio"] == 1.0


def test_contradiction_detector():
    detector = ContradictionDetector()
    matrix = EvidenceMatrix()

    # Claim states success, but exit code is 1
    claim = matrix.record_claim(statement="Build succeeded", claimed_success=True)
    bad_proof = VerificationProof(
        proof_type=ProofType.EXIT_CODE_ZERO,
        command_run="cargo build",
        exit_code=1,
        output_snippet="error: cannot find symbol",
        verified=False,
    )
    entry = matrix.attach_proof(claim.claim_id, bad_proof)
    assert entry.certified is False
    assert entry.proof.contradiction_detected is True
    assert "CONTRADICTION" in entry.audit_notes


def test_finish_first_auditor_gatekeeper():
    matrix = EvidenceMatrix()
    auditor = FinishFirstAuditor(matrix)

    # Empty matrix -> blocked
    can_fin, _, issues = auditor.audit_finalization()
    assert can_fin is False
    assert any("No claims" in i for i in issues)

    # Unverified claim -> blocked
    c1 = matrix.record_claim(statement="Documentation updated")
    can_fin, _, issues = auditor.audit_finalization()
    assert can_fin is False
    assert any("lack physical verification" in i for i in issues)

    # Attach valid proof -> allowed
    p1 = VerificationProof(
        proof_type=ProofType.FILE_CONTENT_MATCH,
        exit_code=0,
        output_snippet="File exists and verified",
        verified=True,
    )
    matrix.attach_proof(c1.claim_id, p1)

    can_fin, msg, issues = auditor.audit_finalization()
    assert can_fin is True
    assert len(issues) == 0
    assert "AUDIT_PASSED" in msg
