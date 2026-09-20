"""Inbound Message Debouncer and Turn Batcher."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueuedMessage:
    sender: str
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchedTurn:
    session_id: str
    senders: list[str]
    merged_content: str
    message_count: int
    created_at: float = field(default_factory=time.time)


class InboundDebouncer:
    """Debounces rapid bursts of inbound channel/user messages into unified turns."""

    def __init__(self, debounce_seconds: float = 0.5):
        self.debounce_seconds = debounce_seconds
        self._buffers: dict[str, list[QueuedMessage]] = {}
        self._lock = threading.Lock()

    def push(
        self,
        session_id: str,
        sender: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Add a message to session buffer. Returns current queue depth for session."""
        with self._lock:
            if session_id not in self._buffers:
                self._buffers[session_id] = []
            self._buffers[session_id].append(
                QueuedMessage(sender=sender, content=content, metadata=metadata or {})
            )
            return len(self._buffers[session_id])

    def flush(self, session_id: str) -> BatchedTurn | None:
        """Immediately flush and batch all queued messages for a session."""
        with self._lock:
            queue = self._buffers.pop(session_id, [])
            if not queue:
                return None

            senders = list(dict.fromkeys(m.sender for m in queue))
            merged = "\n".join(m.content for m in queue)
            return BatchedTurn(
                session_id=session_id,
                senders=senders,
                merged_content=merged,
                message_count=len(queue),
            )

    async def wait_and_flush(self, session_id: str, max_wait_seconds: float | None = None) -> BatchedTurn | None:
        """Wait for debouncing window to settle then flush batched turn."""
        wait_time = max_wait_seconds or self.debounce_seconds
        await asyncio.sleep(wait_time)
        return self.flush(session_id)
