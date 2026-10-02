"""Replaceable expert utility scoring (prompt §21).

The formula is deliberately **not** hardcoded into the router. A router that
computes utility inline cannot be re-tuned, A/B tested, or measured, and the
prompt's own instruction — "do not hardcode one formula forever, make the scorer
replaceable" — is exactly that. So the contract here is a protocol plus one
sensible default, and :class:`alpha.intelligence.router.CapabilityRouter`
accepts any object satisfying it.

Why the default looks the way it does
-------------------------------------
``utility = quality_gain x reliability x reuse / (compute_cost + 1)``

The ``+ 1`` in the denominator is the load-bearing part. Without it, an
**unobserved** expert (``quality_gain == 0.0``, ``cost == 0.0``) would score
``0/0`` — either a ``ZeroDivisionError`` or, worse, a silently infinite score
that makes "nobody has ever used this" look like the best expert in the pool.
The floor makes an unobserved expert score ``0.0``, which is correct: it has
demonstrated nothing.

Reliability is likewise never fabricated. :attr:`ExpertMetrics.success_rate` is
``None`` before any observation, and an unobserved expert gets a *neutral*
reliability of 0.5 rather than a perfect 1.0 or a zero — the middle is the only
honest answer for "not measured", and it means exploration (which is the
mechanism for *earning* a real reliability number) is what lifts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from alpha.intelligence.models import ExpertMetrics, ExpertRecord

__all__ = [
    "ExpertScore",
    "UtilityScorer",
    "DefaultUtilityScorer",
    "unobserved_reliability",
]

#: Reliability assigned to an expert with no observations. Explicitly the
#: midpoint: not a pass, not a failure.
unobserved_reliability = 0.5


@dataclass(frozen=True)
class ExpertScore:
    """One expert's scored components. Every component is reported, not just the total."""

    expert_id: str
    utility: float
    quality_gain: float
    reliability: float
    reuse: float
    compute_cost: float
    regression_impact: float = 0.0
    observed: bool = True
    """False when the expert has no observations at all."""

    components: dict[str, float] = field(default_factory=dict)
    """The full breakdown, so a score can be explained without re-deriving it."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "expert_id": self.expert_id,
            "utility": round(self.utility, 6),
            "quality_gain": round(self.quality_gain, 6),
            "reliability": round(self.reliability, 6),
            "reuse": round(self.reuse, 6),
            "compute_cost": round(self.compute_cost, 6),
            "regression_impact": round(self.regression_impact, 6),
            "observed": self.observed,
            "components": {key: round(value, 6) for key, value in self.components.items()},
        }


@runtime_checkable
class UtilityScorer(Protocol):
    """The seam an alternative scoring policy must satisfy."""

    name: str

    def score(self, metrics: ExpertMetrics, *, expert_id: str) -> ExpertScore:
        """Score one expert from its measured metrics."""
        ...


class DefaultUtilityScorer:
    """The shipped scorer. See the module docstring for the formula's shape."""

    name = "default_utility_v1"

    #: Weight on measured quality gain. Above 1.0 so a genuine improvement
    #: outweighs a small reliability gap, which is the trade the prompt's own
    #: formula implies.
    quality_weight: float = 2.0

    #: Regression impact is subtracted, not divided, so a capability that helps
    #: on average and breaks something badly ranks below one that merely helps
    #: a little.
    regression_weight: float = 1.5

    def score(self, metrics: ExpertMetrics, *, expert_id: str) -> ExpertScore:
        observed = metrics.samples > 0
        quality_gain = metrics.quality_gain
        reliability = metrics.success_rate if observed else unobserved_reliability
        if reliability is None:
            reliability = unobserved_reliability
        reuse = min(1.0, metrics.usage / 20.0)
        compute_cost = metrics.mean_cost if metrics.mean_cost is not None else 0.0
        regression_impact = metrics.regression_impact

        numerator = self.quality_weight * quality_gain * reliability * reuse
        utility = numerator / (1.0 + compute_cost) - self.regression_weight * regression_impact
        return ExpertScore(
            expert_id=expert_id,
            utility=utility,
            quality_gain=quality_gain,
            reliability=reliability,
            reuse=reuse,
            compute_cost=compute_cost,
            regression_impact=regression_impact,
            observed=observed,
            components={
                "numerator": numerator,
                "cost_floor_denominator": 1.0 + compute_cost,
                "quality_weight": self.quality_weight,
                "regression_weight": self.regression_weight,
                "samples": float(metrics.samples),
            },
        )

    def score_record(self, record: ExpertRecord) -> ExpertScore:
        """Score a whole record. Convenience for callers holding records."""
        return self.score(record.metrics, expert_id=record.identity.expert_id)
