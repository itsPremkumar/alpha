"""Built-in tools for the Deep Agent fleet and hierarchical delegation.

Exposes autonomous isolated delegation without human-in-the-loop blocking.
All results return compact synthesis contracts safe for the parent context.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool


def _parse_target_files(raw: Any) -> list[str]:
    """Parse target files from tool input.

    Args:
        raw: Raw target file input (list or JSON string).

    Returns:
        Normalized list of file paths.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            pass
        return [part.strip() for part in text.split(",") if part.strip()]
    return []


@tool("delegate_to_deep_agent", parse_docstring=True)
def delegate_to_deep_agent(
    agent_type: str,
    task_description: str,
    target_files: list[str] | str | None = None,
    max_iterations: int = 15,
) -> dict:
    """Spawn an isolated deep agent for specialized multi-turn tasks.

    The child agent runs autonomously in a fresh unpolluted context with hard
    token and time budgets. Only a compact typed synthesis contract returns to
    the parent; raw terminal output never crosses the delegation boundary.

    Args:
        agent_type: Deep specialist type (architect, debugger, security, test_synthesizer, performance, code_reviewer).
        task_description: Goal description for the isolated deep agent.
        target_files: Targeted file paths relevant to the task.
        max_iterations: Maximum autonomous iterations before automatic halt. Default 15.
    """
    try:
        from alpha.subagents.hierarchical_delegator import get_delegation_engine

        files = _parse_target_files(target_files)
        iterations = int(max_iterations)
        if not task_description or not task_description.strip():
            return {"success": False, "error": "task_description must be a non-empty string"}
        if iterations <= 0 or iterations > 100:
            return {"success": False, "error": "max_iterations must be within 1 and 100"}
        engine = get_delegation_engine()
        contract = engine.delegate(
            agent_type=agent_type,
            task_description=task_description.strip(),
            target_files=files,
            max_iterations=iterations,
        )
        payload = contract.to_dict()
        # The delegation succeeded only when the contract says so — an
        # UNRECOVERABLE_ERROR contract must not be reported as success.
        payload["success"] = contract.is_success()
        if contract.error_detail:
            payload["error"] = contract.error_detail
        return payload
    except Exception as exc:
        return {"success": False, "error": f"Deep delegation failed: {exc}"}


@tool("list_available_deep_agents", parse_docstring=True)
def list_available_deep_agents() -> dict:
    """List available deep specialist agents and their capabilities.

    Returns compact descriptors for every autonomous deep specialist without
    exposing internal execution traces.
    """
    try:
        from alpha.subagents.hierarchical_delegator import get_delegation_engine

        engine = get_delegation_engine()
        return {"success": True, "agents": engine.list_agents(), "count": len(engine.list_agents())}
    except Exception as exc:
        return {"success": False, "error": f"Failed to list deep agents: {exc}"}


@tool("inspect_deep_agent_telemetry", parse_docstring=True)
def inspect_deep_agent_telemetry(session_id: str) -> dict:
    """Inspect bounded telemetry for an isolated deep agent session.

    Args:
        session_id: Isolated deep agent session identifier.
    """
    try:
        from alpha.subagents.hierarchical_delegator import get_delegation_engine

        if not session_id or not session_id.strip():
            return {"success": False, "error": "session_id must be a non-empty string"}
        engine = get_delegation_engine()
        return engine.telemetry(session_id.strip())
    except Exception as exc:
        return {"success": False, "error": f"Failed to inspect telemetry: {exc}"}
