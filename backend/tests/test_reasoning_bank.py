"""ReasoningBank: durable, evidence-gated procedure memory (harness-level RSI).

Pins the contract: records aggregate measured verdicts, never self-asserted
confidence; recall is deterministic and bounded; failures remain visible
unless the caller asks to drop them; a corrupt store degrades to empty
rather than crashing the process; and a "success" verdict without
evidence is demoted to "unknown" — the bank cannot be talked into trusting
an unevidenced win.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path / ".alpha"))
    for module_name in ("bank",):
        module = __import__(f"alpha.reasoning_bank.{module_name}", fromlist=["_bank", "_bank_path"])
        monkeypatch.setattr(module, "_bank", None, raising=False)
        monkeypatch.setattr(module, "_bank_path", None, raising=False)
    yield


def _bank(tmp_path):
    from alpha.reasoning_bank.bank import ReasoningBank

    return ReasoningBank(tmp_path / "bank.json")


class TestRecord:
    def test_success_with_evidence_is_stored(self, tmp_path):
        bank = _bank(tmp_path)
        record = bank.record("code", "flaky tests", "rerun with -p no:cacheprovider", verdict="success", evidence_ref="run:abc")
        assert record.attempts == 1
        assert record.wins == 1
        assert record.losses == 0
        assert bank.path.exists()

    def test_unevidenced_success_is_demoted_to_unknown(self, tmp_path):
        bank = _bank(tmp_path)
        record = bank.record("code", "flaky tests", "rerun", verdict="success", evidence_ref="")
        assert record.verdict == "unknown"
        assert record.wins == 0
        assert record.losses == 0

    def test_same_strategy_upserts_and_aggregates(self, tmp_path):
        bank = _bank(tmp_path)
        first = bank.record("code", "flaky tests", "rerun", verdict="failure", evidence_ref="run:abc")
        second = bank.record("code", "flaky tests", "rerun", verdict="success", evidence_ref="run:def")
        assert first.strategy_id == second.strategy_id
        loaded = bank.get(second.strategy_id)
        assert loaded.attempts == 2
        assert loaded.wins == 1
        assert loaded.losses == 1
        assert len(bank.records()) == 1

    def test_failure_stays_honest(self, tmp_path):
        bank = _bank(tmp_path)
        record = bank.record("code", "flaky tests", "rerun", verdict="failure", evidence_ref="run:abc")
        assert record.verdict == "failure"
        assert record.wins == 0
        assert record.losses == 1


class TestRecall:
    def test_relevant_ranks_above_unrelated(self, tmp_path):
        bank = _bank(tmp_path)
        bank.record("code", "flaky tests", "rerun with -p no:cacheprovider", verdict="success", evidence_ref="r1")
        bank.record("docs", "spellcheck", "run the md linter", verdict="success", evidence_ref="r2")
        results = bank.recall("flaky tests rerun cacheprovider", limit=5)
        assert results, "expected a match"
        assert results[0].scope == "code"

    def test_limit_is_respected(self, tmp_path):
        bank = _bank(tmp_path)
        for i in range(10):
            bank.record("code", f"t{i}", f"strategy {i}", verdict="success", evidence_ref=f"r{i}")
        assert len(bank.recall("strategy", limit=3)) == 3

    def test_include_failures_false_drops_losing_strategies(self, tmp_path):
        bank = _bank(tmp_path)
        bank.record("code", "x", "bad strategy", verdict="failure", evidence_ref="r1")
        bank.record("code", "y", "good strategy", verdict="success", evidence_ref="r2")
        results = bank.recall("strategy", include_failures=False)
        assert {r.strategy for r in results} == {"good strategy"}

    def test_render_is_bounded(self, tmp_path):
        bank = _bank(tmp_path)
        for i in range(8):
            bank.record("code", "x", f"strategy number {i} with lots of wrapper text to pad the rendering out", verdict="success", evidence_ref=f"r{i}")
        rendered = bank.render("strategy", limit=3, max_chars=600)
        assert len(rendered) <= 600
        assert rendered.count("\n") <= 4


class TestStore:
    def test_corrupt_file_starts_empty_without_raising(self, tmp_path):
        path = tmp_path / "bank.json"
        path.write_text("{ not json", encoding="utf-8")
        from alpha.reasoning_bank.bank import ReasoningBank

        bank = ReasoningBank(path)
        assert bank.records() == []
        bank.record("code", "x", "y", verdict="success", evidence_ref="r1")
        assert path.exists()

    def test_upsert_persists_across_instances(self, tmp_path):
        bank = _bank(tmp_path)
        bank.record("code", "x", "y", verdict="success", evidence_ref="r1")
        fresh = _bank(tmp_path)
        assert len(fresh.records()) == 1

    def test_prune_keeps_best_records(self, tmp_path):
        bank = _bank(tmp_path)
        keep = bank.record("code", "x", "winner", verdict="success", evidence_ref="r1")
        bank.record("code", "y", "loser", verdict="failure", evidence_ref="r2")
        bank.prune(max_records=1)
        remaining = bank.records()
        assert len(remaining) == 1
        assert remaining[0].strategy_id == keep.strategy_id

    def test_update_reinforces(self, tmp_path):
        bank = _bank(tmp_path)
        record = bank.record("code", "x", "y", verdict="success", evidence_ref="r1")
        updated = bank.reinforce(record.strategy_id, "failure")
        assert updated.attempts == 2
        assert updated.losses == 1
        assert bank.get(record.strategy_id).losses == 1


class TestSingleton:
    def test_get_reasoning_bank_caches_for_the_default_path(self):
        from alpha.reasoning_bank.bank import get_reasoning_bank

        first = get_reasoning_bank()
        assert first is get_reasoning_bank()

    def test_default_path_uses_runtime_home(self, tmp_path):
        from alpha.reasoning_bank.bank import get_reasoning_bank

        bank = get_reasoning_bank()
        assert ".alpha" in bank.path.parts
        assert bank.path.name == "records.json"


class TestRalphSeedHelpers:
    def test_round_prompt_includes_hints_at_round_one(self):
        from alpha.tools.builtins.self_improvement_tool import _round_prompt

        prompt = _round_prompt("do thing", "tests_passed: make test", 1, None, hints="\n- try: clean first")
        assert "Previous similar tasks" in prompt
        assert "try: clean first" in prompt

    def test_round_prompt_without_hints_unchanged(self):
        from alpha.tools.builtins.self_improvement_tool import _round_prompt

        prompt = _round_prompt("do thing", "tests_passed: make test", 1, None)
        assert "Previous similar tasks" not in prompt
        assert "Address the promise point by point" in prompt
