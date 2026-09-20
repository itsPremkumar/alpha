"""Built-in five_pass_search tool inspired by hermes-asi-master."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.research.five_pass import FivePassSearchCompiler


@tool("compile_five_pass_search", parse_docstring=True)
def compile_five_pass_search(
    question: str,
    domain_context: str = "software engineering",
) -> str:
    """Compile a natural language question into 5 parallel search facets, including adversarial contradiction discovery.

    Funnels search across: Discovery, Specific Evidence, Adversarial Contradiction, Fact Verification, and Strategic Synthesis.

    Args:
        question: The user research question or engineering inquiry.
        domain_context: Optional domain context hint.
    """
    plan = FivePassSearchCompiler.compile(question=question, domain_context=domain_context)
    return json.dumps(plan.to_dict(), indent=2)
