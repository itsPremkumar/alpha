from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

ActionOutcome = Literal["succeeded", "failed", "denied"]
ActionStatus = Literal["pending", "succeeded", "failed", "denied"]
ErrorCategory = Literal["timeout", "permission_denied", "validation", "not_found", "conflict", "cancelled", "unavailable", "tool_error", "internal", "unknown"]

OUTCOMES = ("succeeded", "failed", "denied")
ERROR_CATEGORIES = ("timeout", "permission_denied", "validation", "not_found", "conflict", "cancelled", "unavailable", "tool_error", "internal", "unknown")


def validate_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"Invalid {field}")


def validate_timestamp(value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid timestamp")


@dataclass(frozen=True)
class ActionIntent:
    id: str
    owner_id: str
    tool_name: str
    arguments_digest: str
    created_at: float
    thread_id: str | None = None
    status: ActionStatus = "pending"

    def __post_init__(self) -> None:
        for field in ("id", "owner_id", "tool_name"):
            validate_text(getattr(self, field), field)
        if self.thread_id is not None:
            validate_text(self.thread_id, "thread_id")
        if not isinstance(self.arguments_digest, str) or re.fullmatch(r"[0-9a-f]{64}", self.arguments_digest) is None:
            raise ValueError("Invalid arguments_digest")
        validate_timestamp(self.created_at)
        if self.status not in ("pending", *OUTCOMES):
            raise ValueError("Invalid status")


@dataclass(frozen=True)
class ActionReceipt:
    intent_id: str
    outcome: ActionOutcome
    started_at: float
    completed_at: float
    error_category: ErrorCategory | None = None
    exit_ref: str | None = None

    def __post_init__(self) -> None:
        validate_text(self.intent_id, "intent_id")
        if self.outcome not in OUTCOMES:
            raise ValueError("Invalid outcome")
        if self.error_category is not None and self.error_category not in ERROR_CATEGORIES:
            raise ValueError("Invalid error_category")
        if self.outcome == "succeeded" and self.error_category is not None:
            raise ValueError("Successful receipts cannot have an error_category")
        validate_timestamp(self.started_at)
        validate_timestamp(self.completed_at)
        if self.completed_at < self.started_at:
            raise ValueError("Completion precedes start")
        if self.exit_ref is not None:
            validate_text(self.exit_ref, "exit_ref")
