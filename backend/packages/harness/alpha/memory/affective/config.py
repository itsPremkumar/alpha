"""In-package configuration for the opt-in affective-memory subsystem."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AffectiveConfig(BaseModel):
    """Affective-memory settings, independent of shared ``MemoryConfig``.

    The subsystem is deliberately off by default. A host may inject this model
    directly today; promotion into ``memory.affective`` is a separate additive
    shared-config change reviewed by the central owner.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master switch for affective capture, mood computation, and recall.",
    )
    half_life_hours: float = Field(
        default=72.0,
        gt=0.0,
        le=8760.0,
        description="Exponential half-life used to decay old affect events.",
    )
    max_events_per_user: int = Field(
        default=500,
        ge=1,
        le=10000,
        description="Maximum retained affect events per user.",
    )
    max_surfaced: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum remembered moments in one prompt block.",
    )
    min_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for ingest and recall.",
    )
    extraction_model: str | None = Field(
        default=None,
        description="Optional named model for sentiment extraction; None means no model.",
    )
    enable_model_extraction: bool = Field(
        default=False,
        description="Allow model-based extraction. Explicit caller labels remain available when false.",
    )
    storage_path: str | None = Field(
        default=None,
        description="Affective state root. None resolves to the Alpha runtime home.",
    )


__all__ = ["AffectiveConfig"]
