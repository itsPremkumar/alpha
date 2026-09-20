"""Bot Profile and Capability Epoch Engine (inspired by Hermes Bot Mode, upgraded).

Defines first-class Bot profiles with custom SOUL instructions, toolsets,
skills, and deterministic 12-hex capability epoch fingerprinting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class BotProfile:
    """A first-class autonomous Bot persona."""

    name: str
    display_name: str
    role: str
    soul: str
    model: str | None = None
    toolsets: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    # Inventory #1/#25: avatar label plus lifecycle status. Both are display /
    # scheduling metadata and intentionally excluded from the capability
    # fingerprint so status flips never churn the epoch.
    avatar: str = ""
    status: str = "active"
    # match API & runtime tracking
    last_active: str | None = None
    version: int = 1
    # Organizational hierarchy & responsibility (Master Inventory #17-#20)
    department: str = "engineering"
    reports_to: str | None = None
    responsibilities: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    # Liveness & succession (Master Inventory #27-#35)
    heartbeat: str | None = None
    succession_fallback: str | None = None
    # Performance & reputation metrics (Master Inventory #51-#52)
    reputation_score: float = 1.0
    task_stats: dict[str, Any] = field(
        default_factory=lambda: {
            "completed": 0,
            "failed": 0,
            "total_runs": 0,
            "avg_duration_sec": 0.0,
        }
    )
    # Bot-owned routines (Master Inventory #9)
    routines: list[dict[str, Any]] = field(default_factory=list)
    # ------------------------------------------------------------------
    # Bot-owned memory. Every field is None by default, meaning "inherit the
    # host/global memory config". Set any of them to give this bot its own
    # memory behaviour and namespace.
    # ------------------------------------------------------------------
    memory_enabled: bool | None = None
    memory_scope: str | None = None
    """Namespace key for this bot's memories. Defaults to the bot name."""
    memory_mode: str | None = None
    """``"middleware"`` (passive summarization) or ``"tool"`` (model calls
    memory tools). ``None`` inherits the global setting."""
    memory_injection_enabled: bool | None = None
    # Per-bot overrides for the rest of the agentic surface.
    mcp_servers: list[str] = field(default_factory=list)
    """MCP servers this bot may use. Empty means no additional restriction."""
    agent_preset: str | None = None
    """Named agent preset (see ``agent_presets`` in config). None = default."""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def memory_namespace(self) -> str:
        """The namespace this bot's memories live under.

        Defaults to the bot name so each bot gets its own memory by default
        rather than sharing one global pool, while still letting an operator
        point several bots at a shared namespace when that is intended.
        """
        return (self.memory_scope or self.name).strip().lower()

    def memory_storage_key(self) -> str:
        """Path-safe form of :meth:`memory_namespace` for use as a directory name.

        Bot names are operator-supplied and may contain separators, dots or
        ``..``. :meth:`memory_namespace` is the logical key; this is what may be
        joined onto a filesystem path. Without the sanitisation a bot named
        ``../../etc`` could point its memory store outside the intended root.
        """
        import re

        raw = self.memory_namespace()
        safe = re.sub(r"[^a-z0-9._-]+", "-", raw)
        # Collapse dot-runs: "a/../b" becomes "a-..-b", and ".." is still a
        # traversal segment on its own.
        safe = re.sub(r"\.{2,}", ".", safe)
        safe = safe.strip("-.")
        return safe or "bot"

    def effective_memory_settings(self) -> dict[str, Any]:
        """Only the memory fields this bot actually overrides.

        Callers merge this over the global memory config, so ``None`` always
        means "leave the host setting alone" instead of "false".
        """
        overrides: dict[str, Any] = {}
        if self.memory_enabled is not None:
            overrides["enabled"] = self.memory_enabled
        if self.memory_mode is not None:
            overrides["mode"] = self.memory_mode
        if self.memory_injection_enabled is not None:
            overrides["injection_enabled"] = self.memory_injection_enabled
        overrides["namespace"] = self.memory_namespace()
        return overrides

    def effective_capabilities(
        self,
        candidate_tools: list[str] | None = None,
        *,
        enabled_skills: list[str] | None = None,
        available_mcp_servers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve what this bot can actually do, for preview and audit.

        There was no way to ask "what can this bot use?" — ``toolsets`` and
        ``skills`` are stored on the profile, but nothing combined them with the
        role ring or the configured skill/MCP allowlists into one answer. That
        made the declared surface invisible to operators and to the UI.

        Pure and side-effect free: pass the candidate names in, get the resolved
        surface back. ``candidate_tools=None`` means "cannot resolve tools yet",
        which yields ``None`` rather than a misleading empty list.
        """
        allowed_tools: list[str] | None = None
        if candidate_tools is not None:
            from agent_workspace.bots.permissions import ToolPermissionGate

            gate = ToolPermissionGate()
            kept: list[str] = []
            for name in candidate_tools:
                allowed, _reason, requires_approval = gate.check_permission(
                    self.role, name
                )
                if allowed or requires_approval:
                    kept.append(name)
            allowed_tools = kept

        skills = list(self.skills)
        if enabled_skills is not None:
            declared = set(self.skills)
            skills = [s for s in enabled_skills if s in declared]

        servers = list(self.mcp_servers)
        if available_mcp_servers is not None and self.mcp_servers:
            configured = set(available_mcp_servers)
            servers = [s for s in self.mcp_servers if s in configured]

        return {
            "bot": self.name,
            "role": self.role,
            "model": self.model or "inherit",
            "agent_preset": self.agent_preset or "default",
            "tools": allowed_tools,
            "skills": skills,
            "mcp_servers": servers,
            "memory_namespace": self.memory_namespace(),
            "epoch": self.capability_fingerprint(),
        }

    def capability_fingerprint(self) -> str:
        """Compute a deterministic 12-hex digest of the bot's capability surface.

        Hashed surface includes: name, role, soul content, model, sorted
        toolsets, sorted skills, the memory namespace/mode, the agent preset and
        the MCP server list. Memory and MCP are part of the capability surface:
        changing what a bot can remember or which servers it can reach is a real
        capability change, so the epoch must move with it.
        """
        surface = {
            "name": self.name.lower().strip(),
            "role": self.role.strip(),
            "soul_hash": hashlib.sha256(self.soul.encode("utf-8")).hexdigest(),
            "model": self.model or "default",
            "toolsets": sorted(self.toolsets),
            "skills": sorted(self.skills),
            "memory_namespace": self.memory_namespace(),
            "memory_mode": self.memory_mode or "inherit",
            "memory_enabled": self.memory_enabled,
            "agent_preset": self.agent_preset or "default",
            "mcp_servers": sorted(self.mcp_servers),
        }
        raw_json = json.dumps(surface, sort_keys=True)
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()[:12]

    def epoch_header(self) -> str:
        """The capability epoch stamp to embed in system prompts."""
        return f"Capability epoch: {self.capability_fingerprint()}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["epoch"] = self.capability_fingerprint()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BotProfile:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


def generate_default_soul(name: str, role: str) -> str:
    """Generate an authoritative, context-aware SOUL for auto-provisioned bots."""
    return f"""# SOUL.md - {name.capitalize()} ({role})

You are **{name}**, operating as a specialized AI teammate in the role of **{role}**.

## Core Directives:
1. Actively contribute your specialized domain expertise in group chats and tasks.
2. In team discussions, address teammates with their @handle when handing off work.
3. Be concise, direct, and action-oriented. Avoid cheerful filler text.
4. When you have nothing essential to add to a turn, respond with `(pass)`.
5. Claim tasks from the Kanban board matching your specialty and submit deliverables for peer review.
"""
