"""Claude/Codex-style shell-hook bridge configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HooksConfig(BaseModel):
    """Config section for the hooks bridge middleware.

    Points at a ``hooks.json`` file in the Claude ``hooks`` shape so teams
    can reuse their existing hook ecosystems (``PreToolUse`` /
    ``PostToolUse`` / ``SessionStart`` / ``SessionEnd`` / ``Stop`` with
    ``{"type": "command", "command": ...}`` entries) inside DeerFlow runs.
    Only ``command`` hooks are executed; anything else is skipped with a
    debug log. The file is operator-trusted configuration: hook commands run
    with Gateway privileges, so it belongs next to ``config.yaml``, never in
    API-writable state.
    """

    enabled: bool = Field(
        default=False,
        description="Enable the hooks bridge middleware. Disabled by default; no subprocess is ever spawned while off.",
    )
    hooks_path: str | None = Field(
        default=None,
        description="Absolute path to hooks.json. None (or a missing file) means fail-open passthrough with one debug log per process.",
    )
    default_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Per-hook wall-clock budget when the hook entry sets no timeout. A timed-out hook fails open (warns) unless fail_open is false.",
    )
    fail_open: bool = Field(
        default=True,
        description="When true (default), hook load errors, timeouts, and crashes pass the run through. When false, they block the guarded call.",
    )
