from __future__ import annotations

import logging
from typing import Any

from .contradiction import ContradictionDetector
from .models import ClaimItem, EvidenceEntry, VerificationProof

logger = logging.getLogger("agent_workspace.verification.evidence.matrix")


class EvidenceMatrix:
    """
    Evidence Matrix maintaining physical proof records for all high-value tasks.
    Tabulates:
      - What was claimed
      - What evidence proves it
      - What was executed
      - What remains uncertain
    """

    def __init__(self) -> None:
        self.entries: dict[str, EvidenceEntry] = {}
        self.detector = ContradictionDetector()

    def record_claim(
        self,
        statement: str,
        target_path: str | None = None,
        claimed_success: bool = True,
    ) -> ClaimItem:
        claim = ClaimItem(
            statement=statement,
            target_path=target_path,
            claimed_success=claimed_success,
        )
        self.entries[claim.claim_id] = EvidenceEntry(claim=claim)
        return claim

    def attach_proof(
        self,
        claim_id: str,
        proof: VerificationProof,
    ) -> EvidenceEntry | None:
        entry = self.entries.get(claim_id)
        if not entry:
            return None

        entry.proof = proof
        contradicted, diag = self.detector.audit_claim_against_proof(entry.claim, proof)
        proof.contradiction_detected = contradicted

        if contradicted:
            entry.certified = False
            entry.audit_notes = f"REJECTED: {diag}"
        else:
            entry.certified = proof.verified
            entry.audit_notes = "CERTIFIED: Physical execution corroborates statement."

        return entry

    def summary(self) -> dict[str, Any]:
        total = len(self.entries)
        certified = sum(1 for e in self.entries.values() if e.certified)
        unverified = sum(1 for e in self.entries.values() if e.proof is None)
        contradictions = sum(
            1 for e in self.entries.values() if e.proof and e.proof.contradiction_detected
        )

        return {
            "total_claims": total,
            "certified_claims": certified,
            "unverified_claims": unverified,
            "contradictions_count": contradictions,
            "completeness_ratio": round(certified / max(1, total), 2),
            "entries": [e.to_dict() for e in self.entries.values()],
        }
