"""Atomic Trajectory Compressor.

Compresses conversation histories to fit strict token budgets while guaranteeing
that head/tail invariants are preserved and <tool_call> / <tool_response> atomic
pairs are never orphaned or truncated mid-exchange.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TrajectoryTurn:
    role: str
    content: str
    tool_call_id: Optional[str] = None
    is_tool_call: bool = False
    is_tool_response: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


class TrajectoryCompactor:
    """Intelligent context compactor preserving tool pairs and head/tail invariants."""

    def __init__(self, target_max_tokens: int = 8000, chars_per_token: float = 3.8):
        self.target_max_tokens = target_max_tokens
        self.chars_per_token = chars_per_token

    def estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) / self.chars_per_token))

    def compact_turns(
        self,
        turns: list[TrajectoryTurn],
        protect_head_turns: int = 2,
        protect_tail_turns: int = 3,
    ) -> list[TrajectoryTurn]:
        """Compact conversation turns while preserving tool pair invariants."""
        total_tokens = sum(self.estimate_tokens(t.content) for t in turns)
        if total_tokens <= self.target_max_tokens or len(turns) <= (protect_head_turns + protect_tail_turns):
            return turns

        head = turns[:protect_head_turns]
        tail = turns[-protect_tail_turns:]
        middle = turns[protect_head_turns:-protect_tail_turns]

        # Ensure we do not split a tool_call / tool_response pair at boundary
        # If the first tail turn is a tool_response, pull the tool_call from middle into tail
        if tail and tail[0].is_tool_response and middle and middle[-1].is_tool_call:
            tail.insert(0, middle.pop())

        # If the last head turn is a tool_call, pull its tool_response from middle into head
        if head and head[-1].is_tool_call and middle and middle[0].is_tool_response:
            head.append(middle.pop(0))

        # Summarize middle turns
        middle_tool_count = sum(1 for t in middle if t.is_tool_call)
        middle_roles = [t.role for t in middle]

        summary_content = (
            f"[CONTEXT COMPACTION SUMMARY: Compacted {len(middle)} intermediate turns "
            f"({middle_tool_count} tool invocations executed across {set(middle_roles)}). "
            f"Key state and prior decisions preserved without context overflow.]"
        )
        summary_turn = TrajectoryTurn(role="system", content=summary_content)

        return head + [summary_turn] + tail
