"""Configuration for the continual-intelligence layer (``config.yaml`` -> ``intelligence:``).

Why a section and not Python constants
--------------------------------------
The repository has one config file and a standing rule that a hand-maintained
threshold table in Python is a second source of truth that drifts silently. Every
number the intelligence layer acts on — reservoir capacity, plasticity
multipliers, expert grace periods, paging budgets, regression tolerances — is
therefore declared here with an honest default and an ``OBSERVE_ONLY`` starting
posture.

Three properties are deliberate and load-bearing:

1. **Default-off.** ``enabled`` defaults to ``False`` and ``mode`` defaults to
   :data:`LearningMode.OBSERVE_ONLY`. A fresh install therefore *observes* and
   writes no learned state; nothing new mutates a promotion path unless an
   operator opts in. This mirrors how ``evolution_evidence`` and ``self_tuning``
   already ship.
2. **No magic constants in code.** Every field below is read through
   :func:`intelligence_config`; a module that hardcodes a threshold instead of
   reading it here is a bug, and the package docstring says so.
3. **Thresholds that must fail closed.** ``min_regression_*, min_heldout_*``
   and the plasticity windows are all *deliberate* so a candidate must clear a
   real bar; nothing here can be satisfied by a missing measurement because
   :mod:`alpha.intelligence.plasticity` and
   :mod:`alpha.intelligence.regression` both refuse to treat "unmeasured" as a
   pass.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alpha.intelligence.models import LearningMode

__all__ = [
    "IntelligenceConfig",
    "ExpertConfig",
    "PagingConfig",
    "PlasticityConfig",
    "ReplayConfig",
    "RegressionConfig",
    "intelligence_config",
]


class ReplayConfig(BaseModel):
    """Bounded replay reservoir (prompt §7/§29)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Master switch for reservoir sampling. Off means experience is stored but never sampled for learning.")
    capacity: int = Field(default=512, ge=1, description="Hard ceiling on retained replay items. The reservoir is bounded; it never grows with the experience bank.")
    strata: dict[str, float] = Field(
        default_factory=dict,
        description="Sampling weight per stratum. Unknown strata fall back to REPLAY_STRATUM_WEIGHTS rather than being dropped.",
    )
    recency_half_life_seconds: float = Field(default=604800.0, gt=0, description="Half-life for the recency component of an item's priority.")
    min_priority: float = Field(default=0.0, ge=0.0, description="Items scoring below this are evicted first when the reservoir is full.")


class PlasticityConfig(BaseModel):
    """How aggressively Alpha is currently allowed to learn (prompt §12/§13)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Master switch. Off freezes the controller at MAINTAIN and never changes a tier multiplier.")
    #: Per-surface multipliers. The prompt's learning tiers, as data.
    core_multiplier: float = Field(default=0.05, ge=0.0, le=1.0, description="Stable-core surfaces. Very low by design: catastrophic forgetting is the default risk.")
    router_multiplier: float = Field(default=0.25, ge=0.0, le=1.0, description="Routing surfaces. Low to moderate.")
    expert_multiplier: float = Field(default=0.6, ge=0.0, le=1.0, description="Learned experts. Moderate.")
    new_expert_multiplier: float = Field(default=1.0, ge=0.0, le=1.0, description="Trial/new experts. High, because there is no history to forget.")
    window: int = Field(default=5, ge=2, description="Number of recent observations a trend is computed over. One measurement is never a trend.")
    rollback_regression_delta: float = Field(default=0.05, gt=0, description="Regression worse than this over the window forces ROLLBACK, not just DECREASE.")
    forgetting_ratio: float = Field(default=0.15, gt=0, le=1.0, description="Held-out retention below this fraction of its peak is treated as forgetting and forces aggressive decrease + replay.")


class RegressionConfig(BaseModel):
    """The standing intelligence regression suite (prompt §8) and its gate."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Master switch for running the standing suite.")
    min_regression_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Absolute floor the candidate must clear.")
    max_regression_delta: float = Field(default=0.0, ge=-1.0, le=1.0, description="Largest tolerated fall against the incumbent. 0.0 means any regression rejects.")
    min_heldout_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Floor on the hidden holdout suite.")
    overfitting_gap: float = Field(default=0.2, gt=0, description="Train-minus-heldout gap above which a candidate is flagged as possibly overfitting.")
    max_retries: int = Field(default=2, ge=0, description="Bounded promotion attempts. 0 disables promotion entirely.")


class ExpertConfig(BaseModel):
    """Dynamic expert growth, trial, and pruning (prompt §14-§22)."""

    model_config = ConfigDict(extra="forbid")

    dynamic_growth: bool = Field(default=False, description="Allow the fabric to propose a new expert from a detected capability gap. Proposals are TRIAL, never ACTIVE.")
    dynamic_pruning: bool = Field(default=False, description="Allow prune candidates to be marked. Archiving still happens before any removal and protection is absolute.")
    max_experts: int = Field(default=64, ge=1, description="Hard ceiling on registered experts.")
    trial_window: int = Field(default=5, ge=1, description="Observations an expert must survive before it can be promoted out of TRIAL.")
    trial_min_success_rate: float = Field(default=0.5, ge=0.0, le=1.0, description="Success rate a TRIAL expert must reach over its trial window.")
    grace_period_seconds: float = Field(default=604800.0, ge=0.0, description="Minimum age before an underused expert may become prune-eligible.")
    exploration_bonus: float = Field(default=0.1, ge=0.0, description="Maximum exploration credit. Applied only when exploring=True; never above the reliability term during production routing.")
    protected: list[str] = Field(default_factory=list, description="Expert ids that can never be pruned. A protected expert is reported as protected, not skipped.")
    recent_window: int = Field(default=5, ge=1, description="Observations the recent-usefulness component reads.")


class PagingConfig(BaseModel):
    """Disk -> RAM cache -> resident tier (prompt §23)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Master switch. Off keeps every expert resident in the RAM cache and pages nothing.")
    ram_cache_size: int = Field(default=32, ge=1, description="Maximum experts held in the RAM cache.")
    max_resident: int = Field(default=4, ge=1, description="Maximum experts held in the active/resident tier. Must be <= ram_cache_size.")
    policy: str = Field(default="lru", description="Eviction policy name. Unknown names fall back to the built-in LRU and say so.")

    @model_validator(mode="after")
    def _resident_within_cache(self) -> PagingConfig:
        if self.max_resident > self.ram_cache_size:
            raise ValueError(f"intelligence.paging.max_resident ({self.max_resident}) cannot exceed ram_cache_size ({self.ram_cache_size}); the resident tier is a subset of the RAM cache, not a peer of it.")
        return self


class IntelligenceConfig(BaseModel):
    """The ``intelligence:`` section: one switch, one mode, five sub-blocks."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master switch for the continual-intelligence layer. Off means every read endpoint still answers (from real state) and nothing is written, sampled, or promoted.",
    )
    mode: LearningMode = Field(
        default=LearningMode.OBSERVE_ONLY,
        description="Learning posture. OBSERVE_ONLY records what *would* happen and mutates nothing. Only an operator setting PROMOTE can advance learned state.",
    )
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    plasticity: PlasticityConfig = Field(default_factory=PlasticityConfig)
    regression: RegressionConfig = Field(default_factory=RegressionConfig)
    experts: ExpertConfig = Field(default_factory=ExpertConfig)
    paging: PagingConfig = Field(default_factory=PagingConfig)
    difficulty_thresholds: dict[str, float] = Field(
        default_factory=dict,
        description="Difficulty band boundaries as normalised 0..1 scores. Unknown keys fall back to DIFFICULTY_BANDS.",
    )

    @model_validator(mode="after")
    def _mode_requires_master_switch(self) -> IntelligenceConfig:
        """A mode above OBSERVE_ONLY without the master switch is a config error.

        Without this, ``enabled: false, mode: PROMOTE`` would read as "promote
        everything" while every write path correctly refuses — a configuration
        whose two halves disagree, which is the exact class of bug this
        repository's honesty rules exist to prevent.
        """
        if self.mode is not LearningMode.OBSERVE_ONLY and not self.enabled:
            raise ValueError(f"intelligence.mode is {self.mode.value!r} but intelligence.enabled is false. The mode and the master switch must agree: set intelligence.enabled: true, or set mode: OBSERVE_ONLY.")
        return self

    def allows(self, required: LearningMode) -> bool:
        """True when this configuration permits ``required`` to be performed.

        Ordered by severity, so a weaker mode never satisfies a stronger
        requirement. Disabled is checked first: a disabled section is inert
        regardless of the mode it nominally carries.
        """
        if not self.enabled:
            return False
        return _MODE_RANK[self.mode] >= _MODE_RANK[required]


#: Severity order. Higher is more capable.
_MODE_RANK: dict[LearningMode, int] = {
    LearningMode.OBSERVE_ONLY: 0,
    LearningMode.DRY_RUN: 1,
    LearningMode.EVALUATE_ONLY: 2,
    LearningMode.TRIAL: 3,
    LearningMode.LEARN: 4,
    LearningMode.PROMOTE: 5,
}


def intelligence_config() -> IntelligenceConfig:
    """Read the hot-reloaded ``intelligence:`` section.

    The import is deferred inside the function so this module stays importable
    from ``AppConfig`` at module scope without cycling through the harness.
    """
    from alpha.config.app_config import get_app_config

    return get_app_config().intelligence
