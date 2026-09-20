"""Data models for Mission Compilation, Risk Governance, and Proof Obligations."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RiskTier(str, Enum):
    """Multi-tier risk classification inspired by hermes-agi-asi-harness."""
    R0 = "r0"  # Pure reasoning / analysis — auto-approve
    R1 = "r1"  # Read-only exploration (read file, search) — auto-approve
    R2 = "r2"  # Reversible local changes (source edits with git backup) — auto-approve with audit
    R3 = "r3"  # External low-impact (docs search, package install) — auto with log
    R4 = "r4"  # Significant side-effects (database migrations, architectural refactor) — proof required
    R5 = "r5"  # Irreversible destructive (mass deletion, cloud provisioning, drop table) — explicit gate
    R6 = "r6"  # Strategic / systemic changes — multi-party review gate


@dataclass
class ProofObligation:
    """Formal obligation that must be verified before a mission can be completed."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str = ""
    verification_type: str = "test_pass"  # "test_pass", "file_exists", "diff_non_empty", "manual_approval"
    satisfied: bool = False
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Mission:
    """A durable, verifiable mission contract compiled from natural language."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    raw_request: str = ""
    interpreted_intent: str = ""
    latent_needs: str = ""
    desired_outcome: str = ""
    risk_tier: RiskTier = RiskTier.R2
    constraints: dict[str, list[str]] = field(default_factory=lambda: {
        "hard": [],
        "soft": [],
        "forbidden": [],
        "legal": [],
        "ethical": [],
        "physical": [],
    })
    acceptance_criteria: list[str] = field(default_factory=list)
    proof_obligations: list[ProofObligation] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_tier"] = self.risk_tier.value
        data["proof_obligations"] = [p.to_dict() for p in self.proof_obligations]
        return data

    def to_markdown(self) -> str:
        lines = [
            f"# Mission Contract [{self.id[:8]}]",
            f"**Raw Request**: {self.raw_request}",
            f"**Interpreted Intent**: {self.interpreted_intent}",
            f"**Latent Needs**: {self.latent_needs}",
            f"**Risk Tier**: `{self.risk_tier.value.upper()}`",
            f"**Desired Outcome**: {self.desired_outcome}\n",
            "## Constraints",
        ]
        for ctype, items in self.constraints.items():
            if items:
                lines.append(f"- **{ctype.capitalize()}**: {', '.join(items)}")

        if self.acceptance_criteria:
            lines.append("\n## Acceptance Criteria")
            for ac in self.acceptance_criteria:
                lines.append(f"- [ ] {ac}")

        if self.proof_obligations:
            lines.append("\n## Proof Obligations")
            for po in self.proof_obligations:
                status = "✅" if po.satisfied else "⏳"
                lines.append(f"- {status} [{po.verification_type}] {po.description}")

        return "\n".join(lines)
