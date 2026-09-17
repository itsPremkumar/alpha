"""Reflexion Tool: Epistemic Failure-Pattern & Trajectory Memory Tool.

Allows agents to query and record structured lessons learned from runtime failures.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.learning.reflexion import get_reflexion_engine


@tool
def manage_reflexion_memory(
    action: str,
    problem_signature: str | None = None,
    observed_failure: str | None = None,
    root_cause: str | None = None,
    lesson: str | None = None,
    query: str | None = None,
    confidence: float = 0.85,
    workspace: str = "",
) -> str:
    """Manage Reflexion failure memory.

    Actions:
      - 'record': Record a verified lesson learned from a failure/repair.
      - 'query': Search previous failure lessons by keyword or problem signature.

    Args:
        action: 'record' or 'query'.
        problem_signature: Identifier/pattern of the issue (e.g. 'pytest_windows_path_error').
        observed_failure: The error or symptom observed.
        root_cause: The verified underlying reason for the issue.
        lesson: The actionable rule to prevent future recurrences.
        query: Search query for retrieving historical lessons.
        confidence: Confidence score between 0.0 and 1.0 (defaults to 0.85).
        workspace: Optional workspace directory tag.
    """
    engine = get_reflexion_engine()

    if action == "record":
        if not (problem_signature and observed_failure and root_cause and lesson):
            return "Error: record requires problem_signature, observed_failure, root_cause, and lesson."
        entry = engine.record_reflection(
            problem_signature=problem_signature,
            observed_failure=observed_failure,
            root_cause=root_cause,
            lesson=lesson,
            confidence=confidence,
            workspace=workspace,
        )
        return json.dumps({
            "status": "RECORDED",
            "entry_id": entry.id,
            "problem_signature": entry.problem_signature,
            "lesson": entry.lesson,
            "created_at": entry.created_at,
        }, indent=2)

    elif action == "query":
        q = query or problem_signature or ""
        if not q.strip():
            return "Error: query text required."
        results = engine.query_reflections(q)
        return json.dumps({
            "query": q,
            "matches_found": len(results),
            "reflections": [r.to_dict() for r in results],
        }, indent=2)

    return f"Error: unknown action '{action}'. Use 'record' or 'query'."
