"""Working Memory Scratchpad Engine.

Provides an ephemeral, high-speed attention-focused scratchpad for active agent reasoning,
dynamic hypotheses, goal tracking, and in-flight step observations.
"""

from __future__ import annotations

import math
import time
from typing import Any

from agent_workspace.memory.cognitive.models import WorkingMemoryItem


class WorkingMemoryEngine:
    """Ephemeral attention-decaying scratchpad for active cognitive context."""

    def __init__(
        self,
        max_items: int = 50,
        decay_half_life_seconds: float = 300.0,
        prune_threshold: float = 0.05,
    ) -> None:
        self.max_items = max_items
        self.decay_half_life_seconds = decay_half_life_seconds
        self.prune_threshold = prune_threshold
        self._items: dict[str, WorkingMemoryItem] = {}

    def add(
        self,
        content: str,
        context_tag: str = "scratch",
        attention_score: float = 1.0,
        salience: float = 0.5,
        task_id: str = "default",
        metadata: dict[str, Any] | None = None,
        item_id: str | None = None,
        created_at: float | None = None,
        last_decayed_at: float | None = None,
    ) -> WorkingMemoryItem:
        """Add or refresh a working memory item."""
        now = time.time()
        item = WorkingMemoryItem(
            content=content.strip(),
            context_tag=context_tag,
            attention_score=max(0.0, min(1.0, attention_score)),
            salience=max(0.0, min(1.0, salience)),
            task_id=task_id,
            created_at=created_at or now,
            last_decayed_at=last_decayed_at or created_at or now,
            metadata=metadata or {},
        )
        if item_id:
            item.item_id = item_id
        self._items[item.item_id] = item
        self._enforce_capacity()
        return item

    def get(self, item_id: str) -> WorkingMemoryItem | None:
        """Retrieve item and slightly refresh its attention."""
        item = self._items.get(item_id)
        if item:
            item.attention_score = min(1.0, item.attention_score + 0.1)
            item.last_decayed_at = time.time()
        return item

    def update_attention(self, item_id: str, new_score: float) -> bool:
        """Explicitly set an item's attention score."""
        if item_id in self._items:
            self._items[item_id].attention_score = max(0.0, min(1.0, new_score))
            self._items[item_id].last_decayed_at = time.time()
            return True
        return False

    def decay_all(self, current_time: float | None = None) -> int:
        """Apply exponential temporal decay to all attention scores and prune faded ones."""
        now = current_time or time.time()
        pruned_count = 0
        to_delete = []

        decay_constant = math.log(2) / max(1.0, self.decay_half_life_seconds)

        for item_id, item in self._items.items():
            dt = max(0.0, now - item.last_decayed_at)
            item.attention_score *= math.exp(-decay_constant * dt)
            item.last_decayed_at = now
            if item.attention_score < self.prune_threshold:
                to_delete.append(item_id)

        for item_id in to_delete:
            del self._items[item_id]
            pruned_count += 1

        return pruned_count

    def list_active(self, task_id: str | None = None, min_attention: float = 0.1) -> list[WorkingMemoryItem]:
        """List active items sorted by attention score descending."""
        items = list(self._items.values())
        if task_id:
            items = [it for it in items if it.task_id == task_id]
        filtered = [it for it in items if it.attention_score >= min_attention]
        return sorted(filtered, key=lambda x: (x.attention_score, x.salience), reverse=True)

    def get_salient_for_promotion(self, min_salience: float = 0.65) -> list[WorkingMemoryItem]:
        """Identify high-salience items deserving promotion to episodic or semantic memory."""
        return [it for it in self._items.values() if it.salience >= min_salience]

    def clear(self, task_id: str | None = None) -> int:
        """Clear scratchpad items."""
        if task_id is None:
            count = len(self._items)
            self._items.clear()
            return count
        to_remove = [k for k, v in self._items.items() if v.task_id == task_id]
        for k in to_remove:
            del self._items[k]
        return len(to_remove)

    def _enforce_capacity(self) -> None:
        if len(self._items) <= self.max_items:
            return
        sorted_keys = sorted(
            self._items.keys(),
            key=lambda k: (self._items[k].attention_score, self._items[k].salience),
        )
        excess = len(self._items) - self.max_items
        for k in sorted_keys[:excess]:
            del self._items[k]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_items": len(self._items),
            "items": [it.to_dict() for it in self.list_active(min_attention=0.0)],
        }
