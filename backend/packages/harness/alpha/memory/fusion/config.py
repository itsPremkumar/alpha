"""Configuration and deterministic per-memory-type budget profiles for fusion."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FusionStrategy = Literal["weighted", "rrf"]
BudgetProfile = Literal["coding", "debugging", "planning"]

#: Section 11's source-of-truth positive-signal weights. Missing candidate
#: components contribute zero and are listed in ``FusedCandidate.missing_components``.
DEFAULT_FUSION_WEIGHTS: dict[str, float] = {
    "semantic": 0.30,
    "lexical": 0.20,
    "graph": 0.15,
    "temporal": 0.10,
    "scope_match": 0.10,
    "importance": 0.05,
    "confidence": 0.05,
    "task_relevance": 0.03,
    "access_history": 0.02,
}

#: Stable type order is also the deterministic largest-remainder tie-break.
BUDGET_TYPE_ORDER: tuple[str, ...] = (
    "identity_soul",
    "project_facts",
    "recent_task_state",
    "codebase_facts",
    "procedures_skills",
    "failures_lessons",
    "evidence_provenance",
)

#: Percentages sum to 100 for every profile. The coding profile is the exact
#: section-12 coding-task example. Debugging shifts weight to codebase/failure
#: evidence. Planning shifts weight to recent goals/decisions and provenance;
#: procedure/trajectory memory remains separately budgeted.
BUDGET_PROFILES: dict[str, dict[str, int]] = {
    "coding": {
        "identity_soul": 10,
        "project_facts": 15,
        "recent_task_state": 20,
        "codebase_facts": 20,
        "procedures_skills": 15,
        "failures_lessons": 10,
        "evidence_provenance": 10,
    },
    "debugging": {
        "identity_soul": 5,
        "project_facts": 10,
        "recent_task_state": 10,
        "codebase_facts": 30,
        "procedures_skills": 10,
        "failures_lessons": 30,
        "evidence_provenance": 5,
    },
    "planning": {
        "identity_soul": 10,
        "project_facts": 20,
        "recent_task_state": 25,
        "codebase_facts": 5,
        "procedures_skills": 10,
        "failures_lessons": 10,
        "evidence_provenance": 20,
    },
}


class FusionConfig(BaseModel):
    """Standalone, default-off retrieval-fusion settings.

    Custom ``weights`` may disable a signal by setting it to zero or omitting
    its name; disabled/missing names are materialized as zero. The resulting
    vector must sum to 1.0 so a configured score remains on the section-11
    scale. This package never mutates shared ``MemoryConfig``.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Host call-site gate for multi-stage fusion; disabled by default.",
    )
    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_FUSION_WEIGHTS))
    strategy: FusionStrategy = Field(
        default="weighted",
        description="Weighted section-11 fusion or optional reciprocal-rank fusion.",
    )
    mmr_lambda: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="MMR relevance weight; 0.70 balances relevance and novelty.",
    )
    max_candidates_per_stage: int = Field(default=50, ge=1, le=1000)
    max_results: int = Field(default=24, ge=1, le=1000)
    total_budget_tokens: int = Field(
        default=2000,
        ge=1,
        le=1_000_000,
        description="Hard ceiling before per-memory-type allocation.",
    )
    profile: BudgetProfile = Field(default="coding")
    drop_contradictions: bool = Field(
        default=True,
        description="Keep one authority/newest contradiction winner and disclose losers.",
    )
    latency_budget_ms: float = Field(
        default=750.0,
        ge=0.0,
        description="Latency disclosure threshold; stages not attempted after exhaustion are unavailable.",
    )
    storage_path: str | None = Field(
        default=None,
        description="Fusion state root. None resolves through the existing runtime home.",
    )

    @field_validator("weights")
    @classmethod
    def _known_finite_weights(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - set(DEFAULT_FUSION_WEIGHTS))
        if unknown:
            raise ValueError(f"unknown fusion weights: {unknown}")
        cleaned: dict[str, float] = {}
        for key, score in value.items():
            numeric = float(score)
            if not math.isfinite(numeric) or numeric < 0.0:
                raise ValueError(f"fusion weight {key!r} must be finite and non-negative")
            cleaned[key] = numeric
        return cleaned

    @model_validator(mode="after")
    def _complete_normalized_weights(self) -> FusionConfig:
        normalized = {key: self.weights.get(key, 0.0) for key in DEFAULT_FUSION_WEIGHTS}
        if not math.isclose(sum(normalized.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("fusion weights must sum to 1.0")
        self.weights = normalized
        return self


def fusion_enabled(config: FusionConfig) -> bool:
    """Read the explicit host gate without adding a global singleton."""
    return config.enabled


def allocate_type_budgets(total_budget: int, profile: BudgetProfile = "coding") -> dict[str, int]:
    """Allocate every token exactly once using integer largest remainders.

    The section-12 percentages are converted with exact integer arithmetic. The
    leftover tokens go to the largest fractional remainders, with
    :data:`BUDGET_TYPE_ORDER` as the deterministic tie-break. Consequently the
    returned values always sum exactly to ``total_budget``.
    """
    if total_budget < 0:
        raise ValueError("total_budget must be non-negative")
    percentages = BUDGET_PROFILES.get(profile)
    if percentages is None:
        raise ValueError(f"unknown budget profile: {profile}")

    allocations = {memory_type: 0 for memory_type in BUDGET_TYPE_ORDER}
    ranked_remainders: list[tuple[int, int]] = []
    for index, memory_type in enumerate(BUDGET_TYPE_ORDER):
        numerator = total_budget * percentages[memory_type]
        allocations[memory_type], remainder = divmod(numerator, 100)
        ranked_remainders.append((remainder, index))
    leftover = total_budget - sum(allocations.values())
    for remainder, index in sorted(ranked_remainders, key=lambda item: (-item[0], item[1]))[:leftover]:
        allocations[BUDGET_TYPE_ORDER[index]] += 1
    return allocations


__all__ = [
    "BUDGET_PROFILES",
    "BUDGET_TYPE_ORDER",
    "DEFAULT_FUSION_WEIGHTS",
    "BudgetProfile",
    "FusionConfig",
    "FusionStrategy",
    "allocate_type_budgets",
    "fusion_enabled",
]
