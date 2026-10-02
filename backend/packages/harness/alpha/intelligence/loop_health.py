"""Phase F — loop health: answer "is the self-improvement loop working?"

The question nobody was answering
---------------------------------
Six subsystems, one promotion gate, and **no endpoint anywhere** that reports
whether the loop is actually making the system better. Every existing surface
reports *about* something — a candidate's score, a fabric's contents, a run's
status. None reports on the loop.

That question is the one the literature says matters most. Every current
self-improvement loop saturates, and internal verification is fragile
(arXiv 2607.04277). Alpha cannot see its own saturation.

This module **composes** Phases A–E and computes nothing new about capability.
That is deliberate: a dashboard with its own idea of "good" would be a seventh
source of truth competing with the six subsystems. Each field here is read from
a phase that already owns it.

Regimes
-------
``improving`` · ``stable`` · ``saturating`` · ``regressing`` ·
``insufficient_data``

:attr:`Regime.INSUFFICIENT_DATA` is a **first-class regime**, not an empty
response. A loop with two observations has not been measured, and returning
``"stable"`` there would be a fabricated reassurance.

Why ``saturating`` is its own regime
------------------------------------
It is distinct from ``stable`` and the distinction is actionable. ``stable`` at a
good score is fine. ``saturating`` means attempts are still being spent and the
score has stopped responding — which is exactly the condition under which
spending more compute is wasted, and the point at which the honest recommendation
is to improve the *evaluator* rather than to try harder.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.intelligence.diversity import DiversityReport
from alpha.intelligence.evaluator_stability import NoiseFloor
from alpha.intelligence.evidence_ledger import ConvergedVerdict
from alpha.intelligence.pathway import PathwayReport

__all__ = [
    "Regime",
    "LoopHealthReport",
    "SaturationDetector",
    "assess_loop_health",
    "MIN_OBSERVATIONS_FOR_REGIME",
]

#: Below this many scored attempts, no regime is claimed.
MIN_OBSERVATIONS_FOR_REGIME = 3


class Regime(StrEnum):
    """How the loop is behaving."""

    IMPROVING = "improving"
    STABLE = "stable"
    SATURATING = "saturating"
    REGRESSING = "regressing"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class SaturationDetector:
    """Detects attempts being spent with no score response.

    Counts consecutive non-improving attempts and, separately, tracks whether the
    per-attempt gain has flattened. They answer different questions: consecutive
    non-improvement can be noise, while a *flattening gain trend* while the score
    holds is the saturation signature.
    """

    consecutive_non_improving: int = 0
    total_attempts: int = 0
    gain_trend: float | None = None
    saturating_at: int = 4
    """Consecutive non-improving attempts that constitute saturation."""

    def observe(self, improved: bool, *, gain: float | None = None) -> SaturationDetector:
        """Record one scored attempt."""
        self.total_attempts += 1
        if improved:
            self.consecutive_non_improving = 0
        else:
            self.consecutive_non_improving += 1
        if gain is not None:
            previous = self.gain_trend
            self.gain_trend = gain if previous is None else (previous + gain) / 2.0
        return self

    @property
    def is_saturated(self) -> bool:
        return self.consecutive_non_improving >= self.saturating_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "consecutive_non_improving": self.consecutive_non_improving,
            "total_attempts": self.total_attempts,
            "gain_trend": self.gain_trend,
            "saturating_at": self.saturating_at,
            "is_saturated": self.is_saturated,
        }


@dataclass(frozen=True)
class LoopHealthReport:
    """Whether the loop is working, and what is limiting it."""

    regime: Regime
    scored_attempts: int
    consecutive_non_improving: int
    gain_per_attempt_trend: float | None
    pathway_alignment: float | None
    action_space_coverage_delta: float | None
    measured_noise_floor: float | None
    subsystems_agreeing: int
    subsystems_total: int
    subsystems_unreconciled: tuple[str, ...] = ()
    bottleneck: str = ""
    recommended_action: str = ""
    reasons: list[str] = field(default_factory=list)
    contributions: dict[str, Any] = field(default_factory=dict)
    """Which phase supplied which field, so a reader can audit the composition."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "scored_attempts": self.scored_attempts,
            "consecutive_non_improving": self.consecutive_non_improving,
            "gain_per_attempt_trend": self.gain_per_attempt_trend,
            "pathway_alignment": self.pathway_alignment,
            "action_space_coverage_delta": self.action_space_coverage_delta,
            "measured_noise_floor": self.measured_noise_floor,
            "subsystems_agreeing": self.subsystems_agreeing,
            "subsystems_total": self.subsystems_total,
            "subsystems_unreconciled": list(self.subsystems_unreconciled),
            "bottleneck": self.bottleneck,
            "recommended_action": self.recommended_action,
            "reasons": list(self.reasons),
            "contributions": dict(self.contributions),
        }


def _pathway_alignment(reports: Sequence[PathwayReport]) -> float | None:
    """Fraction of pathway checks that were ENGAGED.

    ``None`` when nothing was checkable. Notably ``NOT_ENGAGED`` counts against
    the ratio: a loop whose candidates claim a mechanism and never touch it has
    low alignment, and that is exactly the failure Phase A exists to catch.
    """
    checkable = [report for report in reports if report.evidence is not None]
    if not checkable:
        return None
    engaged = sum(1 for report in checkable if report.verdict.value == "ENGAGED")
    return engaged / len(checkable)


def assess_loop_health(
    *,
    detector: SaturationDetector,
    pathway_reports: Sequence[PathwayReport] = (),
    diversity: DiversityReport | None = None,
    noise: NoiseFloor | None = None,
    convergence: ConvergedVerdict | None = None,
    noise_floor_source: str = "max_of_both",
) -> LoopHealthReport:
    """Compose Phases A–E into one verdict on the loop itself.

    The bottleneck is chosen by **severity**, not by iteration order, so the same
    inputs always name the same limiting factor. Order:

    1. unverified evidence anywhere -> measurement cannot be trusted
    2. subsystem disagreement -> the gates do not agree with each other
    3. evaluator noise >= typical candidate delta -> the evaluator cannot resolve
    4. pathway misalignment -> gains are not arriving by the claimed route
    5. behaviour collapse -> the agent is narrowing
    6. saturation -> compute is being spent with no response
    """
    reasons: list[str] = []
    contributions: dict[str, Any] = {}
    scored_attempts = detector.total_attempts

    if scored_attempts < MIN_OBSERVATIONS_FOR_REGIME:
        return LoopHealthReport(
            regime=Regime.INSUFFICIENT_DATA,
            scored_attempts=scored_attempts,
            consecutive_non_improving=detector.consecutive_non_improving,
            gain_per_attempt_trend=detector.gain_trend,
            pathway_alignment=_pathway_alignment(pathway_reports),
            action_space_coverage_delta=diversity.coverage_delta if diversity else None,
            measured_noise_floor=noise.floor if noise and noise.observed else None,
            subsystems_agreeing=0,
            subsystems_total=0,
            bottleneck="not enough scored attempts to judge the loop",
            recommended_action=(f"collect at least {MIN_OBSERVATIONS_FOR_REGIME} scored attempts before acting on any loop-health signal"),
            reasons=[f"{scored_attempts} scored attempt(s) is below the {MIN_OBSERVATIONS_FOR_REGIME} required to claim a regime; reporting 'stable' here would be fabricated reassurance"],
            contributions={"phase_f": "insufficient data"},
        )

    alignment = _pathway_alignment(pathway_reports)
    contributions["phase_a_pathway_alignment"] = alignment
    contributions["phase_b_noise_floor"] = noise.to_dict() if noise else None
    contributions["phase_c_diversity"] = diversity.to_dict() if diversity else None
    contributions["phase_e_convergence"] = convergence.status.value if convergence else None
    contributions["phase_f_saturation"] = detector.to_dict()

    measured_floor = noise.floor if (noise and noise.observed) else None
    if noise is not None and not noise.observed:
        reasons.append(f"the evaluator's own noise was not measured: {noise.reason}")

    subsystems_agreeing = 0
    subsystems_total = 0
    unreconciled: tuple[str, ...] = ()
    if convergence is not None:
        subsystems_total = len(convergence.records) + len(convergence.unreconciled)
        subsystems_agreeing = sum(1 for record in convergence.records if record.verdict.value == "PASS")
        unreconciled = convergence.unreconciled

    regime = Regime.STABLE
    bottleneck = ""
    recommended = "continue; the loop is not currently limited by anything measured here"

    # 1. evidence trust
    if noise is not None and not noise.observed:
        regime = Regime.INSUFFICIENT_DATA
        bottleneck = "evaluator noise is unmeasured, so no delta this loop produces can be trusted"
        recommended = f"raise the repeat count for the stability probe (currently {noise.samples} observation(s)) before trusting any promotion"
        reasons.append(noise.reason)
    # 2. subsystem disagreement
    elif convergence is not None and convergence.rejecting:
        regime = Regime.REGRESSING
        bottleneck = f"a subsystem rejected a recent candidate: {', '.join(convergence.rejecting)}"
        recommended = "inspect the rejecting subsystem's evidence bundle before spending further attempts"
        reasons.append(convergence.reason)
    # 3. noise dominating deltas
    elif measured_floor is not None and detector.gain_trend is not None and measured_floor >= abs(detector.gain_trend):
        regime = Regime.SATURATING
        bottleneck = f"measured evaluator noise ({measured_floor:.4f}) is at least the per-attempt gain ({detector.gain_trend:.6f}), so the evaluator cannot resolve the changes being made"
        recommended = f"improve the evaluator before trying again: raise regression repeats beyond {noise.samples if noise else 0} and re-measure, using noise_floor_source={noise_floor_source}"
        reasons.append(bottleneck)
    # 4. pathway misalignment
    elif alignment is not None and alignment < 0.5:
        regime = Regime.SATURATING
        bottleneck = f"only {alignment:.0%} of checkable candidates actually engaged the mechanism they claimed, so measured gains are not arriving by the stated route"
        recommended = "audit candidates against Phase A pathway evidence; a score gain without an engaged mechanism is unexplained"
        reasons.append(bottleneck)
    # 5. behaviour collapse
    elif diversity is not None and diversity.collapsed:
        regime = Regime.REGRESSING
        bottleneck = f"behaviour coverage collapsed: action space {diversity.action_space_before} -> {diversity.action_space_after}"
        recommended = "reject the narrowing change and re-run the diversity comparison with the noise floor applied"
        reasons.extend(diversity.reasons)
    # 6. saturation
    elif detector.is_saturated:
        regime = Regime.SATURATING
        bottleneck = f"{detector.consecutive_non_improving} consecutive attempts produced no improvement"
        recommended = "stop spending attempts on this surface; change the method (see alpha.avo StrategicPivotDirective)"
        reasons.append(bottleneck)
    elif detector.gain_trend is not None and detector.gain_trend > 0:
        regime = Regime.IMPROVING
        reasons.append(f"per-attempt gain trend is positive at {detector.gain_trend:+.6f}")

    if unreconciled:
        reasons.append(f"subsystems that never evaluated a recent candidate: {', '.join(unreconciled)}")

    return LoopHealthReport(
        regime=regime,
        scored_attempts=scored_attempts,
        consecutive_non_improving=detector.consecutive_non_improving,
        gain_per_attempt_trend=detector.gain_trend,
        pathway_alignment=alignment,
        action_space_coverage_delta=diversity.coverage_delta if diversity else None,
        measured_noise_floor=measured_floor,
        subsystems_agreeing=subsystems_agreeing,
        subsystems_total=subsystems_total,
        subsystems_unreconciled=unreconciled,
        bottleneck=bottleneck,
        recommended_action=recommended,
        reasons=reasons,
        contributions=contributions,
    )
