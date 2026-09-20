"""Built-in Smart Approvals Guardian tool inspired by Hermes Agent."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.safety.guardian.smart import evaluate_command_safety


@tool("verify_command_approval", parse_docstring=True)
def verify_command_approval(
    command: str,
) -> str:
    """Evaluate whether a shell command is safe to execute using Smart Approvals guardian.

    Strips shell comments to prevent injection bypasses, checks against dangerous destruction
    patterns, enforces permanent allowlists, and respects denial circuit breakers.

    Args:
        command: The shell command line to evaluate for safety.
    """
    res = evaluate_command_safety(command)
    return json.dumps({
        "verdict": res.verdict,
        "reason": res.reason,
        "sanitized_command": res.sanitized_command,
    }, indent=2)
