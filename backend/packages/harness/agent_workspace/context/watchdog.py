"""Prefix-Preserving Compaction Watchdog for Long-Running Agent Sessions."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CompactionWatchdog:
    """Preemptively summarizes middle turns while preserving prefix and recent turns."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Heuristic token estimation (~4 chars per token)."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    @classmethod
    def estimate_messages_tokens(cls, messages: list[dict[str, Any]]) -> int:
        total = 0
        for m in messages:
            content = str(m.get("content", ""))
            total += cls.estimate_tokens(content) + 4  # message envelope overhead
        return total

    @classmethod
    def compact(
        cls,
        messages: list[dict[str, Any]],
        keep_recent_count: int = 4,
    ) -> list[dict[str, Any]]:
        """Compact intermediate history into a structured summary without breaking prefix."""
        if len(messages) <= keep_recent_count + 1:
            return messages

        # Separate prefix (system/goal message if present) from the body
        prefix: list[dict[str, Any]] = []
        body: list[dict[str, Any]] = []

        for m in messages:
            if m.get("role") in ("system", "goal_definition") or m.get("pinned", False):
                prefix.append(m)
            else:
                body.append(m)

        if len(body) <= keep_recent_count:
            return prefix + body

        # Middle turns to compact
        to_compact = body[:-keep_recent_count]
        recent_turns = body[-keep_recent_count:]

        # Build structured synopsis
        synopsis_lines = ["[HISTORICAL CONTEXT SUMMARY - COMPACTED MIDDLE TURNS]"]
        for m in to_compact:
            role = m.get("role", "unknown").upper()
            content = str(m.get("content", ""))
            # Truncate each historical message snippet to 150 chars
            snippet = (content[:150] + "...") if len(content) > 150 else content
            synopsis_lines.append(f"- {role}: {snippet}")

        summary_message = {
            "role": "system",
            "content": "\n".join(synopsis_lines),
            "compacted": True,
        }

        return prefix + [summary_message] + recent_turns
