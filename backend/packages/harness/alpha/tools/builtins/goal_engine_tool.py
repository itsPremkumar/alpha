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

    # A corrupt store used to answer "No active autonomous goals." here, which
    # told the model there was no work when the truth was that its work could
    # not be read. Say so instead, and name the file so the operator can act.
    if store.is_degraded:
        return f"Error: the goal store at {store.storage_path} could not be read ({store.load_error}). Goal state is UNKNOWN — this is not an empty catalog. Repair or remove the file, then retry."

    if action == "list":
        goals = store.list_goals()
        if not goals:
            return "No autonomous goals are recorded."
        counts: dict[str, int] = {}
        for goal in goals:
            counts[goal.status] = counts.get(goal.status, 0) + 1
        out = [
            f"=== Autonomous Goal Catalog ({len(goals)} total) ===",
            "Status counts: " + ", ".join(f"{status}={count}" for status, count in sorted(counts.items())),
        ]
        limit = 50
        for g in goals[:limit]:
            out.append(f"- **{g.goal_id}**: {g.title} [Status: `{g.status}`, Iteration: {g.iteration}/{g.max_iterations}]")
        if len(goals) > limit:
            out.append(f"- {len(goals) - limit} additional goal(s) omitted; inspect a goal by ID for details.")
        return "\n".join(out)

    elif action == "start":
        if not title:
            return "Error: 'title' is required for 'start'."
        try:
            goal = runner.start_goal(title=title, description=description, max_iterations=max_iterations)
            if not store.is_durable:
                return f"Error: goal '{goal.goal_id}' was created in memory but could NOT be persisted to {store.storage_path} ({store.save_error}). It is not durable."
            return f"Continuous Autonomous Goal initialized: {goal.goal_id}\n- Title: {goal.title}\n- Status: {goal.status}\n- Milestones provisioned: {len(goal.milestones)}"
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
        if not store.is_durable:
            return f"Error: step for goal '{goal_id}' was not durably persisted ({store.save_error}). Outcome may be incomplete."
        if res.get("status") == "awaiting_executor":
            return f"Goal step was not executed for {goal_id}: {res.get('message')} The goal remains {res.get('goal_status')}; do not report progress or verification."
        return f"Step executed on {goal_id}: Status={res.get('status')}, Milestone={res.get('milestone_id')}, Success={res.get('success')}."

    elif action == "pause":
        if not goal_id:
            return "Error: 'goal_id' is required for 'pause'."
        goal = store.get_goal(goal_id)
        if goal is None:
            return f"Error: goal '{goal_id}' was not found."
        if goal.status in {"achieved", "blocked"}:
            return f"Error: goal '{goal_id}' is already {goal.status} and cannot be paused."
        if goal.status == "paused":
            return f"Goal {goal_id} is already paused."
        goal = store.update_goal_status(goal_id, "paused", "Paused by operator tool command.")
        if goal is None or not store.is_durable:
            return f"Error: goal '{goal_id}' could NOT be durably paused ({store.save_error or 'goal not found'})."
        return f"Goal {goal_id} status updated to: paused."

    elif action == "resume":
        if not goal_id:
            return "Error: 'goal_id' is required for 'resume'."
        goal = store.get_goal(goal_id)
        if goal is None:
            return f"Error: goal '{goal_id}' was not found."
        if goal.status != "paused":
            return f"Error: goal '{goal_id}' is {goal.status}, not paused."
        goal = store.update_goal_status(goal_id, "executing", "Resumed by operator tool command.")
        if goal is None or not store.is_durable:
            return f"Error: goal '{goal_id}' status could NOT be persisted to {store.storage_path} ({store.save_error or 'goal not found'}). The goal is NOT durably resumed."
        return f"Goal {goal_id} status updated to: executing."

    return f"Error: Unknown action '{action}'."
