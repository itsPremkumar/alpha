from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvaluationVector:
    """
    Multi-dimensional evaluation vector f(x) for Autonomous AVO.
    Tracks configuration-specific metrics (e.g. sequence lengths, batch sizes, level action efficiencies)
    with hard correctness gating and Pareto dominance verification.
    """
    metrics: dict[str, float] = field(default_factory=dict)
    correctness: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def effective_metric(self, key: str) -> float:
        """Score is strictly 0.0 if correctness fails, regardless of measured throughput."""
        if not self.correctness:
            return 0.0
        return max(0.0, float(self.metrics.get(key, 0.0)))

    def geometric_mean(self) -> float:
        """
        Calculates the geometric mean of metrics across all evaluation configurations.
        Returns 0.0 if correctness is False or no metrics exist.
        """
        if not self.correctness or not self.metrics:
            return 0.0

        vals = [self.effective_metric(k) for k in self.metrics]
        if any(v <= 0.0 for v in vals):
            return 0.0

        # Log-space geometric mean to avoid numerical underflow/overflow
        log_sum = sum(math.log(v) for v in vals)
        return round(math.exp(log_sum / len(vals)), 4)

    def arithmetic_mean(self) -> float:
        """Arithmetic mean across evaluation dimensions."""
        if not self.correctness or not self.metrics:
            return 0.0
        vals = [self.effective_metric(k) for k in self.metrics]
        return round(sum(vals) / len(vals), 4)

    def rhae_score(
        self,
        total_levels: int,
        levels_solved: int,
        baseline_actions: int,
        agent_actions: int,
    ) -> float:
        """
        Calculates Relative Human Action Efficiency (RHAE) as defined in ARC-AGI-3:
        RHAE = 100.0 * (levels_solved / total_levels) * min(1.0, baseline_actions / agent_actions)
        """
        if not self.correctness or total_levels <= 0 or agent_actions <= 0:
            return 0.0

        completion_ratio = min(1.0, max(0.0, levels_solved / total_levels))
        action_efficiency = min(1.0, max(0.0, baseline_actions / agent_actions))
        return round(100.0 * completion_ratio * action_efficiency, 2)

    def dominates(self, other: EvaluationVector) -> bool:
        """
        Checks if this candidate Pareto-dominates another candidate.
        self dominates other iff:
          - self is correct
          - other is not correct OR
          - self is >= other on all common metrics and strictly > on at least one.
        """
        if not self.correctness:
            return False
        if not other.correctness:
            return True

        common_keys = set(self.metrics.keys()).intersection(set(other.metrics.keys()))
        if not common_keys:
            # If no common keys, fall back to comparing geometric mean
            return self.geometric_mean() > other.geometric_mean()

        strictly_greater = False
        for k in common_keys:
            v_self = self.effective_metric(k)
            v_other = other.effective_metric(k)
            if v_self < v_other:
                return False
            if v_self > v_other:
                strictly_greater = True

        return strictly_greater

    def matches_or_improves(
        self,
        other: EvaluationVector | None,
        rel_tolerance: float = 0.0,
    ) -> bool:
        """
        Enforces Autonomous AVO's Matches-or-Improves policy:
        A candidate is acceptable if:
          1. It passes correctness checks.
          2. AND it dominates the baseline OR its geometric mean matches/improves the baseline.
        """
        if not self.correctness:
            return False
        if other is None or not other.correctness:
            return True

        if self.dominates(other):
            return True

        self_gm = self.geometric_mean()
        other_gm = other.geometric_mean()
        return self_gm >= (other_gm * (1.0 - rel_tolerance))

    def to_dict(self) -> dict[str, Any]:
        return {
            "correctness": self.correctness,
            "metrics": self.metrics,
            "geometric_mean": self.geometric_mean(),
            "arithmetic_mean": self.arithmetic_mean(),
            "metadata": self.metadata,
        }
