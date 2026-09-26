"""Built-in Sandboxed Computer Worker LangChain Tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.sandbox.computer_use import (
    BlastRadiusPolicy,
    ComputerWorker,
)

_GLOBAL_WORKER = ComputerWorker()


@tool("execute_sandboxed_computer_action", parse_docstring=True)
def execute_sandboxed_computer_action(
    action: str,
    command: str = "",
    dry_run: bool = False,
) -> str:
    """Evaluate a desktop/terminal command against the 3-tier blast-radius safety gate.

    The gate classifies and validates; it never runs anything. ``SAFE`` commands
    clear the gate immediately, ``SENSITIVE`` ones come back as
    ``approval_required`` with an opaque ``approval_id``, and ``FORBIDDEN`` ones
    are rejected outright and cannot be approved by anyone.

    There is deliberately no argument you can pass to approve a SENSITIVE
    command: an ``approval_id`` is only honoured once a human operator has
    granted it out of band, so a model cannot authorise its own host action.

    Args:
        action: 'classify_command', 'execute_command', 'get_audit_log'.
        command: Terminal or shell command string to evaluate.
        dry_run: If True, report the simulated outcome without claiming a change was applied.
    """
    try:
        if action == "classify_command":
            cls = BlastRadiusPolicy.classify(command)
            return json.dumps(cls.to_dict(), indent=2)

        if action == "execute_command":
            res = _GLOBAL_WORKER.execute(command=command, dry_run=dry_run)
            return json.dumps(res, indent=2)

        if action == "get_audit_log":
            return json.dumps(_GLOBAL_WORKER.get_audit_log(), indent=2)

        return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error in sandboxed computer execution: {exc}"
