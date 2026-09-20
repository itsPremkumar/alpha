import json

from alpha.orchestration.discipline.team_dispatcher import CategoryTeamDispatcher
from alpha.tools.builtins import (
    consult_plan_gap_analysis,
    dispatch_discipline_worker,
    review_plan_invariant_gate,
)


def test_category_team_dispatcher_routes_correctly():
    dispatcher = CategoryTeamDispatcher()

    # Visual engineering dispatch
    vis_res = dispatcher.dispatch(
        category="visual-engineering",
        task_prompt="Create interactive status dashboard",
        context={"component_name": "StatusDashboard", "theme": "dark"},
    )
    assert vis_res["category"] == "visual-engineering"
    assert vis_res["model_used"] == "anthropic/claude-3-7-sonnet"
    assert "widget" in vis_res
    assert "<!DOCTYPE html>" in vis_res["rendered_html"]

    # Ultrabrain dispatch
    brain_res = dispatcher.dispatch(
        category="ultrabrain",
        task_prompt="Design consensus algorithm across distributed workers",
        constraints=["Byzantine fault tolerance"],
    )
    assert brain_res["category"] == "ultrabrain"
    assert brain_res["model_used"] == "openai/gpt-4o"
    assert "solution" in brain_res
    assert brain_res["reasoning_level"] == "max"

    # Plan consultant dispatch
    consult_res = dispatcher.dispatch(
        category="plan-consultant",
        task_prompt="Implement cache eviction policy",
        context={"proposed_steps": ["Add LRU cache", "Store entries"]},
    )
    assert consult_res["category"] == "plan-consultant"
    assert consult_res["model_used"] == "anthropic/claude-3-7-sonnet"
    assert "gap_report" in consult_res

    # Plan reviewer dispatch
    review_res = dispatcher.dispatch(
        category="plan-reviewer",
        task_prompt="Format drive",
        context={"proposed_steps": ["rm -rf /"]},
    )
    assert review_res["category"] == "plan-reviewer"
    assert review_res["model_used"] == "openai/gpt-4o"
    assert review_res["verdict"]["approved"] is False

    # Explore dispatch
    explore_res = dispatcher.dispatch(
        category="explore",
        task_prompt="consultant",
        context={"file_tree": ["src/consultant.py", "src/main.py"]},
    )
    assert explore_res["category"] == "explore"
    assert explore_res["model_used"] == "openai/gpt-5.6-luna-fast"


def test_consult_plan_gap_analysis_tool():
    res = consult_plan_gap_analysis.invoke({
        "task_title": "Database Cleanup",
        "task_description": "Clean temporary tables without test coverage",
        "proposed_steps_csv": "Drop temp tables, Exit",
        "is_visual_or_frontend": False,
    })
    parsed = json.loads(res)
    assert "gaps_identified" in parsed
    assert parsed["model_family"] == "anthropic/claude-3-7-sonnet"
    assert parsed["readiness_score"] < 1.0


def test_review_plan_invariant_gate_tool():
    # Dangerous step rejected
    res_rejected = review_plan_invariant_gate.invoke({
        "task_goal": "Clean project files",
        "proposed_steps_csv": "git push --force origin main, remove cache",
        "constraints_csv": "no data loss",
        "budget_cap_usd": 5.0,
    })
    parsed_rej = json.loads(res_rejected)
    assert parsed_rej["approved"] is False
    assert parsed_rej["model_family"] == "openai/gpt-4o"

    # Sound step approved
    res_approved = review_plan_invariant_gate.invoke({
        "task_goal": "Run test verification",
        "proposed_steps_csv": "Execute pytest backend/tests, Assert exit code 0",
        "constraints_csv": "read only",
        "budget_cap_usd": 5.0,
    })
    parsed_app = json.loads(res_approved)
    assert parsed_app["approved"] is True


def test_dispatch_discipline_worker_tool():
    res = dispatch_discipline_worker.invoke({
        "category": "visual-engineering",
        "task_prompt": "Render memory usage meter",
        "context_json": json.dumps({"component_name": "MemoryMeter"}),
        "constraints_csv": "",
    })
    parsed = json.loads(res)
    assert parsed["category"] == "visual-engineering"
    assert parsed["model_used"] == "anthropic/claude-3-7-sonnet"
    assert "widget" in parsed
