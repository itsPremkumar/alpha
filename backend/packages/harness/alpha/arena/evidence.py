"""Evidence ledger: claim-level fact verification with honest unknowns.

Each candidate's factual claims are checked against retrieved
evidence. A claim is SUPPORTED, CONTRADICTED, or UNVERIFIED - and
UNVERIFIED is a first-class state, never silently promoted to
supported because the check could not run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Verdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"  # no evidence one way or the other
    NOT_CHECKED = "NOT_CHECKED"  # the check never ran (report, don't guess)


@dataclass(frozen=True)
class ClaimCheck:
    claim: str
    verdict: Verdict
    evidence_ref: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "verdict": self.verdict.value,
            "evidence_ref": self.evidence_ref,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaimCheck:
        return cls(
            claim=data["claim"],
            verdict=Verdict(data["verdict"]),
            evidence_ref=data.get("evidence_ref", ""),
            note=data.get("note", ""),
        )


@dataclass
class EvidenceLedger:
    run_id: str
    checks: list[ClaimCheck] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------ recording

    def record(self, check: ClaimCheck) -> None:
        self.checks.append(check)
        self.updated_at = time.time()

    def record_batch(self, checks: list[ClaimCheck]) -> None:
        self.checks.extend(checks)
        self.updated_at = time.time()

    # ------------------------------------------------------------ queries

    def counts(self) -> dict[str, int]:
        """Counts per verdict. Every verdict key is always present so a
        consumer can render 0 rather than treat absence as zero."""
        return {
            "supported": sum(1 for c in self.checks if c.verdict is Verdict.SUPPORTED),
            "contradicted": sum(1 for c in self.checks if c.verdict is Verdict.CONTRADICTED),
            "unverified": sum(1 for c in self.checks if c.verdict is Verdict.UNVERIFIED),
            "not_checked": sum(1 for c in self.checks if c.verdict is Verdict.NOT_CHECKED),
            "total": len(self.checks),
        }

    def contradicted_claims(self) -> list[ClaimCheck]:
        return [c for c in self.checks if c.verdict is Verdict.CONTRADICTED]

    def has_contradiction(self) -> bool:
        return bool(self.contradicted_claims())

    def support_ratio(self) -> float | None:
        """Supported / checked. ``None`` when nothing was checked - an
        unknown ratio is not 0 and not 1."""
        checked = [c for c in self.checks if c.verdict in (Verdict.SUPPORTED, Verdict.CONTRADICTED)]
        if not checked:
            return None
        supported = sum(1 for c in checked if c.verdict is Verdict.SUPPORTED)
        return round(supported / len(checked), 4)

    # ------------------------------------------------------------ serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "checks": [c.to_dict() for c in self.checks],
            "counts": self.counts(),
            "support_ratio": self.support_ratio(),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceLedger:
        ledger = cls(run_id=data["run_id"])
        ledger.checks = [ClaimCheck.from_dict(c) for c in data.get("checks", [])]
        ledger.updated_at = data.get("updated_at", time.time())
        return ledger


def gate_from_ledger(ledger: EvidenceLedger) -> tuple[bool, str]:
    """Turn ledger state into a hard-gate decision.

    ``tripped`` is True only on a contradiction - a claim nobody could
    check is reported in the detail but does not fail the candidate,
    because failing on missing evidence punishes honesty.
    """
    contradicted = ledger.contradicted_claims()
    if contradicted:
        first = contradicted[0]
        return True, f"claim contradicted: {first.claim[:120]}"
    counts = ledger.counts()
    if counts["unverified"] or counts["not_checked"]:
        return False, (f"{counts['unverified']} unverified, {counts['not_checked']} not checked of {counts['total']} claims")
    return False, ""


__all__ = [
    "Verdict",
    "ClaimCheck",
    "EvidenceLedger",
    "gate_from_ledger",
]
