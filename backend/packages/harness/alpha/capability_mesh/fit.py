"""Context-aware fit scoring: a deterministic composite of observable parts (plan §3.3).

Fit answers "how well does THIS passport fit THIS microtask", from parts a
reader can re-derive: capability coverage, memory-measured quality, tool
margin, and a recency penalty. Every factor appends a human-readable line
with its number into ``FitScore.score_explanation`` — the plan requires the
component scores to be logged so a routing decision can be understood, and a
score without its factors is vibes.

Five rules decide the shape of this module:

* **Hard constraints are gates, not penalties.** :func:`eligible_entities`
  runs BEFORE any score exists: a disabled passport or one missing a
  required tool is *refused*, and no fit score — however great — can
  override a refusal (plan invariant §25: "no failed hard constraint can be
  overridden by an aggregate fit score").
* **An unmeasured capability counts as a PRIOR, never as evidence.** A
  capability covered only by an UNMEASURED estimate contributes
  ``UNMEASURED_PRIOR`` (0.25) to coverage, and a missing/unmeasured memory
  quality contributes ``NEUTRAL_QUALITY`` (0.5). Priors keep a fresh
  passport in the running; they never masquerade as measurements.
* **The evidence level is the WORST of the driving estimates.** Per
  capability the stronger of the passport's and the memory's estimate
  drives (ties keep the passport's), and across required capabilities the
  *worst* level drives the whole score — one unmeasured requirement makes
  the answer an estimate (``estimated=True``), which is what lets
  :func:`rank_entities` keep priors behind measurements.
* **Recency is a multiplier with a stated reason.** When the fingerprint
  requires recency and the driving estimate's ``last_evaluated_at`` is
  stale, absent, future-dated or unparseable, the score is multiplied by
  ``RECENCY_PENALTY`` (0.8) and the explanation says so — a timestamp
  nobody can read is stale, never recent (the Phase A rule).
* **The weights are a documented starting policy, not truth.**
  ``COVERAGE_WEIGHT``/``QUALITY_WEIGHT``/``TOOL_WEIGHT`` are module
  constants chosen as a transparent first pass to be tuned against
  measured routing outcomes later (plan §3.3 explicitly forbids presenting
  hard-coded weights as objectively correct). ``profile_version`` is
  threaded into every memory read: a rubric change invalidates
  comparability, so memory quality is always scoped to one rubric (Phase B
  rule).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from alpha.capability_mesh.decomposition import Microtask
from alpha.capability_mesh.fingerprint import SituationFingerprint
from alpha.capability_mesh.memory import CapabilityMemory
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EvidenceLevel, _parse_timestamp, evidence_rank, worst_evidence_level

#: The documented starting weights (they sum to 1.0). A starting policy to
#: tune against measured routing outcomes, never a claim of correctness.
COVERAGE_WEIGHT = 0.5
QUALITY_WEIGHT = 0.35
TOOL_WEIGHT = 0.15

#: Coverage contribution of a capability covered only by an UNMEASURED
#: estimate: a prior that keeps a fresh passport in the running, never
#: evidence. Mirrors "an unmeasured capability counts a little".
UNMEASURED_PRIOR = 0.25

#: Quality contribution when nothing was measured for a capability (no
#: memory, no records, or records with no measured quality): neutral, never
#: a fabricated number.
NEUTRAL_QUALITY = 0.5

#: The fit uncertainty reported when the driving evidence is UNMEASURED: a
#: modest constant saying "this is a prior", not a measured spread.
UNMEASURED_UNCERTAINTY = 0.1

#: The multiplier applied when recency is required but no recent measurement
#: backs the driving estimate.
RECENCY_PENALTY = 0.8

#: How recent a measurement must be to satisfy a recency requirement — the
#: same window ``derive_evidence_level`` uses for revalidation.
RECENCY_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class HardConstraintRefusal:
    """One entity refused for one microtask by a hard constraint, before scoring.

    ``unmet`` carries the observable reasons ("disabled", "missing
    tool:foo"), never a judgement. ``microtask_id`` scopes the refusal to
    the microtask it was computed for (``eligible_entities`` always fills
    it); it is optional only so a hand-built entity-level row is possible,
    and :class:`alpha.capability_mesh.assignment.RoutingPlan` uses the
    scoping to prove no microtask was silently dropped.
    """

    entity_id: str
    unmet: tuple[str, ...]
    microtask_id: str | None = None

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("entity_id must be non-empty")
        unmet = tuple(self.unmet)
        if not unmet:
            raise ValueError("unmet must be non-empty: a refusal without its observable reasons is an assertion")
        for reason in unmet:
            if not reason:
                raise ValueError("unmet entries must be non-empty strings")
        object.__setattr__(self, "unmet", unmet)
        if self.microtask_id is not None and not self.microtask_id:
            raise ValueError("microtask_id must be a non-empty string or None")

    def to_dict(self) -> dict[str, Any]:
        return {"entity_id": self.entity_id, "unmet": list(self.unmet), "microtask_id": self.microtask_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HardConstraintRefusal:
        return cls(entity_id=str(data["entity_id"]), unmet=tuple(str(reason) for reason in data["unmet"]), microtask_id=data.get("microtask_id"))


@dataclass(frozen=True)
class FitScore:
    """One passport's fit for one microtask, with every factor's number (plan §3.3).

    ``estimated`` is True exactly when the driving evidence is UNMEASURED —
    this score is a prior, not a measurement — and ``uncertainty`` is the
    UNMEASURED constant in that case (the estimate's own spread otherwise,
    where a measured estimate reporting no spread is 0.0 and travels with
    its ``sample_count``). ``score_explanation`` is non-empty by
    construction: a score a reader cannot re-derive is vibes.
    """

    entity_id: str
    fit_score: float
    score_explanation: tuple[str, ...]
    uncertainty: float
    evidence_level: EvidenceLevel
    estimated: bool

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("entity_id must be non-empty")
        if isinstance(self.fit_score, bool) or not isinstance(self.fit_score, (int, float)) or not math.isfinite(float(self.fit_score)) or not 0.0 <= float(self.fit_score) <= 1.0:
            raise ValueError(f"fit_score must be a finite number in [0, 1], got {self.fit_score!r}")
        object.__setattr__(self, "fit_score", float(self.fit_score))
        explanation = tuple(self.score_explanation)
        if not explanation:
            raise ValueError("score_explanation must be non-empty: every factor with its number, or the score is vibes")
        for line in explanation:
            if not line:
                raise ValueError("score_explanation entries must be non-empty strings")
        object.__setattr__(self, "score_explanation", explanation)
        if isinstance(self.uncertainty, bool) or not isinstance(self.uncertainty, (int, float)) or not math.isfinite(float(self.uncertainty)) or float(self.uncertainty) < 0:
            raise ValueError(f"uncertainty must be a finite number >= 0, got {self.uncertainty!r}")
        object.__setattr__(self, "uncertainty", float(self.uncertainty))
        try:
            object.__setattr__(self, "evidence_level", EvidenceLevel(str(self.evidence_level)))
        except ValueError as error:
            allowed = ", ".join(level.value for level in EvidenceLevel)
            raise ValueError(f"unknown evidence_level {self.evidence_level!r}; expected one of: {allowed}") from error
        if not isinstance(self.estimated, bool):
            raise ValueError(f"estimated must be a bool, got {self.estimated!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "fit_score": self.fit_score,
            "score_explanation": list(self.score_explanation),
            "uncertainty": self.uncertainty,
            "evidence_level": self.evidence_level.value,
            "estimated": self.estimated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FitScore:
        return cls(
            entity_id=str(data["entity_id"]),
            fit_score=float(data["fit_score"]),
            score_explanation=tuple(str(line) for line in data["score_explanation"]),
            uncertainty=float(data["uncertainty"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            evidence_level=data["evidence_level"],
            estimated=bool(data["estimated"]),
        )


def _tool_covered(passport: CapabilityPassport, tool: str) -> bool:
    """Is ``tool`` covered by this passport's tools or declared capabilities?

    A capability named after the tool, or the ``use:<tool>`` form
    ``passports.py`` mints for tools, both count as knowing the tool; and a
    passport that itself *requires* the tool obviously has it.
    """
    capability_names = {estimate.capability for estimate in passport.capabilities}
    return tool in passport.required_tools or tool in capability_names or f"use:{tool}" in capability_names


def eligible_entities(microtask: Microtask, passports: Iterable[CapabilityPassport]) -> tuple[tuple[CapabilityPassport, ...], tuple[HardConstraintRefusal, ...]]:
    """Apply the hard gates FIRST: eligibility is a gate, never a penalty (plan §3.3).

    A disabled passport is refused, and every ``required_tools`` entry of
    the microtask must appear in the passport's tools or capabilities, else
    the refusal names the missing tool. Refusals returned here can never be
    overridden by a fit score downstream — this function runs before any
    score exists, and :func:`rank_entities` scores only what passes it.
    """
    if not isinstance(microtask, Microtask):
        raise ValueError(f"eligible_entities expects a Microtask, got {microtask!r}")
    eligible: list[CapabilityPassport] = []
    refusals: list[HardConstraintRefusal] = []
    for passport in passports:
        if not isinstance(passport, CapabilityPassport):
            raise ValueError(f"passports must contain CapabilityPassport rows, got {passport!r}")
        reasons: list[str] = []
        if not passport.enabled:
            reasons.append("disabled")
        for tool in microtask.required_tools:
            if not _tool_covered(passport, tool):
                reasons.append(f"missing tool:{tool}")
        if reasons:
            refusals.append(HardConstraintRefusal(entity_id=passport.entity_id, unmet=tuple(reasons), microtask_id=microtask.microtask_id))
        else:
            eligible.append(passport)
    return tuple(eligible), tuple(refusals)


def _stronger(first: CapabilityEstimate | None, second: CapabilityEstimate | None) -> CapabilityEstimate | None:
    """The estimate with the stronger evidence; ties keep ``first`` (the passport's).

    The memory estimate only overrides the passport's when it carries
    strictly stronger evidence under the profile version just passed;
    memory *absence* is not evidence against the passport.
    """
    if first is None:
        return second
    if second is None:
        return first
    return second if evidence_rank(second.evidence_level) > evidence_rank(first.evidence_level) else first


def _reference_clock(now: datetime | None) -> datetime:
    reference = now if now is not None else datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return reference.astimezone(UTC)


def _is_fresh(stamp_text: str | None, reference: datetime) -> bool:
    """True only for a real timestamp no older than the window (never future-dated)."""
    stamp = _parse_timestamp(stamp_text)
    if stamp is None:
        return False
    age = reference - stamp
    return timedelta(0) <= age <= RECENCY_WINDOW


def fit_score(
    passport: CapabilityPassport,
    microtask: Microtask,
    *,
    memory: CapabilityMemory | None = None,
    profile_version: str,
    fingerprint: SituationFingerprint | None = None,
    now: datetime | None = None,
) -> FitScore:
    """Score one passport against one microtask from observable parts (plan §3.3).

    The composite is ``COVERAGE_WEIGHT * coverage + QUALITY_WEIGHT * quality
    + TOOL_WEIGHT * tool_margin``, multiplied by ``RECENCY_PENALTY`` when
    the fingerprint requires recency and the driving estimate is not fresh,
    and clamped to [0, 1]. ``profile_version`` is required and threaded into
    every ``memory.estimate_for`` call: memory quality is always scoped to
    one rubric (the Phase B comparability rule), so a rubric change yields
    honest UNMEASURED rather than a non-comparable average.

    Note this scores even a passport that would fail the hard gates — the
    ROUTER never does that (:func:`rank_entities` gates first); scoring is
    pure math, eligibility is the gate's job.
    """
    if not profile_version:
        raise ValueError("fit_score requires a non-empty profile_version (no score without its profile version)")
    reference = _reference_clock(now)
    required = microtask.required_capabilities

    coverage_parts: list[float] = []
    quality_parts: list[float] = []
    driving_per_capability: list[CapabilityEstimate | None] = []
    for capability in required:
        passport_estimate = passport.estimate_for(capability)
        memory_estimate = memory.estimate_for(passport.entity_id, capability, "*", profile_version=profile_version, now=reference) if memory is not None else None
        driving = _stronger(passport_estimate, memory_estimate)
        driving_per_capability.append(driving)
        if driving is None:
            coverage_parts.append(0.0)
        elif driving.evidence_level is EvidenceLevel.UNMEASURED:
            coverage_parts.append(UNMEASURED_PRIOR)
        else:
            coverage_parts.append(1.0)
        if memory_estimate is None or memory_estimate.quality_mean is None:
            quality_parts.append(NEUTRAL_QUALITY)
        else:
            quality_parts.append(memory_estimate.quality_mean)

    coverage = sum(coverage_parts) / len(coverage_parts) if coverage_parts else 1.0
    quality = sum(quality_parts) / len(quality_parts) if quality_parts else NEUTRAL_QUALITY

    if required:
        levels = [estimate.evidence_level if estimate is not None else EvidenceLevel.UNMEASURED for estimate in driving_per_capability]
        candidates = [estimate for estimate in driving_per_capability if estimate is not None]
    else:
        # No declared capability: the whole passport is the evidence base.
        levels = [estimate.evidence_level for estimate in passport.capabilities]
        candidates = list(passport.capabilities)
    overall_level = worst_evidence_level(levels)
    estimated = overall_level is EvidenceLevel.UNMEASURED
    driving_estimate = min(candidates, key=lambda estimate: evidence_rank(estimate.evidence_level)) if candidates else None

    if estimated or driving_estimate is None:
        uncertainty = UNMEASURED_UNCERTAINTY
    else:
        # A measured estimate reporting no spread is 0.0 — the ABSENCE of a
        # spread estimate, not a claim of exactness; its sample_count
        # travels with the estimate for exactly that reason.
        uncertainty = driving_estimate.uncertainty if driving_estimate.uncertainty is not None else 0.0

    if microtask.required_tools:
        covered_tools = sum(1 for tool in microtask.required_tools if _tool_covered(passport, tool))
        tool_margin = covered_tools / len(microtask.required_tools)
    else:
        tool_margin = 1.0

    raw = COVERAGE_WEIGHT * coverage + QUALITY_WEIGHT * quality + TOOL_WEIGHT * tool_margin

    explanation: list[str] = []
    if required:
        measured = sum(1 for part in coverage_parts if part == 1.0)
        prior = sum(1 for part in coverage_parts if part == UNMEASURED_PRIOR)
        missing = len(coverage_parts) - measured - prior
        explanation.append(f"coverage {coverage:.3f} over {len(required)} required capability(ies): {measured} measured (1.0 each), {prior} unmeasured-prior ({UNMEASURED_PRIOR} each), {missing} unknown (0.0 each)")
    else:
        explanation.append("coverage 1.000: no required capability declared, nothing to cover")
    if memory is None:
        explanation.append(f"quality {quality:.3f}: no capability memory provided; neutral {NEUTRAL_QUALITY} prior")
    elif not required:
        explanation.append(f"quality {quality:.3f}: no required capability to ask the memory about; neutral {NEUTRAL_QUALITY} prior")
    else:
        measured_qualities = sum(1 for part in quality_parts if part != NEUTRAL_QUALITY)
        explanation.append(f"quality {quality:.3f} = mean over {len(quality_parts)} capability(ies): {measured_qualities} memory-measured quality_mean, {len(quality_parts) - measured_qualities} neutral {NEUTRAL_QUALITY} prior")
    if microtask.required_tools:
        explanation.append(f"tool margin {tool_margin:.3f} = {covered_tools}/{len(microtask.required_tools)} required tool(s) covered by this passport")
    else:
        explanation.append("tool margin 1.000: no required tool to gate on")
    explanation.append(f"evidence level {overall_level.value} ({'estimated prior' if estimated else 'measured'})")

    if fingerprint is not None and fingerprint.recency_required:
        fresh = not estimated and driving_estimate is not None and _is_fresh(driving_estimate.last_evaluated_at, reference)
        if fresh:
            explanation.append(f"recency required; driving estimate measured {driving_estimate.last_evaluated_at} within {RECENCY_WINDOW.days} days")
        else:
            raw *= RECENCY_PENALTY
            explanation.append(f"recency required; no recent measurement — fit multiplied by {RECENCY_PENALTY}")
    explanation.append(f"weighted fit {min(1.0, max(0.0, raw)):.3f} = {COVERAGE_WEIGHT}*{coverage:.3f} + {QUALITY_WEIGHT}*{quality:.3f} + {TOOL_WEIGHT}*{tool_margin:.3f}")

    return FitScore(
        entity_id=passport.entity_id,
        fit_score=min(1.0, max(0.0, raw)),
        score_explanation=tuple(explanation),
        uncertainty=uncertainty,
        evidence_level=overall_level,
        estimated=estimated,
    )


def rank_entities(
    microtask: Microtask,
    passports: Iterable[CapabilityPassport],
    *,
    memory: CapabilityMemory | None = None,
    profile_version: str,
    fingerprint: SituationFingerprint | None = None,
    now: datetime | None = None,
) -> tuple[tuple[FitScore, ...], tuple[HardConstraintRefusal, ...]]:
    """Score the eligible passports and rank them deterministically (plan §3.5).

    Hard gates run FIRST: only passports passing :func:`eligible_entities`
    are scored, and the refusals travel back untouched — a refused passport
    appears nowhere in the ranking, whatever it would have scored. The sort
    is ``fit_score`` desc, then **estimated last**, then ``uncertainty``
    asc, then ``entity_id`` asc. The estimated-after-measured tiebreak sits
    immediately after the score (before uncertainty) because an unmeasured
    prior's constant 0.1 uncertainty would otherwise beat a measured 0.3 and
    smuggle the prior ahead of the measurement on an equal score — which
    the plan forbids outright. Same input, same order: no randomness.
    """
    eligible, refusals = eligible_entities(microtask, passports)
    scores = [fit_score(passport, microtask, memory=memory, profile_version=profile_version, fingerprint=fingerprint, now=now) for passport in eligible]
    scores.sort(key=lambda score: (-score.fit_score, 0 if not score.estimated else 1, score.uncertainty, score.entity_id))
    return tuple(scores), refusals


__all__ = [
    "COVERAGE_WEIGHT",
    "NEUTRAL_QUALITY",
    "QUALITY_WEIGHT",
    "RECENCY_PENALTY",
    "RECENCY_WINDOW",
    "TOOL_WEIGHT",
    "UNMEASURED_PRIOR",
    "UNMEASURED_UNCERTAINTY",
    "FitScore",
    "HardConstraintRefusal",
    "eligible_entities",
    "fit_score",
    "rank_entities",
]
