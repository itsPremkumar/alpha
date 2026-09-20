"""Built-in compile_mission tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.mission.compiler import MissionCompiler


@tool("compile_mission", parse_docstring=True)
def compile_mission(
    raw_request: str,
) -> str:
    """Compile an ambiguous user prompt into a formal, structured Mission contract with R0-R6 risk classification.

    Extracts core intent, latent needs, hard/soft/forbidden constraints, acceptance criteria,
    and verifiable proof obligations before autonomous execution begins.

    Args:
        raw_request: The natural language objective or user request.
    """
    compiler = MissionCompiler()
    mission = compiler.compile(raw_request)

    return json.dumps({
        "mission_id": mission.id,
        "interpreted_intent": mission.interpreted_intent,
        "latent_needs": mission.latent_needs,
        "desired_outcome": mission.desired_outcome,
        "risk_tier": mission.risk_tier.value,
        "constraints": mission.constraints,
        "acceptance_criteria": mission.acceptance_criteria,
        "proof_obligations": [p.to_dict() for p in mission.proof_obligations],
        "markdown_contract": mission.to_markdown(),
    }, indent=2)
