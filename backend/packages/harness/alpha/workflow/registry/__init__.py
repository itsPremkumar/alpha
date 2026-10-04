"""Discovery registries for the selection plane — what may I use, and who am I?

Read-only ``list`` / ``describe`` / ``health`` over every source an agent needs to
answer "what am I, and what can I do?" without guessing:

* ``capabilities`` — ``alpha.capabilities.catalog`` (per-entry ``find_spec`` probe)
* ``tools``       — ``alpha.tools.tools.BUILTIN_TOOLS`` (production registration list)
* ``skills``      — installed skill storage (SKILL.md scan + enabled state)
* ``mcp``         — ``extensions_config.json`` ``mcpServers`` config (no connections)
* ``memory``      — ``alpha.memory.*`` importability probes (no store construction)
* ``models``      — ``config.yaml`` ``models[]`` / ``providers`` (declared, never probed)
* ``bots``        — live ``alpha.bots`` profiles (archived excluded)
* ``commands``    — slash catalog, availability gated on a bound handler
* ``engines``     — generated manifest engine rows (importability probe)
* ``wiring``      — generated manifest routers / middlewares / loops (static wiring)
* ``identity``    — ``config/project-manifest.json`` + runtime identity

Honesty (repo-wide rules): every descriptor carries ``evidence_kind``; nothing
here claims a subsystem is *running* (``health`` stays ``unverified``);
``version`` is ``None`` unless a source declares one; broken sources fail
closed via :class:`RegistryUnavailable` with the real exception text instead of
reading as "zero entries".

Two rules that decide the shape of this package
-----------------------------------------------

**One source of truth per fact, never a re-derivation.** ``engines`` and ``wiring``
read ``contracts/feature_manifest.json`` rather than walking the filesystem, and
``models`` reads the live ``AppConfig`` rather than parsing ``config.yaml``. Every
capability count in this repository is generated and drift-gated; a registry that
formed its own opinion would be an unreviewed second answer to a question the
build already settles.

**Availability is what the source says, health is what we probed.** A model that
is *declared* is ``available`` with ``health="unverified"``; an MCP server that is
*enabled* is ``available`` with ``health="unverified"``. Collapsing those two words
is how "configured" becomes "working" in a status line nobody checked.

Wiring: :class:`WorkflowRegistry` is the ``alpha.capabilities.catalog`` target
of the ``workflow_registry`` capability entry, so the production capability
loader (``alpha.capabilities.registry`` → Gateway startup /
``GET /api/ops/integration-health``) is the production import chain for this
package. ``alpha.intelligence.self_inventory`` aggregates every kind into the
single self-knowledge payload the ``alpha_capability`` tool serves, and
``GET /api/workflows/system/registries`` exposes them per-kind over HTTP.

This package is deliberately side-effect-free at import: heavy sources
(``alpha.tools.tools``, skill storage, extensions config, the config file, git)
are imported lazily inside the registry methods, and no subsystem is instantiated
here. That also means every ``list()`` may do blocking I/O — disk, ``git``, or a
config re-read — so Gateway callers wrap these in ``asyncio.to_thread``.
"""

from __future__ import annotations

import threading

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    DescriptorRegistry,
    RegistryHealth,
    RegistryUnavailable,
)
from alpha.workflow.registry.bots import BotProfileRegistry
from alpha.workflow.registry.capabilities import CapabilityCatalogRegistry
from alpha.workflow.registry.commands import CommandRegistry
from alpha.workflow.registry.engines import EngineRegistry
from alpha.workflow.registry.identity import IdentityRegistry
from alpha.workflow.registry.mcp import MCPServerRegistry
from alpha.workflow.registry.memory import MemoryRegistry
from alpha.workflow.registry.models import ModelRegistry
from alpha.workflow.registry.skills import SkillRegistry
from alpha.workflow.registry.tools import BuiltinToolRegistry
from alpha.workflow.registry.wiring import WiringRegistry

#: Registry kinds exposed by the facade, in discovery order: "what am I" first,
#: then what I can call, then what I am built from. ``identity`` leads because
#: every other answer is relative to it.
REGISTRY_KINDS: tuple[str, ...] = (
    "identity",
    "tools",
    "skills",
    "mcp",
    "models",
    "bots",
    "commands",
    "capabilities",
    "engines",
    "wiring",
    "memory",
)


class WorkflowRegistry:
    """Aggregate facade over the selection-plane discovery registries."""

    def __init__(self) -> None:
        self._identity = IdentityRegistry()
        self._tools = BuiltinToolRegistry()
        self._skills = SkillRegistry()
        self._mcp = MCPServerRegistry()
        self._models = ModelRegistry()
        self._bots = BotProfileRegistry()
        self._commands = CommandRegistry()
        self._capabilities = CapabilityCatalogRegistry()
        self._engines = EngineRegistry()
        self._wiring = WiringRegistry()
        self._memory = MemoryRegistry()

    @property
    def identity(self) -> IdentityRegistry:
        """Registry over the shipped repository/runtime identity."""
        return self._identity

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
    def models(self) -> ModelRegistry:
        """Registry over the ``config.yaml`` model and provider namespaces."""
        return self._models

    @property
    def bots(self) -> BotProfileRegistry:
        """Registry over the live ``alpha.bots`` profiles."""
        return self._bots

    @property
    def commands(self) -> CommandRegistry:
        """Registry over the slash command catalog."""
        return self._commands

    @property
    def engines(self) -> EngineRegistry:
        """Registry over the generated engine package rows."""
        return self._engines

    @property
    def wiring(self) -> WiringRegistry:
        """Registry over the generated router / middleware / loop rows."""
        return self._wiring

    @property
    def memory(self) -> MemoryRegistry:
        """Registry over the ``alpha.memory.*`` import probes."""
        return self._memory

    def _registries(self) -> dict[str, DescriptorRegistry]:
        """Kind -> registry. Rebuilt per call, cheap, and the only lookup path.

        It is a method rather than a cached attribute on purpose: adding a kind to
        ``__init__`` without adding it here would make ``registry(kind)`` raise
        ``KeyError`` for a kind that ``REGISTRY_KINDS`` advertises, which is a
        confusing failure to debug. A property would hide the same mistake from
        the reader instead.
        """
        return {
            "identity": self._identity,
            "capabilities": self._capabilities,
            "tools": self._tools,
            "skills": self._skills,
            "mcp": self._mcp,
            "models": self._models,
            "bots": self._bots,
            "commands": self._commands,
            "engines": self._engines,
            "wiring": self._wiring,
            "memory": self._memory,
        }

    def registry(self, kind: str) -> DescriptorRegistry:
        """One registry by kind; unknown kinds raise an honest KeyError."""
        registries = self._registries()
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
    "BotProfileRegistry",
    "CapabilityCatalogRegistry",
    "CapabilityDescriptor",
    "CommandRegistry",
    "DescriptorRegistry",
    "EngineRegistry",
    "IdentityRegistry",
    "MCPServerRegistry",
    "MemoryRegistry",
    "ModelRegistry",
    "REGISTRY_KINDS",
    "RegistryHealth",
    "RegistryUnavailable",
    "SkillRegistry",
    "BuiltinToolRegistry",
    "WorkflowRegistry",
    "WiringRegistry",
    "get_workflow_registry",
]
