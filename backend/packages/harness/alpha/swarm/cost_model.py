"""Calibrated cost projection from Alpha's own recorded telemetry.

**The gap this closes.** ``SwarmTelemetry.cost_projection`` projects wall-clock
only: ``projected_tokens`` and ``projected_tool_calls`` are ``None`` because no
per-task token or tool-call estimator exists anywhere in the tree, and
``budget_pressure`` is a *post-spend* ratio.  Neither answers the question that
actually decides whether to run: how many tokens and dollars will this
decomposition spend?  This module supplies that answer from the only evidence
Alpha is entitled to trust -- its own completed tasks, as recorded by the swarm
runner (``token_usage``, ``tool_calls``, capability tags, ``model_override``) --
with honest intervals and sample sizes, never a hardcoded constant.

Four rules hold here:

- **Cold start is unmeasured, not zero.**  With no usable history the method is
  ``insufficient_history`` and every figure is ``None`` -- never ``0`` and never
  an invented estimate.  A plan nobody measured must not read as free.
- **Classes relax, and the relaxation is disclosed.**  A task's sample class is
  chosen by the first tier with at least ``MIN_CLASS_SAMPLES`` members:
  capability tags -> prompt-size bucket -> model -> all completed tasks.  Every
  per-task row names the tier it used, so a global fallback is visible rather
  than hidden.
- **A partial figure is labelled partial.**  When a metric could only be summed
  over a subset of the projected tasks, the value is a lower bound whose metric
  name lands in ``partial_metrics``; ``budget_verdict`` refuses only on complete
  projections, because a lower bound must not refuse work.
- **USD needs the operator's price.**  Prices come from
  ``config.yaml -> model_pricing`` only; one priced model is not a price for
  another.  A model without a declared price yields ``None`` for the whole USD
  figure (a partial sum would understate spend, which is worse than no number)
  with the unpriced model named.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alpha.swarm.models import SwarmPlan, SwarmTaskNode, TaskNodeState

COST_MODEL_BASIS = "empirical_task_percentiles_v1"
INSUFFICIENT_HISTORY = "insufficient_history"
MIN_CLASS_SAMPLES = 3
COST_REFUSAL_MULTIPLE = 2.0
MAX_HISTORY_SAMPLES = 500
_SIZE_BUCKET_BOUNDS = (200, 1000)

_BASIS_NOTE = "per-task nearest-rank percentiles over completed tasks Alpha recorded itself; classes relax from capability tags to prompt-size bucket to model to all tasks; USD requires config.yaml -> model_pricing for every projected task"


@dataclass(frozen=True)
class TaskCostSample:
    """One completed task's measured cost, as the runner recorded it."""

    tokens: int | None
    input_tokens: int | None
    output_tokens: int | None
    tool_calls: int | None
    capability_tags: frozenset[str]
    model: str | None
    size_bucket: int


def _size_bucket(objective: str) -> int:
    length = len(objective or "")
    return sum(1 for bound in _SIZE_BUCKET_BOUNDS if length >= bound)


def _sample_from_task(task: SwarmTaskNode) -> TaskCostSample | None:
    if task.state != TaskNodeState.COMPLETED:
        return None
    usage = task.token_usage if isinstance(task.token_usage, Mapping) else {}
    if not usage:
        # No recorded usage means the runner never metered this task (e.g. an
        # external ``complete_task``); a sample built from it would only
        # contribute zeros nobody measured.
        return None

    def _count(key: str) -> int | None:
        value = usage.get(key)
        if value is None:
            return None
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    input_tokens = _count("input_tokens")
    output_tokens = _count("output_tokens")
    tokens = None
    if input_tokens is not None or output_tokens is not None:
        tokens = (input_tokens or 0) + (output_tokens or 0)
    try:
        tool_calls: int | None = max(0, int(task.tool_calls or 0))
    except (TypeError, ValueError):
        tool_calls = None
    tags = frozenset(str(tag).strip().lower() for tag in (task.capability_tags or []) if str(tag).strip())
    model = str(task.model_override).strip() if task.model_override else None
    return TaskCostSample(
        tokens=tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=tool_calls,
        capability_tags=tags,
        model=model,
        size_bucket=_size_bucket(task.objective),
    )


def samples_from_plans(plans: Iterable[SwarmPlan], *, limit: int = MAX_HISTORY_SAMPLES) -> list[TaskCostSample]:
    """Collect measured task samples from recorded plans, newest kept.

    Plans are ordered by ``created_at`` (stable for ties), each plan's tasks in
    ``task_id`` order, and the *most recent* ``limit`` samples survive so a
    long-lived registry cannot make projection cost grow without bound.  Only
    completed tasks with a recorded ``token_usage`` qualify; an in-flight or
    failed task is not a measurement of cost.
    """

    ordered = sorted(plans, key=lambda plan: str(getattr(plan, "created_at", "") or ""))
    samples: list[TaskCostSample] = []
    for plan in ordered:
        for task in sorted(plan.tasks.values(), key=lambda item: item.task_id):
            sample = _sample_from_task(task)
            if sample is not None:
                samples.append(sample)
    if limit > 0:
        return samples[-limit:]
    return samples


def _percentile(values: Sequence[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[min(rank, len(ordered) - 1)]


def _class_samples(
    task: SwarmTaskNode,
    samples: Sequence[TaskCostSample],
    *,
    default_model: str | None,
) -> tuple[list[TaskCostSample], str] | None:
    """The first relaxation tier with enough members, or ``None``."""

    if not samples:
        return None
    task_tags = frozenset(str(tag).strip().lower() for tag in (task.capability_tags or []) if str(tag).strip())
    model = str(task.model_override).strip() if task.model_override else (default_model or None)
    tiers: list[tuple[str, list[TaskCostSample]]] = []
    if task_tags:
        tiers.append(("capability_tags", [sample for sample in samples if sample.capability_tags & task_tags]))
    tiers.append(("prompt_size", [sample for sample in samples if sample.size_bucket == _size_bucket(task.objective)]))
    if model:
        tiers.append(("model", [sample for sample in samples if sample.model == model]))
    tiers.append(("all_tasks", list(samples)))
    for tier_name, matched in tiers:
        if len(matched) >= MIN_CLASS_SAMPLES:
            return matched, tier_name
    return None


def _confidence(sample_size: int | None) -> str | None:
    if sample_size is None:
        return None
    if sample_size >= 10:
        return "high"
    if sample_size >= 5:
        return "medium"
    if sample_size >= MIN_CLASS_SAMPLES:
        return "low"
    return None


def project_plan_costs(
    plan: SwarmPlan,
    samples: Sequence[TaskCostSample],
    *,
    pricing: Mapping[str, tuple[float, float]] | None = None,
    default_model: str | None = None,
) -> dict[str, Any]:
    """Project a plan's token, tool-call and USD cost from recorded history.

    Returns a payload that separates *what was measured* (``sample_size``,
    ``coverage``, ``confidence``, per-task tier rows) from *what is claimed*
    (the p50/p90 sums).  A task with no usable class is disclosed by task_id; a
    metric that could only be summed over a subset of projected tasks is named
    in ``partial_metrics`` and its value is a labelled lower bound.
    """

    tasks = sorted(plan.tasks.values(), key=lambda item: item.task_id)
    per_task: list[dict[str, Any]] = []
    projected = 0
    contributing: dict[str, int] = {"tokens": 0, "tool_calls": 0}
    tokens_p50_sum = tokens_p90_sum = 0
    calls_p50_sum = calls_p90_sum = 0
    usd_p50_sum = usd_p90_sum = 0.0
    usd_complete = True
    class_sizes: list[int] = []
    usd_unpriced: set[str] = set()
    for task in tasks:
        selected = _class_samples(task, samples, default_model=default_model)
        if selected is None:
            per_task.append({"task_id": task.task_id, "class": None, "sample_size": 0, "note": "no usable history class"})
            continue
        matched, tier = selected
        projected += 1
        class_sizes.append(len(matched))
        tokens = [sample.tokens for sample in matched if sample.tokens is not None]
        calls = [sample.tool_calls for sample in matched if sample.tool_calls is not None]
        inputs = [sample.input_tokens for sample in matched if sample.input_tokens is not None]
        outputs = [sample.output_tokens for sample in matched if sample.output_tokens is not None]
        task_tokens_p50 = _percentile(tokens, 0.5)
        task_tokens_p90 = _percentile(tokens, 0.9)
        task_calls_p50 = _percentile(calls, 0.5)
        task_calls_p90 = _percentile(calls, 0.9)
        if task_tokens_p50 is not None:
            tokens_p50_sum += task_tokens_p50
            contributing["tokens"] += 1
        if task_tokens_p90 is not None:
            tokens_p90_sum += task_tokens_p90
        if task_calls_p50 is not None:
            calls_p50_sum += task_calls_p50
            contributing["tool_calls"] += 1
        if task_calls_p90 is not None:
            calls_p90_sum += task_calls_p90
        model = str(task.model_override).strip() if task.model_override else (default_model or None)
        price = None
        if pricing and model:
            price = pricing.get(str(model))
        task_usd_p50 = task_usd_p90 = None
        input_p50, input_p90 = _percentile(inputs, 0.5), _percentile(inputs, 0.9)
        output_p50, output_p90 = _percentile(outputs, 0.5), _percentile(outputs, 0.9)
        if price is not None and input_p50 is not None and output_p50 is not None:
            task_usd_p50 = (input_p50 * price[0] + output_p50 * price[1]) / 1_000_000
            if input_p90 is not None and output_p90 is not None:
                task_usd_p90 = (input_p90 * price[0] + output_p90 * price[1]) / 1_000_000
        if task_usd_p50 is None or task_usd_p90 is None:
            usd_complete = False
            if model and price is None:
                usd_unpriced.add(str(model))
        else:
            usd_p50_sum += task_usd_p50
            usd_p90_sum += task_usd_p90
        per_task.append(
            {
                "task_id": task.task_id,
                "class": tier,
                "sample_size": len(matched),
                "tokens_p50": task_tokens_p50,
                "tokens_p90": task_tokens_p90,
                "tool_calls_p50": task_calls_p50,
                "tool_calls_p90": task_calls_p90,
                "usd_p50": None if task_usd_p50 is None else round(task_usd_p50, 6),
                "usd_p90": None if task_usd_p90 is None else round(task_usd_p90, 6),
            }
        )

    coverage = {"tasks": len(tasks), "projected": projected}
    if projected == 0:
        return {
            "estimate_method": INSUFFICIENT_HISTORY,
            "basis": _BASIS_NOTE,
            "sample_size": 0,
            "samples_scanned": len(samples),
            "coverage": coverage,
            "confidence": None,
            "projected_tokens_p50": None,
            "projected_tokens_p90": None,
            "projected_tool_calls_p50": None,
            "projected_tool_calls_p90": None,
            "projected_usd_p50": None,
            "projected_usd_p90": None,
            "partial_metrics": [],
            "usd_unpriced_models": sorted(usd_unpriced),
            "per_task": per_task,
            "warnings": ["no completed task history matched this plan; projections stay unmeasured (None) until recorded runs accumulate"],
        }

    partial_metrics = [metric for metric in ("tokens", "tool_calls") if 0 < contributing[metric] < projected]
    warnings: list[str] = []
    for metric in ("tokens", "tool_calls"):
        if contributing[metric] == 0:
            warnings.append(f"none of the {projected} projected task(s) had recorded {metric}; the figure is unmeasured")
        elif contributing[metric] < projected:
            warnings.append(f"{contributing[metric]} of {projected} projected task(s) contributed {metric}; the figure is a lower bound")
    sample_size = min(class_sizes) if class_sizes else None
    return {
        "estimate_method": COST_MODEL_BASIS,
        "basis": _BASIS_NOTE,
        "sample_size": sample_size,
        "samples_scanned": len(samples),
        "coverage": coverage,
        "confidence": _confidence(sample_size),
        "projected_tokens_p50": tokens_p50_sum if contributing["tokens"] else None,
        "projected_tokens_p90": tokens_p90_sum if contributing["tokens"] else None,
        "projected_tool_calls_p50": calls_p50_sum if contributing["tool_calls"] else None,
        "projected_tool_calls_p90": calls_p90_sum if contributing["tool_calls"] else None,
        "projected_usd_p50": round(usd_p50_sum, 6) if usd_complete else None,
        "projected_usd_p90": round(usd_p90_sum, 6) if usd_complete else None,
        "partial_metrics": partial_metrics,
        "usd_unpriced_models": sorted(usd_unpriced),
        "per_task": per_task,
        "warnings": warnings,
    }


def budget_verdict(projection: Mapping[str, Any] | None, budget: Any) -> dict[str, Any]:
    """Compare a calibrated projection against the plan's declared ceilings.

    The policy is explicit and self-consistent, like the wall-clock refusal it
    sits beside: a p90 at or below the declared ceiling is silent; above the
    ceiling it **warns**; above ``COST_REFUSAL_MULTIPLE`` times the ceiling it
    **refuses**.  Refusal requires a complete, non-partial projection -- a lower
    bound must not refuse work, and a cold start (``insufficient_history``) has
    nothing to compare.  No config key: the check cannot drift from the budget
    it checks.
    """

    warnings: list[str] = []
    refusal: str | None = None
    if not isinstance(projection, Mapping) or projection.get("estimate_method") != COST_MODEL_BASIS:
        return {"warnings": warnings, "refusal": refusal}
    partial = {str(item) for item in (projection.get("partial_metrics") or [])}
    coverage = projection.get("coverage") if isinstance(projection.get("coverage"), Mapping) else {}
    tasks = coverage.get("tasks") if isinstance(coverage.get("tasks"), int) else None
    projected = coverage.get("projected") if isinstance(coverage.get("projected"), int) else None
    complete = tasks is not None and projected is not None and tasks > 0 and tasks == projected

    max_tokens = getattr(budget, "max_tokens", None)
    tokens_p90 = projection.get("projected_tokens_p90")
    if max_tokens is not None and isinstance(tokens_p90, (int, float)) and not isinstance(tokens_p90, bool):
        over = float(tokens_p90) > float(max_tokens)
        if over and complete and "tokens" not in partial and float(tokens_p90) > COST_REFUSAL_MULTIPLE * float(max_tokens):
            refusal = (
                f"calibrated cost projection p90 ({int(tokens_p90):,} tokens over {projected} task(s), class of "
                f"{projection.get('sample_size')} recorded task(s)) exceeds {COST_REFUSAL_MULTIPLE:g}x the declared "
                f"max_tokens ({int(max_tokens):,}); raise max_tokens or reduce/split the goal"
            )
        elif over:
            suffix = " (lower bound: not every projected task contributed)" if "tokens" in partial else ""
            warnings.append(f"calibrated cost projection p90 ({int(tokens_p90):,} tokens) exceeds the declared max_tokens ({int(max_tokens):,}){suffix}")

    max_calls = getattr(budget, "max_tool_calls", None)
    calls_p90 = projection.get("projected_tool_calls_p90")
    if refusal is None and max_calls is not None and isinstance(calls_p90, (int, float)) and not isinstance(calls_p90, bool):
        over = float(calls_p90) > float(max_calls)
        if over and complete and "tool_calls" not in partial and float(calls_p90) > COST_REFUSAL_MULTIPLE * float(max_calls):
            refusal = (
                f"calibrated cost projection p90 ({int(calls_p90)} tool calls over {projected} task(s), class of "
                f"{projection.get('sample_size')} recorded task(s)) exceeds {COST_REFUSAL_MULTIPLE:g}x the declared "
                f"max_tool_calls ({int(max_calls)}); raise max_tool_calls or reduce/split the goal"
            )
        elif over:
            suffix = " (lower bound: not every projected task contributed)" if "tool_calls" in partial else ""
            warnings.append(f"calibrated cost projection p90 ({int(calls_p90)} tool calls) exceeds the declared max_tool_calls ({int(max_calls)}){suffix}")
    return {"warnings": warnings, "refusal": refusal}


def resolve_pricing() -> tuple[dict[str, tuple[float, float]], str | None]:
    """Read the operator's declared fallback prices and default model.

    Returns ``({}, None)`` when the config cannot be read: routing/pricing is
    advisory, and an unreadable config yields *no* USD figure rather than a
    guessed one.  Only ``model_pricing`` is consulted -- no prices are
    hardcoded anywhere in this module.
    """

    try:
        from alpha.config import get_app_config

        config = get_app_config()
    except Exception:
        return {}, None
    table: dict[str, tuple[float, float]] = {}
    raw = getattr(config, "model_pricing", None)
    if isinstance(raw, Mapping):
        for name, entry in raw.items():
            try:
                if isinstance(entry, Mapping):
                    input_price = float(entry.get("input"))
                    output_price = float(entry.get("output"))
                else:
                    input_price = float(entry.input)
                    output_price = float(entry.output)
            except (AttributeError, TypeError, ValueError):
                continue
            table[str(name)] = (input_price, output_price)
    default_model = getattr(config, "default_model_name", None)
    return table, (str(default_model) if default_model else None)
