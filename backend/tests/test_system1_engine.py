"""System 1 fast reflex harness tests.

Covers: the three typed primitives (choice/score/noul) on the local default
path, the Jev cloud client with a mocked transport (success, transport
failure, and response-contract violation -> honest disclosed fallback; both-
paths-broken -> propagates, never fabricates), local-default-without-key,
tool-catalog pruning (121 candidates -> 4-6, node-required kept, irrelevant
dropped), the noul()-based loop-termination gate (fail-closed), capability
registration, and a non-flaky latency benchmark.
"""

from __future__ import annotations

import asyncio
import importlib
import statistics
import time
from types import SimpleNamespace

import pytest

from alpha.capabilities.catalog import CAPABILITY_CATALOG
from alpha.system1 import (
    ChoiceRequest,
    ChoiceResult,
    DecisionType,
    LocalReflexClassifier,
    NoulRequest,
    NoulResult,
    ScoreRequest,
    ScoreResult,
    System1Engine,
    evaluate_loop_termination,
    get_system1_engine,
    prune_tool_catalog,
    reset_system1_engine,
)

CLOUD_KEY = "unit-test-jev-key"
CHOICE_CONTEXT = "run the pytest suite and fix failing test assertions"
CHOICE_CANDIDATES = ["pytest_runner", "code_editor", "web_search", "git_commit"]
NEUTRAL_CONTEXT = "ambient conditions nominal"
SUCCESS_CONTEXT = "5 passed, 0 failed in 0.42s"
FAILURE_CONTEXT = "3 failed, 4 passed with AssertionError traceback"
Noul_QUESTION = "Has the objective been completely satisfied?"
CLOUD_CHOICE_RESPONSE = {
    "winner": "code_editor",
    "probabilities": {"pytest_runner": 0.1, "code_editor": 0.7, "web_search": 0.1, "git_commit": 0.1},
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch):
    """Every test runs as if no key/model env vars exist; singleton stays clean."""
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("ALPHA_SYSTEM1_ONNX_MODEL", raising=False)
    monkeypatch.delenv("ALPHA_SYSTEM1_ONNX_VOCAB", raising=False)
    reset_system1_engine()
    yield
    reset_system1_engine()


def _catalog_121() -> list[dict[str, str]]:
    """A 121-entry catalog shaped like the real tool registry."""
    entries: list[dict[str, str]] = [
        {"name": "pytest_runner", "description": "Run the pytest suite and report failing assertions"},
        {"name": "fix_failing_tests", "description": "Fix failing test assertions in the repo"},
        {"name": "assertion_resolver", "description": "Resolve assertion errors raised by tests"},
        {"name": "test_reporter", "description": "Summarize test results into a report"},
        {"name": "run_tests_local", "description": "Run the local test suite quickly"},
        {"name": "format_disk_drive", "description": "Format the attached disk drive"},
        {"name": "web_crawler", "description": "Crawl external marketing pages"},
    ]
    entries.extend({"name": f"filler_tool_{index:03d}", "description": f"filler tool item number {index}"} for index in range(114))
    assert len(entries) == 121
    return entries


# --------------------------------------------------------------------------- engine selection


def test_local_is_default_without_jev_key() -> None:
    engine = System1Engine()
    assert engine.use_cloud is False
    assert engine.backend == "local_reflex"
    # Singleton: same instance, still local because the env has no key.
    assert get_system1_engine() is get_system1_engine()
    assert get_system1_engine().use_cloud is False
    # Empty/whitespace keys are treated as absent (local path), not as cloud.
    assert System1Engine(api_key="").use_cloud is False
    assert System1Engine(api_key="   ").use_cloud is False


def test_cloud_configured_only_when_env_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_API_KEY", CLOUD_KEY)
    reset_system1_engine()
    engine = get_system1_engine()
    assert engine.use_cloud is True
    assert engine.backend == "jev_cloud"


# --------------------------------------------------------------------------- local primitives


def test_choice_returns_winner_and_full_distribution() -> None:
    engine = System1Engine()
    result = engine.choice(CHOICE_CONTEXT, CHOICE_CANDIDATES)
    assert isinstance(result, ChoiceResult)
    assert result.engine == "local_reflex"
    assert result.fallback_reason is None
    assert result.winner in CHOICE_CANDIDATES
    assert set(result.probabilities) == set(CHOICE_CANDIDATES)
    assert all(0.0 <= prob <= 1.0 for prob in result.probabilities.values())
    assert abs(sum(result.probabilities.values()) - 1.0) < 1e-6
    assert result.probabilities[result.winner] == max(result.probabilities.values())
    # Deterministic property of the local classifier on this fixed input
    # (hashing + softmax are RNG-free), verified by measurement, not timing.
    assert result.winner == "pytest_runner"


def test_choice_rejects_invalid_inputs() -> None:
    engine = System1Engine()
    with pytest.raises(ValueError):  # duplicate candidates cannot carry a distribution
        engine.choice("ctx", ["a", "a"])
    with pytest.raises(ValueError):  # empty candidate name
        engine.choice("ctx", ["a", "  "])
    with pytest.raises(ValueError):  # blank context
        engine.choice("   ", ["a", "b"])
    with pytest.raises(ValueError):  # a bare string is not a candidate sequence
        engine.choice("ctx", "not-a-list")


def test_score_orders_dangerous_below_benign_and_neutral_is_honest() -> None:
    engine = System1Engine()
    danger = engine.score("rm -rf / --no-preserve-root --force")
    benign = engine.score("read the config file and print its contents")
    neutral = engine.score(NEUTRAL_CONTEXT)
    for result in (danger, benign, neutral):
        assert isinstance(result, ScoreResult)
        assert result.engine == "local_reflex"
        assert 0.0 <= result.score <= 1.0
        assert 0.0 <= result.confidence <= 1.0
    assert danger.score < neutral.score < benign.score
    # No lexical signal -> exactly neutral score with low, honest confidence.
    assert abs(neutral.score - 0.5) < 1e-9
    assert neutral.confidence < 0.3
    assert danger.confidence > neutral.confidence


def test_noul_is_fail_closed_and_evidence_aware() -> None:
    engine = System1Engine()
    success = engine.noul(SUCCESS_CONTEXT, Noul_QUESTION)
    neutral = engine.noul(NEUTRAL_CONTEXT, Noul_QUESTION)
    failure = engine.noul(FAILURE_CONTEXT, Noul_QUESTION)
    for result in (success, neutral, failure):
        assert isinstance(result, NoulResult)
        assert result.engine == "local_reflex"
        assert isinstance(result.decision, bool)
        assert 0.0 <= result.probability <= 1.0
    assert success.decision is True and success.probability >= 0.8
    # Fail-closed: evidence-free context -> False at ~0.5 even though the
    # question itself asks for termination (the question never decides).
    assert neutral.decision is False and neutral.probability <= 0.5
    assert failure.decision is False and failure.probability > 0.5


# --------------------------------------------------------------------------- cloud path (mocked)


def test_cloud_success_uses_jev_for_all_primitives(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = System1Engine(api_key=CLOUD_KEY)
    responses = {
        "/choice": CLOUD_CHOICE_RESPONSE,
        "/score": {"score": 0.93, "confidence": 0.8},
        "/noul": {"decision": True, "probability": 0.97},
    }
    calls: list[str] = []

    def fake_post(path: str, payload: dict) -> dict:
        calls.append(path)
        return responses[path]

    monkeypatch.setattr(engine, "_post_json", fake_post)
    choice = engine.choice(CHOICE_CONTEXT, CHOICE_CANDIDATES)
    score = engine.score("rm -rf /tmp/build_cache")
    noul = engine.noul(SUCCESS_CONTEXT, Noul_QUESTION)
    assert calls == ["/choice", "/score", "/noul"]
    assert choice.engine == "jev_cloud" and choice.fallback_reason is None
    assert choice.winner == "code_editor"
    assert score.engine == "jev_cloud" and score.score == 0.93 and score.fallback_reason is None
    assert noul.engine == "jev_cloud" and noul.decision is True and noul.fallback_reason is None


def test_cloud_transport_failure_falls_back_to_local_with_disclosure(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = System1Engine(api_key=CLOUD_KEY)

    def cloud_down(path: str, payload: dict) -> dict:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(engine, "_post_json", cloud_down)
    choice = engine.choice(CHOICE_CONTEXT, CHOICE_CANDIDATES)
    score = engine.score("read the config file")
    noul = engine.noul(NEUTRAL_CONTEXT, Noul_QUESTION)
    for result in (choice, score, noul):
        assert result.engine == "local_reflex"
        assert result.fallback_reason is not None
        assert result.fallback_reason.startswith("jev_cloud_failed")
        assert "connection refused" in result.fallback_reason
    # The fallback decision is a real local computation, never fabricated:
    assert choice.winner in CHOICE_CANDIDATES
    assert 0.0 <= score.score <= 1.0
    assert isinstance(noul.decision, bool)


def test_cloud_contract_violation_is_rejected_not_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = System1Engine(api_key=CLOUD_KEY)
    holder: dict[str, dict] = {}
    monkeypatch.setattr(engine, "_post_json", lambda path, payload: holder["response"])

    # 1. Hallucinated winner outside the candidate set -> rejected -> local.
    holder["response"] = {
        "winner": "ghost_tool",
        "probabilities": {"pytest_runner": 0.1, "code_editor": 0.1, "web_search": 0.1, "ghost_tool": 0.7},
    }
    choice = engine.choice(CHOICE_CONTEXT, CHOICE_CANDIDATES)
    assert choice.engine == "local_reflex"
    assert choice.fallback_reason is not None and "candidate set" in choice.fallback_reason
    assert choice.winner in CHOICE_CANDIDATES  # the hallucinated name never surfaces as a decision

    # 2. Out-of-range score -> rejected -> local.
    holder["response"] = {"score": 1.7, "confidence": 0.9}
    score = engine.score("read the config file")
    assert score.engine == "local_reflex"
    assert score.fallback_reason is not None
    assert score.fallback_reason.startswith("jev_cloud_failed")
    assert 0.0 <= score.score <= 1.0

    # 3. Non-boolean noul decision -> rejected (strict bool) -> local.
    holder["response"] = {"decision": "yes", "probability": 0.9}
    noul = engine.noul(NEUTRAL_CONTEXT, Noul_QUESTION)
    assert noul.engine == "local_reflex"
    assert noul.fallback_reason is not None
    assert noul.fallback_reason.startswith("jev_cloud_failed")
    assert isinstance(noul.decision, bool)


def test_both_paths_broken_propagates_instead_of_fabricating(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = System1Engine(api_key=CLOUD_KEY)

    def cloud_down(path: str, payload: dict) -> dict:
        raise RuntimeError("cloud down")

    def local_broken(*args: object, **kwargs: object) -> ChoiceResult:
        raise RuntimeError("local classifier broken")

    monkeypatch.setattr(engine, "_post_json", cloud_down)
    monkeypatch.setattr(engine.local, "predict_choice", local_broken)
    with pytest.raises(RuntimeError, match="local classifier broken"):
        engine.choice(CHOICE_CONTEXT, CHOICE_CANDIDATES)


def test_async_api_cloud_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = System1Engine(api_key=CLOUD_KEY)

    async def ok(path: str, payload: dict) -> dict:
        return dict(CLOUD_CHOICE_RESPONSE)

    monkeypatch.setattr(engine, "_apost_json", ok)
    success = asyncio.run(engine.achoice(CHOICE_CONTEXT, CHOICE_CANDIDATES))
    assert success.engine == "jev_cloud" and success.fallback_reason is None
    assert success.winner == "code_editor"

    async def down(path: str, payload: dict) -> dict:
        raise RuntimeError("async transport down")

    monkeypatch.setattr(engine, "_apost_json", down)
    fallback = asyncio.run(engine.achoice(CHOICE_CONTEXT, CHOICE_CANDIDATES))
    assert fallback.engine == "local_reflex"
    assert fallback.fallback_reason is not None
    assert "async transport down" in fallback.fallback_reason
    assert fallback.winner in CHOICE_CANDIDATES


def test_sync_api_on_running_loop_skips_cloud_and_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariant: no blocking sync HTTP on the event loop — skip + disclose."""
    engine = System1Engine(api_key=CLOUD_KEY)

    def must_not_run(path: str, payload: dict) -> dict:
        raise AssertionError("blocking cloud call attempted on the event loop")

    monkeypatch.setattr(engine, "_post_json", must_not_run)

    async def run() -> ChoiceResult:
        return engine.choice(CHOICE_CONTEXT, CHOICE_CANDIDATES)

    result = asyncio.run(run())
    assert result.engine == "local_reflex"
    assert result.fallback_reason is not None
    assert "event loop" in result.fallback_reason
    assert result.winner in CHOICE_CANDIDATES


# --------------------------------------------------------------------------- pruning seam


def test_prune_121_candidates_to_4_6_keeps_required_drops_irrelevant() -> None:
    catalog = _catalog_121()
    node_context = {"prompt": CHOICE_CONTEXT, "required_tools": ["pytest_runner"]}
    selected = prune_tool_catalog(catalog, node_context)
    names = [entry["name"] for entry in selected]
    assert 4 <= len(selected) <= 6
    assert names[0] == "pytest_runner"  # node-required tool always kept, first
    assert "pytest_runner" in names
    assert "format_disk_drive" not in names  # irrelevant tool always dropped
    assert all(set(entry) == {"name", "description", "relevance"} for entry in selected)
    # Deterministic pure function: same inputs -> same selection.
    assert [entry["name"] for entry in prune_tool_catalog(catalog, node_context)] == names


def test_prune_accepts_node_object_and_attribute_style_tool_entries() -> None:
    """Mirrors the DWE hook shape: WorkflowNode-like object + BaseTool-like entries."""
    catalog = [SimpleNamespace(name=e["name"], description=e["description"]) for e in _catalog_121()]
    node = SimpleNamespace(
        id="n1",
        type="tool",
        executor="alpha.tool",
        prompt=CHOICE_CONTEXT,
        config={"required_tools": ["pytest_runner"], "tools": ["candidate-list-key-excluded"]},
    )
    selected = prune_tool_catalog(catalog, node)
    names = [entry["name"] for entry in selected]
    assert 4 <= len(selected) <= 6
    assert names[0] == "pytest_runner"
    assert "format_disk_drive" not in names


def test_prune_small_catalog_returns_everything() -> None:
    small = _catalog_121()[:5]
    selected = prune_tool_catalog(small, "whatever the node asks")
    assert len(selected) == 5


def test_prune_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        prune_tool_catalog([], "ctx", min_tools=0)
    with pytest.raises(ValueError):
        prune_tool_catalog([], "ctx", min_tools=5, max_tools=3)


# --------------------------------------------------------------------------- loop termination


class _StubEngine:
    def __init__(self, *, result: NoulResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    def noul(self, context: str, question: str) -> NoulResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def test_loop_termination_confident_yes_terminates() -> None:
    stub = _StubEngine(result=NoulResult(decision=True, probability=0.93, engine="local_reflex"))
    gate = evaluate_loop_termination(SUCCESS_CONTEXT, engine=stub)
    assert gate.terminate is True
    assert gate.probability == 0.93
    assert gate.decision is not None and gate.decision.decision is True
    assert "gate met" in gate.reason


def test_loop_termination_uncertain_or_no_keeps_looping() -> None:
    weak = evaluate_loop_termination(
        "ctx",
        engine=_StubEngine(result=NoulResult(decision=True, probability=0.6, engine="local_reflex")),
    )
    assert weak.terminate is False
    assert "min_probability" in weak.reason

    denied = evaluate_loop_termination(
        "ctx",
        engine=_StubEngine(result=NoulResult(decision=False, probability=0.95, engine="local_reflex")),
    )
    assert denied.terminate is False
    assert "decision=False" in denied.reason


def test_loop_termination_fails_closed_on_engine_error() -> None:
    gate = evaluate_loop_termination("ctx", engine=_StubEngine(error=RuntimeError("boom")))
    assert gate.terminate is False
    assert gate.probability == 0.0
    assert gate.decision is None
    assert gate.reason.startswith("fail_closed")
    assert "boom" in gate.reason  # the real reason is reported, not invented


def test_loop_termination_with_real_local_default_engine() -> None:
    """No key -> local engine; real noul evidence drives the gate."""
    yes = evaluate_loop_termination(SUCCESS_CONTEXT)
    neutral = evaluate_loop_termination(NEUTRAL_CONTEXT)
    assert yes.terminate is True and yes.probability >= 0.8
    assert neutral.terminate is False


def test_loop_termination_rejects_invalid_min_probability() -> None:
    with pytest.raises(ValueError):
        evaluate_loop_termination("ctx", min_probability=0.0)
    with pytest.raises(ValueError):
        evaluate_loop_termination("ctx", min_probability=float("nan"))


# --------------------------------------------------------------------------- capability + schemas


def test_system1_reflex_capability_registered_and_loadable() -> None:
    spec = CAPABILITY_CATALOG["system1_reflex"]
    assert spec.module == "alpha.system1.engine"
    assert spec.target == "System1Engine"
    assert spec.kind == "engine"
    assert getattr(importlib.import_module(spec.module), spec.target) is System1Engine


def test_request_and_result_schemas_enforce_contracts() -> None:
    assert DecisionType.CHOICE == "choice" and DecisionType.NOUL == "noul"
    request = ChoiceRequest(context="ctx", candidates=["a", "b"])
    assert request.model_dump() == {"context": "ctx", "candidates": ["a", "b"]}
    with pytest.raises(ValueError):
        ChoiceRequest(context="ctx", candidates=["a", "a"])
    with pytest.raises(ValueError):
        ScoreRequest(context="ctx", scale=(1.0, 1.0))  # lo must be < hi
    with pytest.raises(ValueError):
        NoulRequest(context="ctx", question="q?", threshold=1.5)
    with pytest.raises(ValueError):  # strict boolean, no string coercion
        NoulResult(decision="yes", probability=0.9, engine="local_reflex")
    with pytest.raises(ValueError):  # winner must exist in the distribution
        ChoiceResult(winner="ghost", probabilities={"a": 1.0}, engine="local_reflex")


def test_acceleration_status_degrades_honestly_when_onnx_absent() -> None:
    status = LocalReflexClassifier().acceleration_status()
    # Fixture removed any configured model env -> numpy path with a disclosed
    # "unavailable" reason, never an implied ONNX capability.
    assert status["backend"] == "numpy_hash"
    assert status["onnx_available"] is False
    assert status["onnx_reason"].startswith("unavailable:")


# --------------------------------------------------------------------------- latency benchmark


def test_local_decision_latency_median_ceiling() -> None:
    """Latency benchmark with a non-flaky, measured-number ceiling.

    Design target (System 1 architecture spec): <15ms per decision on a
    laptop CPU, <50ms worst case. CI runners are shared, noisy VMs (GC
    pauses, CPU contention, cold caches), so hard-failing on the design
    target itself would be flaky. We therefore measure the REAL median over
    N runs, print it for the record, and hard-fail only if the measured
    median exceeds a deliberately generous 250ms ceiling (choice) / 500ms
    (pruning 121 candidates). The assertions are always on measured numbers,
    never on a claimed constant.
    """
    engine = System1Engine()  # local default (fixture removed the key)
    for _ in range(3):  # warmup (also fills the prune embedding cache)
        engine.choice(CHOICE_CONTEXT, CHOICE_CANDIDATES)

    choice_samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        engine.choice(CHOICE_CONTEXT, CHOICE_CANDIDATES)
        choice_samples.append((time.perf_counter() - start) * 1000.0)
    choice_median = statistics.median(choice_samples)

    catalog = _catalog_121()
    node_context = {"prompt": CHOICE_CONTEXT, "required_tools": ["pytest_runner"]}
    for _ in range(2):  # warm the catalog-entry embedding cache
        prune_tool_catalog(catalog, node_context)
    prune_samples: list[float] = []
    for _ in range(10):
        start = time.perf_counter()
        prune_tool_catalog(catalog, node_context)
        prune_samples.append((time.perf_counter() - start) * 1000.0)
    prune_median = statistics.median(prune_samples)

    print(f"system1_latency_median_ms choice={choice_median:.3f} prune121={prune_median:.3f} (n_choice=30, n_prune=10; design target <15-50ms, generous CI ceilings 250/500ms)")
    assert choice_median < 250, f"measured choice median {choice_median:.1f}ms exceeded the generous 250ms CI ceiling"
    assert prune_median < 500, f"measured prune median {prune_median:.1f}ms exceeded the generous 500ms CI ceiling"
