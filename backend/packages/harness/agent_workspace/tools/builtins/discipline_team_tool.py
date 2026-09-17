"""Built-in Multi-Model Discipline Team LangChain Tools (Enterprise Discipline Engine)."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.orchestration.discipline import (
    CategoryTeamDispatcher,
    PlanConsultant,
    PlanReviewer,
)

_GLOBAL_DISPATCHER = CategoryTeamDispatcher()
_GLOBAL_CONSULTANT = PlanConsultant()
_GLOBAL_REVIEWER = PlanReviewer()


@tool("consult_plan_gap_analysis", parse_docstring=True)
def consult_plan_gap_analysis(
    task_title: str,
    task_description: str,
    proposed_steps_csv: str = "",
    is_visual_or_frontend: bool = False,
) -> str:
    """Run pre-planning gap analysis (powered by Enterprise Strategic Consultant profile) to catch edge cases and UI requirements.

    Args:
        task_title: Name/title of the planned task.
        task_description: High-level requirements or user prompt.
        proposed_steps_csv: Comma-separated list of planned execution steps.
        is_visual_or_frontend: True if the task includes frontend, UI/UX, or canvas components.
    """
    try:
        steps = [s.strip() for s in proposed_steps_csv.split(",") if s.strip()]
        report = _GLOBAL_CONSULTANT.analyze_gaps(
            task_title=task_title,
            task_description=task_description,
            proposed_steps=steps,
            is_visual_or_frontend=is_visual_or_frontend,
        )
        return json.dumps(report.to_dict(), indent=2)
    except Exception as exc:
        return f"Error in plan consultation: {exc}"


@tool("review_plan_invariant_gate", parse_docstring=True)
def review_plan_invariant_gate(
    task_goal: str,
    proposed_steps_csv: str,
    constraints_csv: str = "",
    budget_cap_usd: float = 10.0,
) -> str:
    """Rigorous plan invariant review (powered by Enterprise Invariant Gatekeeper profile) to prevent rubber-stamping and defects.

    Args:
        task_goal: End goal or objective statement.
        proposed_steps_csv: Comma-separated list of execution steps.
        constraints_csv: Comma-separated constraints to enforce.
        budget_cap_usd: Dollar cost budget limit.
    """
    try:
        steps = [s.strip() for s in proposed_steps_csv.split(",") if s.strip()]
        consts = [c.strip() for c in constraints_csv.split(",") if c.strip()]
        verdict = _GLOBAL_REVIEWER.review_plan(
            task_goal=task_goal,
            constraints=consts,
            proposed_steps=steps,
            budget_cap_usd=budget_cap_usd,
        )
        return json.dumps(verdict.to_dict(), indent=2)
    except Exception as exc:
        return f"Error in plan invariant review: {exc}"


@tool("dispatch_discipline_worker", parse_docstring=True)
def dispatch_discipline_worker(
    category: str,
    task_prompt: str,
    context_json: str = "{}",
    constraints_csv: str = "",
) -> str:
    """Dispatch work by category to specialized discipline models (e.g. visual engineering, algorithmic ultrabrain, plan reviewer).

    Args:
        category: Task category ('visual-engineering', 'ultrabrain', 'deep', 'plan-consultant', 'plan-reviewer', 'explore').
        task_prompt: Specific task instruction or goal statement.
        context_json: Optional JSON context dictionary (e.g. component_name, proposed_steps, file_tree).
        constraints_csv: Comma-separated list of hard constraints.
    """
    try:
        ctx = json.loads(context_json) if context_json else {}
    except Exception:
        ctx = {"raw": context_json}

    try:
        consts = [c.strip() for c in constraints_csv.split(",") if c.strip()]
        res = _GLOBAL_DISPATCHER.dispatch(
            category=category,
            task_prompt=task_prompt,
            context=ctx,
            constraints=consts,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        return f"Error dispatching discipline worker: {exc}"
