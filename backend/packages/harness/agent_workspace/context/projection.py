"""Context Projection and Epoch lifecycle for persistent LLM sessions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

ProjectionMode = Literal["per_turn", "thread_bootstrap"]


@dataclass
class ContextProjection:
    """Controls how assembled context is projected into LLM session backends."""

    mode: ProjectionMode = "per_turn"
    epoch: str = "default_epoch"
    fingerprint: str = ""

    @classmethod
    def compute(
        cls,
        system_prompt: str,
        goal_id: str = "",
        mode: ProjectionMode = "thread_bootstrap",
    ) -> ContextProjection:
        """Derive a stable capability epoch and fingerprint for context reuse."""
        content = f"{system_prompt}|{goal_id}|{mode}"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        epoch = digest[:12]
        fingerprint = digest[12:24]
        return cls(mode=mode, epoch=epoch, fingerprint=fingerprint)
