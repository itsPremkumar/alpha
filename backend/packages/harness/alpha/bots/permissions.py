"""Role-Based Tool Permission Rings and Least-Privilege Gate.

Prevents specialist bots (e.g. Researcher) from executing unintended destructive actions
(e.g. file writing, code refactoring, production bash commands) as highlighted in
recent MetaGPT security audits and OWASP Agentic AI guidelines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

logger = logging.getLogger(__name__)


class NamedTool(Protocol):
    """Anything with a tool name - keeps the filter reusable across tool types."""

    name: str


ToolT = TypeVar("ToolT", bound=NamedTool)


@dataclass
class RolePermissionRing:
    role_name: str
    allowed_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)
    approval_required_tools: set[str] = field(default_factory=set)
    allow_all: bool = False


DEFAULT_ROLE_RINGS: dict[str, RolePermissionRing] = {
    "researcher": RolePermissionRing(
        role_name="researcher",
        allowed_tools={
            "web_search",
            "read_url_content",
            "view_file",
            "grep_search",
            "find_by_name",
            "list_dir",
            "message_agent",
            "ask_question",
            "execute_slash_command",
            "identify_autonomous_command",
        },
        denied_tools={
            "write_to_file",
            "replace_file_content",
            "run_command",
            "delete_file",
            "git_push",
        },
    ),
    "architect": RolePermissionRing(
        role_name="architect",
        allowed_tools={
            "view_file",
            "grep_search",
            "find_by_name",
            "list_dir",
            "message_agent",
            "ask_question",
            "execute_slash_command",
            "identify_autonomous_command",
        },
        denied_tools={
            "replace_file_content",
            "run_command",
        },
    ),
    "coder": RolePermissionRing(
        role_name="coder",
        allowed_tools={
            "view_file",
            "replace_file_content",
            "write_to_file",
            "run_command",
            "grep_search",
            "find_by_name",
            "list_dir",
            "message_agent",
            "ask_question",
            "execute_slash_command",
            "identify_autonomous_command",
        },
        approval_required_tools={
            "deploy_production",
            "drop_database",
        },
    ),
    "developer": RolePermissionRing(
        role_name="developer",
        allowed_tools={
            "view_file",
            "replace_file_content",
            "write_to_file",
            "run_command",
            "grep_search",
            "find_by_name",
            "list_dir",
            "message_agent",
            "ask_question",
            "execute_slash_command",
        },
    ),
    "tester": RolePermissionRing(
        role_name="tester",
        allowed_tools={
            "view_file",
            "run_command",
            "grep_search",
            "find_by_name",
            "list_dir",
            "message_agent",
            "ask_question",
            "execute_slash_command",
            "identify_autonomous_command",
        },
        denied_tools={
            "replace_file_content",
        },
    ),
    "qa": RolePermissionRing(
        role_name="qa",
        allowed_tools={
            "view_file",
            "run_command",
            "grep_search",
            "find_by_name",
            "list_dir",
            "message_agent",
            "ask_question",
            "execute_slash_command",
        },
        denied_tools={
            "replace_file_content",
        },
    ),
    "lead": RolePermissionRing(role_name="lead", allow_all=True),
    "supervisor": RolePermissionRing(role_name="supervisor", allow_all=True),
    "admin": RolePermissionRing(role_name="admin", allow_all=True),
}

ROLE_PERMISSION_RINGS = DEFAULT_ROLE_RINGS


def filter_tools_by_role(
    tools: list[ToolT],
    bot_role: str | None,
    gate: ToolPermissionGate | None = None,
    *,
    enabled: bool = True,
) -> list[ToolT]:
    """Pure, testable helper: drop tools a bot role may not execute.

    Bot profiles declare a ``toolsets`` capability, but until now nothing
    consulted it at runtime, so a Researcher could still be handed write or
    shell tools. This enforces the declared role ring at assembly time.

    Tools that merely require human approval are **kept** here: they are a
    legitimate part of the toolset and must be surfaced so the runtime can ask,
    rather than silently vanishing.

    Args:
        tools: Candidate tools (anything exposing ``.name``).
        bot_role: Role/name of the acting bot. ``None``/empty means "not a bot".
        gate: Gate instance to consult; a default one is built when omitted.
        enabled: Master switch. When False the list is returned untouched, which
            is the default posture until an operator opts in.
    """
    if not enabled or not bot_role:
        return tools
    resolved = gate or ToolPermissionGate()
    kept: list[ToolT] = []
    for tool in tools:
        allowed, _reason, requires_approval = resolved.check_permission(bot_role, tool.name)
        if allowed or requires_approval:
            kept.append(tool)
        else:
            logger.debug(
                "Tool %s withheld from role %s by permission ring", tool.name, bot_role
            )
    return kept


class ToolPermissionGate:
    """Evaluates whether an agent with a given role is authorized to execute a tool."""

    def __init__(self, custom_rings: dict[str, RolePermissionRing] | None = None) -> None:
        self.rings = dict(DEFAULT_ROLE_RINGS)
        if custom_rings:
            self.rings.update(custom_rings)

    def _resolve_ring(self, role: str) -> RolePermissionRing:
        r = (role or "").lower().strip()

        # Privileged rings must match EXACTLY. The old code used a bare substring
        # test (`if k in r`) for every ring, so "not-an-admin",
        # "readonly-admin-auditor" and "team-lead-backup" all resolved to an
        # allow_all ring and were granted every tool - a privilege escalation
        # reachable from any free-text role string.
        for k, ring in self.rings.items():
            if ring.allow_all and k == r:
                return ring

        # Unprivileged rings may keep fuzzy matching: matching the wrong one is
        # fail-safe here, because it can only grant a narrower set.
        for k, ring in self.rings.items():
            if not ring.allow_all and k in r:
                return ring
        # Fallback default: general worker (can read, message, ask)
        return RolePermissionRing(
            role_name=r,
            allowed_tools={
                "view_file",
                "grep_search",
                "find_by_name",
                "list_dir",
                "message_agent",
                "ask_question",
                "execute_slash_command",
            },
        )

    def check_permission(
        self,
        bot_role: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None, bool]:
        """Check if bot_role is permitted to call tool_name.

        Returns: (is_allowed, denial_reason, requires_human_approval)
        """
        ring = self._resolve_ring(bot_role)
        if ring.allow_all:
            return True, None, False

        clean_tool = tool_name.lower().strip()

        # Check approval requirement
        if clean_tool in ring.approval_required_tools:
            return False, f"Tool '{tool_name}' requires human operator approval for role '{bot_role}'.", True

        # Check explicit denial
        if clean_tool in ring.denied_tools:
            return (
                False,
                f"Permission Denied: Role '{bot_role}' is not authorized to execute tool '{tool_name}'. "
                f"Please delegate this task to an authorized specialist (e.g. coder or DevOps).",
                False,
            )

        # If allowed_tools is configured, enforce whitelist
        if ring.allowed_tools and clean_tool not in ring.allowed_tools:
            return (
                False,
                f"Permission Denied: Tool '{tool_name}' is not within the authorized toolset for role '{bot_role}'.",
                False,
            )

        return True, None, False


_DEFAULT_GATE: ToolPermissionGate | None = None


def get_permission_gate() -> ToolPermissionGate:
    global _DEFAULT_GATE
    if _DEFAULT_GATE is None:
        _DEFAULT_GATE = ToolPermissionGate()
    return _DEFAULT_GATE
