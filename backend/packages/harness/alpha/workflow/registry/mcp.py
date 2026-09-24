"""MCP-server registry — read-only view of ``extensions_config.json``.

Source of truth: ``alpha.config.extensions_config.ExtensionsConfig.from_file()``
(the live ``mcpServers`` map). Reads only; never connects to a server, never
starts a transport.

Honesty contract:

* ``availability="available"`` = the server is ``enabled`` in config — a
  measured config fact, NOT a connection. ``health`` therefore stays
  ``unverified`` for every descriptor, enabled or not: no transport is probed
  here (live connection state belongs to ``alpha.mcp.session_pool`` at call
  time, which this registry does not touch).
* Disabled servers are honestly ``unavailable`` with a non-empty ``reason``.
* An unparsable/unreadable config raises :class:`RegistryUnavailable` from
  ``list()`` with the real exception text (fail-closed: a broken config must
  not read as "zero servers configured").

``alpha.config.extensions_config`` is imported lazily so this module stays
cheap to import.
"""

from __future__ import annotations

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
    RegistryUnavailable,
)

_AUTHORITY = "operator config (extensions_config.json mcpServers)"
_DISABLED_REASON = "server disabled in extensions_config.json (mcpServers enabled flag)"


class MCPServerRegistry:
    """list/describe/health over configured MCP servers."""

    name = "mcp"

    def list(self) -> list[CapabilityDescriptor]:
        config = self._load_config()
        base = self._config_base()
        return [
            self._describe_server(name, server, f"{base}.{name}")
            for name, server in config.mcp_servers.items()
        ]

    def describe(self, server_name: str) -> CapabilityDescriptor | None:
        config = self._load_config()
        server = config.mcp_servers.get(server_name)
        if server is None:
            return None
        base = self._config_base()
        return self._describe_server(server_name, server, f"{base}.{server_name}")

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
    def _load_config() -> object:
        try:
            from alpha.config.extensions_config import ExtensionsConfig

            return ExtensionsConfig.from_file()
        except Exception as exc:
            raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _config_base() -> str:
        try:
            from alpha.config.extensions_config import ExtensionsConfig

            path = ExtensionsConfig.resolve_config_path()
        except Exception:
            path = None
        return f"{path}#mcpServers" if path is not None else "extensions_config.json (unresolved)#mcpServers"

    @staticmethod
    def _describe_server(server_name: str, server: object, source: str) -> CapabilityDescriptor:
        enabled = bool(getattr(server, "enabled", False))
        return CapabilityDescriptor(
            id=server_name,
            kind="mcp_server",
            availability="available" if enabled else "unavailable",
            source=source,
            version=None,
            health="unverified",
            authority=_AUTHORITY,
            evidence_kind="measured",
            reason=None if enabled else _DISABLED_REASON,
        )
