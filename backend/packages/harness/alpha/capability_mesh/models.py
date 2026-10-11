"""Typed value types for the Alpha Capability Mesh (ACM).

One passport is one *runnable configuration* — an agent, a model config, a
tool, or a skill bundle — with the evidence behind what it can demonstrably
do (plan §2/§25 in ``docs/alpha-arena-new-capability-mesh-features-plan.md``).
Everything here is a frozen dataclass with an explicit ``to_dict``/``from_dict``
pair, matching ``alpha.arena.models``: the record round-trips to JSON with no
translation layer, and every honesty rule is enforced in ``__post_init__``
so a hand-edited file cannot smuggle a fabricated statistic past the door.

Three rules decide the shape of this module:

* **No measured estimate with zero observations.** ``quality_mean`` and
  ``uncertainty`` require ``sample_count >= 1``; anything above UNMEASURED
  requires it too.
* **No score without its profile version.** OUTCOME_VERIFIED and
  RECENTLY_REVALIDATED require a non-empty ``profile_version``, so a
  measurement can always be traced to the profile/rubric that produced it.
* **Evidence level is derived, never hand-set.** ``derive_evidence_level()``
  is the single rule function; the enum is data, and its ordering lives in
  ``EVIDENCE_RANK`` because a StrEnum compares by string, not by rank
  (``"unmeasured" > "recently_revalidated"`` lexicographically).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any


class EvidenceLevel(StrEnum):
    """How strongly a capability estimate is backed by evidence (plan §2.3)."""

    UNMEASURED = "unmeasured"
    OBSERVED = "observed"
    BENCHMARKED = "benchmarked"
    OUTCOME_VERIFIED = "outcome_verified"
    RECENTLY_REVALIDATED = "recently_revalidated"


#: The only ordering of :class:`EvidenceLevel`. A ``StrEnum`` compares by
#: string value, so ``min(levels)`` would pick the lexicographic smallest —
#: "benchmarked" beating "outcome_verified" — and every aggregation must go
#: through this table instead.
EVIDENCE_RANK: dict[EvidenceLevel, int] = {
    EvidenceLevel.UNMEASURED: 0,
    EvidenceLevel.OBSERVED: 1,
    EvidenceLevel.BENCHMARKED: 2,
    EvidenceLevel.OUTCOME_VERIFIED: 3,
    EvidenceLevel.RECENTLY_REVALIDATED: 4,
}

#: The strongest evidence level that any finite sample count can earn without
#: an outcome check.
_BENCHMARK_FLOOR_SAMPLES = 10


def evidence_rank(level: EvidenceLevel) -> int:
    """Numeric rank of ``level`` (0 = weakest). The only way to order levels."""
    return EVIDENCE_RANK[EvidenceLevel(level)]


def worst_evidence_level(levels: Iterable[EvidenceLevel]) -> EvidenceLevel:
    """The weakest level among ``levels``; UNMEASURED for an empty iterable.

    A passport reports its *worst* evidence so it can never over-claim: one
    unmeasured estimate next to three verified ones makes the whole answer
    unmeasured for that capability.
    """
    materialised = list(levels)
    if not materialised:
        return EvidenceLevel.UNMEASURED
    return min(materialised, key=evidence_rank)


class EntityKind(StrEnum):
    """What kind of runnable configuration a passport describes (plan §2.2)."""

    AGENT = "agent"
    MODEL_CONFIG = "model_config"
    TOOL = "tool"
    SKILL_BUNDLE = "skill_bundle"


# --------------------------------------------------------------- timestamps


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    Handles the ``Z`` suffix; a naive timestamp is read as UTC. Anything
    unparseable returns ``None`` — callers treat that as *stale*, never as
    recent, because a timestamp nobody can read is not evidence of recency.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def derive_evidence_level(
    sample_count: int,
    *,
    has_outcome: bool,
    last_evaluated_at: str | None,
    now: datetime | None = None,
    revalidation_window: timedelta = timedelta(days=30),
) -> EvidenceLevel:
    """The single evidence-level rule (plan §2.3), applied honestly:

    * ``sample_count <= 0`` -> UNMEASURED — no observations, no evidence;
    * an outcome with a recent-enough ``last_evaluated_at`` -> RECENTLY_REVALIDATED;
    * an outcome otherwise (missing, stale, future-dated or unparseable
      timestamp) -> OUTCOME_VERIFIED;
    * ``sample_count >= 10`` -> BENCHMARKED;
    * ``sample_count >= 1`` -> OBSERVED.

    A future-dated timestamp is never "recent": a measurement dated ahead of
    the clock cannot prove revalidation happened.
    """
    if sample_count <= 0:
        return EvidenceLevel.UNMEASURED
    if has_outcome:
        stamp = _parse_timestamp(last_evaluated_at)
        if stamp is not None:
            reference = now if now is not None else datetime.now(UTC)
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=UTC)
            reference = reference.astimezone(UTC)
            age = reference - stamp
            if timedelta(0) <= age <= revalidation_window:
                return EvidenceLevel.RECENTLY_REVALIDATED
        return EvidenceLevel.OUTCOME_VERIFIED
    if sample_count >= _BENCHMARK_FLOOR_SAMPLES:
        return EvidenceLevel.BENCHMARKED
    return EvidenceLevel.OBSERVED


# ----------------------------------------------------------------- estimate


@dataclass(frozen=True)
class CapabilityEstimate:
    """One measured (or explicitly unmeasured) capability of one entity.

    ``task_category`` is the task-relative dimension this estimate answers
    for (plan §2.2's capability vector); ``"*"`` is the declared wildcard
    entry used when no narrower category was measured. ``profile_version``
    names the evaluation profile/rubric that produced the statistics — the
    evidence levels above UNMEASURED refuse to exist without it, so a score
    can always be traced to how it was produced.
    """

    capability: str
    task_category: str
    quality_mean: float | None = None
    uncertainty: float | None = None
    sample_count: int = 0
    evidence_level: EvidenceLevel = EvidenceLevel.UNMEASURED
    last_evaluated_at: str | None = None
    profile_version: str = ""

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "evidence_level", EvidenceLevel(str(self.evidence_level)))
        except ValueError as error:
            allowed = ", ".join(level.value for level in EvidenceLevel)
            raise ValueError(f"unknown evidence_level {self.evidence_level!r}; expected one of: {allowed}") from error

        if self.sample_count < 0:
            raise ValueError(f"sample_count must be >= 0, got {self.sample_count}")
        if self.quality_mean is not None and not math.isfinite(self.quality_mean):
            raise ValueError(f"quality_mean must be a finite number, got {self.quality_mean!r}")
        if self.uncertainty is not None and (not math.isfinite(self.uncertainty) or self.uncertainty < 0):
            raise ValueError(f"uncertainty must be a finite number >= 0, got {self.uncertainty!r}")
        if (self.quality_mean is not None or self.uncertainty is not None) and self.sample_count < 1:
            raise ValueError("quality_mean/uncertainty require sample_count >= 1 (no fabricated statistics)")
        if self.evidence_level is not EvidenceLevel.UNMEASURED and self.sample_count < 1:
            raise ValueError(f"evidence_level '{self.evidence_level.value}' requires sample_count >= 1 (no measured estimate with zero observations)")
        if self.evidence_level in (EvidenceLevel.OUTCOME_VERIFIED, EvidenceLevel.RECENTLY_REVALIDATED) and not self.profile_version:
            raise ValueError(f"evidence_level '{self.evidence_level.value}' requires a non-empty profile_version (no score without its profile version)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "task_category": self.task_category,
            "quality_mean": self.quality_mean,
            "uncertainty": self.uncertainty,
            "sample_count": self.sample_count,
            "evidence_level": self.evidence_level.value,
            "last_evaluated_at": self.last_evaluated_at,
            "profile_version": self.profile_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityEstimate:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            capability=str(data["capability"]),
            task_category=str(data["task_category"]),
            quality_mean=None if data.get("quality_mean") is None else float(data["quality_mean"]),
            uncertainty=None if data.get("uncertainty") is None else float(data["uncertainty"]),
            sample_count=int(data.get("sample_count", 0)),
            # Coerced (and validated with the friendly message) in __post_init__.
            evidence_level=data.get("evidence_level", EvidenceLevel.UNMEASURED),
            last_evaluated_at=data.get("last_evaluated_at"),
            profile_version=str(data.get("profile_version", "")),
        )


# ---------------------------------------------------------------- passport


@dataclass(frozen=True)
class CapabilityPassport:
    """The evidence-backed capability record for one runnable configuration.

    The identity fields say *what* is being described; ``capabilities`` says
    what it can demonstrably do and how strong the evidence is. A passport
    never fabricates: a question it has no estimate for is answered with
    ``None``/UNMEASURED, never with a guess (plan §2.5: "Missing data never
    silently turns into a high score").
    """

    entity_id: str
    entity_type: EntityKind
    version: str
    model_provider: str | None = None
    model_id: str | None = None
    model_family: str | None = None
    capabilities: tuple[CapabilityEstimate, ...] = ()
    required_tools: tuple[str, ...] = ()
    known_failure_modes: tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("entity_id must be non-empty")
        try:
            object.__setattr__(self, "entity_type", EntityKind(str(self.entity_type)))
        except ValueError as error:
            allowed = ", ".join(kind.value for kind in EntityKind)
            raise ValueError(f"unknown entity_type {self.entity_type!r}; expected one of: {allowed}") from error
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "required_tools", tuple(self.required_tools))
        object.__setattr__(self, "known_failure_modes", tuple(self.known_failure_modes))
        for estimate in self.capabilities:
            if not isinstance(estimate, CapabilityEstimate):
                raise ValueError(f"capabilities must contain CapabilityEstimate rows, got {estimate!r}")

    def estimate_for(self, capability: str, task_category: str | None = None) -> CapabilityEstimate | None:
        """The estimate answering one capability question, or None.

        Preference order for an exact capability match: the requested
        ``task_category``, then the ``"*"`` wildcard entry. With no category
        requested the wildcard answers, else the first recorded estimate for
        the capability (a real measurement, not a fabrication). No estimate
        at all returns ``None`` — never a guess.
        """
        candidates = [estimate for estimate in self.capabilities if estimate.capability == capability]
        if not candidates:
            return None
        if task_category is not None:
            for estimate in candidates:
                if estimate.task_category == task_category:
                    return estimate
        for estimate in candidates:
            if estimate.task_category == "*":
                return estimate
        if task_category is None:
            return candidates[0]
        return None

    def overall_evidence_level(self, capability: str | None = None) -> EvidenceLevel:
        """The WORST evidence level among matching estimates.

        With ``capability`` the worst across that capability's estimates
        (all categories); without it the worst across the whole passport.
        An empty selection is UNMEASURED. Reporting the floor rather than
        the ceiling is what keeps a passport from over-claiming.
        """
        if capability is None:
            matches = self.capabilities
        else:
            matches = tuple(estimate for estimate in self.capabilities if estimate.capability == capability)
        return worst_evidence_level(estimate.evidence_level for estimate in matches)

    def summary(self) -> dict[str, Any]:
        """Compact JSON-safe projection for HTTP/tool surfaces."""
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "version": self.version,
            "enabled": self.enabled,
            "capabilities": [
                {
                    "capability": estimate.capability,
                    "task_category": estimate.task_category,
                    "evidence_level": estimate.evidence_level.value,
                    "sample_count": estimate.sample_count,
                    "quality_mean": estimate.quality_mean,
                }
                for estimate in self.capabilities
            ],
            "known_failure_modes": list(self.known_failure_modes),
            "required_tools": list(self.required_tools),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "version": self.version,
            "model_provider": self.model_provider,
            "model_id": self.model_id,
            "model_family": self.model_family,
            "capabilities": [estimate.to_dict() for estimate in self.capabilities],
            "required_tools": list(self.required_tools),
            "known_failure_modes": list(self.known_failure_modes),
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityPassport:
        return cls(
            entity_id=str(data["entity_id"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            entity_type=data["entity_type"],
            version=str(data.get("version", "")),
            model_provider=data.get("model_provider"),
            model_id=data.get("model_id"),
            model_family=data.get("model_family"),
            capabilities=tuple(CapabilityEstimate.from_dict(entry) for entry in data.get("capabilities", ())),
            required_tools=tuple(str(name) for name in data.get("required_tools", ())),
            known_failure_modes=tuple(str(mode) for mode in data.get("known_failure_modes", ())),
            enabled=bool(data.get("enabled", True)),
        )


__all__ = [
    "EVIDENCE_RANK",
    "CapabilityEstimate",
    "CapabilityPassport",
    "EntityKind",
    "EvidenceLevel",
    "derive_evidence_level",
    "evidence_rank",
    "worst_evidence_level",
]
