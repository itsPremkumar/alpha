"""Built-in Deferred Tool Search, Describe, and Call suite inspired by OpenClaw."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from alpha.tools.search.catalog import get_universal_catalog
from alpha.tools.tool_discovery_metrics import (
    ERROR,
    NO_MATCH,
    PROMOTED,
    REASON_CALL_RAISED,
    REASON_ERROR_RESULT,
    REASON_NO_RESULTS,
    REASON_RESULTS_RETURNED,
    REASON_SCHEMA_RETURNED,
    classify_catalog_result,
    current_telemetry,
    record_call,
)


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
        record_call(current_telemetry(), kind="catalog_tool_search", outcome=NO_MATCH, reason=REASON_NO_RESULTS)
        return f"No tools found matching query '{query}'."
    record_call(current_telemetry(), kind="catalog_tool_search", outcome=PROMOTED, reason=REASON_RESULTS_RETURNED)
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
    telemetry = current_telemetry()
    try:
        details = catalog.describe(tool_name)
    except KeyError as e:
        record_call(telemetry, kind="catalog_tool_describe", outcome=ERROR, reason=REASON_ERROR_RESULT)
        return f"Error: {e}"
    record_call(telemetry, kind="catalog_tool_describe", outcome=PROMOTED, reason=REASON_SCHEMA_RETURNED)
    return json.dumps(details, indent=2)


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
    telemetry = current_telemetry()
    try:
        res = catalog.call(tool_name, arguments or {})
    except Exception as e:
        # A non-throwing call is not a success and neither is a throwing one:
        # record the error outcome before the handler's error string is built.
        record_call(telemetry, kind="catalog_tool_call", outcome=ERROR, reason=REASON_CALL_RAISED)
        return f"Error calling '{tool_name}': {e}"
    # Telemetry only: a `catalog.call()` that returns an error-valued
    # `ToolMessage` or an error string is an error outcome, never a promotion.
    # `classify_catalog_result` reads the result status before anything is
    # counted, so an error result can never reach the promotion counter.
    outcome, reason = classify_catalog_result(res)
    record_call(telemetry, kind="catalog_tool_call", outcome=outcome, reason=reason)
    if isinstance(res, (dict, list)):
        return json.dumps(res, indent=2)
    return str(res)
