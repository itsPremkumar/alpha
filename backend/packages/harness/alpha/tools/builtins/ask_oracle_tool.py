"""Built-in ask_oracle tool inspired by OpenHands."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.agent.oracle.service import OracleService


@tool("ask_oracle", parse_docstring=True)
def ask_oracle(
    query: str,
    technical_domain: str | None = None,
) -> str:
    """Consult the isolated Oracle advisor on technical standards, API contracts, or architecture best practices.

    Uses an isolated subagent interface to retrieve authoritative guidance without polluting the main
    agent's scratchpad or execution context.

    Args:
        query: Specific technical question, library API lookup, or architectural decision query.
        technical_domain: Optional domain tag (e.g. 'asyncio', 'docker', 'git', 'database').
    """
    oracle = OracleService()
    resp = oracle.consult(query=query, technical_domain=technical_domain)

    return json.dumps({
        "query": resp.query,
        "guidance": resp.guidance,
        "best_practices": resp.best_practices,
        "common_pitfalls": resp.common_pitfalls,
        "references": resp.references,
        "confidence": resp.confidence,
        "markdown": resp.to_markdown(),
    }, indent=2)
