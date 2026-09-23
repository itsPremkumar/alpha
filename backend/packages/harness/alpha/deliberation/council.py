"""3-Stage Anonymous LLM Council Engine.

Implements the classical 3-stage deliberation paradigm with REAL model calls:
1. Stage 1: Independent Blind Candidate Generation — one invocation per roster
   model against the same query (no cross-model contamination). Claims and
   self-confidence are parsed from what the model actually said (the old code
   hardcoded responses by index, canned claims, and ``self_confidence=0.92``).
2. Stage 2: Anonymous Rubric-Based Peer Review — the reviewer's model scores a
   peer's REAL response (self-vote strictly excluded, randomized ordering).
   An unparseable review is retried once and then skipped; it is never
   backfilled with invented scores (the old rubric was constants like
   ``correctness = 0.90 if "architecture" in response else 0.85``).
3. Stage 3: Chairman Synthesis with Minority Dissent Preservation — honest
   aggregation; a candidate nobody could review scores 0.0 with
   ``review_count: 0`` rather than the old fabricated 0.8 default.
"""

from __future__ import annotations

import logging
import random
import re
import time
import uuid
from typing import Any

from alpha.deliberation import invocation, parsing
from alpha.deliberation.models import (
    AnonymousReview,
    DeliberationConfidence,
    DeliberationResult,
    DeliberationStrategy,
    ParticipantCandidate,
    make_deliberation_id,
)

logger = logging.getLogger(__name__)

RUBRIC_WEIGHTS = {
    "correctness": 0.35,
    "evidence": 0.25,
    "reasoning": 0.20,
    "completeness": 0.10,
    "clarity": 0.10,
}

ANONYMOUS_LABELS = [
    "Candidate Alpha",
    "Candidate Beta",
    "Candidate Gamma",
    "Candidate Delta",
    "Candidate Epsilon",
]


class CouncilEngine:
    """Executes the 3-Stage Anonymous Peer Review Deliberation Council."""

    @classmethod
    def run_council(
        cls,
        query: str,
        roster: list[str] | None = None,
    ) -> DeliberationResult:
        """Executes full 3-stage council deliberation over REAL model calls."""
        start_time = time.time()
        deliberation_id = make_deliberation_id()
        models = roster or invocation.configured_model_roster()
        if len(models) < 2:
            # Peer review with a single model would be self-review — the old
            # code happily "ran" it and synthesized a fabricated 0.8 score.
            raise RuntimeError(
                f"Council requires at least 2 configured chat models; found {len(models)}. "
                "Add another model under `models:` in config.yaml."
            )

        # ---------------------------------------------------------------------
        # STAGE 1: Independent Blind Generation
        # ---------------------------------------------------------------------
        candidates = cls._stage1_blind_generation(query, models)

        # ---------------------------------------------------------------------
        # STAGE 2: Anonymous Rubric-Based Peer Review
        # ---------------------------------------------------------------------
        reviews = cls._stage2_peer_review(query, candidates)

        # ---------------------------------------------------------------------
        # STAGE 3: Chairman Synthesis & Minority Dissent Preservation
        # ---------------------------------------------------------------------
        result = cls._stage3_chairman_synthesis(deliberation_id, query, candidates, reviews, start_time)
        return result

    @classmethod
    def _stage1_blind_generation(cls, query: str, models: list[str]) -> list[ParticipantCandidate]:
        candidates: list[ParticipantCandidate] = []
        for idx, m in enumerate(models):
            label = ANONYMOUS_LABELS[idx % len(ANONYMOUS_LABELS)]
            cid = f"cand-{uuid.uuid4().hex[:6]}"

            # One REAL independent answer from this roster model.
            response = invocation.invoke_model(
                m,
                system=(
                    "You are an independent expert participating in an anonymous deliberation. "
                    "Answer the query directly with your best-reasoned recommendation in a few "
                    "sentences. Do not mention other participants.\n"
                    "End with exactly these two trailing lines:\n"
                    "STATED CLAIMS: <claim 1> | <claim 2> | <claim 3>\n"
                    "SELF CONFIDENCE: <0.00-1.00>"
                ),
                user=query,
            )
            claims, self_confidence = parsing.parse_claims_and_confidence(response)
            # Keep the answer body clean of the trailing metadata block.
            body = re.split(r"(?im)^STATED CLAIMS:", response)[0].strip()

            candidates.append(
                ParticipantCandidate(
                    candidate_id=cid,
                    model_id=m,
                    anonymous_label=label,
                    role="specialist",
                    response=body,
                    reasoning_trace=body[:200],
                    claims=claims,
                    # Honest default when the model did not self-report.
                    self_confidence=self_confidence if self_confidence is not None else 0.5,
                )
            )

        return candidates

    @classmethod
    def _invoke_rubric_review(
        cls,
        query: str,
        reviewer: ParticipantCandidate,
        target: ParticipantCandidate,
    ) -> dict | None:
        """One REAL reviewer-model call scoring the target's real response."""
        system = (
            "You are an impartial anonymous peer reviewer in a deliberation council. "
            "Score strictly from the rubric; output ONLY the requested lines."
        )
        user = (
            f"QUERY:\n{query}\n\n"
            f"TARGET RESPONSE ({target.anonymous_label}):\n{target.response}\n\n"
            "Score the TARGET RESPONSE on each rubric axis from 0.00 to 1.00. "
            "Output EXACTLY these lines:\n"
            "CORRECTNESS: <0.00-1.00>\n"
            "EVIDENCE: <0.00-1.00>\n"
            "REASONING: <0.00-1.00>\n"
            "COMPLETENESS: <0.00-1.00>\n"
            "CLARITY: <0.00-1.00>\n"
            "CRITIQUE: <one sentence>\n"
            "FLAWS: <flaw 1> | <flaw 2>"
        )
        text = invocation.invoke_model(reviewer.model_id, system=system, user=user)
        parsed = parsing.parse_rubric(text)
        if parsed is None:
            # One honest retry, then the review is skipped (never invented).
            retry = invocation.invoke_model(
                reviewer.model_id,
                system=system,
                user=user + "\nReminder: reply with ONLY the seven rubric lines shown above.",
            )
            parsed = parsing.parse_rubric(retry)
        return parsed

    @classmethod
    def _stage2_peer_review(
        cls,
        query: str,
        candidates: list[ParticipantCandidate],
    ) -> list[AnonymousReview]:
        reviews: list[AnonymousReview] = []

        for reviewer in candidates:
            # Targets: all candidates EXCEPT self (Self-Vote Exclusion Invariant)
            targets = [c for c in candidates if c.candidate_id != reviewer.candidate_id]

            # Randomized presentation order per reviewer to prevent position bias
            shuffled_targets = list(targets)
            random.seed(reviewer.candidate_id)
            random.shuffle(shuffled_targets)

            for target in shuffled_targets:
                parsed = cls._invoke_rubric_review(query, reviewer, target)
                if parsed is None:
                    logger.warning(
                        "Council review by %s of %s was unparseable after retry; skipping (no invented scores).",
                        reviewer.anonymous_label,
                        target.anonymous_label,
                    )
                    continue

                scores = parsed["scores"]
                composite = sum(scores[k] * RUBRIC_WEIGHTS[k] for k in RUBRIC_WEIGHTS)
                reviews.append(
                    AnonymousReview(
                        review_id=f"rev-{uuid.uuid4().hex[:6]}",
                        reviewer_candidate_id=reviewer.candidate_id,
                        target_candidate_id=target.candidate_id,
                        rubric_scores=dict(scores),
                        composite_score=round(composite, 3),
                        critique=parsed["critique"]
                        or f"{reviewer.anonymous_label} reviewed {target.anonymous_label}.",
                        identified_flaws=list(parsed["flaws"]),
                    )
                )

        return reviews

    @classmethod
    def _stage3_chairman_synthesis(
        cls,
        deliberation_id: str,
        query: str,
        candidates: list[ParticipantCandidate],
        reviews: list[AnonymousReview],
        start_time: float,
    ) -> DeliberationResult:
        # 1. Compute aggregate score per candidate
        candidate_scores: dict[str, list[float]] = {c.candidate_id: [] for c in candidates}
        for r in reviews:
            candidate_scores[r.target_candidate_id].append(r.composite_score)

        rankings: list[dict[str, Any]] = []
        for c in candidates:
            scores = candidate_scores.get(c.candidate_id) or []
            # Honest: nobody reviewed this candidate -> 0.0 with an explicit
            # review_count of 0 (the old code injected a fabricated 0.8).
            avg_score = (sum(scores) / len(scores)) if scores else 0.0
            rankings.append(
                {
                    "candidate_id": c.candidate_id,
                    "label": c.anonymous_label,
                    "model_id": c.model_id,
                    "average_score": round(avg_score, 3),
                    "review_count": len(scores),
                    "claims": c.claims,
                }
            )

        rankings.sort(key=lambda x: x["average_score"], reverse=True)
        winner = rankings[0]
        runner_up = rankings[1] if len(rankings) > 1 else None

        # 2. Consensus & Dissent Analysis
        top_score = winner["average_score"]
        runner_score = runner_up["average_score"] if runner_up else top_score
        consensus_pct = round(min(100.0, (1.0 - abs(top_score - runner_score)) * 100.0), 1)

        # 3. Minority Report Preservation (real margin, real labels)
        minority_dissent = None
        if runner_up and abs(top_score - runner_score) < 0.15:
            minority_dissent = (
                f"{runner_up['label']} ({runner_up['model_id']}) dissents: it finishes "
                f"{abs(top_score - runner_score):.3f} behind {winner['label']}, inside the "
                "0.15 threshold, so the trade-offs remain contested."
            )

        # 4. Synthesize Final Answer from the REAL winner response
        winner_response = next(
            c.response for c in candidates if c.candidate_id == winner["candidate_id"]
        )
        stated_claims = "\n".join(f"- {clm}" for clm in winner["claims"]) or "(no claims stated)"
        final_answer = (
            f"### Consensus Recommendation\n"
            f"Based on anonymous multi-model peer review, **{winner['label']}** ({winner['model_id']}) "
            f"emerged as the strongest solution with a composite score of {top_score}/1.0 "
            f"across {winner['review_count']} peer review(s).\n\n"
            f"**Core Strategic Proposal**:\n"
            f"{winner_response}\n\n"
            f"**Stated Claims**:\n{stated_claims}"
        )

        return DeliberationResult(
            deliberation_id=deliberation_id,
            query=query,
            strategy_used=DeliberationStrategy.COUNCIL,
            final_answer=final_answer,
            confidence_score=round(top_score, 2),
            confidence_level=DeliberationConfidence.HIGH_CONFIDENCE if top_score >= 0.85 else DeliberationConfidence.MEDIUM_CONFIDENCE,
            consensus_percentage=consensus_pct,
            key_evidence=winner["claims"],
            minority_dissent=minority_dissent,
            candidate_rankings=rankings,
            verdict_rationale=(
                f"Winner {winner['label']} achieved the highest real peer-review score "
                f"({top_score}) across correctness and evidence."
            ),
            # LLM-consensus tier until the verifier runs a real check.
            verification_status="consensus_supported",
            duration_seconds=round(time.time() - start_time, 2),
        )
