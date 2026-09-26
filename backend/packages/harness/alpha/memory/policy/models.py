"""Typed inputs and outputs for Alpha's memory-admission boundary.

The fields in this module deliberately carry provenance and scope as plain
strings.  Admission is a local policy decision; authorization and ownership are
enforced later by the memory fabric.  Signals use ``None`` for "not supplied",
which is distinct from a known zero and is disclosed by the scorer.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AdmissionAction = Literal[
    "always_store",
    "durable_profile",
    "durable_project",
    "episodic_archive",
    "session_only",
    "reject",
    "promote_to_skill_candidate",
]
AdmissionTier = Literal["active", "compressed", "archived"]


class AdmissionCandidate(BaseModel):
    """One extracted item considered for durable memory admission.

    Classification fields (``types``, ``subtype``, ``source``, ``claim_status``)
    are structured inputs from the extractor.  A missing weighted signal stays
    ``None``; callers must not turn uncertainty into a fabricated zero (or a
    fabricated one) without recording that choice explicitly.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(default="", max_length=256)
    content: str = Field(min_length=1, max_length=200_000)
    types: list[str] = Field(default_factory=list, max_length=64)
    subtype: str = Field(default="", max_length=128)
    source: str = Field(default="", max_length=512)
    claim_status: str = Field(default="", max_length=128)

    # Scope is intentionally untrusted metadata here.  The fabric owns the
    # separate retrieval/authorization scope check required by plan section 21.
    user_id: str = Field(default="", max_length=256)
    agent_id: str = Field(default="", max_length=256)
    project_id: str = Field(default="", max_length=256)
    team_id: str = Field(default="", max_length=256)
    task_id: str = Field(default="", max_length=256)
    session_id: str = Field(default="", max_length=256)
    provenance: str = Field(default="", max_length=4096)

    # Rule-specific structured evidence.  ``None`` means the extractor did not
    # supply the count; zero is a known unsuccessful procedure.
    success_count: int | None = Field(default=None, ge=0)

    # Plan section 10 weighted signals.  None is missing, not zero.
    importance: float | None = None
    future_utility: float | None = None
    novelty: float | None = None
    confidence: float | None = None
    recurrence: float | None = None
    task_relevance: float | None = None
    explicit_user_request: bool | None = None

    # Additional hard-rule signals and classification hints.
    sensitivity_hint: bool | None = None
    is_secret_like: bool | None = None
    is_transient: bool | None = None
    is_verified: bool | None = None

    @field_validator("content")
    @classmethod
    def _normalize_content(cls, value: str) -> str:
        content = value.strip()
        if not content:
            raise ValueError("content must not be blank")
        return content

    @field_validator("types")
    @classmethod
    def _normalize_types(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            text = str(item).strip().lower().replace("-", "_").replace(" ", "_")
            if text and text not in normalized:
                normalized.append(text[:128])
        return normalized

    @field_validator("subtype", "source", "claim_status")
    @classmethod
    def _normalize_label(cls, value: str) -> str:
        return value.strip()[:512]

    @field_validator(
        "importance",
        "future_utility",
        "novelty",
        "confidence",
        "recurrence",
        "task_relevance",
    )
    @classmethod
    def _finite_signal(cls, value: float | None) -> float | None:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("weighted admission signals must be finite")
        return number


class AdmissionDecision(BaseModel):
    """A fully disclosed admission outcome.

    ``score_breakdown`` contains weighted contributions (not raw signal
    values), so its values sum to ``score`` unless the final total was clamped.
    ``missing_signals`` and ``disclosures`` make omissions and uncertainty
    visible to callers and provenance writers.
    """

    model_config = ConfigDict(extra="forbid")

    admit: bool
    action: AdmissionAction
    score: float = Field(ge=0.0, le=1.0)
    score_breakdown: dict[str, float]
    rule_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    tier: AdmissionTier
    ttl_seconds: int | None = Field(default=None, ge=0)
    missing_signals: list[str] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)

    @field_validator("score_breakdown")
    @classmethod
    def _finite_breakdown(cls, value: dict[str, float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for name, contribution in value.items():
            number = float(contribution)
            if not math.isfinite(number):
                raise ValueError("score breakdown values must be finite")
            result[str(name)] = number
        return result

    @field_validator("missing_signals", "disclosures")
    @classmethod
    def _normalize_disclosures(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in result:
                result.append(text[:512])
        return result


__all__ = [
    "AdmissionAction",
    "AdmissionCandidate",
    "AdmissionDecision",
    "AdmissionTier",
]
