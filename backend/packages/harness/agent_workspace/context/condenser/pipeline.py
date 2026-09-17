"""PipelineCondenser: Executes the 3-stage context condensation pipeline."""

from __future__ import annotations

import logging
from typing import Any

from agent_workspace.context.condenser.pruner import DeterministicPruner
from agent_workspace.context.condenser.summarizer import StructuredStateCondenser, WorkingState
from agent_workspace.context.condenser.truncator import HeadTailBudgetTruncator

logger = logging.getLogger(__name__)


class PipelineCondenser:
    """Multi-stage pipeline: (1) Prune verbose observations -> (2) Truncate within budget -> (3) Inject State Summary."""

    def __init__(
        self,
        max_budget_chars: int = 100_000,
        keep_last_observations: int = 4,
        keep_head_turns: int = 2,
        keep_tail_turns: int = 6,
    ):
        self.pruner = DeterministicPruner(keep_last_n_observations=keep_last_observations)
        self.truncator = HeadTailBudgetTruncator(
            max_budget_chars=max_budget_chars,
            keep_head_turns=keep_head_turns,
            keep_tail_turns=keep_tail_turns,
        )
        self.summarizer = StructuredStateCondenser()

    def condense(
        self,
        messages: list[dict[str, Any]],
        existing_state: WorkingState | None = None,
    ) -> tuple[list[dict[str, Any]], WorkingState]:
        """Condense message history and return (condensed_messages, updated_state)."""
        if not messages:
            return [], WorkingState()

        # 1. Update structured state summary from raw messages
        state = self.summarizer.condense(messages, existing_state=existing_state)

        # 2. Stage 1: Deterministic Pruning
        pruned = self.pruner.prune(messages)

        # 3. Stage 2: Head/Tail Budget Truncation
        truncated = self.truncator.truncate(pruned)

        # 4. Stage 3: Inject State Summary if truncation occurred or messages > 10
        if len(messages) > 8 or any(m.get("truncated") for m in truncated):
            summary_md = state.to_markdown()
            summary_msg = {
                "role": "system",
                "content": summary_md,
                "is_state_summary": True,
            }
            # Insert after the head messages
            head_idx = min(2, len(truncated))
            truncated.insert(head_idx, summary_msg)

        return truncated, state
