"""W-N3 discovery registries for the universal dynamic workflow.

Read-only ``list`` / ``describe`` / ``health`` over the five selection-plane
sources the dynamic-workflow planner needs when it asks "what may I use?":

* ``capabilities`` — ``alpha.capabilities.catalog`` (per-entry ``find_spec`` probe)
* ``tools``       — ``alpha.tools.tools.BUILTIN_TOOLS`` (production registration list)
* ``skills``      — installed skill storage (SKILL.md scan + enabled state)
* ``mcp``         — ``extensions_config.json`` ``mcpServers`` config (no connections)
* ``memory``      — ``alpha.memory.*`` importability probes (no store construction)

Honesty (repo-wide rules): every descriptor carries ``evidence_kind``; nothing
here claims a subsystem is *running* (``health`` stays ``unverified``);
``version`` is ``None`` unless a source declares one; broken sources fail
closed via :class:`RegistryUnavailable` with the real exception text instead of
reading as "zero entries".

Wiring: :class:`WorkflowRegistry` is the ``alpha.capabilities.catalog`` target
of the ``workflow_registry`` capability entry, so the production capability
loader (``alpha.capabilities.registry`` → Gateway startup /
``GET /api/ops/integration-health``) is the production import chain for this
package. Consumers in the orchestrator loop / workflows router arrive with
W-N2+ (those files are lead-owned); integration hooks ship as patch snippets
in the wave report.

This package is deliberately side-effect-free at import: heavy sources
(``alpha.tools.tools``, skill storage, extensions config) are imported lazily
inside the registry methods, and no subsystem is instantiated here.
"""

from __future__ import annotations

import threading

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    DescriptorRegistry,
    RegistryHealth,
    RegistryUnavailable,
)
from alpha.workflow.registry.capabilities import CapabilityCatalogRegistry
from alpha.workflow.registry.mcp import MCPServerRegistry
from alpha.workflow.registry.memory import MemoryRegistry
from alpha.workflow.registry.skills import SkillRegistry
from alpha.workflow.registry.tools import BuiltinToolRegistry

#: Registry kinds exposed by the facade, in discovery order.
REGISTRY_KINDS: tuple[str, ...] = ("capabilities", "tools", "skills", "mcp", "memory")


class WorkflowRegistry:
    """Aggregate facade over the five discovery registries."""

    def __init__(self) -> None:
        self._capabilities = CapabilityCatalogRegistry()
        self._tools = BuiltinToolRegistry()
        self._skills = SkillRegistry()
        self._mcp = MCPServerRegistry()
        self._memory = MemoryRegistry()

    @property
    def capabilities(self) -> CapabilityCatalogRegistry:
        """Registry over ``alpha.capabilities.catalog``."""
        return self._capabilities

    @property
    def tools(self) -> BuiltinToolRegistry:
        """Registry over ``alpha.tools.tools.BUILTIN_TOOLS``."""
        return self._tools

    @property
    def skills(self) -> SkillRegistry:
        """Registry over the installed skill storage."""
        return self._skills

    @property
    def mcp(self) -> MCPServerRegistry:
        """Registry over ``extensions_config.json`` mcpServers config."""
        return self._mcp

    @property
    def memory(self) -> MemoryRegistry:
        """Registry over the ``alpha.memory.*`` import probes."""
        return self._memory

    def registry(self, kind: str) -> DescriptorRegistry:
        """One registry by kind; unknown kinds raise an honest KeyError."""
        registries: dict[str, DescriptorRegistry] = {
            "capabilities": self._capabilities,
            "tools": self._tools,
            "skills": self._skills,
            "mcp": self._mcp,
            "memory": self._memory,
        }
        if kind not in registries:
            raise KeyError(f"unknown registry kind {kind!r}; expected one of {sorted(registries)}")
        return registries[kind]

    def list(self, kind: str) -> list[CapabilityDescriptor]:
        """All descriptors for one registry (fail-closed via RegistryUnavailable)."""
        return self.registry(kind).list()

    def describe(self, kind: str, entry_id: str) -> CapabilityDescriptor | None:
        """One descriptor by registry kind + id, or None when honestly absent."""
        return self.registry(kind).describe(entry_id)

    def health(self) -> dict[str, RegistryHealth]:
        """Aggregate health report; never raises, one failure never hides the rest."""
        report: dict[str, RegistryHealth] = {}
        for kind in REGISTRY_KINDS:
            try:
                report[kind] = self.registry(kind).health()
            except Exception as exc:  # pragma: no cover - sub-health already catches
                report[kind] = RegistryHealth(
                    registry=kind,
                    status="unavailable",
                    count=None,
                    error=f"{type(exc).__name__}: {exc}",
                    evidence_kind="measured",
                )
        return report


_default_registry: WorkflowRegistry | None = None
_default_registry_lock = threading.Lock()


def get_workflow_registry() -> WorkflowRegistry:
    """Process-wide facade singleton (double-checked lock, idempotent)."""
    global _default_registry
    if _default_registry is None:
        with _default_registry_lock:
            if _default_registry is None:
                _default_registry = WorkflowRegistry()
    return _default_registry


__all__ = [
    "CapabilityDescriptor",
    "DescriptorRegistry",
    "REGISTRY_KINDS",
    "RegistryHealth",
    "RegistryUnavailable",
    "WorkflowRegistry",
    "get_workflow_registry",
]
