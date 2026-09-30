"""Built-in tool for executing and identifying autonomous slash commands.

Gives the LLM agent full access to:
1. Execute any of the Master Slash Commands across all 28 categories
2. Query the Autonomous Command Engine to detect which slash command is needed for the current phase

Honesty contract: the first line of every ``execute_slash_command`` result is a
plain-language verdict. A catalog row with no bound handler answers
``status="unimplemented"`` (recognised; nothing ran) rather than ``success``, so
the status alone already says whether anything executed — the verdict spells it
out. Failures, unknown commands, approval gates and timeouts can never read as
success.

This tool is also the one caller for which "the model can just do it instead" is
a true statement: the model holds the context, the tools and the turn, so a
handler-less row is handed back to it as work rather than as a dead end. That is
said here and nowhere else, because no such consumer exists on the HTTP path.
"""

from __future__ import annotations

import os
from typing import Any

from langchain.tools import tool

from alpha.commands.autonomous_engine import LifecyclePhase, autonomous_command_engine
from alpha.commands.registry import UNIMPLEMENTED_STATUS, command_registry

#: Hard ceiling on one slash-command dispatch from the agent tool. Commands such
#: as ``/grill-me`` make a real model turn, so an unbounded call can wedge the
#: agent loop; exceeding the ceiling is reported as a timeout, never swallowed.
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0

#: Registry status -> plain verdict the model reads first.
_VERDICTS: dict[str, str] = {
    "success": "SUCCEEDED",
    "ok": "SUCCEEDED",
    "error": "FAILED",
    "not_found": "FAILED (unknown command)",  # narrowed to "target not found" below when the command itself resolved
    "approval_required": "BLOCKED (needs human approval)",
    "timeout": "FAILED (timed out)",
    # Recognised, no handler, nothing ran. Not a failure either: the model is
    # holding this request and can carry it out itself.
    UNIMPLEMENTED_STATUS: "NOT EXECUTED (no handler is bound to this command; nothing ran)",
}


def _timeout_seconds(override: float | None = None) -> float:
    if override is not None and override > 0:
        return float(override)
    raw = os.environ.get("ALPHA_SLASH_COMMAND_TIMEOUT_SECONDS", "")
    try:
        parsed = float(raw)
    except ValueError:
        return DEFAULT_COMMAND_TIMEOUT_SECONDS
    return parsed if parsed > 0 else DEFAULT_COMMAND_TIMEOUT_SECONDS


@tool("execute_slash_command", parse_docstring=True)
def execute_slash_command_tool(
    command_line: str,
    timeout_seconds: float | None = None,
) -> str:
    """Execute a Master Slash Command from the catalog across 28 categories.

    Args:
        command_line: The full command line to execute (e.g. '/verify', '/goal create <objective>', '/self-heal', '/research deep <query>').
        timeout_seconds: Optional per-call ceiling. Defaults to ALPHA_SLASH_COMMAND_TIMEOUT_SECONDS (or 120s).
    """
    if not command_line.strip():
        return "=== Slash Command Result:  [ERROR] ===\nFAILED: Empty command provided."

    res = command_registry.execute(
        command_line,
        context={"actor": "agent", "origin": "execute_slash_command"},
        timeout_seconds=_timeout_seconds(timeout_seconds),
    )
    status = (res.status or "").lower()
    verdict = _VERDICTS.get(status, "FAILED (unknown status)")
    if status in {"success", "ok"} and not command_registry.has_handler(res.command):
        # Defence in depth: the registry now answers a handler-less row with
        # `unimplemented`, so this branch should be unreachable. It stays so that
        # a future success-without-a-handler still cannot read as SUCCEEDED.
        verdict = "NOT EXECUTED (no handler is bound to this command; nothing ran)"
    elif status == UNIMPLEMENTED_STATUS:
        # The one caller that can act on this: hand the request back to the model
        # instead of reporting a dead end it could resolve itself.
        verdict += f" It is documented as '{res.data.get('category', 'unknown')}'. If the model wants this work done, do it directly with the available tools rather than calling {res.command} again."
    elif status == "not_found" and not res.data.get("unknown_subcommand") and not res.output.startswith("Unknown slash command"):
        # The command resolved; something the command names (a skill, a job, a
        # goal) is what is missing. Say so instead of blaming the command name.
        verdict = "FAILED (command ran, but its target was not found)"

    output_parts = [
        f"=== Slash Command Result: {res.command} [{res.status.upper()}] ===",
        f"VERDICT: {verdict}",
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

    recommendation: dict[str, Any] = {
        "command": detection.command,
        "phase": detection.phase.value,
        "confidence": detection.confidence,
        "reason": detection.reason,
    }
    # A recommendation is only useful if the recommendation can actually be run.
    # Say so here instead of letting the model discover it as a failed tool call.
    resolution = command_registry.resolve(detection.command)
    if resolution.command_def is None:
        recommendation["executable"] = False
        recommendation["executable_reason"] = f"'{detection.command}' is not a registered command; calling execute_slash_command with it returns not_found."
    else:
        recommendation["executable"] = command_registry.has_handler(resolution.command_def.command)
        recommendation["executable_reason"] = f"'{detection.command}' resolves to {resolution.command_def.command}; " + (
            "a handler is bound." if recommendation["executable"] else "it is a catalog row with no handler, so nothing will execute."
        )

    return (
        f"Autonomous Recommendation:\n"
        f"- Target Command: `{detection.command}`\n"
        f"- Phase: `{detection.phase.value}`\n"
        f"- Confidence: {detection.confidence}\n"
        f"- Reason: {detection.reason}\n"
        f"- Executable: {recommendation['executable']} ({recommendation['executable_reason']})\n"
        f"- Next Action: Call execute_slash_command('{detection.command}')"
    )
