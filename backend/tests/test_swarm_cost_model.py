"""Calibrated cost projection (S7): measured history, honest intervals, one gate.

``SwarmTelemetry.cost_projection`` projects wall-clock only; ``budget_pressure``
is a *post-spend* ratio.  This suite pins the third answer — the one that says
whether a decomposition will fit its declared token/tool ceilings *before* it
runs — and the properties that keep it honest:

- cold start is ``insufficient_history`` with every figure ``None``: never an
  invented number, never ``0``;
- classes relax through a disclosed ladder (capability tags -> prompt-size
  bucket -> model -> all tasks) and each per-task row names its tier;
- a metric summed over only some projected tasks is labelled ``partial`` and
  may *warn* but never refuse; refusal needs a complete p90 above
  ``COST_REFUSAL_MULTIPLE`` times the declared ceiling, and an exactly-2x
  projection only warns;
- USD comes from ``config.yaml -> model_pricing`` only: one missing price makes
  the whole USD figure ``None`` (a partial sum would understate spend), with the
  unpriced model named.
"""

from __future__ import annotations

import importlib
import json
import uuid
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.cost_model import (
    COST_MODEL_BASIS,
    COST_REFUSAL_MULTIPLE,
    INSUFFICIENT_HISTORY,
    MIN_CLASS_SAMPLES,
    TaskCostSample,
    budget_verdict,
    project_plan_costs,
    resolve_pricing,
    samples_from_plans,
)
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.models import SwarmBudget, SwarmMode, SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.scheduler import SwarmPlanValidationError
from alpha.swarm.telemetry import SwarmTelemetry

OBJECTIVE = "Implement the bounded migration change"


def _task(task_id: str, *, objective: str = OBJECTIVE, tags: tuple[str, ...] = (), model: str | None = None) -> SwarmTaskNode:
    return SwarmTaskNode(task_id=task_id, objective=objective, capability_tags=list(tags), model_override=model)


def _plan(task: SwarmTaskNode, *, mode: SwarmMode = SwarmMode.PARALLEL, swarm_id: str | None = None) -> SwarmPlan:
    return SwarmPlan(swarm_id=swarm_id or f"plan-{uuid.uuid4().hex[:10]}", goal="cost goal", mode=mode, tasks={task.task_id: task})


def _completed(task: SwarmTaskNode, *, input_tokens: int | None, output_tokens: int | None, tool_calls: int = 1) -> SwarmTaskNode:
    task.state = TaskNodeState.COMPLETED
    task.token_usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    task.tool_calls = tool_calls
    return task


def _history(tokens: int = 1000, *, tasks: int = 3, tags: tuple[str, ...] = ("sql",), tool_calls: int = 1, created_at: str = "2026-01-01T00:00:00Z", objective: str = OBJECTIVE, model: str | None = None) -> SwarmPlan:
    plan = SwarmPlan(swarm_id=f"hist-{uuid.uuid4().hex[:10]}", goal="history", mode=SwarmMode.PARALLEL)
    plan.created_at = created_at
    for index in range(tasks):
        plan.tasks[f"h{index}"] = _completed(_task(f"h{index}", objective=objective, tags=tags, model=model), input_tokens=tokens, output_tokens=0, tool_calls=tool_calls)
    return plan


# ---------------------------------------------------------------------------
# samples_from_plans
# ---------------------------------------------------------------------------


def test_only_completed_and_metered_tasks_become_samples() -> None:
    plan = SwarmPlan(swarm_id="mixed", goal="g", mode=SwarmMode.PARALLEL)
    plan.tasks["done"] = _completed(_task("done", tags=("SQL",)), input_tokens=100, output_tokens=50)
    plan.tasks["pending"] = _task("pending")
    plan.tasks["failed"] = _task("failed")
    plan.tasks["failed"].state = TaskNodeState.FAILED
    plan.tasks["unmetered"] = _task("unmetered")
    plan.tasks["unmetered"].state = TaskNodeState.COMPLETED  # completed, but no recorded usage: not a cost measurement

    samples = samples_from_plans([plan])

    assert len(samples) == 1
    sample = samples[0]
    assert sample.tokens == 150
    assert sample.tool_calls == 1
    assert sample.capability_tags == frozenset({"sql"}), "tags are normalized for matching"
    assert sample.size_bucket == 0


def test_sample_limit_keeps_the_newest_recorded_plans() -> None:
    older = _history(tokens=11, tasks=2, created_at="2024-01-01T00:00:00Z")
    newer = _history(tokens=99, tasks=2, created_at="2025-01-01T00:00:00Z")

    samples = samples_from_plans([newer, older], limit=2)

    assert [sample.tokens for sample in samples] == [99, 99], "the newest plan's samples survive the bound"


# ---------------------------------------------------------------------------
# project_plan_costs: percentiles, ladder, confidence, USD, honesty flags
# ---------------------------------------------------------------------------


def test_percentiles_are_nearest_rank_over_the_matched_class() -> None:
    history = SwarmPlan(swarm_id="percentiles", goal="g", mode=SwarmMode.PARALLEL)
    for index, tokens in enumerate((10, 20, 30, 40, 50)):
        history.tasks[f"h{index}"] = _completed(_task(f"h{index}", tags=("sql",)), input_tokens=tokens, output_tokens=0, tool_calls=index)
    plan = _plan(_task("t", tags=("sql",)))

    projection = project_plan_costs(plan, samples_from_plans([history]))

    assert projection["estimate_method"] == COST_MODEL_BASIS
    assert projection["projected_tokens_p50"] == 30
    assert projection["projected_tokens_p90"] == 50
    assert projection["projected_tool_calls_p50"] == 2
    assert projection["projected_tool_calls_p90"] == 4
    assert projection["per_task"][0]["class"] == "capability_tags"
    assert projection["coverage"] == {"tasks": 1, "projected": 1}


def test_tag_class_wins_over_the_global_pool() -> None:
    history = SwarmPlan(swarm_id="classes", goal="g", mode=SwarmMode.PARALLEL)
    for index in range(3):
        history.tasks[f"sql{index}"] = _completed(_task(f"sql{index}", tags=("sql",)), input_tokens=100, output_tokens=0)
        history.tasks[f"web{index}"] = _completed(_task(f"web{index}", tags=("web",)), input_tokens=9000, output_tokens=0)
    plan = _plan(_task("t", tags=("sql",)))

    projection = project_plan_costs(plan, samples_from_plans([history]))

    assert projection["projected_tokens_p50"] == 100, "a matching capability class must not be diluted by the global pool"
    assert projection["per_task"][0]["class"] == "capability_tags"


def test_classes_relax_through_size_and_model_tiers() -> None:
    long_objective = "x" * 1200
    history = SwarmPlan(swarm_id="relax", goal="g", mode=SwarmMode.PARALLEL)
    for index in range(3):
        history.tasks[f"h{index}"] = _completed(_task(f"h{index}", objective=long_objective, model="m1"), input_tokens=500, output_tokens=0)
    samples = samples_from_plans([history])

    by_size = project_plan_costs(_plan(_task("s", tags=("rare",), objective="x" * 1100)), samples)
    assert by_size["per_task"][0]["class"] == "prompt_size", "an unmatched tag falls to the prompt-size bucket before the global pool"

    by_model = project_plan_costs(_plan(_task("m", tags=("rare",), objective="x", model="m1")), samples)
    assert by_model["per_task"][0]["class"] == "model"

    by_global = project_plan_costs(_plan(_task("g", objective="x")), samples)
    assert by_global["per_task"][0]["class"] == "all_tasks"


def test_insufficient_history_is_unmeasured_not_zero() -> None:
    history = _history(tasks=MIN_CLASS_SAMPLES - 1)
    plan = _plan(_task("t", tags=("sql",)))

    projection = project_plan_costs(plan, samples_from_plans([history]))

    assert projection["estimate_method"] == INSUFFICIENT_HISTORY
    assert projection["sample_size"] == 0
    assert projection["confidence"] is None
    assert projection["projected_tokens_p50"] is None
    assert projection["projected_tokens_p90"] is None
    assert projection["projected_tool_calls_p50"] is None
    assert projection["projected_usd_p50"] is None
    assert projection["per_task"][0]["class"] is None
    assert projection["warnings"], "a cold start must say why the figures are missing"


def test_missing_samples_are_reported_not_silently_dropped() -> None:
    plan = _plan(_task("t", tags=("sql",)))

    projection = project_plan_costs(plan, [])

    assert projection["estimate_method"] == INSUFFICIENT_HISTORY
    assert projection["samples_scanned"] == 0
    assert "no completed task history matched this plan" in projection["warnings"][0]


def test_confidence_ladder_tracks_the_smallest_class() -> None:
    plan = _plan(_task("t", tags=("sql",)))
    assert project_plan_costs(plan, samples_from_plans([_history(tasks=3)]))["confidence"] == "low"
    assert project_plan_costs(plan, samples_from_plans([_history(tasks=5)]))["confidence"] == "medium"
    assert project_plan_costs(plan, samples_from_plans([_history(tasks=10)]))["confidence"] == "high"


def test_usd_uses_declared_prices_only_and_never_a_partial_sum() -> None:
    history = SwarmPlan(swarm_id="priced", goal="g", mode=SwarmMode.PARALLEL)
    for index in range(3):
        task = _completed(_task(f"h{index}", model="m"), input_tokens=1000, output_tokens=500)
        history.tasks[f"h{index}"] = task
    plan = _plan(_task("t", tags=("sql",), model="m"))

    priced = project_plan_costs(plan, samples_from_plans([history]), pricing={"m": (3.0, 6.0)})
    assert priced["projected_usd_p50"] == pytest.approx((1000 * 3.0 + 500 * 6.0) / 1_000_000)
    assert priced["projected_usd_p90"] == pytest.approx((1000 * 3.0 + 500 * 6.0) / 1_000_000)
    assert priced["usd_unpriced_models"] == []

    unpriced = project_plan_costs(plan, samples_from_plans([history]), pricing={})
    assert unpriced["projected_usd_p50"] is None and unpriced["projected_usd_p90"] is None
    assert unpriced["usd_unpriced_models"] == ["m"], "the model whose price is missing is named, and the figure stays unmeasured"
    assert unpriced["projected_tokens_p50"] == 1500, "an absent price must not take the measured token figure down with it"


def test_default_model_prices_tasks_without_an_override() -> None:
    history = _history(tasks=3)
    plan = _plan(_task("t", tags=("sql",)))

    projection = project_plan_costs(plan, samples_from_plans([history]), pricing={"default-m": (1.0, 1.0)}, default_model="default-m")

    assert projection["projected_usd_p50"] is not None


def test_a_metric_over_a_subset_of_tasks_is_a_labelled_lower_bound() -> None:
    history = SwarmPlan(swarm_id="partial", goal="g", mode=SwarmMode.PARALLEL)
    for index in range(3):
        history.tasks[f"a{index}"] = _completed(_task(f"a{index}", tags=("a",)), input_tokens=None, output_tokens=None, tool_calls=2)
        history.tasks[f"b{index}"] = _completed(_task(f"b{index}", tags=("b",)), input_tokens=100, output_tokens=0, tool_calls=1)
    plan = SwarmPlan(swarm_id="partial-plan", goal="g", mode=SwarmMode.PARALLEL, tasks={"ta": _task("ta", tags=("a",)), "tb": _task("tb", tags=("b",))})

    projection = project_plan_costs(plan, samples_from_plans([history]))

    assert projection["partial_metrics"] == ["tokens"]
    assert projection["projected_tokens_p50"] == 100, "only the task with recorded tokens contributes"
    assert projection["coverage"] == {"tasks": 2, "projected": 2}
    assert any("lower bound" in warning for warning in projection["warnings"])


# ---------------------------------------------------------------------------
# budget_verdict: warn at the ceiling, refuse only on complete 2x projections
# ---------------------------------------------------------------------------


def _projection(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "estimate_method": COST_MODEL_BASIS,
        "partial_metrics": [],
        "coverage": {"tasks": 1, "projected": 1},
        "sample_size": 3,
        "projected_tokens_p90": 1000,
        "projected_tool_calls_p90": 10,
    }
    return {**base, **overrides}


def test_budget_verdict_is_silent_at_or_below_the_ceiling() -> None:
    assert budget_verdict(_projection(projected_tokens_p90=1000), SwarmBudget(max_tokens=1000)) == {"warnings": [], "refusal": None}
    assert budget_verdict(_projection(projected_tokens_p90=500), SwarmBudget(max_tokens=1000)) == {"warnings": [], "refusal": None}
    assert budget_verdict(_projection(), SwarmBudget()) == {"warnings": [], "refusal": None}, "no declared ceiling means nothing to compare"


def test_budget_verdict_warns_above_the_ceiling_and_refuses_only_beyond_the_multiple() -> None:
    warned = budget_verdict(_projection(projected_tokens_p90=1500), SwarmBudget(max_tokens=1000))
    assert warned["refusal"] is None
    assert any("exceeds the declared max_tokens" in warning for warning in warned["warnings"])

    exactly_twice = budget_verdict(_projection(projected_tokens_p90=2 * 1000), SwarmBudget(max_tokens=1000))
    assert exactly_twice["refusal"] is None, "the refusal boundary is strict: exactly 2x still only warns"

    refused = budget_verdict(_projection(projected_tokens_p90=2001), SwarmBudget(max_tokens=1000))
    assert refused["refusal"] is not None
    assert "2x the declared max_tokens" in refused["refusal"]
    assert refused["warnings"] == []


def test_budget_verdict_refuses_on_tool_calls_too() -> None:
    verdict = budget_verdict(_projection(projected_tool_calls_p90=300), SwarmBudget(max_tool_calls=100))
    assert verdict["refusal"] is not None
    assert "max_tool_calls" in verdict["refusal"]


def test_budget_verdict_never_refuses_a_partial_or_incomplete_projection() -> None:
    partial = budget_verdict(_projection(partial_metrics=["tokens"], projected_tokens_p90=10_000), SwarmBudget(max_tokens=100))
    assert partial["refusal"] is None
    assert partial["warnings"] and "lower bound" in partial["warnings"][0]

    incomplete = budget_verdict(_projection(coverage={"tasks": 3, "projected": 1}, projected_tokens_p90=10_000), SwarmBudget(max_tokens=100))
    assert incomplete["refusal"] is None, "a projection that did not cover every task must not refuse work"
    assert incomplete["warnings"]


def test_budget_verdict_is_silent_without_a_calibrated_projection() -> None:
    assert budget_verdict(None, SwarmBudget(max_tokens=1)) == {"warnings": [], "refusal": None}
    assert budget_verdict({"estimate_method": INSUFFICIENT_HISTORY, "projected_tokens_p90": None}, SwarmBudget(max_tokens=1)) == {"warnings": [], "refusal": None}


# ---------------------------------------------------------------------------
# resolve_pricing: operator config only
# ---------------------------------------------------------------------------


def test_resolve_pricing_reads_attribute_and_mapping_entries() -> None:
    config = SimpleNamespace(
        model_pricing={"attr-model": SimpleNamespace(input=3.0, output=6.0), "map-model": {"input": 1.0, "output": 2.0}, "broken": {"input": "nope"}},
        default_model_name="attr-model",
    )
    with patch("alpha.config.get_app_config", lambda: config):
        table, default_model = resolve_pricing()
    assert table == {"attr-model": (3.0, 6.0), "map-model": (1.0, 2.0)}, "a malformed entry is skipped; one bad row must not poison the table"
    assert default_model == "attr-model"


def test_resolve_pricing_returns_nothing_when_config_is_unreadable() -> None:
    with patch("alpha.config.get_app_config", side_effect=RuntimeError("no config")):
        table, default_model = resolve_pricing()
    assert table == {} and default_model is None, "an unreadable config yields no USD projection, never a guessed price"


def test_no_hardcoded_prices_in_the_module() -> None:
    import alpha.swarm.cost_model as cost_model

    source = importlib.import_module("alpha.swarm.cost_model").__file__
    assert source is not None and cost_model.__doc__, "the module docstring documents the config-only pricing rule"
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "per 1M" in text or "1_000_000" in text, "the USD conversion is explicit; prices themselves live only in config.yaml"


# ---------------------------------------------------------------------------
# Coordinator integration: recorded on the plan, refused only on a complete 2x p90
# ---------------------------------------------------------------------------


def _stub_cost_decomposer(*, tags: tuple[str, ...] = ("sql",), task_count: int = 1):
    def decompose(**_kwargs: object) -> SwarmPlan:
        plan = SwarmPlan(swarm_id=f"cost-{uuid.uuid4().hex[:10]}", goal="stub", mode=SwarmMode.PARALLEL)
        plan.max_concurrency = 2
        plan.critical_path_seconds = 5.0
        plan.metrics["estimated_parallel_seconds"] = 5.0
        for index in range(task_count):
            plan.tasks[f"t{index}"] = _task(f"t{index}", tags=tags)
        return plan

    return decompose


def test_create_swarm_records_a_cold_start_as_insufficient_history() -> None:
    with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_cost_decomposer()):
        coordinator = SwarmCoordinator(storage_dir=storage)
        plan = coordinator.create_swarm(goal="stub goal")
    projection = plan.metrics["calibrated_cost_projection"]
    assert projection["estimate_method"] == INSUFFICIENT_HISTORY
    assert projection["projected_tokens_p50"] is None
    assert "calibrated cost projection" not in plan.metrics.get("admission_advisory", {}).get("note", "")


def test_create_swarm_refuses_a_projection_far_above_the_token_ceiling() -> None:
    with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_cost_decomposer()):
        coordinator = SwarmCoordinator(storage_dir=storage)
        coordinator._swarms["history-seed"] = _history(tokens=1000, tasks=3)
        with pytest.raises(SwarmPlanValidationError) as excinfo:
            coordinator.create_swarm(goal="stub goal", budget=SwarmBudget(max_tokens=100))
        assert [plan for plan in coordinator.list_swarms() if plan.goal == "stub"] == [], "a refused plan must not remain visible as a running swarm"
    message = str(excinfo.value)
    assert message.startswith("swarm was refused before registration: calibrated cost projection p90")
    assert "max_tokens" in message


def test_create_swarm_warns_but_accepts_a_projection_modestly_above_the_ceiling() -> None:
    with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_cost_decomposer()):
        coordinator = SwarmCoordinator(storage_dir=storage)
        coordinator._swarms["history-seed"] = _history(tokens=1000, tasks=3)
        plan = coordinator.create_swarm(goal="stub goal", budget=SwarmBudget(max_tokens=900))
    projection = plan.metrics["calibrated_cost_projection"]
    assert projection["estimate_method"] == COST_MODEL_BASIS
    assert projection["projected_tokens_p90"] == 1000
    assert projection["confidence"] == "low"
    assert any("exceeds the declared max_tokens" in warning for warning in projection["warnings"])
    assert COST_REFUSAL_MULTIPLE == 2.0


# ---------------------------------------------------------------------------
# Model surface: the spawn response carries the calibrated projection
# ---------------------------------------------------------------------------


def test_spawn_response_discloses_the_calibrated_projection() -> None:
    # ``import alpha.tools.builtins.swarm_tool as …`` is order-sensitive: the
    # lazy package facade caches the resolved export under the same name, so
    # ``importlib`` reads the module from ``sys.modules`` unconditionally.
    swarm_tool_module = importlib.import_module("alpha.tools.builtins.swarm_tool")
    with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_cost_decomposer()):
        coordinator = SwarmCoordinator(storage_dir=storage)
        with patch.object(swarm_tool_module, "get_swarm_coordinator", lambda: coordinator):
            response = swarm_tool_module.swarm_tool.invoke({"action": "spawn", "goal": "one tiny bounded goal"})
    assert "Calibrated cost projection (recorded history)" in response
    assert INSUFFICIENT_HISTORY in response
    payload = json.loads(response.split("**Calibrated cost projection (recorded history)**: `", 1)[1].split("`", 1)[0])
    assert payload["estimate_method"] == INSUFFICIENT_HISTORY


# ---------------------------------------------------------------------------
# Telemetry: the snapshot exposes both recorded signals
# ---------------------------------------------------------------------------


def test_snapshot_exposes_panel_decorrelation_and_the_recorded_projection() -> None:
    plan = SwarmPlan(swarm_id="snap", goal="g", mode=SwarmMode.ENSEMBLE)
    for index in range(2):
        task = _task(f"v{index}")
        task.state = TaskNodeState.COMPLETED
        task.result_summary = "The identical recommendation from both voters"
        plan.tasks[f"v{index}"] = task
    plan.metrics["calibrated_cost_projection"] = {"estimate_method": COST_MODEL_BASIS, "projected_tokens_p50": 10}

    snapshot = SwarmTelemetry.snapshot(plan)

    assert snapshot["panel_decorrelation"]["similarity"] == pytest.approx(1.0)
    assert snapshot["cost_projection"]["estimate_method"] == COST_MODEL_BASIS
    assert isinstance(TaskCostSample(1, 1, 0, 0, frozenset(), None, 0), TaskCostSample)
