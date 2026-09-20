from __future__ import annotations

import logging

from .models import ClaimItem, VerificationProof

logger = logging.getLogger("alpha.verification.evidence.contradiction")


class ContradictionDetector:
    """
    Detects contradictions between agent assertions and physical execution evidence.
    Implements the MYTHOS-style Finish-First truth doctrine.
    """

    @staticmethod
    def audit_claim_against_proof(
        claim: ClaimItem,
        proof: VerificationProof,
    ) -> tuple[bool, str]:
        """
        Returns (is_contradicted, diagnostic_message).
        True indicates physical reality contradicts the claim.
        """
        stmt_lower = claim.statement.lower()

        # Case 1: Agent claims success or tests passed, but exit_code is non-zero
        if claim.claimed_success or "pass" in stmt_lower or "success" in stmt_lower:
            if proof.exit_code != 0:
                msg = (
                    f"CONTRADICTION: Claim asserts '{claim.statement}', "
                    f"but verified exit code is {proof.exit_code} (non-zero)."
                )
                logger.warning(msg)
                return True, msg

            # Check if output contains explicit failure markers
            out_lower = proof.output_snippet.lower()
            if "failed" in out_lower or "error" in out_lower or "traceback" in out_lower:
                if "0 failed" not in out_lower and "errors=0" not in out_lower:
                    msg = (
                        f"CONTRADICTION: Claim asserts '{claim.statement}', "
                        f"but execution output contains failure keywords."
                    )
                    logger.warning(msg)
                    return True, msg

        # Case 2: Physical verification failed explicitly
        if not proof.verified:
            msg = f"CONTRADICTION: Claim '{claim.statement}' has unverified or refuted proof."
            return True, msg

        return False, "CONSISTENT: Proof corroborates claim."
