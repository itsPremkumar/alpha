"""Built-in Cognitive Plan Mode Tool: 8-Dimensional Decision Making & Autonomous Dispatch.

Intercepts raw user prompts and evaluates:
1. Execution Paradigm (Deep Research vs Deep Think vs Swarm vs MoA vs Bot Profile vs Subagents vs Direct)
2. Swarm Strategy Mode (Map-Reduce, Debate, Ensemble, Coding Worktree, Hierarchical)
3. Reasoning Tier (Standard, Extended Reflection, Adversarial Audit)
4. Workforce Allocation (Permanent Hermes Bots vs Ephemeral Subagents vs Hybrid)
5. Compute & Model Routing (Frontier, Fast, Local, Verifier)
6. Workspace Isolation Backend (Shared, Git Worktree, Sandbox)
7. Risk Tier (R1 Safe Read-only to R6 Destructive)
8. Proof Obligations & Quality Gate Verification

Has full capacity to immediately and autonomously dispatch the selected execution subsystem.
"""

from __future__ import annotations

import json
from typing import Literal

from langchain.tools import tool

from alpha.planning.bridge import AutonomousDispatchBridge
from alpha.planning.meta_planner import CognitiveMetaPlanner


@tool("cognitive_plan", parse_docstring=True)
def cognitive_plan(
    action: Literal["evaluate", "plan", "dispatch"] = "evaluate",
    prompt: str = "",
    items_json: str = "[]",
    max_concurrency: int = 8,
    auto_execute: bool = True,
) -> str:
    """Evaluate strategic execution parameters and autonomously trigger execution across subagents, swarms, or bots.

    Args:
        action: Operation to perform:
            - 'evaluate': Run the 8-dimensional strategic decision matrix and return the compiled strategic markdown report.
            - 'plan': Generate and return the full structured MetaPlan JSON with execution waves and proof obligations.
            - 'dispatch': Automatically evaluate the prompt, compile the MetaPlan, and execute autonomous dispatch across the selected subsystem.
        prompt: The high-level user goal, prompt, or technical objective.
        items_json: Optional JSON array of strings for batch/map-reduce tasks (e.g. '["company A", "company B"]').
        max_concurrency: Maximum worker concurrency limit for parallel execution waves (default 8).
        auto_execute: If True (default), automatically triggers execution of the compiled plan.
    """
    if not prompt.strip():
        return "Error: 'prompt' parameter is required for cognitive planning."

    # Parse batch items if present
    items: list[str] = []
    if items_json:
        try:
            parsed = json.loads(items_json)
            if isinstance(parsed, list):
                items = [str(x) for x in parsed]
        except Exception:
            items = []

    # 1. Compile MetaPlan
    meta_plan = CognitiveMetaPlanner.evaluate_and_plan(
        prompt=prompt.strip(),
        items=items if items else None,
        max_concurrency=max_concurrency,
    )

    # ACTION: EVALUATE
    if action == "evaluate":
        return meta_plan.markdown_report

    # ACTION: PLAN
    if action == "plan":
        return json.dumps(meta_plan.to_dict(), indent=2)

    # ACTION: DISPATCH
    if action == "dispatch":
        if not auto_execute:
            return f"### Plan Compiled (Auto-Execution Disabled)\n\n{meta_plan.markdown_report}\n\nPass `auto_execute=True` to trigger immediate dispatch."

        dispatch_result = AutonomousDispatchBridge.dispatch(meta_plan)
        return (
            f"### 🚀 Autonomous Dispatch Executed\n"
            f"- **Dispatch ID**: `{dispatch_result.dispatch_id}`\n"
            f"- **Plan ID**: `{dispatch_result.plan_id}`\n"
            f"- **Paradigm**: `{dispatch_result.paradigm}`\n"
            f"- **Status**: `{dispatch_result.status}`\n"
            f"- **Execution Reference**: `{dispatch_result.execution_id or 'N/A'}`\n"
            f"- **Assigned Agents**: `{', '.join(dispatch_result.assigned_agents) or 'None'}`\n"
            f"- **Summary**: {dispatch_result.summary}\n"
            f"- **Artifacts Generated**: `{', '.join(dispatch_result.artifacts) or 'None'}`\n"
        )

    return f"Unknown action '{action}'. Valid actions are: evaluate, plan, dispatch."
