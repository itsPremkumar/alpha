"""HeadTailBudgetTruncator: Maintains vital system head and recent turn tail within token budgets."""

from __future__ import annotations

import copy
from typing import Any


class HeadTailBudgetTruncator:
    """Stage 2: Keeps head (instructions/goal) and tail (recent turns) within a character/token budget."""

    def __init__(
        self,
        max_budget_chars: int = 120_000,
        keep_head_turns: int = 2,
        keep_tail_turns: int = 6,
    ):
        self.max_budget_chars = max_budget_chars
        self.keep_head_turns = keep_head_turns
        self.keep_tail_turns = keep_tail_turns

    def estimate_size(self, messages: list[dict[str, Any]]) -> int:
        """Estimate length of message sequence in characters."""
        total = 0
        for m in messages:
            total += len(str(m.get("content", "")))
            if "tool_calls" in m:
                total += len(str(m["tool_calls"]))
        return total

    def truncate(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Truncate middle turns if total size exceeds budget."""
        if not messages:
            return []

        current_size = self.estimate_size(messages)
        if current_size <= self.max_budget_chars:
            return copy.deepcopy(messages)

        total_turns = len(messages)
        min_required = self.keep_head_turns + self.keep_tail_turns
        if total_turns <= min_required:
            return copy.deepcopy(messages)

        head = copy.deepcopy(messages[: self.keep_head_turns])
        tail = copy.deepcopy(messages[-self.keep_tail_turns :])
        middle_count = total_turns - (self.keep_head_turns + self.keep_tail_turns)

        notice_content = (
            f"[Context Condenser Notice: {middle_count} intermediate historical turns "
            f"were truncated to conserve context window. Head ({len(head)} turns) and "
            f"Tail ({len(tail)} turns) preserved.]"
        )
        notice_message = {
            "role": "system",
            "content": notice_content,
            "truncated": True,
            "dropped_turn_count": middle_count,
        }

        return head + [notice_message] + tail
