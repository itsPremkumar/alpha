"""Built-in Sandboxed Computer Worker LangChain Tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.sandbox.computer_use import (
    BlastRadiusPolicy,
    ComputerWorker,
)

_GLOBAL_WORKER = ComputerWorker()


@tool("execute_sandboxed_computer_action", parse_docstring=True)
def execute_sandboxed_computer_action(
    action: str,
    command: str = "",
    approval_token: str = "",
    dry_run: bool = False,
) -> str:
    """Execute desktop and terminal commands through a 3-tier blast-radius safety gate.

    Args:
        action: 'classify_command', 'execute_command', 'get_audit_log'.
        command: Terminal or shell command string to evaluate or execute.
        approval_token: Token or confirmation to authorize sensitive commands.
        dry_run: If True, simulates execution without making system state modifications.
    """
    try:
        if action == "classify_command":
            cls = BlastRadiusPolicy.classify(command)
            return json.dumps(cls.to_dict(), indent=2)

        elif action == "execute_command":
            approval = bool(approval_token and approval_token.strip().lower() in ("true", "approved", "admin", "yes"))
            res = _GLOBAL_WORKER.execute(
                command=command,
                approval_granted=approval,
                dry_run=dry_run,
            )
            return json.dumps(res, indent=2)

        elif action == "get_audit_log":
            return json.dumps(_GLOBAL_WORKER.get_audit_log(), indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error in sandboxed computer execution: {exc}"
