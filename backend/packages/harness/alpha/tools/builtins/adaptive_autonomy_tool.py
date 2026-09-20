"""Built-in Adaptive Autonomy tool inspired by GPT-6 Astra and Master Blueprint Chapter 47."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.security.autonomy import (
    AutonomyPolicyEngine,
    AutonomyProfile,
)

_POLICY_ENGINE = AutonomyPolicyEngine()


@tool("check_or_set_autonomy_profile", parse_docstring=True)
def check_or_set_autonomy_profile(
    action: str = "check",
    profile_name: str = "",
    proposed_command: str = "",
) -> str:
    """Inspect or configure the user autonomy profile and evaluate action authorization.

    Enforces the 4-tier autonomy governance model:
    - 'observer': Read-only inspection; all state changes require approval.
    - 'assistant': Routine code edits/tests auto-approved; shell commands require approval.
    - 'operator': Standard development and git branching auto-approved; destructive commands require approval.
    - 'autonomous': Full execution within safety guardrails.

    Args:
        action: Operation: 'get_profile', 'set_profile', 'evaluate_action'.
        profile_name: Target profile name ('observer', 'assistant', 'operator', 'autonomous').
        proposed_command: Shell command or action signature to evaluate under current profile.
    """
    profile_map = {
        "observer": AutonomyProfile.OBSERVER,
        "assistant": AutonomyProfile.ASSISTANT,
        "operator": AutonomyProfile.OPERATOR,
        "autonomous": AutonomyProfile.AUTONOMOUS,
    }

    if action == "get_profile":
        return json.dumps({
            "current_profile": _POLICY_ENGINE.current_profile.value,
        }, indent=2)

    elif action == "set_profile":
        target = profile_map.get(profile_name.lower())
        if not target:
            return json.dumps({
                "error": f"Invalid profile '{profile_name}'. Must be one of: observer, assistant, operator, autonomous."
            }, indent=2)
        _POLICY_ENGINE.set_profile(target)
        return json.dumps({
            "status": "profile_updated",
            "active_profile": _POLICY_ENGINE.current_profile.value,
        }, indent=2)

    elif action == "evaluate_action":
        res = _POLICY_ENGINE.evaluate_action(
            action_name="proposed_action",
            command_or_args=proposed_command,
        )
        return json.dumps(res.to_dict(), indent=2)

    else:
        return json.dumps({
            "error": f"Unknown action '{action}'. Supported: 'get_profile', 'set_profile', 'evaluate_action'."
        }, indent=2)
