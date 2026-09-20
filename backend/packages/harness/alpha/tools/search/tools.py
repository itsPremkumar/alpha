"""Convenience functional endpoints for tool catalog operations."""

from __future__ import annotations

from typing import Any

from alpha.tools.search.catalog import get_universal_catalog


def catalog_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search registered catalog tools for a query string."""
    catalog = get_universal_catalog()
    return catalog.search(query=query, limit=limit)


def catalog_describe(tool_name: str) -> dict[str, Any]:
    """Retrieve the full parameter specification and docstring for a tool."""
    catalog = get_universal_catalog()
    return catalog.describe(tool_name=tool_name)


def catalog_call(tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Execute a registered catalog tool with arguments."""
    catalog = get_universal_catalog()
    return catalog.call(tool_name=tool_name, arguments=arguments)
