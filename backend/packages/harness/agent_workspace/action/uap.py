from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionPrimitive(str, Enum):
    """Normalized Universal Action Protocol (UAP) 15 foundational algebra primitives."""
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"
    COPY = "copy"
    SEND = "send"
    EXECUTE = "execute"
    APPROVE = "approve"
    REJECT = "reject"
    SEARCH = "search"
    TRANSFORM = "transform"
    OBSERVE = "observe"
    WAIT = "wait"
    SUBSCRIBE = "subscribe"


@dataclass
class ActionRequest:
    """Normalized action request in accordance with UAP."""
    primitive: ActionPrimitive
    target: str
    action_id: str = field(default_factory=lambda: f"act_{uuid.uuid4().hex[:10]}")
    parameters: dict[str, Any] = field(default_factory=dict)
    expected_outcome: str = ""
    timeout_seconds: float = 30.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "primitive": self.primitive.value,
            "target": self.target,
            "parameters": self.parameters,
            "expected_outcome": self.expected_outcome,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
        }
