"""Built-in agency_competence tool inspired by hermes-asi-master."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.agency import CompetenceTracker, CuriosityScorer, MotivationArbiter

_GLOBAL_COMPETENCE_TRACKER = CompetenceTracker()
_GLOBAL_CURIOSITY_SCORER = CuriosityScorer()
_GLOBAL_MOTIVATION_ARBITER = MotivationArbiter()


@tool("evaluate_agent_competence", parse_docstring=True)
def evaluate_agent_competence(
    domain: str,
    record_attempt_success: str = "",
    situation_json: str = "",
) -> str:
    """Evaluate statistical competence using 95% Wilson confidence intervals and curiosity scoring.

    Args:
        domain: Domain or skill area to query (e.g. 'code_generation', 'sql_optimization').
        record_attempt_success: Optional: 'true' to record success, 'false' to record failure.
        situation_json: Optional situation dictionary to compute prediction-error curiosity.
    """
    if record_attempt_success.lower() in ("true", "1", "yes"):
        _GLOBAL_COMPETENCE_TRACKER.record_attempt(domain, success=True)
    elif record_attempt_success.lower() in ("false", "0", "no"):
        _GLOBAL_COMPETENCE_TRACKER.record_attempt(domain, success=False)

    competence = _GLOBAL_COMPETENCE_TRACKER.get_competence(domain)

    curiosity = 0.5
    if situation_json:
        try:
            sit = json.loads(situation_json)
            c_score = _GLOBAL_CURIOSITY_SCORER.score(sit)
            curiosity = c_score.score
        except Exception:
            pass

    dominant_mode, total_motivation = _GLOBAL_MOTIVATION_ARBITER.arbitrate(
        curiosity_score=curiosity,
        competence_score=competence,
    )

    return json.dumps({
        "domain": domain,
        "wilson_confidence_95": competence,
        "curiosity_score": curiosity,
        "dominant_mode": dominant_mode,
        "total_motivation": total_motivation,
    }, indent=2)
