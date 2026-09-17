"""Built-in Quality Council deliberation tool inspired by Agent Prime."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.governance.council import QualityCouncil, RiskTier

_COUNCIL = QualityCouncil()


@tool("deliberate_artifact_quality", parse_docstring=True)
def deliberate_artifact_quality(
    artifact_name: str,
    content: str,
    risk_tier: str = "tier_2_standard",
    test_passed: bool = True,
    exit_code: int = 0,
) -> str:
    """Deliberate the quality and safety of an artifact using the 5-Deliberator Council.

    Applies the Agent Prime rule: 'Never let one worker both produce and certify high-risk output.'
    The 5 specialists are: Independent Critic, Invariant Verifier, Security Reviewer,
    Quality Reviewer, and Presiding Judge.

    Args:
        artifact_name: Name or path of the artifact/file being evaluated.
        content: The code, document, or proposed action plan text.
        risk_tier: Risk level: 'tier_1_critical' (requires 4/5 quorum, zero security/invariant vetoes), 'tier_2_standard' (requires 3/5), 'tier_3_low' (requires 2/5).
        test_passed: Whether automated tests passed for this deliverable.
        exit_code: Execution exit code (0 indicates nominal).
    """
    tier_map = {
        "tier_1_critical": RiskTier.TIER_1_CRITICAL,
        "tier_2_standard": RiskTier.TIER_2_STANDARD,
        "tier_3_low": RiskTier.TIER_3_LOW,
    }
    tier = tier_map.get(risk_tier.lower(), RiskTier.TIER_2_STANDARD)

    verdict = _COUNCIL.deliberate(
        artifact_name=artifact_name,
        content=content,
        risk_tier=tier,
        metadata={"test_passed": test_passed, "exit_code": exit_code},
    )

    return json.dumps(verdict.to_dict(), indent=2)
