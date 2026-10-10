"""The generator ↔ proposal-plan seam: P1 as a hard cardinality constraint, and the engine's disclosure.

Three properties are pinned here:

* **The plan block is part of the payload, so it is part of the hash.** A
  variant whose recorded constraints were not hashed could be replayed from a
  payload that no longer carries them, and the lineage hash would still match.
* **Over-budget variants are discarded whole.** P1 is ``‖z_t‖₀ ≤ b_t``; dropping
  one edit of a coupled pair would leave a candidate the operator did not write.
* **Provenance is not an edit.** ``operator`` / ``operator_choice`` describe
  *how* a variant was produced, not *what* it changes — counting them would put
  every template variant permanently over an annealed budget of 1.

Plus the engine-side disclosure: a preview cycle records its round as
UNMEASURED and prints the plan as a constraint, and none of that may collide
with the fabricated-score ban the RSI evidence already carries.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from alpha.rsi.engine import RSIEngine
from alpha.rsi.generator import CandidateFactory, _rrsi_edit_count
from alpha.rsi.lineage import _canonical_payload_hash
from alpha.rsi.rrsi import BudgetOutcome, RrsiHistory, build_proposal_plan

VALID_EVIDENCE = [{"ref": "evidence://benchmark/run-1"}]

#: Substrings that must never appear in RSI evidence — they are the fabricated
#: scores ``tests/test_rsi_cycle.py`` already bans. The plan lines must not
#: smuggle any of them back in.
FABRICATED = ("0.88", "0.89", "0.72", "0.80", "0.92")


@pytest.fixture(autouse=True)
def rsi_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    return tmp_path


def _plan(round_index: int = 1):
    return build_proposal_plan(round_index, round_scores=[], history=RrsiHistory(rsi_history_path()))


def rsi_history_path():
    from alpha.rsi.rrsi import history_path

    return history_path()


def _generate(factory: CandidateFactory, *, plan, surface="prompt", target="compaction"):
    return factory.generate("hyp-1", target=target, surface=surface, evidence_refs=list(VALID_EVIDENCE), population=3, plan=plan)


# --- P1 cardinality -----------------------------------------------------------


def test_provenance_is_not_an_edit():
    base = {"hypothesis_id": "h", "surface": "prompt", "target": "t"}
    produced = dict(base, operator="conservative", operator_choice={"why": "n/a"}, mutation={"class": "conservative"})
    assert _rrsi_edit_count(base, produced) == 1, "only `mutation` changes the harness; the two provenance keys do not"
    assert _rrsi_edit_count(base, dict(produced, operator=None, operator_choice=None)) == 1


def test_every_added_changed_or_removed_field_counts_as_an_edit():
    base = {"hypothesis_id": "h", "surface": "prompt", "target": "t"}
    changed = dict(base, target="t2")
    assert _rrsi_edit_count(base, changed) == 1
    added = dict(base, extra="x")
    assert _rrsi_edit_count(base, added) == 1
    removed = {"hypothesis_id": "h", "surface": "prompt"}
    assert _rrsi_edit_count(base, removed) == 1
    assert _rrsi_edit_count(base, dict(base, operator="conservative")) == 0, "provenance alone is not a change"


def test_an_over_budget_variant_is_discarded_whole_never_trimmed(rsi_home):
    """Budget 0 against one real edit: no variant survives, and none is partially applied."""
    plan = _plan(1)
    tight = replace(plan, budget=BudgetOutcome(budget=0, round_index=plan.round_index, kept=(), dropped=(), saturated=False, reason="forced zero budget for this test"))

    factory = CandidateFactory()
    specs = _generate(factory, plan=tight)
    assert specs == [], "an over-budget variant is discarded, not shrunk to fit"
    assert factory.skipped, "the discard must be reported, not silent"
    assert all(item["skipped"] == "over_edit_budget" for item in factory.skipped)
    assert all(item["edits"] == 1 for item in factory.skipped)
    assert all(item["budget"] == 0 for item in factory.skipped)
    assert all(isinstance(item["payload_hash"], str) and item["payload_hash"] for item in factory.skipped)


def test_a_zero_edit_budget_accepts_single_edit_variants(rsi_home):
    """The other side of the same constraint: b = 1 admits exactly what ‖z_t‖₀ = 1 produces.

    Proves the discard above is the budget doing its job rather than a gate
    that refuses every candidate.
    """
    plan = _plan(1)
    tight = replace(plan, budget=BudgetOutcome(budget=1, round_index=plan.round_index, kept=("mutation",), dropped=(), saturated=False, reason="budget 1 for this test"))
    factory = CandidateFactory()
    specs = _generate(factory, plan=tight)
    assert len(specs) == 3
    assert factory.skipped == []
    assert all(spec.payload["rrsi"]["edit_count"] == 1 for spec in specs)


# --- the plan block -----------------------------------------------------------


def test_the_plan_block_travels_inside_the_hashed_payload(rsi_home):
    plan = _plan(1)
    specs = _generate(CandidateFactory(), plan=plan)
    assert len(specs) == 3
    for spec in specs:
        block = spec.payload["rrsi"]
        assert block["kind"] == "rrsi_proposal_constraints"
        assert block["round_index"] == plan.round_index
        assert block["horizon"] == plan.horizon
        assert block["edit_count"] == 1
        assert block["edit_budget"] == plan.budget.budget
        assert block["component"] == "context_mgmt", "target=compaction attributes to context_mgmt"
        assert block["component_basis"] == "target_hint"
        assert block["component_hint"] == "compaction"
        assert spec.payload_hash == _canonical_payload_hash(spec.payload), "the constraints must be part of the hash, not beside it"
        # a constraint is not a verdict
        assert "score" not in block and "verdict" not in block and "improved" not in block
        assert block["plan_usable"] is plan.usable


def test_without_a_plan_the_payload_carries_no_rrsi_block(rsi_home):
    """`plan=None` is the default, and it must stay byte-identical for existing callers."""
    specs = _generate(CandidateFactory(), plan=None)
    assert len(specs) == 3
    for spec in specs:
        assert "rrsi" not in spec.payload
        assert spec.payload_hash == _canonical_payload_hash(spec.payload)


def test_a_plan_of_the_wrong_type_is_a_caller_bug_not_a_silent_default(rsi_home):
    with pytest.raises(ValueError, match="ProposalPlan or None"):
        _generate(CandidateFactory(), plan=object())


def test_the_plan_block_records_pruning_and_exploration_as_status_not_result(rsi_home):
    plan = _plan(1)
    specs = _generate(CandidateFactory(), plan=plan, surface="code", target="compaction")
    assert specs
    block = specs[0].payload["rrsi"]
    assert block["pruning_status"] in ("ok", "unavailable")
    assert isinstance(block["pruning_targets"], list)
    assert block["exploration_status"] in ("not_stalled", "applied", "unavailable")
    assert isinstance(block["exploration_targets"], list)
    assert "pruning_result" not in block and "exploration_score" not in block


# --- the engine's disclosure --------------------------------------------------


def test_a_preview_cycle_discloses_the_plan_as_a_constraint_and_the_round_as_unmeasured(rsi_home):
    engine = RSIEngine()
    result = engine.run_rsi_cycle(bottleneck="Latency", target_component="compaction")
    text = " ".join(result.evidence)

    assert "RRSI proposal plan (Algorithm 1, round 1 of T = 20): " in text
    assert "it is not a measurement and changes no verdict above" in text
    assert "RRSI round 1 recorded as UNMEASURED" in text
    assert "edit budget b = " in text
    assert "component attribution for 'compaction' -> 'context_mgmt'" in text

    plan_lines = [index for index, line in enumerate(result.evidence) if "RRSI proposal plan (Algorithm 1" in line]
    promotion_lines = [index for index, line in enumerate(result.evidence) if line.startswith("Promotion blocked")]
    assert plan_lines and promotion_lines
    assert plan_lines[0] < promotion_lines[0], "the plan constrains the round; it must precede the verdict, not replace it"
    assert result.evidence[-1].startswith("To obtain a real verdict"), "the last line is unchanged by adding a constraint above it"

    assert not any(bad in text for bad in FABRICATED), "the plan must not smuggle fabricated scores back into the evidence"
    assert "NOT MEASURED" in text
    assert result.evidence_kind == "simulated"


def test_a_second_cycle_advances_the_round_index(rsi_home):
    engine = RSIEngine()
    engine.run_rsi_cycle(bottleneck="Latency", target_component="compaction")
    second = engine.run_rsi_cycle(bottleneck="Latency", target_component="compaction")
    text = " ".join(second.evidence)
    assert "round 2 of T = 20" in text, "the round index is carried durably across cycles"
    assert not any(bad in text for bad in FABRICATED)
