from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextHandle:
    """
    Context-as-Data handle (R_id) representing an addressable context object.
    Allows massive files, repositories, or test outputs to be passed as immutable references.
    """
    handle_id: str = field(default_factory=lambda: f"ctx_{uuid.uuid4().hex[:8]}")
    label: str = ""
    content_hash: str = ""
    byte_size: int = 0
    estimated_tokens: int = 0
    line_count: int = 0
    parent_handle_id: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle_id": self.handle_id,
            "label": self.label,
            "content_hash": self.content_hash,
            "byte_size": self.byte_size,
            "estimated_tokens": self.estimated_tokens,
            "line_count": self.line_count,
            "parent_handle_id": self.parent_handle_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class ContextStore:
    """
    In-memory and cached store for Context-as-Data objects.
    Enforces immutability: once stored, content cannot be mutated in place;
    transformations produce new child ContextHandles.
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._handles: dict[str, ContextHandle] = {}

    def register(
        self,
        content: str,
        label: str = "",
        metadata: dict[str, Any] | None = None,
        parent_handle_id: str | None = None,
    ) -> ContextHandle:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        byte_size = len(content.encode("utf-8"))
        lines = content.splitlines()
        # Roughly 1 token per 4 chars as standard heuristic
        est_tokens = max(1, byte_size // 4)

        handle = ContextHandle(
            label=label or f"context_{content_hash[:8]}",
            content_hash=content_hash,
            byte_size=byte_size,
            estimated_tokens=est_tokens,
            line_count=len(lines),
            parent_handle_id=parent_handle_id,
            metadata=metadata or {},
        )

        self._data[handle.handle_id] = content
        self._handles[handle.handle_id] = handle
        return handle

    def get_handle(self, handle_id: str) -> ContextHandle | None:
        return self._handles.get(handle_id)

    def get_content(self, handle_id: str) -> str | None:
        return self._data.get(handle_id)

    def delete(self, handle_id: str) -> bool:
        if handle_id in self._handles:
            del self._handles[handle_id]
            del self._data[handle_id]
            return True
        return False

    def list_handles(self) -> list[ContextHandle]:
        return sorted(self._handles.values(), key=lambda h: h.created_at)

    def stats(self) -> dict[str, Any]:
        return {
            "total_handles": len(self._handles),
            "total_bytes": sum(h.byte_size for h in self._handles.values()),
            "total_estimated_tokens": sum(h.estimated_tokens for h in self._handles.values()),
        }
