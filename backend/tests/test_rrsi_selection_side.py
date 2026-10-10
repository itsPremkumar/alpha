"""RRSI selection-side regularizers: S1 critic, S2 floor, S3 branches, S5 guard, Algorithm 2, and the block-only promotion conjunct.

The selection side is where a candidate either earns admission or does not.
Three properties are load-bearing and each is pinned here:

* **Non-compensatory.** A rule that cannot be evaluated is a *third* state
  (`ok is None`), never a pass and never a silent "the rule rejected it".
  A cheaper candidate cannot buy its way past a floor it failed, and a higher
  score cannot buy its way past a cost rule it cannot pay for.
* **S1 runs before evaluation.** A leaking candidate is refused before the
  inflated score exists, because screening afterwards cannot undo a promotion
  already made on that number.
* **The promotion conjunct is tri-state.** `not_run` (incomplete measurement
  set) neither blocks nor passes — it is a disclosed non-event logged with
  every quantity that could not be resolved. `not_run` reported as a pass
  would be the over-claim this gate exists to prevent.

Fixture note: the gate reads durable state under `runtime_home()`, so these
tests point `ALPHA_HOME` at a temp dir and never touch the developer's store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import alpha.evolution.promotion_route as promotion_route
from alpha.evolution.promotion_route import route_evolution_gate, rrsi_selection_gate
from alpha.rsi.rrsi import (
    Measurement,
    RrsiParams,
    ScoredCandidate,
    admit,
    attribution_guard,
    cost_rule,
    domain_guard_default,
    election_gate_for,
    floor_rule,
    preset,
    relative_cost_change,
    score_change,
    screen_candidate,
    select_round,
    selected_branch,
    within_band_rule,
    within_band_utility,
)


@pytest.fixture(autouse=True)
def rsi_home(tmp_path, monkeypatch):
    """Every gate here resolves durable state lazily through ``ALPHA_HOME``."""
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    return tmp_path


def measured(score, cost, *, label="") -> Measurement:
    return Measurement(score=score, cost=cost, evidence_kind="measured", label=label)


#: A complete measurement set: both scores, both costs, ``S*`` and an
#: attributed component. Everything `election_gate_for` needs to decide.
COMPLETE_SIGNALS: dict[str, object] = {
    "candidate_score": 0.90,
    "candidate_cost": 100.0,
    "incumbent_score": 0.88,
    "incumbent_cost": 100.0,
    "best_score": 0.90,
    "components": ("client_tool",),
}


# --- measurement primitives --------------------------------------------------


def test_simulated_evidence_is_never_measured():
    preview = Measurement(score=0.9, cost=100, evidence_kind="simulated")
    assert preview.measured is False
    assert score_change(preview, measured(0.8, 100)) is None, "a preview constant must not reach a rule"


def test_a_relative_cost_change_never_divides_by_zero():
    value, note = relative_cost_change(measured(0.5, 50), measured(0.5, 0))
    assert value is None
    assert "division by zero" in note


def test_the_cost_change_is_relative_to_the_incumbent():
    value, _ = relative_cost_change(measured(0.5, 150), measured(0.5, 100))
    assert value == pytest.approx(0.5)


def test_delta_s_is_unknown_when_either_side_was_not_measured():
    assert score_change(measured(0.5, 1), Measurement(score=None, cost=1, evidence_kind="measured")) is None


def test_the_branch_selector_is_strictly_greater_than_delta():
    params = RrsiParams(noise_delta=0.25)
    assert selected_branch(0.25, params) == "within_band", "ΔS = δ is inside the band, not above it"
    assert selected_branch(0.2500001, params) == "cost_rule"
    assert selected_branch(None, params) is None


# --- S2: the noise floor -----------------------------------------------------


def test_the_floor_is_measured_against_S_star_not_against_the_incumbent():
    params = RrsiParams(noise_delta=0.25)
    passed = floor_rule(measured(0.75, 10), 1.0, params)
    assert passed.ok is True and passed.verdict == "pass"
    refused = floor_rule(measured(0.74, 10), 1.0, params)
    assert refused.ok is False
    assert refused.verdict == "fail"
    assert "below the floor" in refused.reason
    assert "cannot be accepted even when it is cheaper" in refused.reason


def test_the_floor_is_unevaluable_without_S_star():
    check = floor_rule(measured(0.5, 10), None)
    assert check.ok is None
    assert check.ran is False
    assert check.verdict == "not_evaluable"
    assert "S* (best evolve-set score observed so far) is unknown" in check.reason


# --- S3: the two branches ----------------------------------------------------


def test_the_cost_rule_refuses_growth_it_cannot_pay_for():
    params = RrsiParams(cost_base=0.10, cost_slope=35.4)
    assert cost_rule(0.10, 0.10, params).ok is True  # allowance = 0.1 + 3.54
    refused = cost_rule(0.10, 99.0, params)
    assert refused.ok is False
    assert "not justified by the measured improvement" in refused.reason


def test_the_cost_rule_is_unevaluable_when_delta_c_is_unknown():
    check = cost_rule(0.10, None, RrsiParams())
    assert check.ok is None
    assert check.ran is False
    assert "ΔC is unknown" in check.reason


def test_the_within_band_expression_is_the_stated_linear_form():
    params = RrsiParams(noise_delta=0.004, weight_score=1.0, weight_cost=1.0, weight_novelty=1.0)
    assert within_band_utility(0.001, -0.5, 0, params) == pytest.approx(0.501)
    assert within_band_utility(0.0, 0.0, 0, params) == 0.0


def test_the_within_band_rule_is_unevaluable_when_any_input_is_unknown():
    assert within_band_utility(0.01, None, 1) is None
    check = within_band_rule(0.01, None, 1)
    assert check.ok is None and check.ran is False
    unknown_novelty = within_band_rule(0.01, 0.0, None)
    assert unknown_novelty.ok is None
    assert "credit-assignment ledger could not be read" in unknown_novelty.reason


def test_the_coding_preset_cannot_earn_admission_from_score_alone():
    """``w_s = 0`` for coding: inside the band only cost and novelty count."""
    params = preset("coding")  # δ = 0.017, so ΔS = 0.01 is inside the band
    assert within_band_utility(0.01, 0.0, 0, params) == pytest.approx(0.0)
    check = within_band_rule(0.01, 0.0, 0, params)
    assert check.ok is False
    assert "bought nothing" in check.reason


# --- S5 + the Alpha-side attribution guard -----------------------------------


def test_the_default_domain_guard_states_that_no_guard_is_configured():
    check = domain_guard_default(measured(0.5, 1), measured(0.5, 1))
    assert check.ok is True
    assert "no domain-specific guard is configured" in check.reason


def test_the_attribution_guard_refuses_a_component_outside_K():
    assert attribution_guard(["prompt"]).ok is True
    refused = attribution_guard(["prompt_tuning"])
    assert refused.ok is False
    assert "outside K" in refused.reason
    unevaluable = attribution_guard([])
    assert unevaluable.ok is None and unevaluable.ran is False


# --- Algorithm 2 admission ---------------------------------------------------


def test_a_gain_the_candidate_cannot_pay_for_is_refused():
    """A 5-point gain bought with 9× the inference cost fails S3a, not S2."""
    params = RrsiParams(noise_delta=0.004, cost_base=0.10, cost_slope=35.4)
    result = admit(candidate=measured(0.90, 1000), incumbent=measured(0.85, 100), best_score=0.85, components=("prompt",), params=params)
    assert result.admitted is False
    assert result.check("noise_floor").ok is True
    assert result.check("cost_rule").ok is False
    assert result.check("within_band").applicable is False, "exactly one S3 branch governs a candidate"
    assert "cost_rule" in result.detail


def test_a_cheap_regression_cannot_buy_its_way_past_the_floor():
    """Non-compensatory: the within-band utility passes and S2 still refuses."""
    params = RrsiParams(noise_delta=0.004)
    result = admit(
        candidate=measured(0.50, 1),
        incumbent=measured(0.90, 100),
        best_score=0.90,
        components=("prompt",),
        winning_components=frozenset(),
        params=params,
    )
    assert result.admitted is False
    assert result.check("noise_floor").ok is False
    assert result.check("within_band").ok is True, "the branch rule passing is exactly why the floor must not be foldable into it"
    assert len(result.reasons) == 1


def test_an_undeclared_ledger_leaves_the_within_band_rule_unevaluable():
    """`admit` defaults `winning_components` to ``None`` — that means unreadable, not empty.

    Passing ``None`` is the honest answer when the ledger could not be read;
    passing ``frozenset()`` is the caller asserting it *was* readable and
    nothing has won. Collapsing the two would let a broken ledger hand out
    full novelty credit.
    """
    params = RrsiParams(noise_delta=0.25)
    result = admit(candidate=measured(0.6, 101), incumbent=measured(0.5, 100), best_score=0.5, components=("prompt",), params=params)
    assert result.check("within_band").ok is None
    assert "credit-assignment ledger could not be read" in result.check("within_band").reason
    assert result.admitted is False, "unknown novelty credit cannot buy admission"


def test_exactly_one_s3_branch_governs_a_candidate():
    params = RrsiParams(noise_delta=0.25)
    gainful = admit(candidate=measured(0.9, 101), incumbent=measured(0.5, 100), best_score=0.5, components=("prompt",), winning_components=frozenset(), params=params)
    assert gainful.check("cost_rule").applicable is True
    assert gainful.check("within_band").applicable is False
    assert gainful.admitted is True

    within = admit(candidate=measured(0.6, 101), incumbent=measured(0.5, 100), best_score=0.5, components=("prompt",), winning_components=frozenset(), params=params)
    assert within.check("cost_rule").applicable is False
    assert within.check("within_band").applicable is True
    assert within.admitted is True


def test_unknown_inputs_leave_a_rule_unevaluable_and_never_imply_a_pass():
    result = admit(
        candidate=measured(0.5, 100),
        incumbent=Measurement(score=None, cost=100, evidence_kind="measured"),
        best_score=0.5,
        components=("prompt",),
    )
    assert result.admitted is False
    assert result.check("cost_rule").ok is None
    assert result.check("within_band").ok is None
    assert result.ran is True, "the floor still ran and passed; only the S3 branch is unevaluable"
    assert "ΔS is unknown" in result.detail, "the refusal names the gap, it does not dress it as a judgement"


def test_a_blocking_rule_is_reported_with_its_own_text():
    params = RrsiParams(noise_delta=0.004)
    result = admit(candidate=measured(0.10, 1), incumbent=measured(0.90, 100), best_score=0.90, components=("prompt",), params=params)
    assert result.reasons
    assert all(isinstance(item, str) and item for item in result.reasons)
    assert result.to_dict()["admitted"] is False
    assert result.rules_applicable >= 3


# --- S1: the leakage critic --------------------------------------------------


def test_a_diff_naming_a_benchmark_is_refused_before_evaluation():
    verdict = screen_candidate("if task == 'gsm8k': return the oracle")
    assert verdict.passed is False
    assert "benchmark_reference" in verdict.reason
    assert "gsm8k" in verdict.reason
    assert verdict.reviewer_ran is False
    assert verdict.basis == "static"
    assert verdict.rules_applied == 2


def test_ground_truth_scaffolding_in_a_diff_is_a_finding():
    verdict = screen_candidate("expected_answer = load_gold()")
    assert verdict.passed is False
    assert "answer_marker" in verdict.reason


def test_a_long_task_literal_reproduced_verbatim_is_a_finding():
    task = "optimize the recursive_context_compaction pipeline"
    verdict = screen_candidate("apply recursive_context_compaction before every step", task_text=task)
    assert verdict.passed is False
    assert "task_literal" in verdict.reason
    assert verdict.rules_applied == 3


def test_a_clean_diff_passes_and_says_how_many_rules_ran():
    verdict = screen_candidate("def handler(request): return {'ok': True}")
    assert verdict.passed is True
    assert verdict.findings == ()
    assert verdict.rules_applied == 2
    assert "no leakage" in verdict.reason


def test_a_reviewer_cannot_clear_a_static_finding():
    """A judge that could overrule a finding it disliked would make S1 advisory."""
    verdict = screen_candidate("gsm8k shortcut", reviewer=lambda _text: True)
    assert verdict.passed is False
    assert verdict.basis == "static+reviewer"
    assert verdict.reviewer_ran is True
    assert "leakage finding" in verdict.reason


def test_a_rejecting_reviewer_fails_an_otherwise_clean_diff():
    verdict = screen_candidate("def handler(x): return x", reviewer=lambda _text: (False, "looks suspicious"))
    assert verdict.passed is False
    assert "reviewer rejected the candidate: looks suspicious" in verdict.reason


def test_a_raising_reviewer_fails_the_screen_with_the_real_error():
    def boom(_text):
        raise RuntimeError("reviewer exploded")

    verdict = screen_candidate("def handler(x): return x", reviewer=boom)
    assert verdict.passed is False
    assert verdict.basis == "reviewer_error"
    assert "reviewer exploded" in verdict.reason


def test_repairs_are_bounded_and_the_count_is_reported():
    verdict = screen_candidate("gsm8k", repair_attempts=["still gsm8k", "gsm8k again", "gsm8k"], max_repairs=2)
    assert verdict.passed is False
    assert verdict.repairs_attempted == 2
    assert "2 repair attempt(s) tried" in verdict.reason


def test_a_malformed_screen_argument_is_a_caller_bug_not_a_leakage_verdict():
    with pytest.raises(ValueError, match="must be a string"):
        screen_candidate(123)
    with pytest.raises(ValueError, match="repair_attempts must be a sequence"):
        screen_candidate("a", repair_attempts="b")
    with pytest.raises(ValueError, match="max_repairs must be an integer"):
        screen_candidate("a", max_repairs=-1)


# --- Algorithm 2 over a round ------------------------------------------------


def test_a_rejected_higher_scorer_is_absent_from_the_maximisation():
    params = RrsiParams(noise_delta=0.25)
    rich = ScoredCandidate("rich", measurement=measured(2.0, 100_000), components=("prompt",))
    poor = ScoredCandidate("poor", measurement=measured(0.6, 101), components=("prompt",))
    decision = select_round([rich, poor], incumbent=measured(0.5, 100), best_score=0.5, params=params)
    assert decision.winner_id == "poor"
    assert "rich" in decision.rejected
    assert "rich" not in decision.admitted_ids
    assert decision.admitted_ids == ("poor",)
    assert decision.accepted_ids == ("poor",)


def test_an_empty_admitted_set_retains_the_incumbent():
    params = RrsiParams(noise_delta=0.004)
    candidate = ScoredCandidate("bad", measurement=measured(0.10, 1), components=("prompt",))
    decision = select_round([candidate], incumbent=measured(0.90, 100), best_score=0.90, params=params)
    assert decision.winner_id is None
    assert decision.incumbent_retained is True
    assert decision.accepted_ids == ()
    assert decision.best_score == 0.90
    assert decision.candidates_seen == 1
    assert "incumbent retained" in decision.detail


def test_no_candidates_is_an_empty_round_not_an_error():
    decision = select_round([], incumbent=measured(0.90, 100), best_score=0.90)
    assert decision.winner_id is None
    assert decision.incumbent_retained is True
    assert "no candidates supplied" in decision.detail


def test_S_star_never_falls_even_when_a_lower_scoring_candidate_wins():
    """The floor lets a marginally lower candidate through; ``S*`` must not follow it down."""
    params = RrsiParams(noise_delta=0.004)
    candidate = ScoredCandidate("c", measurement=measured(0.897, 100), components=("client_tool",))
    decision = select_round([candidate], incumbent=measured(0.90, 100), best_score=0.90, params=params)
    assert decision.winner_id == "c"
    assert decision.winner_score == pytest.approx(0.897)
    assert decision.best_score == 0.90, "S* ← max(S*, Ŝ'); it is the best ever observed, not the latest"


def test_leakage_is_rejected_before_the_rules_can_judge_it():
    candidate = ScoredCandidate("leaky", measurement=measured(9.9, 1), components=("prompt",), diff_text="hardcode gsm8k answers")
    decision = select_round([candidate], incumbent=measured(0.50, 100), best_score=0.50)
    assert decision.winner_id is None
    assert decision.rejected["leaky"][0].startswith("S1 leakage screen")
    assert decision.screens["leaky"].passed is False
    assert "leaky" not in decision.admissibility, "a leaking candidate must never reach the evaluation whose score it would inflate"


def test_select_round_refuses_the_wrong_container_types():
    with pytest.raises(ValueError, match="not a bare string"):
        select_round("candidates", incumbent=measured(0.5, 1), best_score=0.5)
    with pytest.raises(ValueError, match="ScoredCandidate instances"):
        select_round([{"candidate_id": "x"}], incumbent=measured(0.5, 1), best_score=0.5)


def test_a_round_decision_serialises_without_inventing_a_winner():
    decision = select_round([], incumbent=measured(0.5, 1), best_score=0.5)
    blob = decision.to_dict()
    assert blob["winner_id"] is None
    assert blob["admitted_ids"] == []
    assert blob["best_score"] == 0.5
    assert blob["candidates_seen"] == 0
    assert isinstance(blob["screens"], dict) and isinstance(blob["admissibility"], dict)


# --- the block-only promotion conjunct ---------------------------------------


def test_the_gate_is_not_run_when_any_quantity_is_missing():
    outcome = election_gate_for("cand-x")
    assert outcome.ran is False
    assert outcome.blocking is False
    assert outcome.status == "not_run"
    assert outcome.admitted is False
    assert outcome.reason_text() == "", "a non-blocking gate has nothing to append to the engine's reason"
    assert len(outcome.missing) == 6
    assert "S* (best score observed so far)" in outcome.missing
    assert "candidate score Ŝ'" in outcome.missing
    assert "incumbent cost Ĉ_t" in outcome.missing
    assert "comp(H')" in outcome.missing
    assert outcome.basis == {}


def test_a_partial_measurement_set_is_not_run_not_interpolated():
    signals = {key: value for key, value in COMPLETE_SIGNALS.items() if key != "incumbent_cost"}
    outcome = election_gate_for("cand-x", signals=signals)
    assert outcome.status == "not_run"
    assert outcome.ran is False and outcome.blocking is False
    assert any("incumbent cost" in item for item in outcome.missing)


def test_a_bool_is_never_a_score():
    signals = dict(COMPLETE_SIGNALS, candidate_score=True)
    outcome = election_gate_for("cand-x", signals=signals)
    assert outcome.status == "not_run"
    assert any("not a number" in item for item in outcome.reasons)


def test_a_complete_measurement_set_admits_the_candidate():
    outcome = election_gate_for("cand-x", signals=COMPLETE_SIGNALS)
    assert outcome.ran is True
    assert outcome.blocking is False
    assert outcome.status == "admitted"
    assert outcome.admitted is True
    assert outcome.reason_text() == ""
    assert outcome.missing == ()
    assert outcome.basis["candidate_score"] == "signals.candidate_score"
    assert outcome.basis["S*"] == "signals.best_score"
    assert outcome.screen is None, "no diff was supplied, so S1 is reported as absent rather than as a pass"


def test_a_candiate_below_the_floor_is_blocked_with_the_rules_own_reason():
    outcome = election_gate_for("cand-x", signals=dict(COMPLETE_SIGNALS, candidate_score=0.50))
    assert outcome.ran is True
    assert outcome.blocking is True
    assert outcome.status == "blocked"
    text = outcome.reason_text()
    assert text.startswith("RRSI selection gate: blocked")
    assert "S2 refused" in text
    assert outcome.admitted is False


def test_leakage_blocks_before_the_score_is_ever_used():
    outcome = election_gate_for("cand-x", signals=dict(COMPLETE_SIGNALS, diff="hardcode gsm8k answers"))
    assert outcome.ran is True and outcome.blocking is True
    assert outcome.screen is not None and outcome.screen.passed is False
    assert "S1 leakage screen" in outcome.reason_text()


def test_a_non_blocking_outcome_formats_to_an_empty_string():
    admitted = election_gate_for("cand-x", signals=COMPLETE_SIGNALS)
    assert admitted.reason_text() == ""
    not_run = election_gate_for("cand-x")
    assert not_run.reason_text() == ""


def test_candidate_ids_that_look_like_paths_are_refused():
    from alpha.rsi.rrsi.selection import _safe_bundle_dir

    for hostile in ("../escape", "a/b", "a\\b", "..", ".", ""):
        assert _safe_bundle_dir(hostile) is None, hostile
    assert _safe_bundle_dir("cand-1") is not None


# --- wiring into the promotion route -----------------------------------------


def test_rrsi_selection_gate_collapses_the_tri_state_to_a_conjunct():
    not_run_blocking, not_run_text = rrsi_selection_gate("cand-x", None)
    assert not_run_blocking is False and not_run_text == "", "not_run must not block"

    admitted_blocking, admitted_text = rrsi_selection_gate("cand-x", None, signals=COMPLETE_SIGNALS)
    assert admitted_blocking is False and admitted_text == ""

    blocked_blocking, blocked_text = rrsi_selection_gate("cand-x", None, signals=dict(COMPLETE_SIGNALS, candidate_score=0.50))
    assert blocked_blocking is True
    assert blocked_text.startswith("RRSI selection gate: blocked")


def _stub_engine(monkeypatch, tuple_):
    """Replace the real engine with one that answers exactly ``tuple_``.

    The composition property under test is about the *route*, so the engine is
    stubbed rather than driven: a real engine refusing for its own reasons
    would leave "the route returned a ``False``" indistinguishable from "the
    gate subtracted the ``True``".
    """
    monkeypatch.setattr(promotion_route, "get_evolution_engine", lambda: type("E", (), {"gate": staticmethod(lambda *args, **kwargs: tuple_)})())


def test_the_route_returns_the_engine_tuple_verbatim_when_a_silent_gate_neither_helps_nor_hurts(monkeypatch):
    """`not_run` must not touch a promotion the engine already granted."""
    _stub_engine(monkeypatch, (True, "engine promoted on its own evidence"))
    promoted, reason = route_evolution_gate("cand-x", {"kind": "code"}, human_approved=True)
    assert (promoted, reason) == (True, "engine promoted on its own evidence"), "a non-blocking gate leaves the engine's tuple byte-for-byte"


def test_the_route_keeps_every_independent_verdict_separable_when_one_blocks(monkeypatch):
    """A refused conjunct turns ``True`` into ``False`` and keeps both findings readable."""
    _stub_engine(monkeypatch, (True, "engine promoted on its own evidence"))
    promoted, reason = route_evolution_gate("cand-x", {"kind": "code"}, human_approved=True, rrsi_signals=dict(COMPLETE_SIGNALS, candidate_score=0.50))
    assert promoted is False, "the conjunct is strictly subtractive — this is the property under test"
    parts = reason.split(" + ")
    assert parts[0] == "engine promoted on its own evidence", "the engine's own finding stays first"
    assert any(part.startswith("RRSI selection gate: blocked") for part in parts)


def test_an_admitted_rrsi_gate_leaves_a_refusing_engine_alone(monkeypatch):
    """Conjuncts only subtract: an admitted candidate cannot rescue a refusal."""
    _stub_engine(monkeypatch, (False, "not strictly better than baseline"))
    promoted, reason = route_evolution_gate("cand-x", {"kind": "code"}, human_approved=True, rrsi_signals=COMPLETE_SIGNALS)
    assert (promoted, reason) == (False, "not strictly better than baseline")


def test_the_route_still_consults_both_gates():
    source = Path(promotion_route.__file__).read_text(encoding="utf-8")
    assert "evidence_verdict_for(candidate_id, baseline)" in source
    assert "rrsi_selection_gate(candidate_id, baseline, signals=rrsi_signals)" in source
    assert 'return False, " + ".join([engine_reason, *blocked])' in source
    assert "return promoted, engine_reason" in source
