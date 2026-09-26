"""Typed, agent-readable errors for the script bridge.

Every limit the bridge enforces surfaces as one of these.  Nothing is ever
silently truncated: a breached ceiling raises a :class:`ScriptBridgeError`
subclass whose ``detail`` dict travels back to the model inside the tool
result, so the agent can read *what* was exceeded and by how much.
"""

from __future__ import annotations

from typing import Any


class ScriptBridgeError(RuntimeError):
    """Base class for every script-bridge failure the model may observe."""

    code = "script_bridge_error"

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = dict(detail)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class PolicyViolation(ScriptBridgeError):
    """The script tried to do something the policy forbids outright."""

    code = "policy_violation"


class ToolNotCallable(ScriptBridgeError):
    """The script asked for a tool it is not permitted to call."""

    code = "tool_not_callable"


class WallClockTimeout(ScriptBridgeError):
    """The execution exceeded its wall-clock budget and was killed."""

    code = "wall_clock_timeout"


class OutputCapExceeded(ScriptBridgeError):
    """stdout/stderr exceeded its cap.  Never a silent truncation."""

    code = "output_cap_exceeded"


class ToolCallCapExceeded(ScriptBridgeError):
    """The execution exceeded its tool-call budget."""

    code = "tool_call_cap_exceeded"


class StaleStubError(ScriptBridgeError):
    """The generated tool stub has drifted from the live tool registry."""

    code = "stale_tool_stub"


class KernelUnavailable(ScriptBridgeError):
    """A persistent kernel could not be spawned; caller must degrade."""

    code = "kernel_unavailable"


class TransportError(ScriptBridgeError):
    """The parent<->script socket failed."""

    code = "transport_error"


class DeniedByPolicy(ScriptBridgeError):
    """A tool call made from inside a script was denied by the runtime policy.

    This is the *same* middleware decision a normal (model-issued) tool call
    would have received; the bridge never re-implements it.
    """

    code = "denied_by_policy"
