"""15. Thread-bound ACP agents as first-class runtimes.

OpenClaw 2026.3.1 made ACP agents first-class for thread sessions with
bindings CLI (bind/unbind). Agent Workspace has acp_agents config + invoke_acp_agent
tool with per-thread workspaces. This adds the missing thread->agent
binding registry (in-memory; durable thread metadata stays the owner).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AcpBindingRegistry:
    """thread_id -> acp agent name. Empty name = lead_agent default."""

    _bindings: dict[str, str] = field(default_factory=dict)

    def bind(self, thread_id: str, agent_name: str) -> None:
        if agent_name.strip():
            self._bindings[thread_id] = agent_name.strip()

    def unbind(self, thread_id: str) -> bool:
        return self._bindings.pop(thread_id, None) is not None

    def resolve(self, thread_id: str, default: str = "lead_agent") -> str:
        return self._bindings.get(thread_id, default)

    def list(self) -> dict[str, str]:
        return dict(self._bindings)


_registry_singleton: AcpBindingRegistry | None = None


def get_acp_registry() -> AcpBindingRegistry:
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = AcpBindingRegistry()
    return _registry_singleton
