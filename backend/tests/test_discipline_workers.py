from agent_workspace.orchestration.discipline.recon import FastReconWorker, ReconResult
from agent_workspace.orchestration.discipline.ultrabrain import UltrabrainSolution, UltrabrainWorker
from agent_workspace.orchestration.discipline.visual_engineering import (
    VisualEngineeringWorker,
    VisualWidgetSpec,
)


def test_visual_engineering_worker():
    worker = VisualEngineeringWorker()
    widget = worker.build_component(
        component_name="MetricsCard",
        requirements="Display active users and throughput stats",
        primary_color="#10B981",
        theme="dark",
    )

    assert isinstance(widget, VisualWidgetSpec)
    assert widget.component_name == "MetricsCard"
    assert widget.model_family == "anthropic/claude-3-7-sonnet"
    assert "--primary" in widget.design_tokens
    assert widget.design_tokens["--primary"] == "#10B981"
    assert "widget-card" in widget.css_styles
    assert "<div class=\"widget-card\"" in widget.html_markup

    html = widget.render_standalone_html()
    assert "<!DOCTYPE html>" in html
    assert "<title>MetricsCard</title>" in html
    assert "handleWidgetAction" in html


def test_ultrabrain_worker():
    worker = UltrabrainWorker()
    solution = worker.solve_goal(
        goal_statement="Compute shortest paths in streaming graph",
        constraints=["Sub-linear space", "Idempotency"],
    )

    assert isinstance(solution, UltrabrainSolution)
    assert solution.model_family == "openai/gpt-4o"
    assert "shortest paths" in solution.goal
    assert solution.time_complexity != ""
    assert solution.space_complexity != ""
    assert "UltrabrainEngine" in solution.code_implementation
    assert any("Sub-linear space" in inv for inv in solution.invariants_maintained)


def test_fast_recon_worker():
    worker = FastReconWorker()

    # Explore mode (symbols)
    tree = [
        "backend/packages/harness/agent_workspace/agents/planner.py",
        "backend/packages/harness/agent_workspace/orchestration/discipline/ultrabrain.py",
        "backend/tests/test_planner.py",
    ]
    res_code = worker.search_codebase_symbols("discipline", file_tree=tree)
    assert isinstance(res_code, ReconResult)
    assert res_code.recon_type == "explore"
    assert len(res_code.matches) == 1
    assert "ultrabrain.py" in res_code.matches[0]["file"]
    assert res_code.model_family == "openai/gpt-5.6-luna-fast"

    # Librarian mode (docs)
    docs = {
        "architecture.md": "Discipline teams map specific AI model families to specialized reasoning disciplines.",
        "readme.md": "Alpha Agent Harness overview and quickstart.",
    }
    res_doc = worker.search_documentation("discipline", available_docs=docs)
    assert res_doc.recon_type == "librarian"
    assert len(res_doc.matches) == 1
    assert res_doc.matches[0]["doc_name"] == "architecture.md"
