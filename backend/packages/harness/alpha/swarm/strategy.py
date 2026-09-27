"""Automated swarm strategy resolution: when, how, and how *much* to swarm.

Three signals disagree by design and this module reconciles them in a fixed
precedence, recording which one won:

1. **explicit** — the caller named a mode; nothing overrides intent.
2. **topology** — the plan's measured dependency shape
   (:mod:`alpha.swarm.topology`), strongest once a graph exists.
3. **heuristic** — goal-text signals from
   :class:`~alpha.swarm.estimator.SwarmBenefitEstimator`, all that is available
   before decomposition.

On top of the mode it assigns a **complexity tier**.  Coordination machinery is
not free: adaptive deliberation, stigmergic traces, candidate planning and
critical-path telemetry each cost tokens and latency, so they are gated behind
escalating tiers instead of being switched on for a three-node plan.  A tier is
derived from measured shape plus explicit asks, and it is recorded on the plan
so an operator can see *why* a run got the expensive treatment.

The debate-vs-ensemble decision lives here rather than in topology because it is
semantic, not structural — the literature is also clear that interactive debate
can underperform independent self-correction, so debate is opt-in (explicit mode
or an explicit adversarial request) and ensemble is the structural default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from alpha.swarm.estimator import SwarmBenefitEstimator
from alpha.swarm.models import SwarmDecision, SwarmMode
from alpha.swarm.topology import COUPLING_THRESHOLD, DagFeatures, TopologyRoute, route_topology

__all__ = [
    "ComplexityTier",
    "StrategyResolution",
    "resolve_strategy",
    "tier_for",
]


class ComplexityTier(StrEnum):
    """Escalating coordination gates, cheapest first.

    ``DIRECT`` runs no swarm at all.  Each later tier keeps everything the
    previous tiers had, so a ``FULL`` plan is a superset rather than a
    different feature set.
    """

    DIRECT = "direct"  # single agent; no coordination, no deliberation
    SIMPLE = "simple"  # flat parallel dispatch only
    ADAPT = "adapt"  # structural routing + reflection + budget telemetry
    MEDIUM = "medium"  # + candidate planning, stigmergy, conflict reconciliation
    FULL = "full"  # + adaptive deliberation, leader election, critical-path telemetry

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]


_TIER_RANK: dict[ComplexityTier, int] = {
    ComplexityTier.DIRECT: 0,
    ComplexityTier.SIMPLE: 1,
    ComplexityTier.ADAPT: 2,
    ComplexityTier.MEDIUM: 3,
    ComplexityTier.FULL: 4,
}

# Semantic overrides: the goal text is the only signal available pre-decomposition.
_DEBATE_KEYWORDS = ("debate", "argue both sides", "adversarial", "red team", "devil's advocate", "pros and cons")
_ENSEMBLE_KEYWORDS = ("consensus", "panel", "vote", "majority", "independent review", "cross-check")


@dataclass(frozen=True)
class StrategyResolution:
    """The resolved mode plus the full audit trail of how it was chosen."""

    mode: SwarmMode
    should_swarm: bool
    source: str
    tier: ComplexityTier
    confidence: float
    rationale: str
    features: DagFeatures | None = None
    decision: SwarmDecision | None = None
    route: TopologyRoute | None = None
    considered: tuple[str, ...] = ()

    # NOTE: these compare ``rank``, never the enum directly.  ``ComplexityTier``
    # is a StrEnum, so ``tier >= ComplexityTier.FULL`` compares *strings* and
    # ``"full" >= "medium"`` is False — a silent inversion that would gate every
    # capability off on the most complex plans.
    @property
    def allows_deliberation(self) -> bool:
        return self.tier.rank >= ComplexityTier.FULL.rank

    @property
    def allows_stigmergy(self) -> bool:
        return self.tier.rank >= ComplexityTier.MEDIUM.rank

    @property
    def allows_candidate_planning(self) -> bool:
        return self.tier.rank >= ComplexityTier.MEDIUM.rank

    @property
    def allows_structural_routing(self) -> bool:
        return self.tier.rank >= ComplexityTier.ADAPT.rank

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "should_swarm": self.should_swarm,
            "source": self.source,
            "tier": self.tier.value,
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
            "features": self.features.to_dict() if self.features else None,
            "route": self.route.to_dict() if self.route else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "considered": list(self.considered),
        }


def tier_for(
    *,
    should_swarm: bool,
    features: DagFeatures | None,
    requires_consensus: bool = False,
    explicit: bool = False,
    goal: str = "",
) -> ComplexityTier:
    """Assign the cheapest tier that honestly covers the run's requirements.

    Deliberation and consensus are *demands*, not shapes: an explicit
    ``requires_consensus`` request therefore raises the tier regardless of how
    simple the graph is, because refusing to deliberate would ignore an
    explicit requirement.
    """

    if not should_swarm:
        return ComplexityTier.DIRECT

    if requires_consensus:
        return ComplexityTier.FULL

    if features is None:
        # Pre-decomposition: only text signals exist, so stay conservative and
        # let the measured shape upgrade the tier once the plan is built.  An
        # explicit request still earns coordination (it is never ``direct``),
        # but it does not earn the expensive machinery on an unmeasured shape.
        return ComplexityTier.SIMPLE

    if features.cyclic:
        # Unmeasurable shape: run the cheapest topology that still terminates.
        return ComplexityTier.SIMPLE

    if features.depth >= 6 or (features.component_count >= 3 and features.coupling >= COUPLING_THRESHOLD):
        return ComplexityTier.FULL
    if features.coupling >= COUPLING_THRESHOLD or features.component_count >= 2 or features.max_width >= 5:
        return ComplexityTier.MEDIUM
    if features.depth >= 3 or features.max_width >= 3:
        return ComplexityTier.ADAPT
    return ComplexityTier.SIMPLE


def _semantic_override(goal: str, mode: SwarmMode) -> SwarmMode | None:
    """Return a semantic mode override when the goal asks for one explicitly.

    Returns ``None`` when nothing is asked for, so structural/heuristic signals
    stay in charge.  Debate is never inferred from a comparison goal alone —
    "compare A and B" is answered better by independent answers than by a
    conversation that can converge early.
    """

    if mode != SwarmMode.AUTO:
        return None
    goal_lower = str(goal or "").lower()
    if any(keyword in goal_lower for keyword in _DEBATE_KEYWORDS):
        return SwarmMode.DEBATE
    if any(keyword in goal_lower for keyword in _ENSEMBLE_KEYWORDS):
        return SwarmMode.ENSEMBLE
    return None


def resolve_strategy(
    goal: str,
    *,
    items: Sequence[str] | None = None,
    mode: SwarmMode = SwarmMode.AUTO,
    is_code: bool = False,
    files_count: int = 1,
    features: DagFeatures | None = None,
    requires_consensus: bool = False,
    max_concurrency: int = 8,
) -> StrategyResolution:
    """Resolve the mode, tier, and confidence for one swarm request.

    ``features`` is optional: pass it when a candidate plan already exists so
    structure outranks text, and omit it for a pre-decomposition decision.
    """

    explicit = mode != SwarmMode.AUTO
    decision: SwarmDecision | None = None
    route: TopologyRoute | None = None

    if explicit:
        # A named mode is a requirement, not a preference: report it as the
        # source and keep the tier derived from whatever shape is measurable.
        should_swarm = True
        resolved = mode
        source = "explicit"
        confidence = 1.0
        rationale = f"caller specified mode `{mode.value}`; no automatic override is applied"
    else:
        decision = SwarmBenefitEstimator.estimate(goal, items=items, is_code=is_code, files_count=files_count)
        if features is not None:
            route = route_topology(features, items=items, max_concurrency=max_concurrency)
        semantic = _semantic_override(goal, SwarmMode.AUTO)

        if semantic is not None:
            # An explicit adversarial/ensemble request outranks structure: the
            # shape says how to execute it, the text says what it must be.
            resolved = semantic
            should_swarm = True
            source = "semantic"
            confidence = 0.9
            rationale = f"goal explicitly requests `{semantic.value}`; structural routing is applied as execution shape, not as the mode"
        elif route is not None and route.should_swarm:
            resolved = route.mode
            should_swarm = True
            source = "topology"
            confidence = route.confidence
            rationale = route.rationale
        elif decision is not None and decision.should_swarm:
            resolved = decision.mode
            should_swarm = True
            source = "heuristic"
            confidence = 0.6
            rationale = decision.reason
        elif route is not None:
            # Structure was measured and said no: trust the measurement over a
            # keyword guess, but keep the heuristic text as the stated reason
            # so the operator sees the original concern too.
            resolved = SwarmMode.PARALLEL
            should_swarm = False
            source = "topology"
            confidence = route.confidence
            rationale = f"{route.rationale}; heuristic check: {decision.reason if decision else 'n/a'}"
        else:
            resolved = SwarmMode.PARALLEL
            should_swarm = False
            source = "heuristic"
            confidence = 0.7
            rationale = decision.reason if decision else "no signal available; single agent"

    tier = tier_for(
        should_swarm=should_swarm,
        features=features,
        requires_consensus=requires_consensus,
        explicit=explicit,
        goal=goal,
    )
    if tier == ComplexityTier.DIRECT:
        should_swarm = False
        resolved = SwarmMode.PARALLEL

    considered = ["explicit", "topology", "heuristic"]
    return StrategyResolution(
        mode=resolved,
        should_swarm=should_swarm,
        source=source,
        tier=tier,
        confidence=confidence,
        rationale=rationale,
        features=features,
        decision=decision,
        route=route,
        considered=tuple(considered),
    )
