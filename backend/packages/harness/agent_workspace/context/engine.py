"""Token-budgeted Context Engine with prefix-preserving compaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_workspace.context.projection import ContextProjection
from agent_workspace.context.watchdog import CompactionWatchdog


@dataclass
class AssembleResult:
    """Assembled prompt payload ready for model inference."""

    messages: list[dict[str, Any]]
    estimated_tokens: int
    projection: ContextProjection
    is_compacted: bool = False


class ContextEngine:
    """Manages token budget, prefix preservation, and dynamic context compaction."""

    def __init__(
        self,
        max_context_tokens: int = 16000,
        compaction_threshold: float = 0.8,
        keep_recent_count: int = 4,
    ):
        self.max_context_tokens = max_context_tokens
        self.compaction_threshold = compaction_threshold
        self.keep_recent_count = keep_recent_count
        self.watchdog = CompactionWatchdog()

    def assemble(
        self,
        system_prompt: str,
        goal_text: str = "",
        scratchpad: str = "",
        history_messages: list[dict[str, Any]] | None = None,
        goal_id: str = "",
    ) -> AssembleResult:
        """Assemble system instructions, active goal, scratchpad, and history within token limits."""
        messages: list[dict[str, Any]] = []

        # 1. Pinned System Prompt
        messages.append({
            "role": "system",
            "content": system_prompt,
            "pinned": True,
        })

        # 2. Pinned Goal & Scratchpad (if present)
        if goal_text:
            goal_content = f"### ACTIVE AUTONOMOUS GOAL ###\n{goal_text}"
            if scratchpad:
                goal_content += f"\n\n### CURRENT SCRATCHPAD ###\n{scratchpad}"
            messages.append({
                "role": "system",
                "content": goal_content,
                "pinned": True,
            })

        # 3. Append history
        if history_messages:
            messages.extend(history_messages)

        # 4. Token estimation and compaction check
        est_tokens = self.watchdog.estimate_messages_tokens(messages)
        threshold_tokens = int(self.max_context_tokens * self.compaction_threshold)

        is_compacted = False
        if est_tokens > threshold_tokens:
            messages = self.watchdog.compact(messages, keep_recent_count=self.keep_recent_count)
            est_tokens = self.watchdog.estimate_messages_tokens(messages)
            is_compacted = True

        projection = ContextProjection.compute(system_prompt=system_prompt, goal_id=goal_id)

        return AssembleResult(
            messages=messages,
            estimated_tokens=est_tokens,
            projection=projection,
            is_compacted=is_compacted,
        )
