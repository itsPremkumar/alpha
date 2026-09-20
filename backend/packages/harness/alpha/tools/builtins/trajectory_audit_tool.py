"""Built-in tool for inspecting and exporting deterministic agent execution trajectories."""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from alpha.trajectory.store import get_trajectory_store


@tool("trajectory_audit", parse_docstring=True)
def trajectory_audit_tool(
    action: Literal["inspect", "export", "list"],
    goal_id: str = "",
    output_file: str = "",
) -> str:
    """Inspect and export step-by-step reasoning and tool execution trajectories.

    Args:
        action: Operation ('inspect', 'export', 'list').
        goal_id: Identifier of the autonomous goal (required for 'inspect' and 'export').
        output_file: Target path for JSONL trajectory export (required for 'export').
    """
    store = get_trajectory_store()

    if action == "list":
        ids = store.list_goal_ids()
        if not ids:
            return "No recorded trajectories in store."
        return "=== Recorded Trajectories ===\n" + "\n".join(f"- {gid}" for gid in ids)

    elif action == "inspect":
        if not goal_id:
            return "Error: 'goal_id' is required for 'inspect'."
        trace = store.get_trajectory(goal_id)
        if not trace.steps:
            return f"No steps found for goal '{goal_id}'."
        out = [f"=== Execution Trajectory for Goal '{goal_id}' ({trace.total_steps} Steps) ==="]
        for s in trace.steps:
            out.append(
                f"[Step {s.step_index}] Tool: `{s.tool_name}` (Status: `{s.status}`)\n"
                f"- Thought: {s.thought}\n"
                f"- Output: {s.tool_output[:200]}"
                + ("..." if len(s.tool_output) > 200 else "")
            )
        return "\n".join(out)

    elif action == "export":
        if not goal_id or not output_file:
            return "Error: 'goal_id' and 'output_file' are required for 'export'."
        try:
            exported_path = store.export_jsonl(goal_id, output_file)
            return f"Successfully exported trajectory for '{goal_id}' to: {exported_path}"
        except Exception as e:
            return f"Error exporting trajectory: {e}"

    return f"Error: Unknown action '{action}'."
