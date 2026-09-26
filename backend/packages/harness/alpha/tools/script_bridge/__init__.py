"""Script bridge: programmatic tool calling with a real security boundary.

The point of this package is token cost with no privilege gain.  A script calls
tools by name through a **generated** stub, the calls travel over a local socket
to a parent-side dispatcher, and the dispatcher routes every one of them through
the same handler and the same authorisation middleware a model-issued tool call
would meet.  Only the script's own output comes back.

Layout:

``policy``     closed allowlist, forbidden surfaces, every ceiling.
``env``        exact-name environment allowlist; never a prefix passthrough.
``stubgen``    generates the stub from the live registry; drift-detects loudly.
``wire``       Unix socket where available, loopback TCP otherwise.
``dispatcher`` parent side: the real tool list, the real middleware decision.
``runner``     child process, capped streams, SIGTERM then SIGKILL.
``kernel``     persistent sessions; environment frozen at spawn.
``service``    the tool entry point and the catalog wiring.
"""

from __future__ import annotations

from .env import (
    SAFE_ENV_ALLOWLIST,
    SECRET_NAME_SUBSTRINGS,
    EnvironmentPolicyError,
    build_child_env,
    is_secret_env_name,
)
from .errors import (
    DeniedByPolicy,
    KernelUnavailable,
    OutputCapExceeded,
    PolicyViolation,
    ScriptBridgeError,
    StaleStubError,
    ToolCallCapExceeded,
    ToolNotCallable,
    TransportError,
    WallClockTimeout,
)
from .policy import (
    FORBIDDEN_TOOL_NAMES,
    ScriptBridgeLimits,
    ScriptBridgeMode,
    ScriptBridgePolicy,
    is_mcp_tool_name,
)

__all__ = [
    "SAFE_ENV_ALLOWLIST",
    "SECRET_NAME_SUBSTRINGS",
    "FORBIDDEN_TOOL_NAMES",
    "DeniedByPolicy",
    "EnvironmentPolicyError",
    "KernelUnavailable",
    "OutputCapExceeded",
    "PolicyViolation",
    "ScriptBridgeError",
    "ScriptBridgeLimits",
    "ScriptBridgeMode",
    "ScriptBridgePolicy",
    "StaleStubError",
    "ToolCallCapExceeded",
    "ToolNotCallable",
    "TransportError",
    "WallClockTimeout",
    "build_child_env",
    "is_mcp_tool_name",
    "is_secret_env_name",
]
