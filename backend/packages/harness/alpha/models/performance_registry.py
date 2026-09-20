"""Empirical Model Performance Registry and Dynamic Cost/Latency Router.

Inspired by Chapters 16 and 17 of the Master Architecture Blueprint:
- Empirically tracks model performance across task types (coding, research, architecture, etc.)
- Records running statistics: success_rate, tool_success_rate, latency, cost USD, verification score
- Dynamically selects optimal model based on utility optimization and hard budget/quality thresholds
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelPerformanceMetrics:
    """Live empirical telemetry for a specific (model, task_type) pair."""
    model_id: str
    task_type: str
    sample_count: int = 0
    success_count: int = 0
    tool_success_count: int = 0
    total_tool_calls: int = 0
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    total_verification_score: float = 0.0
    last_updated: float = field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        return (self.success_count / self.sample_count) if self.sample_count > 0 else 0.0

    @property
    def tool_success_rate(self) -> float:
        return (self.tool_success_count / self.total_tool_calls) if self.total_tool_calls > 0 else 1.0

    @property
    def avg_latency_ms(self) -> float:
        return (self.total_latency_ms / self.sample_count) if self.sample_count > 0 else 0.0

    @property
    def avg_cost_usd(self) -> float:
        return (self.total_cost_usd / self.sample_count) if self.sample_count > 0 else 0.0

    @property
    def avg_verification_score(self) -> float:
        return (self.total_verification_score / self.sample_count) if self.sample_count > 0 else 0.0

    def record(
        self,
        success: bool,
        tool_successes: int = 0,
        tool_calls: int = 0,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        verification_score: float = 1.0,
    ) -> None:
        self.sample_count += 1
        if success:
            self.success_count += 1
        self.tool_success_count += tool_successes
        self.total_tool_calls += tool_calls
        self.total_latency_ms += latency_ms
        self.total_cost_usd += cost_usd
        self.total_verification_score += verification_score
        self.last_updated = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "task_type": self.task_type,
            "sample_count": self.sample_count,
            "success_rate": round(self.success_rate, 3),
            "tool_success_rate": round(self.tool_success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "avg_cost_usd": round(self.avg_cost_usd, 5),
            "avg_verification_score": round(self.avg_verification_score, 3),
            "last_updated": self.last_updated,
        }


class ModelPerformanceRegistry:
    """Registry maintaining real empirical benchmark metrics for all models."""

    def __init__(self):
        # Key: (model_id.lower(), task_type.lower()) -> ModelPerformanceMetrics
        self._metrics: dict[tuple[str, str], ModelPerformanceMetrics] = {}

    def record_run(
        self,
        model_id: str,
        task_type: str,
        success: bool,
        tool_successes: int = 0,
        tool_calls: int = 0,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        verification_score: float = 1.0,
    ) -> ModelPerformanceMetrics:
        key = (model_id.strip().lower(), task_type.strip().lower())
        if key not in self._metrics:
            self._metrics[key] = ModelPerformanceMetrics(
                model_id=model_id,
                task_type=task_type,
            )

        metrics = self._metrics[key]
        metrics.record(
            success=success,
            tool_successes=tool_successes,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            verification_score=verification_score,
        )
        return metrics

    def get_metrics(self, model_id: str, task_type: str) -> ModelPerformanceMetrics | None:
        return self._metrics.get((model_id.strip().lower(), task_type.strip().lower()))

    def list_all(self, task_type: str | None = None) -> list[dict[str, Any]]:
        if task_type:
            tt = task_type.strip().lower()
            return [m.to_dict() for (m_id, t), m in self._metrics.items() if t == tt]
        return [m.to_dict() for m in self._metrics.values()]


class DynamicCostLatencyRouter:
    """Routes requests to the Pareto-optimal model based on empirical telemetry."""

    def __init__(self, registry: ModelPerformanceRegistry):
        self.registry: ModelPerformanceRegistry = registry

    def select_optimal_model(
        self,
        task_type: str,
        candidate_models: list[str],
        max_cost_usd: float | None = None,
        max_latency_ms: float | None = None,
        min_success_rate: float = 0.7,
        weight_quality: float = 0.5,
        weight_cost: float = 0.3,
        weight_latency: float = 0.2,
    ) -> dict[str, Any]:
        """Rank candidates by multi-attribute utility: U = w_q*Q - w_c*C - w_l*L."""
        candidates_evaluated: list[dict[str, Any]] = []

        for m_id in candidate_models:
            metrics = self.registry.get_metrics(m_id, task_type)
            if not metrics or metrics.sample_count == 0:
                # Default optimistic prior for unobserved models
                s_rate = 0.85
                v_score = 0.85
                c_usd = 0.005
                l_ms = 1500.0
                samples = 0
            else:
                s_rate = metrics.success_rate
                v_score = metrics.avg_verification_score
                c_usd = metrics.avg_cost_usd
                l_ms = metrics.avg_latency_ms
                samples = metrics.sample_count

            # Filter hard constraints
            if s_rate < min_success_rate and samples >= 3:
                continue
            if max_cost_usd is not None and c_usd > max_cost_usd and samples >= 3:
                continue
            if max_latency_ms is not None and l_ms > max_latency_ms and samples >= 3:
                continue

            # Normalized penalty terms (assuming max baseline cost $0.05, max latency 10000ms)
            norm_cost = min(c_usd / 0.05, 1.0)
            norm_latency = min(l_ms / 10000.0, 1.0)

            # Combined quality score: weighted mix of verification score and task success rate
            quality = 0.6 * v_score + 0.4 * s_rate

            utility = (weight_quality * quality) - (weight_cost * norm_cost) - (weight_latency * norm_latency)

            candidates_evaluated.append({
                "model_id": m_id,
                "utility": round(utility, 4),
                "success_rate": round(s_rate, 3),
                "verification_score": round(v_score, 3),
                "avg_cost_usd": round(c_usd, 5),
                "avg_latency_ms": round(l_ms, 1),
                "sample_count": samples,
            })

        if not candidates_evaluated:
            # Fallback to first candidate if all filtered out
            fallback = candidate_models[0] if candidate_models else "default"
            return {
                "selected_model": fallback,
                "reason": "Fallback: all candidate models filtered out by constraints",
                "ranked_candidates": [],
            }

        # Sort descending by utility
        candidates_evaluated.sort(key=lambda x: x["utility"], reverse=True)
        best = candidates_evaluated[0]

        return {
            "selected_model": best["model_id"],
            "utility_score": best["utility"],
            "reason": f"Empirical Pareto-optimal choice for task '{task_type}'",
            "ranked_candidates": candidates_evaluated,
        }
