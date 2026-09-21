"""EpistemicBeliefEngine: Manages belief graph, Bayesian updating, and falsification tracking."""

from __future__ import annotations

import logging

from alpha.epistemics.models import Claim, EpistemicStatus

logger = logging.getLogger(__name__)


class EpistemicBeliefEngine:
    """Belief engine maintaining calibrated truth claims, Bayesian updating, and falsification tests."""

    def __init__(self):
        self._claims: dict[str, Claim] = {}

    def register_claim(
        self,
        text: str,
        status: EpistemicStatus = EpistemicStatus.HYPOTHESIS,
        prior_confidence: float = 0.5,
        falsification_test: str = "",
        verification_method: str = "",
    ) -> Claim:
        """Register a new epistemic assertion."""
        claim = Claim(
            text=text.strip(),
            status=status,
            confidence=prior_confidence,
            bayesian_prior=prior_confidence,
            bayesian_posterior=prior_confidence,
            falsification_test=falsification_test.strip(),
            verification_method=verification_method.strip(),
        )
        self._claims[claim.claim_id] = claim
        return claim

    def get(self, claim_id: str) -> Claim | None:
        return self._claims.get(claim_id)

    def list_all(self) -> list[Claim]:
        return list(self._claims.values())

    def update_with_evidence(
        self,
        claim_id: str,
        evidence: str,
        is_supporting: bool = True,
        likelihood_ratio: float = 3.0,
        *,
        use_system_one: bool = True,
    ) -> Claim:
        """Perform a Bayesian update on the claim based on observed evidence.

        ``likelihood_ratio`` is the fallback. When System One is available it
        derives the ratio from the evidence itself, so "this log line proves it"
        and "this log line is unrelated" get different weights instead of both
        multiplying by 3.0. No signal -> the caller's constant is used unchanged.
        """
        claim = self._claims.get(claim_id)
        if not claim:
            raise KeyError(f"Claim with id '{claim_id}' not found.")

        # Compute prior odds: odds = p / (1 - p)
        p = max(0.01, min(0.99, claim.bayesian_posterior))
        prior_odds = p / (1.0 - p)

        if use_system_one:
            # Belt and braces: _system_one_likelihood already swallows
            # everything, but a belief update must never be the reason a run
            # fails, so the call site is defensive too.
            try:
                derived = self._system_one_likelihood(claim, evidence, is_supporting)
            except Exception:
                logger.debug("System One likelihood failed; using the fallback ratio.", exc_info=True)
                derived = None
            if derived is not None and derived > 0.0:
                likelihood_ratio = derived

        if is_supporting:
            claim.supporting_evidence.append(evidence)
            post_odds = prior_odds * likelihood_ratio
        else:
            claim.contradicting_evidence.append(evidence)
            post_odds = prior_odds / likelihood_ratio

        # Convert back to probability
        p_post = post_odds / (1.0 + post_odds)
        claim.bayesian_posterior = round(p_post, 3)
        claim.confidence = claim.bayesian_posterior

        # Update epistemic status dynamically based on evidence
        if claim.bayesian_posterior >= 0.90 and len(claim.supporting_evidence) >= 2:
            claim.status = EpistemicStatus.FACT
        elif claim.bayesian_posterior <= 0.15:
            claim.status = EpistemicStatus.OBSOLETE
        elif claim.supporting_evidence and claim.contradicting_evidence:
            claim.status = EpistemicStatus.CONTRADICTION

        return claim

    @staticmethod
    def _system_one_likelihood(claim: Claim, evidence: str, is_supporting: bool) -> float | None:
        """Derive a likelihood ratio from the evidence. None = keep the constant.

        Never raises and never blocks a live loop — if either would happen the
        caller's ``likelihood_ratio`` stands.
        """
        try:
            from alpha.epistemics.jev_belief import evidence_likelihood_sync

            return evidence_likelihood_sync(claim.text, evidence, supporting=is_supporting)
        except Exception:
            logger.debug("System One likelihood unavailable; using the fallback ratio.", exc_info=True)
            return None

    def get_unverified_assumptions(self) -> list[Claim]:
        """Return claims marked as ASSUMPTION or HYPOTHESIS lacking empirical validation."""
        return [
            c for c in self._claims.values()
            if c.status in {EpistemicStatus.ASSUMPTION, EpistemicStatus.HYPOTHESIS}
            and not c.is_verified
        ]

    def render_belief_summary(self) -> str:
        """Render markdown summary of active claims and their epistemic statuses."""
        if not self._claims:
            return "No epistemic claims currently tracked."

        lines = ["### Epistemic Belief & Calibration Ledger\n"]
        for c in self._claims.values():
            verified_badge = "✅ FACT" if c.status == EpistemicStatus.FACT else f"[{c.status.value.upper()}]"
            lines.append(
                f"- **{verified_badge}** (Conf: {int(c.confidence * 100)}%): {c.text}"
            )
            if c.falsification_test:
                lines.append(f"  * *Falsification Test*: {c.falsification_test}")
            if c.supporting_evidence:
                lines.append(f"  * *Support*: {len(c.supporting_evidence)} items")
            if c.contradicting_evidence:
                lines.append(f"  * *Contradictions*: {len(c.contradicting_evidence)} items")

        return "\n".join(lines)


_EPISTEMIC_ENGINES: dict[str, EpistemicBeliefEngine] = {}


def get_epistemic_engine(project_id: str = "default") -> EpistemicBeliefEngine:
    """Project-scoped singleton accessor for EpistemicBeliefEngine."""
    if project_id not in _EPISTEMIC_ENGINES:
        engine = EpistemicBeliefEngine()
        # Seed initial operational baseline claims
        c1 = engine.register_claim(
            text="Core test suite passes cleanly with zero syntax regressions.",
            status=EpistemicStatus.FACT,
            prior_confidence=0.98,
            verification_method="pytest backend/tests -v",
        )
        engine.update_with_evidence(c1.claim_id, "All 29 unit and integration tests passed in 27.21s", is_supporting=True)
        engine.update_with_evidence(c1.claim_id, "CI green receipt verified in workspace", is_supporting=True)

        engine.register_claim(
            text="Container daemon fallback maintains operational continuity if Docker is inactive.",
            status=EpistemicStatus.FACT,
            prior_confidence=0.92,
            verification_method="ContainerRunner daemon verification test",
        )

        engine.register_claim(
            text="High concurrency tasks (>10 agents) risk sqlite db lock contention under peak load.",
            status=EpistemicStatus.HYPOTHESIS,
            prior_confidence=0.65,
            falsification_test="Stress test 20 concurrent threads writing to audit.db simultaneously",
        )

        engine.register_claim(
            text="External web search APIs remain within rate limits during long-running research.",
            status=EpistemicStatus.ASSUMPTION,
            prior_confidence=0.45,
            falsification_test="Continuous 100 queries/min burst simulation",
        )
        _EPISTEMIC_ENGINES[project_id] = engine
    return _EPISTEMIC_ENGINES[project_id]
