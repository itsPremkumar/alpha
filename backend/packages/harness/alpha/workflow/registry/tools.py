"""Builtin-tool registry — read-only view of ``alpha.tools.tools.BUILTIN_TOOLS``.

Source of truth: the production builtin-tool list (deduplicated at import of
``alpha.tools.tools``). ``availability="available"`` means the tool object is
REGISTERED in that production list at call time — a measured fact. It is not a
claim that the tool executes successfully, so ``health="unverified"``.

Honest scope note: config-group tools resolved by ``get_available_tools()`` and
MCP-server tools (see :mod:`alpha.workflow.registry.mcp` for server config) are
NOT enumerated here — only the builtins. ``alpha.tools.tools`` is imported
lazily inside the methods so this module stays cheap to import.
"""

from __future__ import annotations

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
    RegistryUnavailable,
)

_SOURCE = "alpha.tools.tools:BUILTIN_TOOLS"
_AUTHORITY = "developer code + config (alpha.tools.tools.BUILTIN_TOOLS)"


class BuiltinToolRegistry:
    """list/describe/health over the builtin tool list."""

    name = "tools"

    def list(self) -> list[CapabilityDescriptor]:
        tools = self._load_builtin_tools()
        return [
            CapabilityDescriptor(
                id=tool_name,
                kind="tool",
                availability="available",
                source=_SOURCE,
                version=None,
                health="unverified",
                authority=_AUTHORITY,
                evidence_kind="measured",
            )
            for tool_name in self._tool_names(tools)
        ]

    def describe(self, tool_name: str) -> CapabilityDescriptor | None:
        tools = self._load_builtin_tools()
        if tool_name not in self._tool_names(tools):
            return None
        return CapabilityDescriptor(
            id=tool_name,
            kind="tool",
            availability="available",
            source=_SOURCE,
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
        )

    def health(self) -> RegistryHealth:
        try:
            descriptors = self.list()
        except Exception as exc:
            return RegistryHealth(
                registry=self.name,
                status="unavailable",
                count=None,
                error=f"{type(exc).__name__}: {exc}",
                evidence_kind="measured",
            )
        return RegistryHealth(
            registry=self.name,
            status="ok",
            count=len(descriptors),
            error=None,
            evidence_kind="measured",
        )

    @staticmethod
    def _load_builtin_tools() -> list[object]:
        try:
            from alpha.tools.tools import BUILTIN_TOOLS
        except Exception as exc:
            raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return list(BUILTIN_TOOLS)

    @staticmethod
    def _tool_names(tools: list[object]) -> list[str]:
        names: list[str] = []
        for tool in tools:
            name = getattr(tool, "name", None)
            if isinstance(name, str) and name:
                names.append(name)
        return names
