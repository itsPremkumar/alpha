"""Category Team Dispatcher (Enterprise Multi-Model Discipline Council Engine).

Enterprise Category Dispatch:
- Main agent delegates by Category, not by hardcoded vendor models
- Category Mapping:
    • visual-engineering / architect / artistry / writing -> Claude 3.7 Sonnet
    • ultrabrain / deep / plan-reviewer -> GPT-4o
    • explore / librarian / quick -> Fast utility models
"""

from __future__ import annotations

import time
from typing import Any

from agent_workspace.orchestration.discipline.consultant import PlanConsultant
from agent_workspace.orchestration.discipline.recon import FastReconWorker
from agent_workspace.orchestration.discipline.reviewer import PlanReviewer
from agent_workspace.orchestration.discipline.ultrabrain import UltrabrainWorker
from agent_workspace.orchestration.discipline.visual_engineering import VisualEngineeringWorker


class CategoryTeamDispatcher:
    """Routes and dispatches tasks to specialist models based on task nature."""

    def __init__(self):
        self.visual_worker = VisualEngineeringWorker()
        self.ultrabrain_worker = UltrabrainWorker()
        self.consultant = PlanConsultant()
        self.reviewer = PlanReviewer()
        self.recon_worker = FastReconWorker()

    def dispatch_work(
        self,
        category: str,
        task_prompt: str,
        context: dict[str, Any] | None = None,
        constraints: list[str] | None = None,
    ) -> dict[str, Any]:
        """Dispatches work to the designated specialist profile and returns structured result."""
        cat = category.lower().strip()
        ctx = context or {}
        consts = constraints or []
        t_start = time.time()

        if cat in ("visual-engineering", "visual", "frontend", "ui", "ux"):
            component_name = ctx.get("component_name", "InteractiveComponent")
            widget = self.visual_worker.build_component(
                component_name=component_name,
                requirements=task_prompt,
                theme=ctx.get("theme", "dark"),
            )
            result_data = {
                "category": "visual-engineering",
                "model_used": "anthropic/claude-3-7-sonnet",
                "reasoning_level": "max",
                "widget": widget.to_dict(),
                "rendered_html": widget.render_standalone_html(),
            }

        elif cat in ("ultrabrain", "deep", "algorithmic"):
            solution = self.ultrabrain_worker.solve_goal(
                goal_statement=task_prompt,
                constraints=consts,
                context_data=ctx,
            )
            result_data = {
                "category": "ultrabrain",
                "model_used": "openai/gpt-4o",
                "reasoning_level": "max",
                "solution": solution.to_dict(),
            }

        elif cat in ("plan-consultant", "consultant"):
            report = self.consultant.analyze_gaps(
                task_title=ctx.get("task_title", "Task Plan"),
                task_description=task_prompt,
                proposed_steps=ctx.get("proposed_steps", []),
                is_visual_or_frontend=ctx.get("is_visual", False),
            )
            result_data = {
                "category": "plan-consultant",
                "model_used": "anthropic/claude-3-7-sonnet",
                "reasoning_level": "high",
                "gap_report": report.to_dict(),
            }

        elif cat in ("plan-reviewer", "reviewer"):
            verdict = self.reviewer.review_plan(
                task_goal=task_prompt,
                constraints=consts,
                proposed_steps=ctx.get("proposed_steps", []),
            )
            result_data = {
                "category": "plan-reviewer",
                "model_used": "openai/gpt-4o",
                "reasoning_level": "xhigh",
                "verdict": verdict.to_dict(),
            }

        elif cat in ("explore", "librarian", "quick", "search"):
            recon = self.recon_worker.search_codebase_symbols(
                symbol_query=task_prompt,
                file_tree=ctx.get("file_tree", []),
            )
            result_data = {
                "category": "explore",
                "model_used": "openai/gpt-5.6-luna-fast",
                "reasoning_level": "low",
                "recon": recon.to_dict(),
            }

        else:
            # General fallback to ultrabrain
            solution = self.ultrabrain_worker.solve_goal(
                goal_statement=task_prompt,
                constraints=consts,
            )
            result_data = {
                "category": cat,
                "model_used": "openai/gpt-4o",
                "reasoning_level": "high",
                "solution": solution.to_dict(),
            }

        result_data["elapsed_ms"] = round((time.time() - t_start) * 1000.0, 2)
        return result_data

    # Backward compatibility alias
    dispatch = dispatch_work
