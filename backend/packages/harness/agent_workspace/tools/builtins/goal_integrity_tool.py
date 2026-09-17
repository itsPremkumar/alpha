"""Built-in LangChain tool for Goal Integrity & Scope Creep Auditing."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.planning.integrity import GoalIntegrityEngine


@tool("goal_integrity", parse_docstring=True)
def goal_integrity_tool(
    action: str,
    mission_goal: str = "",
    subtasks_json: str = "[]",
) -> str:
    """Audit plans and proposed subtasks for goal drift, scope creep, and overengineering.

    Args:
        action: 'audit_plan', 'check_drift'.
        mission_goal: The high-level primary mission or user prompt objective.
        subtasks_json: JSON string representing list of subtasks (strings or dicts with 'description').
    """
    try:
        if action in ("audit_plan", "check_drift"):
            if not mission_goal:
                return "Error: 'mission_goal' is required for goal integrity audit."

            try:
                subtasks = json.loads(subtasks_json) if subtasks_json else []
            except Exception:
                subtasks = [subtasks_json]

            report = GoalIntegrityEngine.audit_plan(
                mission_goal=mission_goal,
                subtasks=subtasks,
            )
            return json.dumps(report.model_dump(), indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error executing goal_integrity tool: {exc}"
