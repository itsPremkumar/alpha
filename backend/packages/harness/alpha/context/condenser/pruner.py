"""DeterministicPruner: Removes obsolete verbose observation data from past turns."""

from __future__ import annotations

import copy
from typing import Any


class DeterministicPruner:
    """Stage 1: Prunes stale intermediate tool outputs (e.g., file reads, directory listings)."""

    VERBOSE_TOOLS: set[str] = {
        "view_file", "read_file", "cat", "list_dir", "ls",
        "find_by_name", "grep_search", "search_code",
    }

    def __init__(self, keep_last_n_observations: int = 4, max_pruned_length: int = 150):
        self.keep_last_n_observations = keep_last_n_observations
        self.max_pruned_length = max_pruned_length

    def prune(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a pruned copy of messages where older verbose observations are compacted."""
        if not messages:
            return []

        pruned = copy.deepcopy(messages)

        # Identify all tool observation messages (from end to start)
        obs_indices: list[int] = []
        for idx, msg in enumerate(pruned):
            role = msg.get("role")
            msg_type = msg.get("type")
            if role in {"tool", "observation"} or msg_type in {"observation", "tool_result"}:
                obs_indices.append(idx)

        # Indices that should be preserved in full (the last N observations)
        protected_indices = set(obs_indices[-self.keep_last_n_observations :])

        for idx in obs_indices:
            if idx in protected_indices:
                continue

            msg = pruned[idx]
            tool_name = msg.get("name") or msg.get("tool_name", "")
            content = msg.get("content", "")

            # If it's a known verbose tool or very long content (> 500 chars)
            if (tool_name in self.VERBOSE_TOOLS or len(str(content)) > 500) and not msg.get("pruned"):
                str_content = str(content)
                line_count = len(str_content.splitlines())
                first_line = str_content.splitlines()[0][:80] if str_content else ""

                tombstone = (
                    f"[Pruned prior output from '{tool_name or 'tool'}' ({line_count} lines, "
                    f"{len(str_content)} chars). Snippet: {first_line}...]"
                )

                msg["content"] = tombstone
                msg["pruned"] = True
                msg["original_length"] = len(str_content)

        return pruned
