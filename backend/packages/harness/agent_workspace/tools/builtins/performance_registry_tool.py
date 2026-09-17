"""Built-in Model Performance Registry and Dynamic Router LangChain Tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.models.performance_registry import (
    DynamicCostLatencyRouter,
    ModelPerformanceRegistry,
)

_GLOBAL_REGISTRY = ModelPerformanceRegistry()
_GLOBAL_ROUTER = DynamicCostLatencyRouter(registry=_GLOBAL_REGISTRY)


@tool("manage_model_performance_registry", parse_docstring=True)
def manage_model_performance_registry(
    action: str,
    model_id: str = "",
    task_type: str = "coding",
    success: bool = True,
    tool_successes: int = 1,
    tool_calls: int = 1,
    latency_ms: float = 1200.0,
    cost_usd: float = 0.005,
    verification_score: float = 1.0,
    candidate_models_csv: str = "",
    max_cost_usd: float | None = None,
    max_latency_ms: float | None = None,
    min_success_rate: float = 0.7,
) -> str:
    """Manage empirical model benchmarks and dynamically route tasks to Pareto-optimal models.

    Args:
        action: 'record_run', 'get_metrics', 'list_all', 'select_optimal_model'.
        model_id: Identifier of model (e.g. 'gpt-4o', 'claude-3-7-sonnet', 'deepseek-r1').
        task_type: Type of task ('coding', 'research', 'architecture', 'quick_edit').
        success: Whether the task succeeded.
        tool_successes: Number of successful tool calls.
        tool_calls: Total tool calls made.
        latency_ms: Wall-clock latency in milliseconds.
        cost_usd: Total dollar cost of the run.
        verification_score: Verification score between 0.0 and 1.0.
        candidate_models_csv: Comma-separated list of candidate models to evaluate for routing.
        max_cost_usd: Hard maximum cost constraint for model selection.
        max_latency_ms: Hard maximum latency constraint for model selection.
        min_success_rate: Minimum acceptable empirical success rate.
    """
    try:
        if action == "record_run":
            metrics = _GLOBAL_REGISTRY.record_run(
                model_id=model_id,
                task_type=task_type,
                success=success,
                tool_successes=tool_successes,
                tool_calls=tool_calls,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                verification_score=verification_score,
            )
            return json.dumps({"status": "recorded", "metrics": metrics.to_dict()}, indent=2)

        elif action == "get_metrics":
            m = _GLOBAL_REGISTRY.get_metrics(model_id, task_type)
            if not m:
                return json.dumps({"status": "not_found", "model_id": model_id, "task_type": task_type}, indent=2)
            return json.dumps(m.to_dict(), indent=2)

        elif action == "list_all":
            all_m = _GLOBAL_REGISTRY.list_all(task_type=task_type if task_type else None)
            return json.dumps(all_m, indent=2)

        elif action == "select_optimal_model":
            candidates = [c.strip() for c in candidate_models_csv.split(",") if c.strip()]
            if not candidates:
                candidates = ["gpt-4o", "claude-3-7-sonnet", "deepseek-r1", "kimi-highspeed"]
            choice = _GLOBAL_ROUTER.select_optimal_model(
                task_type=task_type,
                candidate_models=candidates,
                max_cost_usd=max_cost_usd,
                max_latency_ms=max_latency_ms,
                min_success_rate=min_success_rate,
            )
            return json.dumps(choice, indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error managing model registry: {exc}"
