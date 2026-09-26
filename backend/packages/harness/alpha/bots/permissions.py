"""Role-Based Tool Permission Rings and Least-Privilege Gate.

Prevents specialist bots (e.g. Researcher) from executing unintended destructive actions
(e.g. file writing, code refactoring, production bash commands) as highlighted in
recent MetaGPT security audits and OWASP Agentic AI guidelines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from alpha.bots.authority_ceiling import AuthorityCeiling, get_ceiling

logger = logging.getLogger(__name__)

#: The MINIMUM authority capability each tool requires, independent of role.
#: A tool with no entry is withheld from every bot-filtered assembly: an
#: unclassified tool is an unbounded one, and the safe assumption about an
#: unbounded tool is that it is above the ceiling.
#:
#: This is a floor, not a grant. The role ring still decides whether the role may
#: use it; this map only decides whether the tool is inside the authority
#: envelope at all.
TOOL_CAPABILITY_FLOOR: dict[str, frozenset[str]] = {
    # observe / read
    "view_file": frozenset({"observe"}),
    "grep_search": frozenset({"observe"}),
    "find_by_name": frozenset({"observe"}),
    "list_dir": frozenset({"observe"}),
    "read_url_content": frozenset({"observe"}),
    "web_search": frozenset({"observe"}),
    "deep_web_search": frozenset({"observe"}),
    "web_fetch": frozenset({"observe"}),
    "read_file": frozenset({"observe"}),
    "ls": frozenset({"observe"}),
    "grep": frozenset({"observe"}),
    # reason
    "ask_question": frozenset({"reason"}),
    "execute_slash_command": frozenset({"reason"}),
    "identify_autonomous_command": frozenset({"reason"}),
    "describe_skill": frozenset({"reason"}),
    # dispatch
    "message_agent": frozenset({"dispatch"}),
    "task": frozenset({"dispatch"}),
    # write inside the workspace
    "write_to_file": frozenset({"workspace_write"}),
    "replace_file_content": frozenset({"workspace_write"}),
    "hashline_edit": frozenset({"workspace_write"}),
    # process execution
    "run_command": frozenset({"process_exec"}),
    "bash": frozenset({"process_exec"}),
    "python_repl": frozenset({"process_exec"}),
    # irreversible: still routed through the approval gate, and above the
    # default ceiling, so a bot-filtered assembly never hands these out.
    "delete_file": frozenset({"process_exec", "repository_mutate"}),
    "git_push": frozenset({"repository_mutate"}),
    "deploy_production": frozenset({"repository_mutate"}),
    "drop_database": frozenset({"repository_mutate"}),
    # self-extension: minting authority is above every default ceiling
    "hire_bot": frozenset({"grant_authority"}),
    "rescope_bot": frozenset({"grant_authority"}),
    "propose_self_modification": frozenset({"grant_authority"}),
}


class NamedTool(Protocol):
    """Anything with a tool name - keeps the filter reusable across tool types."""

    name: str


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
    # The Alpha leader is the most dangerous profile to get wrong, so its ring
    # is an EXPLICIT narrow allowlist rather than `allow_all` and rather than an
    # accidental fallthrough to the general-worker default. It may read, search,
    # message teammates and inspect the roster — the verbs of selection — and it
    # is denied every write/shell/push verb, because dispatching work is not the
    # same authority as performing irreversible work. Nothing here sits in
    # `approval_required_tools`, so a leader dispatch can never quietly satisfy a
    # tool a human must sign off.
    "Autonomous Leader & Capability Dispatch Director": RolePermissionRing(
        role_name="Autonomous Leader & Capability Dispatch Director",
        allowed_tools={
            "view_file",
            "grep_search",
            "find_by_name",
            "list_dir",
            "message_agent",
            "ask_question",
            "ask_clarification",
            "bot_roster",
            "batch_status",
            "cancel_batch",
            "execute_slash_command",
            "identify_autonomous_command",
        },
        denied_tools={
            "write_to_file",
            "replace_file_content",
            "run_command",
            "delete_file",
            "git_push",
            "deploy_production",
            "drop_database",
            "rotate_credentials",
        },
    ),
    "lead": RolePermissionRing(role_name="lead", allow_all=True),
    "supervisor": RolePermissionRing(role_name="supervisor", allow_all=True),
    "admin": RolePermissionRing(role_name="admin", allow_all=True),
}

ROLE_PERMISSION_RINGS = DEFAULT_ROLE_RINGS


def filter_tools_by_role[ToolT: NamedTool](
    tools: list[ToolT],
    bot_role: str | None,
    gate: ToolPermissionGate | None = None,
    *,
    enabled: bool = True,
    ceiling: AuthorityCeiling | None = None,
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
    bound = ceiling or get_ceiling()
    kept: list[ToolT] = []
    for tool in tools:
        allowed, _reason, requires_approval = resolved.check_permission(bot_role, tool.name)
        if not (allowed or requires_approval):
            logger.debug(
                "Tool %s withheld from role %s by permission ring", tool.name, bot_role
            )
            continue
        # The authority ceiling is a SECOND, independent filter on the same
        # path. The role ring answers "may this role use this tool"; the ceiling
        # answers "is this tool inside the authority envelope at all". Because
        # this is the real tool-assembly chokepoint, a profile that somehow
        # acquired an over-broad role still cannot be handed a tool above the
        # ceiling -- the two checks cannot be satisfied by editing one of them.
        if not TOOL_CAPABILITY_FLOOR.get(tool.name):
            logger.debug(
                "Tool %s withheld: no authority-ceiling capability covers it", tool.name
            )
            continue
        ok, _violations = bound.within_ceiling(TOOL_CAPABILITY_FLOOR[tool.name])
        if not ok:
            logger.warning(
                "Tool %s withheld from %s: requires a capability above the authority ceiling",
                tool.name,
                bot_role,
            )
            continue
        kept.append(tool)
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
        #
        # The key is lower-cased before the containment test. It used to be
        # compared as-written against an already-lower-cased role, so a ring
        # declared with any capital letter (e.g. the Alpha leader's
        # "Autonomous Leader & Capability Dispatch Director") could never match
        # and silently fell through to the general-worker default — a ring that
        # looks enforced and is not. Only the FUZZY loop lower-cases; the
        # allow_all loop above stays exact so no privileged ring can be widened
        # by casing.
        for k, ring in self.rings.items():
            if not ring.allow_all and k.lower() in r:
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
