"""Built-in Language Server Protocol Intelligence Tool.

Exposes the LSP/LSIF intelligence engine to the agent as a single tool:
``query_language_server_symbol``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain.tools import tool

from agent_workspace.coding.lsp_client_engine import (
    StaticIndexReader,
    get_language_server_engine,
)


@tool("query_language_server_symbol", parse_docstring=True)
def query_language_server_symbol(
    symbol: str,
    action: str = "definition",
    file_path: str = "",
    line: int = 0,
    character: int = 0,
    workspace_root: str = "",
    index_path: str = "",
    max_results: int = 100,
) -> dict[str, Any]:
    """Query compiler-grade symbol intelligence for a workspace via LSP, LSIF or SCIP.

    Use this tool to jump to definitions, enumerate references, read document
    symbol outlines, infer hover types, or collect diagnostics. It first tries a
    prebuilt LSIF/SCIP index, then a live language server (pyright, tsserver,
    rust-analyzer, gopls), and finally falls back to a built-in static AST
    symbol index so the query never fails in a bare environment.

    Args:
        symbol: Symbol name to resolve, for example ``BlackboardEngine``.
        action: One of ``definition``, ``references``, ``hover``,
            ``document_symbol``, ``diagnostics``, ``servers``, ``load_index``.
        file_path: Optional file path providing context for the query.
        line: Optional 1-based line number for position-scoped queries.
        character: Optional 0-based character offset on ``line``.
        workspace_root: Workspace root to analyze. Defaults to the current
            working directory.
        index_path: Optional path to a prebuilt LSIF or SCIP index file.
        max_results: Maximum number of results to return.

    Returns:
        dict: ``{"success": bool, "action": str, "symbol": str, "source": str,
        "count": int, "results": list}``. On failure ``success`` is False and
        ``error`` describes the reason.
    """
    try:
        root = Path(workspace_root).resolve() if workspace_root else Path(os.getcwd()).resolve()
        engine = get_language_server_engine(root)

        normalized_action = (action or "definition").strip().lower()

        if normalized_action == "servers":
            return {
                "success": True,
                "action": "servers",
                "symbol": symbol,
                "source": "registry",
                "count": len(engine.available_servers()),
                "results": engine.available_servers(),
            }

        if normalized_action == "load_index":
            if not index_path:
                return {
                    "success": False,
                    "action": "load_index",
                    "symbol": symbol,
                    "error": "index_path is required for action='load_index'",
                    "count": 0,
                    "results": [],
                }
            loaded = engine.load_static_index(index_path)
            return {
                "success": loaded >= 0,
                "action": "load_index",
                "symbol": symbol,
                "source": str(engine.static_index.format or ""),
                "count": loaded,
                "results": [],
            }

        if index_path:
            reader = StaticIndexReader()
            reader.load(Path(index_path))
            if reader.symbol_count:
                engine.static_index = reader

        if normalized_action not in {
            "definition",
            "references",
            "hover",
            "document_symbol",
            "diagnostics",
        }:
            return {
                "success": False,
                "action": normalized_action,
                "symbol": symbol,
                "error": f"unsupported action '{action}'",
                "count": 0,
                "results": [],
            }

        return engine.query(
            symbol=symbol,
            action=normalized_action,
            file_path=file_path,
            line=line,
            character=character,
            max_results=max_results,
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "success": False,
            "action": action,
            "symbol": symbol,
            "error": f"{type(exc).__name__}: {exc}",
            "count": 0,
            "results": [],
        }
