from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class ProofType(enum.Enum):
    TEST_EXECUTION = "test_execution"
    EXIT_CODE_ZERO = "exit_code_zero"
    FILE_CONTENT_MATCH = "file_content_match"
    GIT_DIFF_VERIFIED = "git_diff_verified"
    STATIC_ANALYSIS = "static_analysis"


@dataclass
class ClaimItem:
    """A factual claim made by an agent regarding task deliverables or state."""
    claim_id: str = field(default_factory=lambda: f"clm_{uuid.uuid4().hex[:8]}")
    statement: str = ""
    target_path: str | None = None
    claimed_success: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "target_path": self.target_path,
            "claimed_success": self.claimed_success,
            "created_at": self.created_at,
        }


@dataclass
class VerificationProof:
    """Physical, execution-grounded proof supporting or refuting a claim."""
    proof_type: ProofType
    command_run: str | None = None
    exit_code: int = 0
    output_snippet: str = ""
    verified: bool = True
    contradiction_detected: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_type": self.proof_type.value,
            "command_run": self.command_run,
            "exit_code": self.exit_code,
            "output_snippet": self.output_snippet[:300],
            "verified": self.verified,
            "contradiction_detected": self.contradiction_detected,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class EvidenceEntry:
    """Binds a claim to physical verification proof."""
    claim: ClaimItem
    proof: VerificationProof | None = None
    certified: bool = False
    audit_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim.to_dict(),
            "proof": self.proof.to_dict() if self.proof else None,
            "certified": self.certified,
            "audit_notes": self.audit_notes,
        }
