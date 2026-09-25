"""Hermetic contract tests for Alpha's scenario-conditioned recall package."""

from __future__ import annotations

import ast
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alpha.memory.scenarios import (
    DEFAULT_REGISTRY,
    MemorySurface,
    MemorySurfaceRegistry,
    RoutingPlan,
    Scenario,
    ScenarioClassifier,
    ScenarioConfig,
    ScenarioConfigError,
    ScenarioRecall,
    ScenarioRouter,
    ScenarioSignals,
    append_routing_decision,
    classification_status_known,
    load_scenario_config,
    parse_model_response,
    read_entries,
    render,
)


class FakeModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    def invoke(self, prompt: str, config: dict[str, Any] | None = None) -> str:
        self.calls.append(prompt)
        return self.response


class BrokenModel:
    def invoke(self, prompt: str, config: dict[str, Any] | None = None) -> str:
        raise RuntimeError("fake model unavailable")


def enabled_config(**overrides: Any) -> ScenarioConfig:
    values: dict[str, Any] = {"enabled": True}
    values.update(overrides)
    return ScenarioConfig(**values)


def make_registry() -> MemorySurfaceRegistry:
    return MemorySurfaceRegistry(
        [
            MemorySurface(
                name="code_high",
                description="code surface",
                scenarios=[Scenario.CODING],
                base_weight=0.90,
                cost_units=2,
            ),
            MemorySurface(
                name="code_named",
                description="second code surface",
                scenarios=[Scenario.CODING],
                base_weight=0.80,
                cost_units=1,
            ),
            MemorySurface(
                name="research_only",
                description="research surface",
                scenarios=[Scenario.RESEARCH],
                base_weight=0.70,
                cost_units=1,
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Gate, configuration, and model contracts
# ---------------------------------------------------------------------------


def test_model_validators_clamp_and_disclose() -> None:
    surface = MemorySurface(name="bounded", base_weight=4.0, cost_units=0)
    plan = RoutingPlan(scenario="coding", confidence=4.0, surfaces=[("bounded", 4.0, 0)])
    assert surface.base_weight == 1.0
    assert surface.cost_units == 1
    assert "base_weight" in surface.clamped_fields
    assert "cost_units" in surface.clamped_fields
    assert plan.confidence == 1.0
    assert plan.total_budget_units == 1
    assert "confidence" in plan.clamped_fields


def test_gate_is_off_by_default_and_does_not_call_a_model() -> None:
    model = FakeModel(json.dumps({"scenario": "research", "confidence": 0.99}))
    router = ScenarioRouter(config=ScenarioConfig(), model=model)

    plan = router.route(ScenarioSignals(tools_used=["web_search"]))

    assert router.config.enabled is False
    assert plan.enabled is False
    assert plan.scenario is Scenario.GENERAL
    assert plan.surfaces == []
    assert "disabled" in plan.reason.lower()
    assert model.calls == []


def test_every_scenario_config_key_has_a_reader_and_no_shared_config_import() -> None:
    expected = {
        "enabled",
        "default_scenario",
        "classifier_model",
        "enable_model_classifier",
        "min_confidence",
        "total_budget_units",
        "max_surfaces",
        "session_freeze",
        "override_env_freeze",
        "storage_path",
    }
    assert set(ScenarioConfig.model_fields) == expected
    config = enabled_config(
        default_scenario="planning",
        classifier_model="fake-model",
        enable_model_classifier=True,
        min_confidence=0.4,
        total_budget_units=9,
        max_surfaces=4,
        session_freeze=False,
        override_env_freeze=False,
        storage_path="state",
    )
    assert set(config.read_all_keys()) == expected
    bounded = ScenarioConfig(default_scenario="unknown", min_confidence=2, total_budget_units=-1, max_surfaces=0)
    assert bounded.disclosures
    assert "min_confidence" in bounded.clamp_disclosure
    package = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "memory" / "scenarios"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("memory_config")
            if isinstance(node, ast.Import):
                assert all(not alias.name.endswith("memory_config") for alias in node.names)
    assert (package / "config.py").read_text(encoding="utf-8").count("classifier_model") >= 1
    assert (package / "router.py").read_text(encoding="utf-8").count("total_budget_units") >= 1
    assert (package / "router.py").read_text(encoding="utf-8").count("max_surfaces") >= 1


def test_config_override_is_local_and_invalid_override_fails_loudly(tmp_path: Path) -> None:
    override = tmp_path / "scenario-memory.yaml"
    override.write_text(
        "enabled: true\ndefault_scenario: research\nmin_confidence: 0.7\ntotal_budget_units: 7\nmax_surfaces: 2\n",
        encoding="utf-8",
    )
    config = load_scenario_config(override, storage_path=tmp_path / "state")
    assert config.enabled is True
    assert config.default_scenario is Scenario.RESEARCH
    assert config.storage_path == str(tmp_path / "state")

    bad = tmp_path / "bad.yaml"
    bad.write_text("enabled: [not, a, bool]\n", encoding="utf-8")
    with pytest.raises(ScenarioConfigError):
        load_scenario_config(bad)


# ---------------------------------------------------------------------------
# Deterministic classification and honest fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        (ScenarioSignals(explicit_override="research", has_error=True, tools_used=["deploy"]), Scenario.RESEARCH),
        (ScenarioSignals(has_error=True, has_diff=True), Scenario.INCIDENT),
        (ScenarioSignals(has_diff=True), Scenario.CODING),
        (ScenarioSignals(tools_used=["web_search"]), Scenario.RESEARCH),
        (ScenarioSignals(tools_used=["deploy"]), Scenario.OPERATIONS),
        (ScenarioSignals(tools_used=["write_todos"]), Scenario.PLANNING),
        (ScenarioSignals(tools_used=["flashcard"]), Scenario.LEARNING),
        (ScenarioSignals(declared_intent="let's have a conversation"), Scenario.CONVERSATION),
        (ScenarioSignals(declared_intent="teach me how this works"), Scenario.LEARNING),
    ],
)
def test_classification_precedence_and_scenario_signals(signals: ScenarioSignals, expected: Scenario) -> None:
    result = ScenarioClassifier(config=enabled_config()).classify(signals)
    assert result.scenario is expected
    assert classification_status_known(result.status)
    assert result.reason


def test_configured_default_is_the_explicit_safe_fallback() -> None:
    classifier = ScenarioClassifier(config=enabled_config(default_scenario="planning"))
    result = classifier.classify(ScenarioSignals(declared_intent="unknown intent"))
    assert result.scenario is Scenario.PLANNING
    assert result.status == "no_signal"
    assert "planning" in result.reason


def test_ambiguous_signals_and_unknown_override_default_to_general() -> None:
    classifier = ScenarioClassifier(config=enabled_config())
    empty = classifier.classify(ScenarioSignals())
    ambiguous = classifier.classify(ScenarioSignals(tools_used=["web_search", "deploy", "pytest"]))
    unknown = classifier.classify(ScenarioSignals(explicit_override="teleportation"))
    assert empty.scenario is Scenario.GENERAL
    assert empty.status == "no_signal"
    assert ambiguous.scenario is Scenario.GENERAL
    assert ambiguous.status == "ambiguous"
    assert unknown.scenario is Scenario.GENERAL
    assert "override" in unknown.reason.lower()


def test_below_confidence_threshold_defaults_to_general() -> None:
    classifier = ScenarioClassifier(config=enabled_config(min_confidence=0.99))
    result = classifier.classify(ScenarioSignals(declared_intent="teach me"))
    assert result.scenario is Scenario.GENERAL
    assert result.status == "below_threshold"
    assert "min_confidence" in result.reason


def test_model_classifier_requires_strict_json_and_falls_back_on_all_failures() -> None:
    valid = FakeModel('{"scenario":"research","confidence":0.91,"reason":"source work"}')
    classifier = ScenarioClassifier(
        config=enabled_config(enable_model_classifier=True, classifier_model="fake-model"),
        model=valid,
    )
    result = classifier.classify(ScenarioSignals(declared_intent="classify this request"))
    assert result.scenario is Scenario.RESEARCH
    assert result.status == "model"
    assert valid.calls

    failures = [
        BrokenModel(),
        FakeModel("not json"),
        FakeModel('{"scenario":"unknown","confidence":0.9}'),
        FakeModel('{"scenario":"research"}'),
        FakeModel('{"status":"model_error","scenario":"research","confidence":0.99}'),
        FakeModel('{"scenario":"research","confidence":0.1}'),
    ]
    for model in failures:
        failed = ScenarioClassifier(
            config=enabled_config(enable_model_classifier=True, classifier_model="fake-model"),
            model=model,
        ).classify(ScenarioSignals(declared_intent="classify this request"))
        assert failed.scenario is Scenario.GENERAL
        assert classification_status_known(failed.status)
        assert failed.reason


def test_no_signal_does_not_ask_model_to_invent_a_label() -> None:
    model = FakeModel('{"scenario":"research","confidence":0.99}')
    result = ScenarioClassifier(
        config=enabled_config(enable_model_classifier=True),
        model=model,
    ).classify(ScenarioSignals())
    assert result.scenario is Scenario.GENERAL
    assert result.status == "no_signal"
    assert model.calls == []

    assert parse_model_response('```json\n{"scenario":"planning","confidence":0.8}\n```').ok
    for value in (None, "", "[]", '{"scenario":"nope","confidence":1}', '{"scenario":"coding","confidence":2}'):
        outcome = parse_model_response(value)
        assert not outcome.ok
        assert outcome.status in {
            "invalid_json",
            "invalid_shape",
            "unknown_scenario",
            "invalid_confidence",
        }


# ---------------------------------------------------------------------------
# Registry and routing
# ---------------------------------------------------------------------------


def test_default_registry_contains_only_today_surfaces_and_names_are_defensive() -> None:
    assert DEFAULT_REGISTRY.names() == ("l1", "cognitive", "dormant_context")
    surface = DEFAULT_REGISTRY.get("l1")
    assert surface is not None
    surface.base_weight = 0.1
    assert DEFAULT_REGISTRY.get("l1").base_weight == 0.9  # type: ignore[union-attr]


def test_registry_rejects_duplicates_and_serializes_concurrent_registration() -> None:
    registry = MemorySurfaceRegistry()
    registry.register(MemorySurface(name="one", scenarios=[Scenario.GENERAL]))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(MemorySurface(name="one", scenarios=[Scenario.CODING]))

    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def register(index: int) -> None:
        try:
            barrier.wait()
            registry.register(MemorySurface(name=f"surface-{index}", scenarios=[Scenario.GENERAL]))
        except BaseException as exc:  # pragma: no cover - assertion reports the worker error
            errors.append(exc)

    workers = [threading.Thread(target=register, args=(index,)) for index in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert errors == []
    assert len(registry) == 9
    assert registry.unregister("surface-3") is True
    assert registry.unregister("surface-3") is False


def test_routing_filters_sorts_and_accounts_budget_exactly() -> None:
    registry = make_registry()
    router = ScenarioRouter(registry=registry, config=enabled_config(max_surfaces=2, total_budget_units=3))
    plan = router.route(ScenarioSignals(has_diff=True))

    assert plan.scenario is Scenario.CODING
    assert plan.surface_names == ("code_high", "code_named")
    assert plan.total_budget_units == 3
    assert plan.omitted_surfaces == []
    assert sum(route.budget_units for route in plan.surfaces) == plan.total_budget_units

    research = router.route(ScenarioSignals(tools_used=["web_search"]), budget_units=1)
    assert research.surface_names == ("research_only",)
    assert research.total_budget_units == 1

    no_budget = router.route(ScenarioSignals(has_diff=True), budget_units=0)
    assert no_budget.surfaces == []
    assert no_budget.total_budget_units == 0


def test_routing_order_is_weight_desc_then_name() -> None:
    registry = MemorySurfaceRegistry(
        [
            MemorySurface(name="zeta", scenarios=[Scenario.CODING], base_weight=0.5, cost_units=1),
            MemorySurface(name="alpha", scenarios=[Scenario.CODING], base_weight=0.5, cost_units=1),
            MemorySurface(name="high", scenarios=[Scenario.CODING], base_weight=0.9, cost_units=1),
        ]
    )
    router = ScenarioRouter(registry=registry, config=enabled_config(total_budget_units=3))
    plan = router.route(ScenarioSignals(has_diff=True))
    assert plan.surface_names == ("high", "alpha", "zeta")


# ---------------------------------------------------------------------------
# Recall rendering, freeze semantics, explanation, and provenance
# ---------------------------------------------------------------------------


def test_render_uses_only_supplied_blocks_and_reports_missing_surfaces() -> None:
    plan = RoutingPlan(
        scenario=Scenario.INCIDENT,
        confidence=0.9,
        surfaces=[("code_high", 0.9, 2), ("code_named", 0.8, 1)],
        reason="error signal selected incident",
    )
    output = render(plan, {"code_high": "caller supplied evidence"}, max_chars=12_000)
    assert "caller supplied evidence" in output
    assert "code_named" in output
    assert "unavailable" in output.lower()
    assert "invented surface content" not in output


def test_recall_renderer_stats_count_available_and_unavailable_surfaces() -> None:
    plan = RoutingPlan(surfaces=[("a", 0.5, 1), ("b", 0.4, 1)])
    renderer = ScenarioRecall()
    renderer.render(plan, {"a": "available"})
    snapshot = renderer.stats()
    assert snapshot["renders"] == 1
    assert snapshot["available_surfaces"] == 1
    assert snapshot["unavailable_surfaces"] == 1
    assert snapshot["last_plan"]["scenario"] == "general"

    plan = RoutingPlan(
        scenario=Scenario.RESEARCH,
        surfaces=[("research_only", 0.7, 1)],
        reason="tool signal selected research",
    )
    output = render(plan, {"research_only": "x" * 20_000}, max_chars=500)
    assert len(output) <= 500
    assert "truncated" in output
    assert "unavailable" in render(plan, {"research_only": "   "}).lower()


def test_session_freeze_returns_same_plan_until_explicit_override() -> None:
    router = ScenarioRouter(
        registry=make_registry(),
        config=enabled_config(session_freeze=True, override_env_freeze=True),
    )
    first = router.plan_for_thread("thread-a", ScenarioSignals(has_diff=True))
    same = router.plan_for_thread("thread-a", ScenarioSignals(tools_used=["web_search"]))
    assert same == first
    assert same.scenario is Scenario.CODING

    overridden = router.plan_for_thread("thread-a", ScenarioSignals(explicit_override="research"))
    assert overridden.scenario is Scenario.RESEARCH
    assert router.plan_for_thread("thread-b", ScenarioSignals(has_diff=True)).scenario is Scenario.CODING


def test_explain_mentions_the_deciding_signal() -> None:
    router = ScenarioRouter(registry=make_registry(), config=enabled_config())
    plan = router.route(ScenarioSignals(has_error=True))
    explanation = router.explain(plan)
    assert "has_error" in explanation
    assert "incident" in explanation


def test_provenance_is_append_only_per_day(tmp_path: Path) -> None:
    router = ScenarioRouter(
        registry=make_registry(),
        config=enabled_config(storage_path=tmp_path),
    )
    first = router.route(ScenarioSignals(has_diff=True), thread_id="t1")
    second = router.route(ScenarioSignals(tools_used=["web_search"]), thread_id="t1")
    day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    entries = read_entries(day, user_id=None, storage_path=tmp_path)
    assert first.scenario is Scenario.CODING
    assert second.scenario is Scenario.RESEARCH
    assert [entry["thread_id"] for entry in entries] == ["t1", "t1"]
    assert [entry["scenario"] for entry in entries] == ["coding", "research"]


def test_provenance_entry_contains_the_required_decision_fields(tmp_path: Path) -> None:
    plan = RoutingPlan(
        scenario=Scenario.PLANNING,
        confidence=0.8,
        surfaces=[("research_only", 0.7, 1)],
        reason="tool signal selected planning",
    )
    path = append_routing_decision(plan, storage_path=tmp_path, now=0.0, thread_id="t2")
    entries = read_entries("1970-01-01", storage_path=tmp_path)
    assert path.exists()
    assert entries[-1]["scenario"] == "planning"
    assert entries[-1]["surfaces"][0]["name"] == "research_only"
    assert entries[-1]["reason"] == "tool signal selected planning"
