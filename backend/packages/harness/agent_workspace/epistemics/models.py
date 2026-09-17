"""Models for Epistemic Belief Status and Bayesian Claims."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EpistemicStatus(str, Enum):
    """Rigorous epistemic statuses from hermes-agi-asi-harness."""
    FACT = "fact"                    # Empirically verified via direct execution or code inspection
    OBSERVATION = "observation"      # Direct tool observation or command output
    INFERENCE = "inference"          # Deductive conclusion from verified facts
    HYPOTHESIS = "hypothesis"        # Plausible working theory undergoing testing
    ASSUMPTION = "assumption"        # Unverified working premise (requires falsification test)
    CONTRADICTION = "contradiction"  # Discrepancy between opposing sources
    SPECULATION = "speculation"      # Low-confidence guess
    OBSOLETE = "obsolete"            # Superceded by newer evidence


@dataclass
class Claim:
    """An epistemic assertion with Bayesian confidence and falsification criteria."""
    text: str
    status: EpistemicStatus = EpistemicStatus.HYPOTHESIS
    confidence: float = 0.5
    bayesian_prior: float = 0.5
    bayesian_posterior: float = 0.5
    falsification_test: str = ""
    verification_method: str = ""
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    claim_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @property
    def is_verified(self) -> bool:
        return self.status in {EpistemicStatus.FACT, EpistemicStatus.OBSERVATION} and self.confidence >= 0.85

    @property
    def is_falsified(self) -> bool:
        return self.bayesian_posterior < 0.15 or len(self.contradicting_evidence) > len(self.supporting_evidence) * 2
