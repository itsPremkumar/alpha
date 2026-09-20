"""Built-in Self-Evolving Dynamic Tool Metacompiler Tools.

Exposes ``synthesize_runtime_tool`` and ``list_dynamic_tools`` to the agent.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import tool

from alpha.metacompiler.dynamic_tool_synthesizer import (
    DEFAULT_ENTRYPOINT,
    get_dynamic_tool_synthesizer,
)


@tool("synthesize_runtime_tool", parse_docstring=True)
def synthesize_runtime_tool(
    name: str,
    source_code: str,
    description: str = "",
    entrypoint: str = DEFAULT_ENTRYPOINT,
    parameters_json: str = "{}",
    test_code: str = "",
    action: str = "synthesize",
    workspace_root: str = "",
    tags: str = "",
) -> dict[str, Any]:
    """Synthesize, security-verify, self-test and register a new agent tool at runtime.

    The source is first screened by a static AST security validator that blocks
    process execution (``os.system``, ``subprocess``), unsafe deserialization
    (``pickle``), network listeners (``socket``), dynamic imports, filesystem
    deletion, and writes resolving outside the workspace. Accepted sources are
    compiled into an isolated ephemeral module namespace with restricted
    builtins, then executed against their own synthetic unit tests. Only tools
    that pass self-validation are registered in the live tool registry.

    Args:
        name: Snake-case tool name; re-synthesizing bumps the tool version.
        source_code: Python source defining a callable named ``entrypoint``.
            Optionally also define ``self_test()`` returning truthy or
            ``{"success": True}`` to supply synthetic unit tests.
        description: Human readable purpose of the tool.
        entrypoint: Name of the callable exposed to the agent (default ``run``).
        parameters_json: Optional JSON object describing the parameters.
        test_code: Optional additional synthetic test source defining
            ``self_test()``.
        action: ``synthesize`` (validate and register), ``dry_run`` (validate
            only), ``invoke`` (call an existing tool), or ``unload``
            (hot-unload and reclaim the namespace).
        workspace_root: Workspace root used to bound filesystem writes.
        tags: Optional comma-separated tags.

    Returns:
        dict: ``{"success": bool, "status": str, "message": str, ...}``. On
        failure ``success`` is False and ``status`` names the reason, for
        example ``security-rejected`` or ``self-validation-failed``.
    """
    try:
        parameters = json.loads(parameters_json) if parameters_json else {}
        if not isinstance(parameters, dict):
            parameters = {}
    except (ValueError, TypeError):
        parameters = {}

    try:
        synthesizer = get_dynamic_tool_synthesizer(workspace_root or None)
        normalized_action = (action or "synthesize").strip().lower()
        tag_list = [item.strip() for item in tags.split(",") if item.strip()]

        if normalized_action == "invoke":
            payload = parameters or {}
            return synthesizer.invoke(name, payload)

        if normalized_action == "unload":
            removed = synthesizer.unload(name)
            return {
                "success": removed,
                "tool": name,
                "status": "unloaded" if removed else "not-found",
                "message": f"tool '{name}' unloaded" if removed else f"no tool '{name}'",
            }

        if normalized_action not in {"synthesize", "dry_run"}:
            return {
                "success": False,
                "tool": name,
                "status": "invalid-action",
                "message": f"unsupported action '{action}'",
            }

        return synthesizer.synthesize(
            name=name,
            source_code=source_code,
            description=description,
            entrypoint=entrypoint,
            parameters=parameters,
            test_code=test_code,
            tags=tag_list,
            dry_run=normalized_action == "dry_run",
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "success": False,
            "tool": name,
            "status": "synthesis-error",
            "message": f"{type(exc).__name__}: {exc}",
        }


@tool("list_dynamic_tools", parse_docstring=True)
def list_dynamic_tools(
    action: str = "list",
    name: str = "",
    include_source: bool = False,
    workspace_root: str = "",
) -> dict[str, Any]:
    """Inspect the registry of runtime-synthesized agent tools.

    Args:
        action: ``list`` returns the active inventory, ``history`` returns
            superseded versions of ``name``, and ``stats`` returns registry
            counters.
        name: Tool name required by ``history``.
        include_source: Include the full synthesized source in the listing.
        workspace_root: Workspace root owning the synthesizer.

    Returns:
        dict: ``{"success": bool, "count": int, "results": list}``. On failure
        ``success`` is False and ``error`` describes the reason.
    """
    try:
        synthesizer = get_dynamic_tool_synthesizer(workspace_root or None)
        normalized_action = (action or "list").strip().lower()

        if normalized_action == "history":
            if not name:
                return {
                    "success": False,
                    "error": "name is required for action='history'",
                    "count": 0,
                    "results": [],
                }
            history = synthesizer.registry.history(name)
            return {"success": True, "count": len(history), "results": history}

        if normalized_action == "stats":
            return {
                "success": True,
                "count": len(synthesizer.registry),
                "results": [
                    {
                        "active_tools": len(synthesizer.registry),
                        "tool_names": synthesizer.registry.names(),
                        "max_tools": synthesizer.registry.max_tools,
                        "workspace_root": str(synthesizer.workspace_root),
                    }
                ],
            }

        if normalized_action != "list":
            return {
                "success": False,
                "error": f"unknown action '{action}'",
                "count": 0,
                "results": [],
            }

        return synthesizer.list_tools(include_source=include_source)
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "count": 0,
            "results": [],
        }
