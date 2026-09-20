"""Built-in tool for executing and identifying autonomous slash commands.

Gives the LLM agent full access to:
1. Execute any of the 418 Master Slash Commands across all 28 categories
2. Query the Autonomous Command Engine to detect which slash command is needed for the current phase
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from langchain.tools import tool

from alpha.commands.autonomous_engine import LifecyclePhase, autonomous_command_engine
from alpha.commands.registry import command_registry


@tool("execute_slash_command", parse_docstring=True)
def execute_slash_command_tool(
    command_line: str,
) -> str:
    """Execute a Master Slash Command from the 418-command catalog across 28 categories.

    Args:
        command_line: The full command line to execute (e.g. '/verify all', '/goal create <objective>', '/plan mode:auto', '/self-heal', '/research deep <query>').
    """
    if not command_line.strip():
        return "Error: Empty command provided."

    res = command_registry.execute(command_line)
    output_parts = [
        f"=== Slash Command Result: {res.command} [{res.status.upper()}] ===",
        res.output,
    ]
    if res.autonomous_directives:
        output_parts.append("\nAutonomous Directives:")
        for d in res.autonomous_directives:
            output_parts.append(f"- {d}")

    if res.data:
        output_parts.append(f"\nMetadata: {res.data}")

    return "\n".join(output_parts)


@tool("identify_autonomous_command", parse_docstring=True)
def identify_autonomous_command_tool(
    current_intent_or_error: str,
    phase: str = "",
) -> str:
    """Identify which Master Slash Command to execute for the current task, error, or lifecycle stage.

    Args:
        current_intent_or_error: Description of what the agent is doing or the error encountered.
        phase: Optional lifecycle phase ('planning', 'research', 'swarm', 'coding', 'verification', 'self_heal', 'reflection', 'schedule').
    """
    phase_enum = None
    if phase:
        try:
            phase_enum = LifecyclePhase(phase.lower())
        except ValueError:
            pass

    detection = autonomous_command_engine.identify_and_trigger(
        prompt=current_intent_or_error,
        phase_hint=phase_enum,
        auto_execute=False,
    )

    if not detection.matched:
        return f"No special slash command needed: {detection.reason}"

    return (
        f"Autonomous Recommendation:\n"
        f"- Target Command: `{detection.command}`\n"
        f"- Phase: `{detection.phase.value}`\n"
        f"- Confidence: {detection.confidence}\n"
        f"- Reason: {detection.reason}\n"
        f"- Next Action: Call execute_slash_command('{detection.command}')"
    )
