"""Built-in curriculum tool inspired by hermes-asi-master."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.learning.curriculum import CurriculumBuilder


@tool("generate_curriculum_plan", parse_docstring=True)
def generate_curriculum_plan(
    performance_map_json: str,
    target_score: float = 0.90,
) -> str:
    """Analyze agent capability gaps and generate an autonomous training curriculum.

    Computes urgency priority = (Target - Current) / Difficulty and schedules targeted exercises.

    Args:
        performance_map_json: JSON mapping capability names to current empirical scores (e.g. '{"ast_refactor": 0.6}').
        target_score: Desired benchmark threshold (0.0 to 1.0).
    """
    try:
        perf = json.loads(performance_map_json) if performance_map_json else {}
    except Exception:
        perf = {}

    builder = CurriculumBuilder(default_target_score=target_score)
    curriculum = builder.build_curriculum(performance_map=perf)

    return json.dumps(curriculum.to_dict(), indent=2)
