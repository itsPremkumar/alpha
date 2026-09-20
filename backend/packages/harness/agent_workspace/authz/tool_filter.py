"""Convenience wrapper for Layer 1 tool authorization filtering.

Combines provider resolution, Principal construction, and tool filtering into
a single call so the three assembly paths (lead agent, subagent, embedded
client) stay one-liners.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain_core.tools import BaseTool

from agent_workspace.authz.enforcement import filter_tools_by_authorization
from agent_workspace.authz.principal import build_principal_from_context
from agent_workspace.authz.provider import AuthorizationProvider, Principal
from agent_workspace.authz.runtime import resolve_authorization_provider
from agent_workspace.config.app_config import AppConfig
from agent_workspace.tools.mcp_metadata import mcp_server_name

logger = logging.getLogger(__name__)


def apply_tool_authorization(
    tools: list[BaseTool],
    *,
    context: Mapping[str, Any],
    app_config: AppConfig,
    authorization_provider: AuthorizationProvider | None = None,
    authorize_mcp_servers: bool = False,
) -> tuple[list[BaseTool], AuthorizationProvider | None]:
    """Apply Layer 1 tool authorization filtering.

    Resolves the provider (or reuses a caller-provided one so Layer 1 and
    Layer 2 share a single instance), builds a Principal from *context*, and
    filters *tools* in place by the provider's policy.

    When ``authorization.enabled`` is false, this is a no-op: returns the
    original tools and ``None``.

    Args:
        tools: Candidate tools (already skill-filtered etc.).
        context: Runtime context mapping (the merged ``cfg`` dict or an
            equivalent dict assembled from ``self.*`` fields).
        app_config: The resolved AppConfig (used for authorization settings).
        authorization_provider: An already-resolved provider, or ``None`` to
            resolve from ``app_config.authorization`` here.
        authorize_mcp_servers: Also filter MCP tools by the **server** that
            exposes them, using the ``mcp_server`` resource class RBAC already
            defines. Defaults to False because no live call site has ever passed
            ``resource="mcp_server"``; enabling it changes visibility for any
            role with a server policy, so it must be opted into deliberately.

    Returns:
        ``(filtered_tools, provider)`` — the filtered tool list and the
        provider instance (for passing to Layer 2 middleware wiring, or
        ``None`` when authorization is disabled).
    """
    authz_config = app_config.authorization
    # Guard against Mock objects in tests: MagicMock attribute access returns
    # a truthy child mock for ``enabled``, which would trigger provider
    # resolution on a non-string ``provider.use``. Real AuthorizationConfig
    # has ``enabled: bool``; if it's not actually ``True``, skip.
    if authz_config.enabled is not True:
        return tools, None

    if authorization_provider is None:
        authorization_provider = resolve_authorization_provider(authz_config)

    if authorization_provider is None:
        return tools, None

    principal = build_principal_from_context(context, default_role=authz_config.default_role)
    filtered = filter_tools_by_authorization(
        tools,
        provider=authorization_provider,
        principal=principal,
        fail_closed=authz_config.fail_closed,
    )
    if authorize_mcp_servers:
        filtered = _filter_mcp_tools_by_server(
            filtered, provider=authorization_provider, principal=principal
        )
    return filtered, authorization_provider


def _filter_mcp_tools_by_server(
    tools: list[BaseTool],
    *,
    provider: AuthorizationProvider,
    principal: Principal,
) -> list[BaseTool]:
    """Drop MCP tools whose *server* the principal may not see.

    A tool can be permitted by its own name while the server backing it is not.
    RBAC already models servers as their own resource class, so check both.
    """
    server_names = {s for s in (mcp_server_name(t) for t in tools) if s}
    if not server_names:
        return tools

    try:
        allowed_servers = set(
            provider.filter_resources(principal, "mcp_server", sorted(server_names))
        )
    except Exception:
        # Fault isolation: a provider that cannot answer for this resource type
        # must not remove every MCP tool. Keep them and let Layer 2 decide.
        logger.warning(
            "MCP server authorization unavailable; skipping server-level filter",
            exc_info=True,
        )
        return tools

    return [
        t
        for t in tools
        if mcp_server_name(t) is None or mcp_server_name(t) in allowed_servers
    ]
