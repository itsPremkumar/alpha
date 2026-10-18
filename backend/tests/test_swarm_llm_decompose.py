"""Model-backed swarm decomposition (bot-mode master plan, phase 3.2).

Every test here pins one plan §3.2 acceptance criterion:

- ``source="model"`` never appears without a real model call, and every
  fallback names the reason the attempt did not become a plan;
- the budget pre-check runs *before* any call (zero ceiling blocks; ``auto``
  needs a declared budget; explicit ``model`` is its own opt-in);
- ``tokens`` is measured from the provider's usage report or ``None`` —
  never a fabricated ``0``;
- bounds are refused with the bound named, never clamped or truncated;
- an explicit topology mode keeps its template and never reaches a seam;
- a winning model plan obeys the template plan's invariants
  (``plan.mode == plan.metrics["strategy"]["mode"]``, declared capability
  requirements, evidence-bearing acceptance criteria);
- the production seam is built only when the operator opted in, and its
  construction failure is reported rather than silently becoming templates.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from alpha.swarm import llm_decomposer as llm
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.llm_decomposer import (
    DEFAULT_DECOMPOSITION_MODE,
    LLM_DECOMPOSITION_BASIS,
    MAX_MODEL_CANDIDATES,
    MAX_MODEL_TASKS,
    DecompositionRejected,
    ProductionDecomposeSource,
    budget_precheck,
    build_decompose_prompt,
    normalize_decomposition_mode,
    parse_decomposition_response,
    source_decomposition,
    validate_candidate,
)
from alpha.swarm.models import SwarmMode
from alpha.swarm.strategy import resolve_strategy

GOAL = "Ship the release: implement, test and document the feature."


# -- Fixtures / helpers -----------------------------------------------------


class _Recorder:
    """A fake ``invoke(system, user)`` seam that counts and remembers calls."""

    def __init__(self, reply: object = None, error: Exception | None = None) -> None:
        self.calls = 0
        self.reply = reply
        self.error = error
        self.last_system: str | None = None
        self.last_user: str | None = None

    def __call__(self, system: str, user: str) -> object:
        self.calls += 1
        self.last_system, self.last_user = system, user
        if self.error is not None:
            raise self.error
        return self.reply


class _Message:
    """The provider-message shape the record path reads usage metadata from."""

    def __init__(self, content: object, tokens: int | None = None) -> None:
        self.content = content
        self.response_metadata = {"token_usage": {"total_tokens": tokens}} if tokens is not None else {}


class _Budget:
    def __init__(self, max_tokens: int | None = None) -> None:
        self.max_tokens = max_tokens


def _task(task_id: str, deps: tuple[str, ...] = (), criteria: tuple[str, ...] = (), artifacts: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "id": task_id,
        "objective": f"Perform step {task_id} of the plan",
        "deps": list(deps),
        "capability_tags": ["python"],
        "acceptance_criteria": list(criteria),
        "output_artifacts": list(artifacts),
        "estimated_seconds": 30,
    }


def _reply(*candidates: list[dict[str, object]]) -> _Message:
    return _Message(json.dumps({"candidates": [list(candidate) for candidate in candidates]}))


def _template() -> tuple[object, object]:
    """A real template plan plus a resolution, as ``decompose`` would build."""
    return SwarmTaskDecomposer.decompose(GOAL), resolve_strategy(GOAL)


def _source(**kwargs: object) -> tuple[dict[str, object], object, object]:
    template_plan, template_resolution = _template()
    return source_decomposition(GOAL, template_plan=template_plan, template_resolution=template_resolution, **kwargs)  # type: ignore[arg-type]


# -- Mode vocabulary / signature -------------------------------------------


def test_normalize_decomposition_mode_reads_known_and_refuses_unknown():
    assert normalize_decomposition_mode("model") == "model"
    assert normalize_decomposition_mode(" Auto ") == "auto"
    assert normalize_decomposition_mode("TEMPLATE") == "template"
    # An unknown spelling reads as the free default: a typo never spends.
    assert normalize_decomposition_mode("modle") == "template"
    assert normalize_decomposition_mode(None) == "template"
    assert normalize_decomposition_mode(7) == "template"


def test_decompose_signature_defaults_keep_direct_callers_template_only():
    params = inspect.signature(SwarmTaskDecomposer.decompose).parameters
    assert params["decomposition_mode"].default == DEFAULT_DECOMPOSITION_MODE == "template"
    for name in ("llm_invoke", "llm_model", "llm_error", "budget"):
        assert params[name].default is None


def test_bare_decompose_records_the_template_source():
    plan = SwarmTaskDecomposer.decompose(GOAL)
    assert plan.metrics["decomposition"] == {"source": "template", "reason": "decomposition_mode=template"}


# -- The cost gate runs before any seam ------------------------------------


def test_template_mode_never_touches_the_seam():
    recorder = _Recorder(reply=_reply([_task("a1")]))
    template_plan, template_resolution = _template()
    record, plan, resolution = source_decomposition(GOAL, template_plan=template_plan, template_resolution=template_resolution, mode="template", invoke=recorder)
    assert recorder.calls == 0
    assert record == {"source": "template", "reason": "decomposition_mode=template"}
    assert plan is template_plan
    assert resolution is template_resolution


def test_explicit_topology_mode_keeps_its_template_and_skips_the_seam():
    recorder = _Recorder(reply=_reply([_task("a1")]))
    template_plan, template_resolution = _template()
    record, plan, _ = source_decomposition(
        GOAL,
        template_plan=template_plan,
        template_resolution=template_resolution,
        mode="model",
        requested_mode=SwarmMode.DEBATE,
        invoke=recorder,
        model="m-x",
    )
    assert recorder.calls == 0, "an explicit topology must never reach a model seam"
    assert record["source"] == "template"
    assert "debate" in record["reason"]
    assert "honored as given" in record["reason"]
    # The configured label stays visible even on a skipped path: it says what
    # *would* have been used, never that a call happened.
    assert record["model"] == "m-x"
    assert plan is template_plan


def test_missing_invoke_is_named_and_carrying_errors_win():
    record, _, _ = _source(mode="model", invoke=None, error=None)
    assert record == {"source": "template", "reason": "model_decomposition_not_configured"}

    record, _, _ = _source(mode="model", invoke=None, error="RuntimeError: no such model", model="m-x")
    assert record["source"] == "template"
    assert record["reason"] == "RuntimeError: no such model"
    assert record["model"] == "m-x"
    assert "tokens" not in record, "no call was attempted, so no usage key belongs in the record"


def test_zero_token_ceiling_blocks_before_the_call():
    recorder = _Recorder(reply=_reply([_task("a1")]))
    record, _, _ = _source(mode="model", invoke=recorder, budget=_Budget(max_tokens=0))
    assert recorder.calls == 0, "a zero ceiling must block the call, not clamp it"
    assert record["source"] == "template"
    assert record["reason"].startswith("budget_precheck:")
    assert "0" in record["reason"]


def test_auto_needs_a_declared_budget_but_explicit_model_is_its_own_opt_in():
    recorder = _Recorder(reply=_reply([_task("a1")]))
    record, _, _ = _source(mode="auto", invoke=recorder, budget=None)
    assert recorder.calls == 0, "a heuristic default must never start spending without a budget"
    assert record["reason"].startswith("budget_precheck:")
    assert "auto" in record["reason"]

    recorder = _Recorder(reply=_reply([_task("a1")]))
    record, _, _ = _source(mode="auto", invoke=recorder, budget=_Budget(max_tokens=1_000))
    assert recorder.calls == 1
    assert record["source"] == "model"

    recorder = _Recorder(reply=_reply([_task("a1")]))
    record, _, _ = _source(mode="model", invoke=recorder, budget=None)
    assert recorder.calls == 1, "the operator's explicit model mode is its own opt-in"
    assert record["source"] == "model"


@pytest.mark.parametrize(
    ("mode", "max_tokens", "allowed"),
    [
        ("model", None, True),
        ("model", 1, True),
        ("model", 0, False),
        ("auto", None, False),
        ("auto", 1, True),
        ("auto", -5, False),
    ],
)
def test_budget_precheck_matrix(mode, max_tokens, allowed):
    ok, reason = budget_precheck(mode, _Budget(max_tokens=max_tokens))
    assert ok is allowed
    assert (reason == "") is allowed


# -- A failed or invalid call falls back with its reason --------------------


def test_raising_invoke_falls_back_with_the_failure_named():
    recorder = _Recorder(error=ValueError("boom"))
    record, _, _ = _source(mode="model", invoke=recorder, model="m-x")
    assert recorder.calls == 1
    assert record["source"] == "template"
    assert record["reason"].startswith("model_decomposition_failed: ValueError: boom")
    assert record["model"] == "m-x"
    assert record["tokens"] is None, "a call that never returned a reply has no measured usage"


def test_non_json_reply_is_rejected_with_its_tokens_preserved():
    recorder = _Recorder(reply=_Message("there is no json here", tokens=88))
    record, _, _ = _source(mode="model", invoke=recorder, model="m-x")
    assert recorder.calls == 1
    assert record["source"] == "template"
    assert record["reason"].startswith("model_decomposition_rejected:")
    assert record["tokens"] == 88, "the spend happened even though the reply was unusable"


def test_empty_reply_carries_no_text_and_stays_honest():
    record, _, _ = _source(mode="model", invoke=_Recorder(reply=_Message("")), model="m-x")
    assert record["source"] == "template"
    assert record["reason"].startswith("model_decomposition_rejected:")
    assert "no text content" in record["reason"]
    assert record["tokens"] is None


def test_oversized_reply_is_refused_by_the_bound_before_the_clip():
    with pytest.raises(DecompositionRejected) as excinfo:
        parse_decomposition_response("x" * 17_000)
    assert "extraction bound" in excinfo.value.reason


# -- Envelope and candidate validation -------------------------------------


def test_envelope_requires_the_single_candidates_key():
    parse_decomposition_response(json.dumps({"candidates": [[_task("a1")]]}))  # no raise

    with pytest.raises(DecompositionRejected) as excinfo:
        parse_decomposition_response(json.dumps({"candidates": [[_task("a1")]], "note": "hi"}))
    assert "unexpected top-level field(s): note" in excinfo.value.reason

    with pytest.raises(DecompositionRejected) as excinfo:
        parse_decomposition_response(json.dumps({"candidates": "not-a-list"}))
    assert '"candidates" must be a list of task lists' in excinfo.value.reason

    with pytest.raises(DecompositionRejected) as excinfo:
        parse_decomposition_response(json.dumps({"candidates": []}))
    assert "no candidates" in excinfo.value.reason


def test_candidate_count_bound_is_refused_not_clamped():
    payload = json.dumps({"candidates": [[_task(f"t{i}")] for i in range(MAX_MODEL_CANDIDATES + 1)]})
    with pytest.raises(DecompositionRejected) as excinfo:
        parse_decomposition_response(payload)
    assert f"exceeds the bound of {MAX_MODEL_CANDIDATES}" in excinfo.value.reason


def test_task_count_bound_is_refused_not_clamped():
    specs, reasons = validate_candidate([_task(f"t{i}") for i in range(MAX_MODEL_TASKS + 1)])
    assert specs == []
    assert reasons == [f"{MAX_MODEL_TASKS + 1} tasks exceeds the bound of {MAX_MODEL_TASKS}"]


def test_missing_and_unexpected_fields_are_both_named():
    raw = _task("a1")
    del raw["deps"]
    raw["priority"] = 3
    specs, reasons = validate_candidate([raw])
    assert specs == []
    joined = " ".join(reasons)
    assert "missing field(s): deps" in joined
    assert "unexpected field(s): priority" in joined


def test_duplicate_unknown_and_self_dependencies_are_named():
    specs, reasons = validate_candidate([_task("a1"), _task("a1")])
    assert specs == []
    assert "duplicate task id(s): a1" in reasons[0]

    specs, reasons = validate_candidate([_task("a1", deps=("ghost",))])
    assert specs == []
    assert "unknown dependency id(s): ghost" in reasons[0]

    specs, reasons = validate_candidate([_task("a1", deps=("a1",))])
    assert specs == []
    assert "depends on itself" in reasons[0]


def test_dependency_cycle_is_named_with_its_path():
    specs, reasons = validate_candidate([_task("a1", deps=("b1",)), _task("b1", deps=("a1",))])
    assert specs == []
    assert len(reasons) == 1
    assert reasons[0].startswith("dependency cycle:")
    assert "a1" in reasons[0] and "b1" in reasons[0]


def test_artifacts_without_criteria_are_refused_at_the_model_boundary():
    specs, reasons = validate_candidate([_task("a1", artifacts=("out/report.md",))])
    assert specs == []
    assert "without acceptance_criteria" in reasons[0]

    # Through the entry point the same refusal becomes a template fallback
    # whose record carries the candidate's own reason — not a refused swarm.
    record, plan, _ = _source(mode="model", invoke=_Recorder(reply=_reply([_task("a1", artifacts=("out/report.md",))])))
    assert record["source"] == "template"
    assert record["reason"] == "model_decomposition_no_valid_candidate"
    assert any("without acceptance_criteria" in reason for reason in record["rejected_reasons"])
    assert record["candidates"] == 1 and record["valid"] == 0


def test_estimated_seconds_bounds_are_refused_with_the_bound_named():
    raw = _task("a1")
    raw["estimated_seconds"] = 0
    specs, reasons = validate_candidate([raw])
    assert specs == []
    assert "(0, 3600]" in reasons[0]

    raw = _task("a1")
    raw["estimated_seconds"] = True
    specs, reasons = validate_candidate([raw])
    assert specs == []
    assert "must be a number" in reasons[0]


# -- A valid reply becomes a model-sourced plan -----------------------------


def test_valid_candidates_produce_a_model_plan_with_auditable_selection():
    recorder = _Recorder(
        reply=_Message(json.dumps({"candidates": [[_task("a1"), _task("b1", deps=("a1",))]]}), tokens=1234),
    )
    record, plan, resolution = _source(mode="model", invoke=recorder, model="m-test")

    assert recorder.calls == 1
    assert record["source"] == "model"
    assert record["basis"] == LLM_DECOMPOSITION_BASIS
    assert record["model"] == "m-test"
    assert record["candidates"] == 1
    assert record["valid"] == 1
    assert record["chosen"] == 0
    assert record["rejected_reasons"] == []
    assert record["tokens"] == 1234

    assert plan.mode == plan.metrics["strategy"]["mode"], "the plan and its recorded strategy must agree"
    assert set(plan.tasks) == {"a1", "b1"}
    assert plan.tasks["b1"].dependencies == ["a1"]
    assert plan.metrics["strategy_candidates"], "the losing/scored candidates stay auditable"
    assert resolution.to_dict()["mode"] == plan.mode.value

    # Declared tags are the node's declared requirement, not an inference.
    assert plan.tasks["a1"].capability_source == "declared"


def test_unreported_usage_stays_none_never_zero():
    recorder = _Recorder(reply=_Message(json.dumps({"candidates": [[_task("a1")]]})))
    record, _, _ = _source(mode="model", invoke=recorder, model="m-test")
    assert record["source"] == "model"
    assert record["tokens"] is None


def test_one_valid_candidate_wins_and_the_cyclic_one_is_rejected_by_reason():
    cyclic = [_task("a1", deps=("b1",)), _task("b1", deps=("a1",))]
    recorder = _Recorder(reply=_reply(cyclic, [_task("only")]))
    record, plan, _ = _source(mode="model", invoke=recorder, model="m-test")

    assert record["source"] == "model"
    assert record["candidates"] == 2
    assert record["valid"] == 1
    assert record["chosen"] == 1
    assert any("dependency cycle" in reason for reason in record["rejected_reasons"])
    assert set(plan.tasks) == {"only"}


def test_model_plan_criteria_carry_the_evidence_contract():
    recorder = _Recorder(reply=_reply([_task("a1", criteria=("The migration applies cleanly.",))]))
    record, plan, _ = _source(mode="model", invoke=recorder, model="m-test")
    assert record["source"] == "model"
    criterion = plan.tasks["a1"].acceptance_criteria[0]
    assert criterion["id"] == "The migration applies cleanly."
    assert criterion["description"] == "The migration applies cleanly."
    assert criterion["required_evidence_kinds"] == [], "no kind is mandated, but evidence is still required"


# -- Full decompose() wiring ------------------------------------------------


def test_decompose_forwards_the_seam_and_records_the_model_source():
    recorder = _Recorder(reply=_reply([_task("a1")]))
    plan = SwarmTaskDecomposer.decompose(
        GOAL,
        decomposition_mode="model",
        llm_invoke=recorder,
        llm_model="m-wire",
        budget=_Budget(max_tokens=100),
    )
    assert recorder.calls == 1
    assert plan.metrics["decomposition"]["source"] == "model"
    assert plan.metrics["decomposition"]["model"] == "m-wire"
    assert plan.mode == plan.metrics["strategy"]["mode"]


def test_decompose_honours_the_budget_and_the_requested_topology():
    recorder = _Recorder(reply=_reply([_task("a1")]))
    plan = SwarmTaskDecomposer.decompose(GOAL, decomposition_mode="model", llm_invoke=recorder, budget=_Budget(max_tokens=0))
    assert recorder.calls == 0
    assert plan.metrics["decomposition"]["source"] == "template"
    assert "budget_precheck" in plan.metrics["decomposition"]["reason"]

    recorder = _Recorder(reply=_reply([_task("a1")]))
    plan = SwarmTaskDecomposer.decompose(GOAL, mode=SwarmMode.HIERARCHICAL, decomposition_mode="model", llm_invoke=recorder)
    assert recorder.calls == 0
    assert plan.metrics["decomposition"]["source"] == "template"
    assert "honored as given" in plan.metrics["decomposition"]["reason"]
    assert plan.mode is SwarmMode.HIERARCHICAL


# -- Production seam construction ------------------------------------------


def test_build_production_invoke_reports_a_construction_failure(monkeypatch):
    monkeypatch.setattr("alpha.config.get_app_config", lambda: SimpleNamespace(default_model_name="m1"))

    def _explode(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr("alpha.models.create_chat_model", _explode)
    source = llm.build_production_invoke()
    assert source.invoke is None
    assert source.model == "m1", "the configured name survives so the record says what was tried"
    assert source.error is not None and "RuntimeError: nope" in source.error


def test_build_production_invoke_without_a_default_model(monkeypatch):
    monkeypatch.setattr("alpha.config.get_app_config", lambda: SimpleNamespace(default_model_name=""))
    source = llm.build_production_invoke()
    assert source.invoke is None
    assert source.error == "no default model is configured"


def test_build_production_invoke_returns_a_real_roundtrip_seam(monkeypatch):
    expected = _Message(json.dumps({"candidates": [[_task("a1")]]}), tokens=5)

    class _Model:
        def __init__(self):
            self.seen = None

        def invoke(self, messages):
            self.seen = messages
            return expected

    model = _Model()
    monkeypatch.setattr("alpha.config.get_app_config", lambda: SimpleNamespace(default_model_name="m1"))
    monkeypatch.setattr("alpha.models.create_chat_model", lambda name, app_config=None: model)

    source = llm.build_production_invoke()
    assert source.error is None
    assert source.model == "m1"
    assert source.invoke is not None
    assert source.invoke("system text", "user text") is expected
    assert model.seen == [("system", "system text"), ("human", "user text")]


def test_resolve_decomposition_mode_reads_config_and_fails_open_to_template(monkeypatch):
    monkeypatch.setattr("alpha.config.get_app_config", lambda: SimpleNamespace(swarm=SimpleNamespace(decomposition="model")))
    assert llm.resolve_decomposition_mode() == "model"

    def _broken():
        raise OSError("config unreadable")

    monkeypatch.setattr("alpha.config.get_app_config", _broken)
    assert llm.resolve_decomposition_mode() == "template", "a config fault must never become a model call"


# -- create_swarm wiring ----------------------------------------------------


def test_create_swarm_default_mode_never_constructs_a_model_source(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "resolve_decomposition_mode", lambda: "template")

    def _explode():
        raise AssertionError("template mode must never construct a model source")

    monkeypatch.setattr(llm, "build_production_invoke", _explode)

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = coordinator.create_swarm(GOAL)
    assert plan.metrics["decomposition"] == {"source": "template", "reason": "decomposition_mode=template"}


def test_create_swarm_model_mode_wires_the_production_source(tmp_path, monkeypatch):
    recorder = _Recorder(reply=_reply([_task("a1")]))
    monkeypatch.setattr(llm, "resolve_decomposition_mode", lambda: "model")
    monkeypatch.setattr(llm, "build_production_invoke", lambda: ProductionDecomposeSource(invoke=recorder, model="m-prod"))

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = coordinator.create_swarm(GOAL)
    assert recorder.calls == 1
    assert plan.metrics["decomposition"]["source"] == "model"
    assert plan.metrics["decomposition"]["model"] == "m-prod"
    assert coordinator.get_swarm(plan.swarm_id) is plan


def test_create_swarm_reports_a_failed_construction_as_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "resolve_decomposition_mode", lambda: "model")
    monkeypatch.setattr(
        llm,
        "build_production_invoke",
        lambda: ProductionDecomposeSource(invoke=None, model="m-prod", error="RuntimeError: ctor failed"),
    )

    coordinator = SwarmCoordinator(storage_dir=tmp_path / "swarms")
    plan = coordinator.create_swarm(GOAL)
    record = plan.metrics["decomposition"]
    assert record["source"] == "template"
    assert record["model"] == "m-prod"
    assert record["reason"] == "RuntimeError: ctor failed", "a silent slip back to templates would hide why nothing model-shaped happened"
    assert "tokens" not in record


# -- Prompt contract --------------------------------------------------------


def test_prompt_carries_the_schema_and_discloses_truncation():
    prompt = build_decompose_prompt("x" * 5_000, items=["one", "two"], max_concurrency=4)
    assert '"candidates"' in prompt["system"]
    assert "acceptance_criteria" in prompt["system"]
    assert "output_artifacts" in prompt["system"]
    assert "(goal truncated at 4000 characters)" in prompt["user"]
    assert "- one" in prompt["user"]
    assert "Max concurrency: 4" in prompt["user"]
