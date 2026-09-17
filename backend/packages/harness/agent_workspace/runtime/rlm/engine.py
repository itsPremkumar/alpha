from __future__ import annotations

import logging
from typing import Any

from .context_data import ContextHandle, ContextStore
from .transform import ContextTransformer

logger = logging.getLogger("agent_workspace.runtime.rlm")


class RLMEngine:
    """
    Recursive Language Model (RLM) Context Engine inspired by Prime Agent.
    Treats context as addressable data variables (ContextHandle / R_id)
    allowing lazy retrieval, grep filtering, slicing, and chunked processing.
    """

    def __init__(self, store: ContextStore | None = None) -> None:
        self.store = store or ContextStore()
        self.transformer = ContextTransformer(self.store)

    def load_variable(
        self,
        content: str,
        label: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ContextHandle:
        """Loads large content into context storage and returns an addressable handle."""
        return self.store.register(content=content, label=label, metadata=metadata)

    def grep_variable(
        self,
        handle_id: str,
        query: str,
        case_sensitive: bool = False,
        context_lines: int = 0,
    ) -> ContextHandle | None:
        """Runs programmatic grep on a context variable, returning a narrowed handle."""
        return self.transformer.grep(
            handle_id=handle_id,
            query=query,
            case_sensitive=case_sensitive,
            context_lines=context_lines,
        )

    def slice_variable(
        self,
        handle_id: str,
        start_line: int,
        end_line: int,
    ) -> ContextHandle | None:
        """Slices a line range from a context variable without prompt overhead."""
        return self.transformer.slice_lines(
            handle_id=handle_id,
            start_line=start_line,
            end_line=end_line,
        )

    def peek(self, handle_id: str, max_lines: int = 20) -> dict[str, Any]:
        """Peeks at the beginning and end of a context variable without loading all text."""
        handle = self.store.get_handle(handle_id)
        content = self.store.get_content(handle_id)
        if not handle or content is None:
            return {"error": f"Handle '{handle_id}' not found."}

        lines = content.splitlines()
        preview = lines[:max_lines]
        tail = lines[-5:] if len(lines) > max_lines else []

        return {
            "handle": handle.to_dict(),
            "preview_lines": preview,
            "tail_lines": tail,
            "total_lines": len(lines),
        }

    def fetch_text(self, handle_id: str) -> str | None:
        return self.store.get_content(handle_id)

    def stats(self) -> dict[str, Any]:
        return self.store.stats()
