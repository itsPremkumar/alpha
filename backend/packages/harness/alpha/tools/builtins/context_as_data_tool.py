"""Built-in Context-as-Data (RLM) tool inspired by Prime Agent."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.runtime.rlm import RLMEngine

_RLM_ENGINE = RLMEngine()


@tool("manage_context_data", parse_docstring=True)
def manage_context_data(
    action: str,
    content: str = "",
    label: str = "",
    handle_id: str = "",
    query: str = "",
    start_line: int = 1,
    end_line: int = 50,
) -> str:
    """Manage and transform massive context variables programmatically without prompt bloat.

    Implements the Prime Agent Context-as-Data pattern: contexts are stored as immutable
    addressable handles (R_id) and sliced/grepped on-demand.

    Args:
        action: Operation to perform: 'load' (stores content and returns handle), 'grep' (searches lines in handle), 'slice' (extracts line range), 'peek' (previews head/tail lines), 'stats' (shows memory usage).
        content: Text content to store (for 'load').
        label: Descriptive label for the context variable.
        handle_id: The target ContextHandle ID (for grep, slice, peek).
        query: Search string for 'grep'.
        start_line: Starting line (1-indexed) for 'slice'.
        end_line: Ending line for 'slice'.
    """
    if action == "load":
        handle = _RLM_ENGINE.load_variable(content=content, label=label)
        return json.dumps({
            "action": "load",
            "handle": handle.to_dict(),
        }, indent=2)

    elif action == "grep":
        sub_handle = _RLM_ENGINE.grep_variable(handle_id=handle_id, query=query)
        if not sub_handle:
            return json.dumps({"error": f"Failed to grep on handle '{handle_id}'."}, indent=2)
        peek_res = _RLM_ENGINE.peek(sub_handle.handle_id, max_lines=25)
        return json.dumps({
            "action": "grep",
            "derived_handle": sub_handle.to_dict(),
            "matches_preview": peek_res.get("preview_lines", []),
        }, indent=2)

    elif action == "slice":
        sub_handle = _RLM_ENGINE.slice_variable(
            handle_id=handle_id,
            start_line=start_line,
            end_line=end_line,
        )
        if not sub_handle:
            return json.dumps({"error": f"Failed to slice handle '{handle_id}'."}, indent=2)
        text = _RLM_ENGINE.fetch_text(sub_handle.handle_id)
        return json.dumps({
            "action": "slice",
            "derived_handle": sub_handle.to_dict(),
            "sliced_content": text,
        }, indent=2)

    elif action == "peek":
        res = _RLM_ENGINE.peek(handle_id=handle_id)
        return json.dumps(res, indent=2)

    elif action == "stats":
        return json.dumps(_RLM_ENGINE.stats(), indent=2)

    else:
        return json.dumps({
            "error": f"Unknown action '{action}'. Supported: 'load', 'grep', 'slice', 'peek', 'stats'."
        }, indent=2)
