"""Built-in Skills Hub management tool inspired by Hermes Agent."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.skills.audit.ast_audit import get_skill_ast_auditor
from agent_workspace.skills.hub.discovery import get_skills_hub


@tool("skills_hub_manage", parse_docstring=True)
def skills_hub_manage(
    action: str = "search",
    query_or_name: str = "",
) -> str:
    """Manage Skills Hub discovery, AST security audits, and skill installation.

    Args:
        action: Operational action: 'search' (find skills in hub), 'install' (audit and install by name), 'list' (list installed), or 'audit' (run AST static security scan on code).
        query_or_name: Query string for search, package name for install, or code string for audit.
    """
    hub = get_skills_hub()
    act = action.strip().lower()

    if act == "search":
        results = hub.search(query_or_name)
        return json.dumps([r.to_dict() for r in results], indent=2)

    elif act == "install":
        success, msg = hub.install(query_or_name)
        status = "SUCCESS" if success else "FAILED"
        return f"[{status}] {msg}"

    elif act == "list":
        installed = hub.list_installed()
        return json.dumps(installed, indent=2)

    elif act == "audit":
        auditor = get_skill_ast_auditor()
        res = auditor.audit_code(query_or_name)
        return json.dumps({"is_safe": res.is_safe, "violations": res.violations}, indent=2)

    return f"Unknown action '{action}'. Use 'search', 'install', 'list', or 'audit'."
