"""GRO configuration (`config.yaml -> gro`), default-off and bounded.

The defaults preserve baseline behaviour: with ``enabled: false`` nothing in
Alpha's existing single-attempt or swarm paths changes, and the ``swarm``
tool's ``compete`` action refuses with the reason named. Ceilings here are the
defaults a run may only lower, never raise silently; bounds are refused at
load, never clamped.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GroConfig(BaseModel):
    """Group-relative orchestration settings (plan section 18.1)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Enable GRO candidate groups; off = the `swarm` compete action refuses with the reason named.")
    default_mode: Literal["single", "group", "adaptive"] = Field(default="adaptive", description="Policy posture: single = always one attempt, group = always a candidate group, adaptive = tier-driven.")
    max_candidates_per_task: int = Field(default=3, ge=1, le=8, description="Hard ceiling on candidates per task; refused, never clamped.")
    max_active_candidates_global: int = Field(default=2, ge=1, le=8, description="Concurrent candidate lanes process-wide for one run.")
    max_repair_rounds: int = Field(default=1, ge=0, le=3, description="Bounded failure-directed repair rounds after an all-fail group.")
    early_stop_on_verified_success: bool = Field(default=True, description="Stop dispatching later candidate waves once an eligible candidate exists.")
    candidate_timeout_seconds: float = Field(default=240.0, gt=0.0, description="Per-candidate wall-clock bound; a late result is fenced, never adopted.")
    run_timeout_seconds: float = Field(default=900.0, gt=0.0, description="Whole-run wall-clock bound.")
    require_final_verification: bool = Field(default=True, description="Re-check the selected candidate's gates before reporting a winner.")
    relative_rank_is_tiebreaker_only: bool = Field(default=True, description="Relative rank orders eligible candidates only; it can never promote an ineligible one.")
    require_approval_for_external_side_effects: bool = Field(default=True, description="External-effect tasks resolve to human approval first instead of autonomous candidates.")

    def budget(self) -> dict[str, float | int]:
        """Project the run budget fields from this configuration."""

        return {
            "max_candidates": self.max_candidates_per_task,
            "max_active": self.max_active_candidates_global,
            "candidate_timeout_seconds": self.candidate_timeout_seconds,
            "run_timeout_seconds": self.run_timeout_seconds,
            "max_repair_rounds": self.max_repair_rounds,
        }
