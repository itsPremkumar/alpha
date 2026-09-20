"""Built-in simulate_consequences tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from alpha.consequence.simulator import ConsequenceSimulator


@tool("simulate_consequences", parse_docstring=True)
def simulate_consequences(
    action_name: str,
    target_file: str | None = None,
    command: str | None = None,
    workspace_path: str | None = None,
) -> str:
    """Simulate potential environmental side-effects, blast radius, and cascading failures before execution.

    Probes the host environment affordances, checks if the workspace is git-tracked, analyzes package
    manifest dependencies, and returns safety recommendations.

    Args:
        action_name: Name of tool or action (e.g. 'write_to_file', 'run_command', 'replace_file_content').
        target_file: Target file path being modified or operated on.
        command: Shell command line if action is running a command.
        workspace_path: Root path of the current workspace (optional).
    """
    params: dict[str, Any] = {}
    if target_file:
        params["TargetFile"] = target_file
    if command:
        params["CommandLine"] = command

    sim = ConsequenceSimulator()
    report = sim.simulate(action_name=action_name, parameters=params, workspace_path=workspace_path)

    return json.dumps(report.to_dict(), indent=2)
