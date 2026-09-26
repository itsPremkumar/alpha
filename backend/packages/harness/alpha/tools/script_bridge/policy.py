"""Policy for the script bridge: limits, tool whitelist, and modes.

The whitelist is deliberately closed.  A script may not call itself (that is a
fork bomb with a natural-language interface), may not delegate to a subagent
(that escapes the resource budget entirely), and may not reach MCP tools (they
live behind their own credential and authorisation plane).  Those three denials
are properties of the *policy object*, not of the sandbox, so no sandbox escape
can talk its way past them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping

#: Tool names a script may NEVER call, whatever the opt-in allowlist says.
#: These are the self-recursion, delegation and MCP surfaces.
FORBIDDEN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # self-recursion / re-entry
        "script_bridge",
        "script_bridge_reset",
        "python_repl",
        "code_mode",
        "invoke_python_skill",
        "catalog_tool_call",
        # delegation (escapes the per-execution tool-call budget entirely)
        "task",
        "batch_task",
        "subagent_control",
        "delegate_to_deep_agent",
        "swarm_tool",
        "group_chat_tool",
        "kanban_board_tool",
        "company_tool",
        "a2a_tool",
        "agent_message_tool",
        "agent_observe_tool",
        "message_agent",
        # human approval / kill switches
        "request_secure_credential",
        "estop",
        "smart_approval",
    }
)

#: Prefixes reserved for the bridge's own transport namespace.  A tool name
#: matching one of these is never callable from a script.
FORBIDDEN_TOOL_PREFIXES: tuple[str, ...] = ("mcp__", "mcp_", "__mcp", "alpha_mcp")


def forbidden_tool_names_sorted() -> list[str]:
    """Every tool name a script may never call, sorted, for error messages."""
    return sorted(FORBIDDEN_TOOL_NAMES)


def is_mcp_tool_name(name: str, *, mcp_names: Iterable[str] = ()) -> bool:
    """Return True when *name* is an MCP tool.

    Two independent signals: the name shape used by the MCP wrappers, and the
    live set of MCP tool names the registry reports.  Either one is enough.
    """
    if name in set(mcp_names):
        return True
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in FORBIDDEN_TOOL_PREFIXES)


@dataclass(frozen=True)
class ScriptBridgeLimits:
    """Every ceiling the bridge enforces.  Each one is a real, enforced number.

    ``wall_clock_seconds`` is enforced by SIGTERM followed by SIGKILL after
    ``sigterm_grace_seconds``; a script that installs ``signal.SIG_IGN`` for
    SIGTERM still dies.
    """

    wall_clock_seconds: float = 60.0
    sigterm_grace_seconds: float = 2.0
    max_stdout_bytes: int = 64 * 1024
    max_stderr_bytes: int = 16 * 1024
    max_tool_calls: int = 32
    max_transcript_bytes: int = 512 * 1024
    max_kernel_sessions: int = 8
    #: A SEPARATE budget for subagent-owned kernels.  Subagent kernels never
    #: count against ``max_kernel_sessions``, and a top-level eviction never
    #: reaches into a subagent pool, so a wide fan-out cannot evict a sibling's
    #: kernel in the middle of its task.
    subagent_max_kernel_sessions: int = 16
    kernel_idle_seconds: float = 900.0
    max_script_bytes: int = 256 * 1024
    max_output_inline_bytes: int = 8 * 1024

    def to_dict(self) -> dict[str, float | int]:
        return {
            "wall_clock_seconds": self.wall_clock_seconds,
            "sigterm_grace_seconds": self.sigterm_grace_seconds,
            "max_stdout_bytes": self.max_stdout_bytes,
            "max_stderr_bytes": self.max_stderr_bytes,
            "max_tool_calls": self.max_tool_calls,
            "max_transcript_bytes": self.max_transcript_bytes,
            "max_kernel_sessions": self.max_kernel_sessions,
            "subagent_max_kernel_sessions": self.subagent_max_kernel_sessions,
            "kernel_idle_seconds": self.kernel_idle_seconds,
            "max_script_bytes": self.max_script_bytes,
            "max_output_inline_bytes": self.max_output_inline_bytes,
        }


@dataclass(frozen=True)
class ScriptBridgeMode:
    """Where a script runs.  Never what it can see.

    ``project`` - the session's cwd.  ``strict`` - a fresh temp directory per
    execution, with ``-I`` interpreter semantics (no user site, no cwd on
    ``sys.path``) so the same script resolves the same imports.

    Switching mode changes WHERE the script runs.  It never changes the tool
    whitelist, the environment policy, the limits, or the authorisation chain.
    """

    name: str = "project"
    cwd: str | None = None

    def __post_init__(self) -> None:
        if self.name not in ("project", "strict"):
            raise ValueError(f"unknown script bridge mode: {self.name!r}")

    @property
    def isolated_interpreter(self) -> bool:
        return self.name == "strict"


@dataclass(frozen=True)
class ScriptBridgePolicy:
    """The complete, immutable policy for one bridge instance."""

    limits: ScriptBridgeLimits = field(default_factory=ScriptBridgeLimits)
    mode: ScriptBridgeMode = field(default_factory=ScriptBridgeMode)
    #: Exact-name tool allowlist.  Empty tuple means "no tool is callable",
    #: which is the safe default - it is not the same as "all tools".
    allowed_tool_names: tuple[str, ...] = ()
    #: Per-skill opt-in environment variables (exact names, never prefixes).
    env_opt_in: Mapping[str, str] = field(default_factory=dict)
    #: Cache directory for oversize output.
    cache_dir: str | None = None

    def with_limits(self, **overrides: float | int) -> ScriptBridgePolicy:
        return replace(self, limits=replace(self.limits, **overrides))

    def with_mode(self, mode: ScriptBridgeMode) -> ScriptBridgePolicy:
        return replace(self, mode=mode)

    def is_tool_callable(self, name: str, *, mcp_names: Iterable[str] = ()) -> tuple[bool, str]:
        """Return ``(allowed, reason)`` for a tool name requested by a script.

        Order matters: the forbidden set is checked *before* the allowlist so a
        forbidden name can never be "allowed" by a configuration mistake.
        """
        if name in FORBIDDEN_TOOL_NAMES:
            return False, "forbidden_tool_name: self-recursion, delegation and approval surfaces are never script-callable"
        if is_mcp_tool_name(name, mcp_names=mcp_names):
            return False, "mcp_tool_unreachable: MCP tools are behind their own credential and authorisation plane"
        if any(name.startswith(prefix) for prefix in FORBIDDEN_TOOL_PREFIXES):
            return False, "mcp_tool_unreachable: reserved MCP namespace"
        if name not in self.allowed_tool_names:
            return False, "not_in_allowlist"
        return True, "allowed"
