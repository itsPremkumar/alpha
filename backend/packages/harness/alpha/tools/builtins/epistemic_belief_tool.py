"""Built-in evaluate_epistemic_claim tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.epistemics.engine import EpistemicBeliefEngine
from alpha.epistemics.models import EpistemicStatus

# Shared process-level belief engine singleton for active sessions
_GLOBAL_BELIEF_ENGINE = EpistemicBeliefEngine()


@tool("evaluate_epistemic_claim", parse_docstring=True)
def evaluate_epistemic_claim(
    action: str,
    claim_text: str = "",
    claim_id: str | None = None,
    evidence: str = "",
    is_supporting: bool = True,
    falsification_test: str = "",
) -> str:
    """Register, evaluate, or update epistemic truth claims with Bayesian calibration and falsification tests.

    Action 'register': Adds a new working hypothesis/assumption with an explicit falsification test.
    Action 'update': Supplies empirical observation evidence, performing Bayesian probability update.
    Action 'summary': Returns markdown ledger of all tracked beliefs, statuses, and unverified assumptions.

    Args:
        action: Either 'register', 'update', or 'summary'.
        claim_text: The statement or hypothesis text (required for 'register').
        claim_id: ID of an existing claim (required for 'update').
        evidence: Concrete observation or test result text (for 'update').
        is_supporting: True if evidence supports the claim, False if it refutes it.
        falsification_test: Test or condition that would prove this claim FALSE (for 'register').
    """
    engine = _GLOBAL_BELIEF_ENGINE

    if action.lower() == "register":
        claim = engine.register_claim(
            text=claim_text,
            status=EpistemicStatus.HYPOTHESIS,
            prior_confidence=0.5,
            falsification_test=falsification_test,
        )
        return json.dumps({
            "status": "registered",
            "claim_id": claim.claim_id,
            "claim": claim.text,
            "epistemic_status": claim.status.value,
            "confidence": claim.confidence,
            "falsification_test": claim.falsification_test,
        }, indent=2)

    elif action.lower() == "update":
        if not claim_id:
            return json.dumps({"error": "claim_id is required for 'update' action."})
        try:
            claim = engine.update_with_evidence(
                claim_id=claim_id,
                evidence=evidence,
                is_supporting=is_supporting,
            )
            return json.dumps({
                "status": "updated",
                "claim_id": claim.claim_id,
                "epistemic_status": claim.status.value,
                "posterior_confidence": claim.bayesian_posterior,
                "is_verified": claim.is_verified,
            }, indent=2)
        except KeyError as e:
            return json.dumps({"error": str(e)})

    elif action.lower() == "summary":
        return json.dumps({
            "active_claims_count": len(engine.list_all()),
            "unverified_assumptions": [c.to_dict() for c in engine.get_unverified_assumptions()],
            "markdown_summary": engine.render_belief_summary(),
        }, indent=2)

    return json.dumps({"error": f"Unknown action '{action}'. Must be 'register', 'update', or 'summary'."})
