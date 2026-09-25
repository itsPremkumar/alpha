"""In-package configuration for social/shared memory.

The subsystem owns this schema so it remains independently testable and does
not read Alpha's shared ``MemoryConfig``. A host may later nest this model at
``memory.social``. ``storage_path=None`` deliberately resolves through the L1
runtime-home helper, giving the same per-user runtime layout without sharing
state or configuration singletons.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha.agents.memory.l1.paths import l1_root


class SocialConfig(BaseModel):
    """Configuration for the opt-in social/shared memory subsystem."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Master switch for social memory capture and recall.")
    max_counterparts: int = Field(default=500, description="Maximum retained counterparts per owner scope.")
    max_shared_facts: int = Field(default=1_000, description="Maximum retained shared facts per owner scope.")
    relationship_decay_half_life_days: float = Field(
        default=30.0,
        description="Default relationship trust half-life in days.",
    )
    default_sensitivity: Literal["public", "team", "private"] = Field(
        default="private",
        description="Sensitivity assigned when a fact does not specify one.",
    )
    require_grant_for_team_scope: bool = Field(
        default=True,
        description="Safety invariant: team-audience facts still require an explicit fact or scope grant.",
    )
    summary_model: str | None = Field(
        default=None,
        description="Model identifier recorded for injected interaction-summary calls; this class never constructs a model.",
    )
    enable_model_summaries: bool = Field(
        default=False,
        description="Allow an injected model to produce interaction summaries; failures use the deterministic fallback.",
    )
    storage_path: str | None = Field(
        default=None,
        description="Social state root. None uses the runtime home via the L1 path resolver.",
    )
    clamping_disclosures: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _clamp_and_disclose(self) -> SocialConfig:
        for name, minimum, maximum in (
            ("max_counterparts", 1, 1_000_000),
            ("max_shared_facts", 1, 1_000_000),
        ):
            original = getattr(self, name)
            clamped = min(maximum, max(minimum, int(original)))
            setattr(self, name, clamped)
            if clamped != original:
                self.clamping_disclosures.append(f"{name} clamped from {original!r} to {clamped!r} (allowed {minimum}..{maximum})")

        original_half_life = self.relationship_decay_half_life_days
        half_life = float(original_half_life)
        if math.isnan(half_life):
            half_life = 30.0
        clamped_half_life = min(3650.0, max(0.01, half_life))
        self.relationship_decay_half_life_days = clamped_half_life
        if clamped_half_life != original_half_life:
            self.clamping_disclosures.append(f"relationship_decay_half_life_days clamped from {original_half_life!r} to {clamped_half_life!r} (allowed 0.01..3650.0)")

        if not self.require_grant_for_team_scope:
            self.require_grant_for_team_scope = True
            self.clamping_disclosures.append("require_grant_for_team_scope clamped from False to True; explicit grants cannot be disabled")

        if self.summary_model is not None:
            cleaned_model = self.summary_model.strip()
            self.summary_model = cleaned_model or None
        if self.storage_path is not None:
            cleaned_path = self.storage_path.strip()
            self.storage_path = cleaned_path or None
        return self

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> SocialConfig:
        """Build an isolated config without reading a process-global config."""

        return cls.model_validate(values or {})

    def resolved_root(self) -> Path:
        """Resolve the state root, mirroring ``l1.paths.l1_root`` exactly."""

        return l1_root(self.storage_path)


__all__ = ["SocialConfig"]
