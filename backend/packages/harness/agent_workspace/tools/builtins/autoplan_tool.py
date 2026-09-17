"""Built-in autonomous-planner tool: one raw prompt -> fully decided execution plan."""

from __future__ import annotations

import importlib.util
import json

from langchain.tools import tool

from agent_workspace.planning.autonomous import AutonomousPlanner
from agent_workspace.planning.profiles import install_profiles


def _resolve_capabilities() -> dict:
    """Best-effort deployment snapshot; every probe is isolated (None = unknown)."""
    capabilities: dict = {"enabled_skills": None, "bash": None, "browser": None}
    try:
        from agent_workspace.skills.storage import get_or_new_skill_storage

        capabilities["enabled_skills"] = [s.name for s in get_or_new_skill_storage().load_skills(enabled_only=True)]
    except Exception:  # noqa: BLE001 — capability probes never break planning
        pass
    try:
        from agent_workspace.sandbox.security import is_host_bash_allowed

        capabilities["bash"] = bool(is_host_bash_allowed())
    except Exception:  # noqa: BLE001
        pass
    try:
        capabilities["browser"] = importlib.util.find_spec("playwright") is not None
    except Exception:  # noqa: BLE001
        pass
    return capabilities


@tool("build_autonomous_plan", parse_docstring=True)
def build_autonomous_plan(
    raw_prompt: str,
    workspace_context: str = "",
    context_metadata_json: str = "",
    max_subtasks: int = 8,
    token_budget: int = 0,
    persist_kanban: bool = False,
    install_new_profiles: bool = False,
    approve_profiles: str = "",
) -> str:
    """Turn one raw user prompt into a fully decided autonomous execution plan.

    Decides research need, subagent delegation, subtask split with waves,
    per-task assignees (existing profiles or newly drafted specialist specs),
    Kanban board, skills, tool groups, cost estimate, final goal, assumptions,
    and self-review gate — so the user does nothing beyond writing the prompt.

    Args:
        raw_prompt: The user's raw request, verbatim.
        workspace_context: Optional workspace layout snippet for grounding.
        context_metadata_json: Optional JSON object with repo/file/history hints.
        max_subtasks: Maximum Kanban work items to emit (default 8).
        token_budget: Optional run token budget the cost model is checked against (0 = unknown).
        persist_kanban: Persist the board into the Kanban store (default false).
        install_new_profiles: Install drafted specialist profiles as DISABLED
            managed definitions pending approval (default false = specs only).
        approve_profiles: Comma-separated profile names to enable at install.
    """
    metadata = None
    if context_metadata_json:
        try:
            metadata = json.loads(context_metadata_json)
        except Exception:
            metadata = None
    try:
        planner = AutonomousPlanner()
        kanban_store = None
        if persist_kanban:
            try:
                from agent_workspace.kanban.store import get_kanban_store

                kanban_store = get_kanban_store()
            except Exception:  # noqa: BLE001 — persistence never breaks planning
                kanban_store = None
        plan = planner.plan(
            raw_prompt,
            workspace_context=workspace_context or None,
            context_metadata=metadata,
            max_subtasks=max_subtasks,
            capabilities=_resolve_capabilities(),
            kanban_store=kanban_store,
            token_budget=int(token_budget) or None,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, indent=2)

    payload = plan.to_dict()
    if install_new_profiles and plan.new_profiles:
        approved = [n.strip() for n in (approve_profiles or "").split(",") if n.strip()]
        try:
            from agent_workspace.persistence.managed_subagents import get_managed_subagent_store

            store = get_managed_subagent_store()
        except Exception as exc:  # noqa: BLE001 — install never breaks planning
            store = None
            payload["profile_install"] = {"errors": [{"name": "*", "error": str(exc)}]}
        if store is not None:
            payload["profile_install"] = install_profiles(plan.new_profiles, store=store, approve=approved)
    else:
        payload["profile_install"] = {"note": "Specs only — pass install_new_profiles=true to stage them as disabled definitions."}
    return json.dumps(payload, indent=2)
