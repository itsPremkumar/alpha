"""Wire contracts: one authoritative model per server/client boundary.

Importing this package registers the contracts.  Everything downstream - the
TypeScript generator, the drift gate, the tests - reads the registry, so adding a
field to a model is enough to propagate it.

The contracts here are the ones this change set actually introduces across a
boundary: the script-bridge tool result and the vault handle reference.  They are
real payloads, not illustrations.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ._bootstrap import registry_bootstrap, render_manifest, render_typescript
from .registry import Contract, register
from .registry import registered as _registered


def registered():
    return _registered()


__all__ = [
    "Contract",
    "ScriptBridgeResult",
    "ScriptBridgeStats",
    "ScriptBridgeLimitsWire",
    "SecretHandleWire",
    "VaultUseResult",
    "StreamFrameWire",
    "bootstrap",
    "registered",
    "render_manifest",
    "render_typescript",
    "registry_bootstrap",
]


class ScriptBridgeLimitsWire(BaseModel):
    """The ceilings the bridge enforces, as a consumer sees them."""

    wall_clock_seconds: float
    sigterm_grace_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_tool_calls: int
    max_transcript_bytes: int
    max_kernel_sessions: int
    subagent_max_kernel_sessions: int
    kernel_idle_seconds: float
    max_script_bytes: int
    max_output_inline_bytes: int


class ScriptBridgeStatsWire(BaseModel):
    tool_calls: int
    denied: int
    refused: int
    errors: int


#: Registered under both names so the generated TS matches the Python class the
#: dispatcher actually builds.
ScriptBridgeStats = ScriptBridgeStatsWire


class ScriptBridgeResult(BaseModel):
    """What ``script_bridge`` returns to a caller."""

    status: Literal["ok", "error"]
    mode: Literal["oneshot", "kernel"]
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    duration_seconds: float = 0.0
    tool_calls: int = 0
    denied: int = 0
    refused: int = 0
    errors: int = 0
    dispatcher: str = ""
    transport: str = ""
    stdout_truncated: bool = False
    stdout_full_text_path: str | None = None
    stderr_truncated: bool = False
    stderr_full_text_path: str | None = None
    session_id: str | None = None
    kernel: dict[str, Any] | None = None
    stats: ScriptBridgeStatsWire | None = None
    limits: ScriptBridgeLimitsWire | None = None
    error: dict[str, Any] | None = None
    degradations: list[str] = Field(default_factory=list)


class SecretHandleWire(BaseModel):
    """The complete model-visible shape of a vault handle.

    There is deliberately no field that could hold a secret.  If a future change
    adds one, the drift test fails and the change has to justify itself.
    """

    handle: str
    fingerprint: str
    operation: str
    target: str
    owner: str
    expires_at: float
    label: str = ""


class VaultUseResult(BaseModel):
    """What a model gets back from a vault use: the operation's result, only."""

    ok: bool
    handle_fingerprint: str
    operation: str
    target: str
    result: Any = None
    ledger_outcome: str


class StreamFrameWire(BaseModel):
    """One machine-readable run frame."""

    v: int
    seq: int
    type: str
    run_id: str


def bootstrap() -> tuple[Contract, ...]:
    """Register every contract.  Idempotent."""
    register(
        Contract(
            name="ScriptBridgeResult",
            model=ScriptBridgeResult,
            module="scriptBridge",
        )
    )
    register(
        Contract(
            name="SecretHandle",
            model=SecretHandleWire,
            module="vault",
        )
    )
    register(
        Contract(
            name="VaultUseResult",
            model=VaultUseResult,
            module="vault",
        )
    )
    register(
        Contract(
            name="StreamFrame",
            model=StreamFrameWire,
            module="streamJson",
        )
    )
    return registered()


registry_bootstrap(bootstrap)
