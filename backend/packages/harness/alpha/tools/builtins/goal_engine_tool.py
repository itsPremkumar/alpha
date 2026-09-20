"""Built-in tool for managing continuous goal-driven autonomous executions."""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from alpha.harness.continuous.runner import get_goal_runner
from alpha.harness.continuous.store import get_goal_store


@tool("goal_engine", parse_docstring=True)
def goal_engine_tool(
    action: Literal["start", "inspect", "step", "pause", "resume", "list"],
    goal_id: str = "",
    title: str = "",
    description: str = "",
    max_iterations: int = 100,
) -> str:
    """Manage continuous, goal-driven autonomous execution loops with self-healing.

    Args:
        action: Operation to perform ('start', 'inspect', 'step', 'pause', 'resume', 'list').
        goal_id: Identifier of the goal (required for inspect, step, pause, resume).
        title: High-level title of the objective (required for 'start').
        description: Detailed requirements and acceptance criteria (optional for 'start').
        max_iterations: Maximum iterations before pausing (default: 100).
    """
    runner = get_goal_runner()
    store = get_goal_store()

    if action == "list":
        goals = store.list_goals()
        if not goals:
            return "No active autonomous goals."
        out = ["=== Autonomous Goal Catalog ==="]
        for g in goals:
            out.append(f"- **{g.goal_id}**: {g.title} [Status: `{g.status}`, Iteration: {g.iteration}/{g.max_iterations}]")
        return "\n".join(out)

    elif action == "start":
        if not title:
            return "Error: 'title' is required for 'start'."
        try:
            goal = runner.start_goal(title=title, description=description, max_iterations=max_iterations)
            return (
                f"Continuous Autonomous Goal initialized: {goal.goal_id}\n"
                f"- Title: {goal.title}\n"
                f"- Status: {goal.status}\n"
                f"- Milestones provisioned: {len(goal.milestones)}"
            )
        except Exception as e:
            return f"Error starting goal: {e}"

    elif action == "inspect":
        if not goal_id:
            return "Error: 'goal_id' is required for 'inspect'."
        goal = store.get_goal(goal_id)
        if not goal:
            return f"Error: Goal '{goal_id}' not found."
        out = [
            f"=== Autonomous Goal: {goal.goal_id} ===",
            f"Title: {goal.title}",
            f"Status: {goal.status} (Iteration {goal.iteration}/{goal.max_iterations})",
            f"Heartbeat: {goal.heartbeat_at}",
            "\n--- Milestones ---",
        ]
        for m in goal.milestones.values():
            deps = f" (Depends on: {m.dependencies})" if m.dependencies else ""
            out.append(f"- [{m.status.upper()}] {m.milestone_id}: {m.title}{deps} (Attempts: {m.attempts})")
        if goal.strategy_notes:
            out.append("\n--- Strategy Adaptation Notes ---")
            out.extend(goal.strategy_notes[-5:])
        return "\n".join(out)

    elif action == "step":
        if not goal_id:
            return "Error: 'goal_id' is required for 'step'."
        res = runner.step(goal_id)
        return f"Step executed on {goal_id}: Status={res.get('status')}, Milestone={res.get('milestone_id')}, Success={res.get('success')}."

    elif action == "pause":
        if not goal_id:
            return "Error: 'goal_id' is required for 'pause'."
        goal = store.update_goal_status(goal_id, "paused", "Paused by operator tool command.")
        return f"Goal {goal_id} status updated to: paused."

    elif action == "resume":
        if not goal_id:
            return "Error: 'goal_id' is required for 'resume'."
        goal = store.update_goal_status(goal_id, "executing", "Resumed by operator tool command.")
        return f"Goal {goal_id} status updated to: executing."

    return f"Error: Unknown action '{action}'."
