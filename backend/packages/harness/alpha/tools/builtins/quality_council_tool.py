"""Built-in Quality Council deliberation tool inspired by Agent Prime.

The JSON verdict returned by this tool discloses how every confidence was
produced: each vote carries ``confidence_method`` (``"evidence"`` |
``"heuristic"`` | ``"unverified"``) and a ``confidence_note`` describing the
rule or baseline behind the number, and ``weighted_score`` is exported together
with ``weighted_score_formula``, ``weighted_score_method`` and
``confidence_baseline_disclosed: true``. Heuristic scores are labeled as such —
never presented as measured confidences — and missing execution evidence yields
the neutral unverified baseline (0.5) instead of an assumed pass.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from alpha.governance.council import QualityCouncil, RiskTier

_COUNCIL = QualityCouncil()


@tool("deliberate_artifact_quality", parse_docstring=True)
def deliberate_artifact_quality(
    artifact_name: str,
    content: str,
    risk_tier: str = "tier_2_standard",
    test_passed: bool | None = None,
    exit_code: int | None = None,
) -> str:
    """Deliberate the quality and safety of an artifact using the 5-Deliberator Council.

    Applies the Agent Prime rule: 'Never let one worker both produce and certify high-risk output.'
    The 5 specialists are: Independent Critic, Invariant Verifier, Security Reviewer,
    Quality Reviewer, and Presiding Judge.

    Args:
        artifact_name: Name or path of the artifact/file being evaluated.
        content: The code, document, or proposed action plan text.
        risk_tier: Risk level: 'tier_1_critical' (requires 4/5 quorum, zero security/invariant vetoes), 'tier_2_standard' (requires 3/5), 'tier_3_low' (requires 2/5).
        test_passed: Whether automated tests passed for this deliverable. Omit (null) when no
            test run backs the claim — the Invariant Verifier then reports the check as
            unverified (neutral 0.5 baseline) instead of assuming a pass.
        exit_code: Execution exit code (0 indicates nominal). Omit (null) when nothing was
            executed.
    """
    tier_map = {
        "tier_1_critical": RiskTier.TIER_1_CRITICAL,
        "tier_2_standard": RiskTier.TIER_2_STANDARD,
        "tier_3_low": RiskTier.TIER_3_LOW,
    }
    tier = tier_map.get(risk_tier.lower(), RiskTier.TIER_2_STANDARD)

    # Only pass through evidence the caller actually has; an absent key means
    # "not run", never an assumed pass.
    metadata: dict[str, Any] = {}
    if test_passed is not None:
        metadata["test_passed"] = test_passed
    if exit_code is not None:
        metadata["exit_code"] = exit_code

    verdict = _COUNCIL.deliberate(
        artifact_name=artifact_name,
        content=content,
        risk_tier=tier,
        metadata=metadata,
    )

    return json.dumps(verdict.to_dict(), indent=2)
