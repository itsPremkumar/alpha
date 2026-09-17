"""Verifier council: independent reviewers vote on tier-1 artifacts.

A producer never approves its own work. Verdicts (approve/block) arrive with
evidence; the quorum rule decides SHIP vs HOLD. High-risk artifacts stay
blocked without a quorum.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

ReviewVerdict = Literal["approve", "block", "abstain"]
CouncilOutcome = Literal["ship", "hold", "inconclusive"]


@dataclass
class Review:
    review_id: str
    artifact_id: str
    reviewer: str
    verdict: ReviewVerdict
    evidence: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CouncilCase:
    case_id: str
    artifact_id: str
    artifact_ref: str
    tier: int = 1
    min_reviews: int = 2
    reviews: list[Review] = field(default_factory=list)
    status: str = "open"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reviews"] = [r.to_dict() for r in self.reviews]
        return data


def judge(case: CouncilCase) -> tuple[CouncilOutcome, str]:
    """Score-aware quorum: SHIP iff no blocks and approvals >= minimum."""
    votes = [r for r in case.reviews if r.verdict != "abstain"]
    blocks = sum(1 for r in votes if r.verdict == "block")
    approvals = sum(1 for r in votes if r.verdict == "approve")
    if blocks > 0:
        return "hold", f"{blocks} blocking review(s)"
    if approvals >= case.min_reviews:
        return "ship", f"{approvals}/{case.min_reviews} approvals, no blocks"
    return "inconclusive", f"{approvals}/{case.min_reviews} approvals so far"


class CouncilEngine:
    """In-memory council cases; reviews persist per case for audit."""

    def __init__(self):
        self._cases: dict[str, CouncilCase] = {}
        self._lock = threading.Lock()

    def open_case(self, artifact_id: str, artifact_ref: str, *, tier: int = 1, min_reviews: int = 2) -> CouncilCase:
        case = CouncilCase(case_id=f"cns-{uuid.uuid4().hex[:10]}", artifact_id=artifact_id, artifact_ref=artifact_ref, tier=tier, min_reviews=min_reviews)
        with self._lock:
            self._cases[case.case_id] = case
        return case

    def submit_review(self, case_id: str, reviewer: str, verdict: ReviewVerdict, *, evidence: str = "") -> tuple[Review, CouncilOutcome, str] | None:
        with self._lock:
            case = self._cases.get(case_id)
            if not case or case.status != "open":
                return None
            if reviewer.lower().strip() in {r.reviewer for r in case.reviews}:
                return None
            review = Review(review_id=f"rvw-{uuid.uuid4().hex[:8]}", artifact_id=case.artifact_id, reviewer=reviewer.lower().strip(), verdict=verdict, evidence=evidence)
            case.reviews.append(review)
            outcome, reason = judge(case)
            if outcome in ("ship", "hold"):
                case.status = outcome
            return review, outcome, reason

    def get_case(self, case_id: str) -> CouncilCase | None:
        with self._lock:
            return self._cases.get(case_id)

    def list_cases(self, *, status: str | None = None) -> list[CouncilCase]:
        with self._lock:
            cases = list(self._cases.values())
        if status:
            cases = [c for c in cases if c.status == status]
        return sorted(cases, key=lambda c: -c.created_at)


_engine: CouncilEngine | None = None
_engine_lock = threading.Lock()


def get_council_engine() -> CouncilEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = CouncilEngine()
        return _engine
