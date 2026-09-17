"""Enterprise Blackboard & Cross-Department RFC Protocol Engine."""

from __future__ import annotations

import logging
import time
from typing import Any

from agent_workspace.enterprise.models import (
    DebateArgument,
    EnterpriseRFC,
    RFCReview,
    RFCStatus,
)

logger = logging.getLogger(__name__)


class EnterpriseRFCProtocol:
    """Manages cross-department Request For Comments (RFCs), epistemic debate, and consensus gating."""

    def __init__(self):
        self._rfcs: dict[str, EnterpriseRFC] = {}
        self._bootstrap_sample_rfcs()

    def _bootstrap_sample_rfcs(self) -> None:
        """Seeds initial architectural and security RFCs for the enterprise."""
        rfc_001 = EnterpriseRFC(
            rfc_id="rfc-001",
            title="RFC-001: Zero-Downtime Hot-Swap Multi-Sig Promotion Protocol",
            author_bot="bot-cto",
            department="architecture",
            status=RFCStatus.APPROVED,
            summary="Defines the 3-signature cryptographic verification protocol (CTO + SWE + CISO) before release promotion.",
            proposal_content="Require sha256 HMAC multi-signatures verifying holdout benchmark pass, AST boundary isolation, and architecture sign-off.",
            affected_departments=["architecture", "engineering", "security", "performance"],
            consensus_score=0.92,
            gating_passed=True,
            gating_reason="Consensus threshold exceeded (0.92 >= 0.75) with unanimous approval from Architecture, Security, and Engineering.",
            reviews=[
                RFCReview(
                    reviewer_bot="bot-ciso",
                    department="security",
                    verdict="approve",
                    epistemic_confidence=0.95,
                    argument="Cryptographic multi-sig prevents rogue promotions and enforces zero-trust release pipelines.",
                ),
                RFCReview(
                    reviewer_bot="bot-perf-lead",
                    department="performance",
                    verdict="approve",
                    epistemic_confidence=0.90,
                    argument="Holdout test gating ensures no latency regressions in release candidate.",
                ),
                RFCReview(
                    reviewer_bot="bot-eng-lead",
                    department="engineering",
                    verdict="approve",
                    epistemic_confidence=0.92,
                    argument="Zero-downtime hot-swap allows background daemons to continue execution without restart.",
                ),
            ],
            debate_thread=[
                DebateArgument(
                    speaker_bot="bot-ciso",
                    department="security",
                    stance="pro",
                    claim="3-signature attestation is cryptographically tamper-proof",
                    evidence="ECDSA / HMAC token digest with timestamp prevents replay attacks.",
                    epistemic_weight=0.95,
                ),
                DebateArgument(
                    speaker_bot="bot-perf-lead",
                    department="performance",
                    stance="synthesis",
                    claim="Signing must execute after holdout benchmark passes with >=90%",
                    evidence="Holdout suites test boundary cases outside normal training/context window.",
                    epistemic_weight=0.92,
                ),
            ],
        )

        rfc_002 = EnterpriseRFC(
            rfc_id="rfc-002",
            title="RFC-002: AST Boundary Sandboxing & Scoped Credential Vault",
            author_bot="bot-ciso",
            department="security",
            status=RFCStatus.DEBATING,
            summary="Strict AST parsing of dynamic agent python commands to disallow arbitrary code execution.",
            proposal_content="Enforce static AST inspection on every generated script. Reject eval(), exec(), raw subprocess without lease-fencing.",
            affected_departments=["security", "engineering", "performance"],
            consensus_score=0.82,
            gating_passed=True,
            gating_reason="Consensus achieved (0.82 >= 0.75). Pending implementation into cyclic heartbeat.",
            reviews=[
                RFCReview(
                    reviewer_bot="bot-cto",
                    department="architecture",
                    verdict="approve",
                    epistemic_confidence=0.94,
                    argument="Essential boundary guarantee for production deployment.",
                ),
                RFCReview(
                    reviewer_bot="bot-eng-lead",
                    department="engineering",
                    verdict="amend",
                    epistemic_confidence=0.75,
                    argument="Ensure latency overhead of AST scan is < 5ms per heartbeat cycle.",
                ),
            ],
            debate_thread=[
                DebateArgument(
                    speaker_bot="bot-eng-lead",
                    department="engineering",
                    stance="amend",
                    claim="AST scanning may introduce overhead on large scripts",
                    evidence="Profiling shows 5-10ms per 1k lines of python AST traversal.",
                    epistemic_weight=0.80,
                ),
                DebateArgument(
                    speaker_bot="bot-ciso",
                    department="security",
                    stance="synthesis",
                    claim="We can cache AST digests for identical scripts to achieve sub-millisecond execution",
                    evidence="LRU cache over sha256 digests reduces average check time to 0.1ms.",
                    counter_to_id="arg-eng-lead",
                    epistemic_weight=0.90,
                ),
            ],
        )

        self._rfcs[rfc_001.rfc_id] = rfc_001
        self._rfcs[rfc_002.rfc_id] = rfc_002

    def create_rfc(
        self,
        title: str,
        author_bot: str,
        department: str,
        summary: str,
        proposal_content: str,
        affected_departments: list[str],
    ) -> EnterpriseRFC:
        """Publishes a new RFC onto the Enterprise Blackboard."""
        rfc_num = len(self._rfcs) + 1
        rfc_id = f"rfc-{rfc_num:03d}"

        rfc = EnterpriseRFC(
            rfc_id=rfc_id,
            title=title,
            author_bot=author_bot,
            department=department,
            status=RFCStatus.UNDER_REVIEW,
            summary=summary,
            proposal_content=proposal_content,
            affected_departments=affected_departments,
            consensus_score=0.0,
            gating_passed=False,
            gating_reason="Under multi-agent review",
        )
        self._rfcs[rfc_id] = rfc
        logger.info(f"Published new enterprise RFC {rfc_id}: '{title}' by {author_bot}")
        return rfc

    def submit_review(
        self,
        rfc_id: str,
        reviewer_bot: str,
        department: str,
        verdict: str,
        argument: str,
        epistemic_confidence: float = 0.85,
    ) -> RFCReview:
        """Submits an agent review on an active RFC."""
        rfc = self._rfcs.get(rfc_id)
        if not rfc:
            raise KeyError(f"RFC '{rfc_id}' not found.")

        # Replace existing review from this reviewer or append
        existing = [r for r in rfc.reviews if r.reviewer_bot == reviewer_bot]
        if existing:
            rfc.reviews.remove(existing[0])

        review = RFCReview(
            reviewer_bot=reviewer_bot,
            department=department,
            verdict=verdict.lower(),
            epistemic_confidence=max(0.1, min(1.0, epistemic_confidence)),
            argument=argument,
        )
        rfc.reviews.append(review)
        rfc.updated_at = time.time()

        # Re-evaluate consensus gating
        self.evaluate_consensus_gating(rfc_id)
        return review

    def submit_debate_argument(
        self,
        rfc_id: str,
        speaker_bot: str,
        department: str,
        stance: str,
        claim: str,
        evidence: str,
        counter_to_id: str | None = None,
        epistemic_weight: float = 0.8,
    ) -> DebateArgument:
        """Submits a formal argument into the RFC epistemic debate thread."""
        rfc = self._rfcs.get(rfc_id)
        if not rfc:
            raise KeyError(f"RFC '{rfc_id}' not found.")

        arg = DebateArgument(
            speaker_bot=speaker_bot,
            department=department,
            stance=stance.lower(),
            claim=claim,
            evidence=evidence,
            counter_to_id=counter_to_id,
            epistemic_weight=max(0.1, min(1.0, epistemic_weight)),
        )
        rfc.debate_thread.append(arg)
        if rfc.status == RFCStatus.UNDER_REVIEW:
            rfc.status = RFCStatus.DEBATING
        rfc.updated_at = time.time()

        self.evaluate_consensus_gating(rfc_id)
        return arg

    def evaluate_consensus_gating(self, rfc_id: str) -> tuple[bool, float, str]:
        """Evaluates epistemic consensus gating.

        Consensus rules:
        1. Quorum: Reviews from >= 2 departments (or all affected if fewer).
        2. No unresolved blocking veto from CISO or CTO.
        3. Epistemic weighted confidence threshold >= 0.75.
        """
        rfc = self._rfcs.get(rfc_id)
        if not rfc:
            raise KeyError(f"RFC '{rfc_id}' not found.")

        if not rfc.reviews:
            rfc.consensus_score = 0.0
            rfc.gating_passed = False
            rfc.gating_reason = "No reviews submitted yet."
            return False, 0.0, rfc.gating_reason

        # Check for hard veto
        for r in rfc.reviews:
            if r.verdict in ["reject", "block"] and r.reviewer_bot in ["bot-ciso", "bot-cto", "bot-ceo"]:
                rfc.consensus_score = 0.2
                rfc.gating_passed = False
                rfc.status = RFCStatus.DEBATING
                rfc.gating_reason = f"Blocked by leadership veto from {r.reviewer_bot}: {r.argument}"
                return False, 0.2, rfc.gating_reason

        # Calculate weighted consensus
        total_weight = 0.0
        positive_weight = 0.0
        for r in rfc.reviews:
            total_weight += r.epistemic_confidence
            if r.verdict == "approve":
                positive_weight += r.epistemic_confidence
            elif r.verdict == "amend":
                positive_weight += r.epistemic_confidence * 0.7

        consensus_score = round(positive_weight / max(0.001, total_weight), 2)
        rfc.consensus_score = consensus_score

        # Check quorum
        reviewed_depts = {r.department for r in rfc.reviews}
        required_depts = set(rfc.affected_departments)
        has_quorum = len(reviewed_depts) >= 2 or (bool(required_depts) and reviewed_depts >= required_depts)

        if consensus_score >= 0.75 and has_quorum:
            rfc.gating_passed = True
            rfc.status = RFCStatus.APPROVED
            rfc.gating_reason = f"Consensus achieved ({consensus_score:.2f} >= 0.75) with multi-department quorum."
            logger.info(f"Consensus gating PASSED for {rfc_id}: score={consensus_score}")
        else:
            rfc.gating_passed = False
            if not has_quorum:
                rfc.gating_reason = f"Quorum not met: reviewed depts {list(reviewed_depts)} < required {list(required_depts)}"
            else:
                rfc.gating_reason = f"Consensus score {consensus_score:.2f} below required 0.75 threshold."

        return rfc.gating_passed, rfc.consensus_score, rfc.gating_reason

    def get_rfc(self, rfc_id: str) -> EnterpriseRFC | None:
        return self._rfcs.get(rfc_id)

    def list_rfcs(self, status: str | None = None) -> list[EnterpriseRFC]:
        if status:
            return [r for r in self._rfcs.values() if r.status == status]
        return list(self._rfcs.values())


_RFC_PROTOCOL: EnterpriseRFCProtocol | None = None


def get_rfc_protocol() -> EnterpriseRFCProtocol:
    global _RFC_PROTOCOL
    if _RFC_PROTOCOL is None:
        _RFC_PROTOCOL = EnterpriseRFCProtocol()
    return _RFC_PROTOCOL
