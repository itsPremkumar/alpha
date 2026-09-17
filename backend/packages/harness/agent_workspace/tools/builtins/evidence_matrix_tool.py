"""Built-in Evidence Matrix and Finish-First verification tool inspired by MYTHOS."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.verification.evidence import (
    EvidenceMatrix,
    FinishFirstAuditor,
    ProofType,
    VerificationProof,
)

_MATRIX = EvidenceMatrix()
_AUDITOR = FinishFirstAuditor(_MATRIX)


@tool("audit_finish_first_evidence", parse_docstring=True)
def audit_finish_first_evidence(
    action: str,
    claim_statement: str = "",
    target_path: str | None = None,
    claim_id: str = "",
    proof_type: str = "test_execution",
    command_run: str | None = None,
    exit_code: int = 0,
    output_snippet: str = "",
    verified: bool = True,
) -> str:
    """Track, corroborate, and audit physical execution proof for task claims.

    Implements the MYTHOS Finish-First Doctrine: tasks cannot be declared 'done'
    without 100% physical proof and zero contradictions between claims and reality.

    Args:
        action: 'record_claim' (logs a factual deliverable claim), 'attach_proof' (attaches physical execution evidence to a claim), 'audit_finalization' (gatekeeper check whether task can safely finish), 'summary' (view full matrix).
        claim_statement: The specific deliverable or assertion claimed (e.g. 'All 15 unit tests pass').
        target_path: Associated file or artifact path.
        claim_id: ID of the claim to attach proof to.
        proof_type: 'test_execution', 'exit_code_zero', 'file_content_match', 'git_diff_verified'.
        command_run: Exact command executed to verify the claim.
        exit_code: Exit code of the verification command.
        output_snippet: Terminal stdout/stderr snippet showing proof.
        verified: Whether execution proved the claim.
    """
    proof_map = {
        "test_execution": ProofType.TEST_EXECUTION,
        "exit_code_zero": ProofType.EXIT_CODE_ZERO,
        "file_content_match": ProofType.FILE_CONTENT_MATCH,
        "git_diff_verified": ProofType.GIT_DIFF_VERIFIED,
    }

    if action == "record_claim":
        claim = _MATRIX.record_claim(
            statement=claim_statement,
            target_path=target_path,
        )
        return json.dumps({
            "action": "record_claim",
            "claim": claim.to_dict(),
        }, indent=2)

    elif action == "attach_proof":
        pt = proof_map.get(proof_type.lower(), ProofType.TEST_EXECUTION)
        proof = VerificationProof(
            proof_type=pt,
            command_run=command_run,
            exit_code=exit_code,
            output_snippet=output_snippet,
            verified=verified,
        )
        entry = _MATRIX.attach_proof(claim_id=claim_id, proof=proof)
        if not entry:
            return json.dumps({"error": f"Claim ID '{claim_id}' not found."}, indent=2)

        return json.dumps({
            "action": "attach_proof",
            "entry": entry.to_dict(),
        }, indent=2)

    elif action == "audit_finalization":
        can_finalize, msg, issues = _AUDITOR.audit_finalization()
        return json.dumps({
            "action": "audit_finalization",
            "can_finalize": can_finalize,
            "status_message": msg,
            "blocking_issues": issues,
            "summary": _MATRIX.summary(),
        }, indent=2)

    elif action == "summary":
        return json.dumps(_MATRIX.summary(), indent=2)

    else:
        return json.dumps({
            "error": f"Unknown action '{action}'. Supported: 'record_claim', 'attach_proof', 'audit_finalization', 'summary'."
        }, indent=2)
