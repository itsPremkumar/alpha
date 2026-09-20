"""Built-in Deferred Tool Search, Describe, and Call suite inspired by OpenClaw."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from alpha.tools.search.catalog import get_universal_catalog


@tool("catalog_tool_search", parse_docstring=True)
def catalog_tool_search(
    query: str,
    limit: int = 5,
) -> str:
    """Search available tools in the universal catalog by name, category, or description keywords.

    This provides deferred tool discovery: instead of loading dozens of tool definitions into
    context upfront, search on-demand when a capability is needed.

    Args:
        query: Keyword, capability name, or semantic category to search for.
        limit: Maximum number of search results to return (default: 5).
    """
    catalog = get_universal_catalog()
    results = catalog.search(query=query, limit=limit)
    if not results:
        return f"No tools found matching query '{query}'."
    return json.dumps(results, indent=2)


@tool("catalog_tool_describe", parse_docstring=True)
def catalog_tool_describe(
    tool_name: str,
) -> str:
    """Retrieve full schema, parameter definitions, and documentation for a catalog tool.

    Call this once you have found a tool with `catalog_tool_search` to inspect its parameters.

    Args:
        tool_name: The exact name of the tool to inspect.
    """
    catalog = get_universal_catalog()
    try:
        details = catalog.describe(tool_name)
        return json.dumps(details, indent=2)
    except KeyError as e:
        return f"Error: {e}"


@tool("catalog_tool_call", parse_docstring=True)
def catalog_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Call a discovered catalog tool on-demand without keeping its full schema in the persistent prompt.

    Args:
        tool_name: Name of the tool to execute.
        arguments: Dictionary of arguments matching the schema obtained from `catalog_tool_describe`.
    """
    catalog = get_universal_catalog()
    try:
        res = catalog.call(tool_name, arguments or {})
        if isinstance(res, (dict, list)):
            return json.dumps(res, indent=2)
        return str(res)
    except Exception as e:
        return f"Error calling '{tool_name}': {e}"
