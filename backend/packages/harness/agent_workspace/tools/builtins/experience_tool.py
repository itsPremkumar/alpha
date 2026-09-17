"""Built-in consult_experience tool for querying and updating episodic experience memory."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.learning.experience.models import ExperienceRecord, OutcomeType
from agent_workspace.learning.experience.retriever import ExperienceRetriever
from agent_workspace.learning.experience.store import ExperienceStore


@tool("consult_experience", parse_docstring=True)
def consult_experience(
    query: str,
    action: str = "query",
    outcome: str = "success",
    lessons: list[str] | None = None,
    pitfalls: list[str] | None = None,
) -> str:
    """Consult or update the Episodic Experience Memory to retrieve past lessons or record new ones.

    Action 'query': Retrieves relevant past failure modes, lessons learned, and guidelines for the current task.
    Action 'record': Stores a newly discovered lesson or resolution pattern into the persistent episodic memory.

    Args:
        query: The task goal or technical query to search for lessons on (or title of experience if recording).
        action: Either 'query' (default) or 'record'.
        outcome: Outcome of the task if recording ('success', 'failure', 'partial').
        lessons: List of lessons learned to record (for action 'record').
        pitfalls: List of pitfalls encountered to record (for action 'record').
    """
    store = ExperienceStore()
    retriever = ExperienceRetriever(store=store)

    if action.lower() == "query":
        matches = retriever.retrieve(query, limit=3)
        prompt_md = retriever.render_lessons_prompt(query, limit=3)

        return json.dumps({
            "query": query,
            "match_count": len(matches),
            "experiences": [m.to_dict() for m in matches],
            "markdown_advice": prompt_md,
        }, indent=2)

    elif action.lower() == "record":
        rec = ExperienceRecord(
            task_goal=query,
            outcome=OutcomeType(outcome.lower()),
            lessons_learned=lessons or [],
            pitfalls_to_avoid=pitfalls or [],
        )
        store.record(rec)
        return json.dumps({
            "status": "recorded",
            "experience_id": rec.experience_id,
            "task_goal": rec.task_goal,
        }, indent=2)

    return json.dumps({"error": f"Invalid action '{action}'. Must be 'query' or 'record'."})
