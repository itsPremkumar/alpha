from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from alpha.memory.evaluation import Case, EvaluationConfig, RealStoreProvider, ScriptedProvider, UnavailableProvider
from alpha.memory.evaluation.metrics import (
    abstention_rate,
    contamination_rate,
    evidence_traceability,
    recall_at_k,
    scope_matches,
    token_efficiency,
    update_correctness,
    write_precision,
)
from alpha.memory.evaluation.models import ABILITIES, MemoryRecord, QuestionOutcome
from alpha.memory.evaluation.report import render_json, render_text
from alpha.memory.evaluation.runner import WriteReceipt, run_case
from alpha.memory.evaluation.suite import (
    aggregate_results,
    load_cases,
    run_suite,
    write_baseline,
)

CASES_DIR = Path(__file__).parents[1] / "packages" / "harness" / "alpha" / "memory" / "evaluation" / "cases"


def _case_for(ability: str) -> Case:
    return next(case for case in load_cases(CASES_DIR) if case.ability == ability)


class _AnswerProvider:
    def __init__(self, answer: str, *, scope: dict[str, Any] | None = None, record_id: str = "record-1") -> None:
        self.answer = answer
        self.scope = scope
        self.record_id = record_id
        self.available = True

    def write(self, event: Any) -> WriteReceipt:
        return WriteReceipt(event.id, stored=True)

    def recall(self, query: str, scope: dict[str, Any], now: str) -> list[MemoryRecord]:
        return [MemoryRecord(id=self.record_id, content=self.answer, scope=self.scope or scope, answer=self.answer)]

    def forget(self, id: str) -> None:
        return None


class _NoBackingProvider(_AnswerProvider):
    def recall(self, query: str, scope: dict[str, Any], now: str) -> list[MemoryRecord]:
        return []


class _ExplodingProvider:
    available = True

    def write(self, event: Any) -> Any:
        raise AssertionError("disabled suite touched provider.write")

    def recall(self, query: str, scope: dict[str, Any], now: str) -> Any:
        raise AssertionError("disabled suite touched provider.recall")

    def forget(self, id: str) -> Any:
        raise AssertionError("disabled suite touched provider.forget")


def test_every_bundled_case_is_valid_json_and_declares_a_known_ability() -> None:
    paths = sorted(CASES_DIR.glob("*.json"))
    assert len(paths) >= 12
    parsed: list[Case] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        case = Case.from_dict(payload)
        assert case.ability in ABILITIES
        assert case.id == payload["id"]
        parsed.append(case)
    assert len({case.id for case in parsed}) == len(parsed)
    assert {case.ability for case in parsed} >= set(ABILITIES)


@pytest.mark.parametrize("ability", ABILITIES)
def test_scripted_oracle_passes_one_original_case_per_ability(ability: str) -> None:
    case = _case_for(ability)
    result = run_case(case, ScriptedProvider.from_case(case), provider_kind="scripted")
    assert result.status == "pass", (case.id, result.reason, result.to_dict())
    assert result.evidence_kind == "scripted_oracle"
    assert result.question_outcomes


def test_real_store_adapter_runs_against_an_injected_scope_store() -> None:
    class Store:
        def __init__(self) -> None:
            self.records: list[dict[str, Any]] = []

        def put_records(self, records: list[dict[str, Any]], *, user_id: str | None, agent_name: str | None) -> int:
            self.records.extend(records)
            return len(records)

        def search(self, query: str, top_k: int, *, user_id: str | None, agent_name: str | None) -> list[dict[str, Any]]:
            return self.records[:top_k]

        def delete_records(self, record_ids: list[str], *, user_id: str | None, agent_name: str | None) -> int:
            before = len(self.records)
            self.records = [record for record in self.records if record["id"] not in set(record_ids)]
            return before - len(self.records)

    def record_factory(event: Any, scope: dict[str, Any]) -> dict[str, Any]:
        return {"id": event.id, "content": event.text, "metadata": {"event_id": event.id, "scope": dict(scope)}}

    case = _case_for("extraction")
    result = run_case(case, RealStoreProvider(Store(), record_factory=record_factory), provider_kind="real")
    assert result.status == "pass", result.to_dict()
    assert result.evidence_kind == "provider_returned_records"
    assert result.question_outcomes[0].evidence_ids


def test_wrong_provider_fails_and_identifies_the_offending_question() -> None:
    case = _case_for("extraction")
    result = run_case(case, _AnswerProvider("purple"), provider_kind="real")
    assert result.status == "fail"
    assert result.question_outcomes[0].question_id == "q1"
    assert "answer did not match expected value" in result.reason


def test_unavailable_provider_is_unavailable_for_every_case_not_zero_or_pass() -> None:
    cases = load_cases(CASES_DIR)
    report = run_suite(cases, UnavailableProvider(), config=EvaluationConfig(enabled=True), provider_kind="real")
    assert report.ran_cases == 0
    assert report.unavailable_cases == len(cases)
    assert all(result.status == "unavailable" for result in report.results)
    assert all(value is None for value in report.metrics.values())
    assert not report.passed


def test_write_precision_case_fails_when_unexpected_noise_is_stored() -> None:
    case = _case_for("write_precision")
    result = run_case(case, _AnswerProvider("Jo", scope=case.scope, record_id="important"), provider_kind="real")
    assert result.write_precision == 0.5
    assert result.status == "fail"
    assert "unexpected event" in result.reason


def test_abstention_requires_a_refusal_and_rejects_a_guess() -> None:
    case = _case_for("abstention")
    guessed = run_case(case, _AnswerProvider("hunter2"), provider_kind="real")
    assert guessed.status == "fail"
    assert guessed.question_outcomes[0].expects_abstention is True
    assert guessed.question_outcomes[0].correct is False
    refused = run_case(case, _AnswerProvider("I do not know."), provider_kind="real")
    assert refused.status == "pass"


def test_contamination_detection_fires_for_a_foreign_scope_record() -> None:
    case = _case_for("contamination")
    provider = _AnswerProvider("N-17", scope={"user_id": "foreign-user", "agent_name": "alpha-memory-evaluation"}, record_id="foreign-1")
    result = run_case(case, provider, provider_kind="real")
    assert result.status == "fail"
    assert result.question_outcomes[0].foreign_evidence_ids == ("foreign-1",)
    assert "foreign-scope" in result.reason


def test_temporal_question_rejects_a_conflicting_historical_value() -> None:
    case = _case_for("temporal")
    provider = _AnswerProvider("blue red", scope=case.scope, record_id="both-values")
    result = run_case(case, provider, provider_kind="real")
    assert result.status == "fail"
    assert result.question_outcomes[0].update_correct is False


def test_update_correctness_rejects_a_superseded_value() -> None:
    case = _case_for("update")
    provider = _AnswerProvider("coffee tea", scope=case.scope, record_id="current-and-old")
    result = run_case(case, provider, provider_kind="real")
    assert result.status == "fail"
    assert result.question_outcomes[0].update_correct is False
    assert "superseded" in result.reason


def test_evidence_traceability_fails_without_a_backing_returned_record() -> None:
    case = _case_for("evidence_traceability")
    result = run_case(case, _NoBackingProvider("canary-check"), provider_kind="real")
    assert result.status == "fail"
    assert result.question_outcomes[0].evidence_ids == ()
    assert "no backing returned record" in result.reason
    assert evidence_traceability(result.question_outcomes) == 0.0


def test_runner_drives_explicit_forget_ids() -> None:
    case = Case.from_dict(
        {
            "id": "synthetic-forget",
            "ability": "extraction",
            "scope": {"user_id": "forget-user", "agent_name": "alpha-memory-evaluation"},
            "sessions": [{"session_id": "s1", "events": [{"id": "old", "text": "Old value", "expected_stored": False}, {"id": "new", "text": "New value", "expected_stored": True}]}],
            "questions": [{"id": "q1", "question": "What is the value?", "expected": "New value", "type": "information_extraction", "evidence_ids": ["new"]}],
            "metadata": {"expected_stored": ["new"], "forget_ids": ["old"]},
        }
    )
    provider = ScriptedProvider.from_case(case)
    result = run_case(case, provider, provider_kind="scripted")
    assert result.status == "pass"
    assert provider.forgotten_ids == {"old"}
    assert result.forgotten_ids == ("old",)


def test_metric_formulas_are_explicit() -> None:
    assert recall_at_k(["a", "b", "c"], {"a", "z"}, 2) == 0.5
    assert write_precision(["keep", "extra"], {"keep"}) == 0.5
    assert update_correctness("The current value is coffee", "coffee", ["tea"])
    assert not update_correctness("coffee tea", "coffee", ["tea"])
    assert scope_matches({"user_id": "u"}, {"user_id": "u"})
    assert not scope_matches({"user_id": "u"}, {"user_id": "other"})
    refusal = QuestionOutcome("q", "I do not know", True, expects_abstention=True)
    guess = QuestionOutcome("q", "42", False, expects_abstention=True)
    assert abstention_rate([refusal, guess]) == 0.5
    traced = QuestionOutcome("q", "answer", True, evidence_ids=("r1",), returned_record_ids=("r1",), useful_fact_count=1, composed_tokens=2)
    untraced = QuestionOutcome("q", "answer", True, useful_fact_count=1, composed_tokens=2)
    assert evidence_traceability([traced, untraced]) == 0.5
    assert token_efficiency([traced, untraced]) == 2.0
    assert contamination_rate([traced]) == 0.0


def test_threshold_and_baseline_comparison_reports_deltas(tmp_path: Path) -> None:
    case = _case_for("extraction")
    config = EvaluationConfig(enabled=True, min_extraction_recall=1.0)
    good = run_suite([case], provider=ScriptedProvider.from_case(case), provider_kind="scripted", config=config)
    baseline_path = tmp_path / "baseline.json"
    write_baseline(good, baseline_path)
    bad = run_suite([case], provider=_AnswerProvider("wrong"), provider_kind="real", config=config, baseline_path=baseline_path)
    assert bad.thresholds[0].metric == "extraction_recall"
    assert bad.thresholds[0].passed is False
    assert bad.status == "fail"
    assert bad.baseline is not None
    assert bad.baseline.overall["extraction_recall"]["delta"] == -1.0
    assert bad.baseline.per_ability["extraction"]["extraction_recall"]["delta"] == -1.0
    assert bad.baseline.passed is False


def test_disabled_config_is_a_noop_before_provider_access() -> None:
    report = run_suite(load_cases(CASES_DIR), _ExplodingProvider(), config=EvaluationConfig(enabled=False), provider_kind="real")
    assert report.enabled is False
    assert report.status == "disabled"
    assert report.ran_cases == 0
    assert report.reason == "memory evaluation disabled by config"


def test_report_rendering_shows_unavailable_and_never_fakes_a_zero() -> None:
    case = _case_for("abstention")
    report = aggregate_results([case], [run_case(case, UnavailableProvider(), provider_kind="real")])
    text = render_text(report)
    payload = json.loads(render_json(report))
    assert "UNAVAILABLE" in text
    assert "memory provider reported unavailable" in text
    assert "0 of 1 cases ran; 1 unavailable" in text
    assert payload["metrics"]["abstention_rate"] is None
    assert "abstention_rate: unavailable" in text


def test_every_config_key_has_a_reader() -> None:
    values = {
        "enabled": True,
        "cases_dir": "cases",
        "min_extraction_recall": 0.91,
        "min_multi_session_accuracy": 0.82,
        "min_temporal_accuracy": 0.73,
        "min_update_accuracy": 0.99,
        "min_abstention_rate": 0.88,
        "max_contamination_rate": 0.01,
        "baseline_path": "baseline.json",
        "max_cases": 7,
        "storage_path": "tmp-memory-evaluation",
    }
    config = EvaluationConfig.from_mapping(values)
    assert set(config.keys()) == set(values)
    for key, value in values.items():
        assert config.get(key) == value
        assert config.read_key(key) == value
        assert config[key] == value
    assert config.to_dict() == values


def test_package_root_exports_are_lazy_and_original_cases_are_declared() -> None:
    import alpha.memory.evaluation as evaluation

    assert evaluation.Case is Case
    assert "LoCoMo" in evaluation.__doc__
    assert "LongMemEval" in evaluation.__doc__
    assert callable(evaluation.run_suite)
    assert callable(evaluation.load_cases)
