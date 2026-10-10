"""``config.yaml -> mission_memory`` — durable mission memory policy.

Hot-reloadable, deliberately. The middleware reads this from the resolved
:class:`~alpha.config.app_config.AppConfig` at each agent assembly and the
mission tool reads it at each call, so a change to the anchor budget or the ring
depths takes effect on the next run. Nothing here owns a background task or a
captured snapshot, so there is no reason to force a Gateway restart.

Every field is bounded and defaulted to "produce a bounded, useful anchor". The
runtime value objects (:mod:`alpha.runtime.missions`) are dependency-free; this
pydantic model only validates and bounds operator input and is translated at the
wiring site. See
``backend/packages/harness/alpha/runtime/missions/AGENTS.md`` for the depth.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["MissionMemoryConfig"]


class MissionMemoryConfig(BaseModel):
    """Durable, per-thread mission memory: the re-read that stops drift."""

    model_config = {"extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description="Inject the durable mission anchor into every lead-agent turn and expose the model-facing memory tool.",
    )
    max_anchor_chars: int = Field(
        default=4000,
        ge=500,
        le=20000,
        description="Character budget for the per-turn anchor. Larger keeps more of the status log and scratchpad; the objective is preserved first and the tail is truncated.",
    )
    max_status_lines: int = Field(
        default=40,
        ge=1,
        le=500,
        description="Ring depth for the append-only status log. Older lines are dropped to keep the durable file bounded.",
    )
    max_scratch_entries: int = Field(
        default=60,
        ge=1,
        le=500,
        description="Ring depth for the durable scratchpad of reasoning notes.",
    )
    status_tail: int = Field(
        default=4,
        ge=1,
        le=20,
        description="How many recent status lines the anchor shows.",
    )
    scratch_tail: int = Field(
        default=2,
        ge=1,
        le=20,
        description="How many recent scratchpad notes the anchor shows.",
    )
