"""Problem Model Tool: Structured Task Representation before Execution.

Enforces NVIDIA AVO Section 6: Decomposes raw prompts into structured 12-factor
problem models (constraints, entities, risks, verification methods, success metrics)
before irreversible actions are performed.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.orchestration.problem_model import ProblemModelCompiler


@tool("compile_problem_model", parse_docstring=True)
def compile_problem_model(
    goal: str,
    as_markdown: bool = True,
) -> str:
    """Compile a raw goal or user prompt into a structured 12-factor AVO Problem Model.

    Analyzes objective, domain, entities, explicit constraints, underlying assumptions,
    operational risks, success metrics, and required empirical verification methods.

    Args:
        goal: The user prompt or complex task objective.
        as_markdown: Return formatted markdown (True) or raw JSON structure (False).
    """
    if not goal.strip():
        return "Error: goal text cannot be empty."

    model = ProblemModelCompiler.compile(goal)

    if as_markdown:
        return model.to_markdown()

    return json.dumps(model.to_dict(), indent=2)
