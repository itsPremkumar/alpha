"""Universal Deliberation Engine: Data Contracts and Enums.

Defines schemas for:
- Deliberation strategies (SINGLE, ENSEMBLE, COUNCIL, DEBATE, PEER_REVIEW, MOA, JUDGE)
- Confidence levels, candidate answers, rubric reviews, debate turns
- DeliberationResult contracts with calibrated confidence and preserved minority dissent
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class DeliberationStrategy(StrEnum):
    AUTO = "auto"
    SINGLE = "single"
    ENSEMBLE = "ensemble"
    COUNCIL = "council"
    DEBATE = "debate"
    PEER_REVIEW = "peer_review"
    MOA = "moa"
    JUDGE = "judge"
    RESEARCH_COUNCIL = "research_council"
    RED_TEAM = "red_team"
    EXPERT_PANEL = "expert_panel"


class DeliberationConfidence(StrEnum):
    HIGH_CONFIDENCE = "high_confidence"
    MEDIUM_CONFIDENCE = "medium_confidence"
    LOW_CONFIDENCE = "low_confidence"
    CONTESTED = "contested"
    UNRESOLVED = "unresolved"


@dataclass
class ParticipantCandidate:
    candidate_id: str
    model_id: str
    anonymous_label: str  # e.g. "Candidate Alpha"
    role: str
    response: str
    reasoning_trace: str = ""
    claims: list[str] = field(default_factory=list)
    self_confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnonymousReview:
    review_id: str
    reviewer_candidate_id: str  # Reviewer's own candidate_id
    target_candidate_id: str  # Must NOT equal reviewer_candidate_id (self-vote exclusion)
    rubric_scores: dict[str, float]  # correctness, evidence, reasoning, completeness, clarity
    composite_score: float
    critique: str
    identified_flaws: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DebateTurn:
    turn_id: str
    round_number: int
    speaker_candidate_id: str
    speaker_label: str
    target_candidate_id: str | None
    argument: str
    rebuttal_to: str | None = None
    new_evidence: list[str] = field(default_factory=list)
    similarity_with_previous: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeliberationResult:
    deliberation_id: str
    query: str
    strategy_used: DeliberationStrategy
    final_answer: str
    confidence_score: float  # 0.0 to 1.0
    confidence_level: DeliberationConfidence
    consensus_percentage: float  # 0.0 to 100.0
    key_evidence: list[str] = field(default_factory=list)
    minority_dissent: str | None = None  # Preserved dissenting minority view
    candidate_rankings: list[dict[str, Any]] = field(default_factory=list)
    verdict_rationale: str = ""
    verification_status: str = "consensus_supported"
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["strategy_used"] = self.strategy_used.value
        d["confidence_level"] = self.confidence_level.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliberationResult:
        clean = dict(data)
        clean["strategy_used"] = DeliberationStrategy(clean["strategy_used"])
        clean["confidence_level"] = DeliberationConfidence(clean["confidence_level"])
        return cls(**clean)


def make_deliberation_id() -> str:
    return f"delib-{uuid.uuid4().hex[:8]}"
