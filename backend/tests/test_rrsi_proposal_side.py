"""RRSI proposal-side regularizers: P1 budget, P2 ledger, P3 exploration, S4 pruning.

The proposal side never decides anything — it computes bounded constraints and
renders them into one instruction. What these tests therefore pin is not a
verdict but the *shape* of the constraint and the honesty of every gap:

* **P1** the cosine schedule starts at ``b_max``, ends at ``b_min`` and
  **saturates** past the horizon instead of re-widening (a long search must not
  be handed a larger budget than it started with).
* **P2** an unreadable ledger reports *unknown*, never empty — the two lead to
  opposite decisions and must not collapse.
* **P3** a stall is declared only from enough observations to support one, and
  the boundary is inclusive (``ΔS ≤ δ``).
* **S4** ``max ∅ = −∞``: a component with **no** measured gain in-window is a
  target, and the report keeps "measured and failed" apart from "never
  measured" rather than merging them into one count.

Fixture note: these tests pass an explicit ``path`` wherever a durable store
exists, so nothing here depends on process-global ``ALPHA_HOME``.
"""

from __future__ import annotations

import json

import pytest

from alpha.rsi.rrsi import (
    COMPONENTS,
    STRUCTURAL_COMPONENTS,
    HistoryEntry,
    RrsiHistory,
    RrsiParams,
    StallSignal,
    apply_edit_budget,
    build_proposal_plan,
    detect_stall,
    edit_budget,
    plan_exploration,
    preset,
    pruning_targets,
)
from alpha.rsi.rrsi.proposal import MAX_INSTRUCTION_CHARS, MAX_REASONS


def _ledger(path) -> RrsiHistory:
    return RrsiHistory(path)


def _entry(component: str, *, round_index: int, delta: float | None, accepted: bool = False, suffix: str = "") -> HistoryEntry:
    return HistoryEntry(
        round=round_index,
        candidate_id=f"cand-{component}-{round_index}-{suffix}",
        component=component,
        diff_hash=f"hash-{component}-{round_index}-{suffix}",
        accepted=accepted,
        delta_score=delta,
    )


def _stalled() -> StallSignal:
    """A stall signal built by hand so exploration can be tested independently of scoring."""
    return StallSignal(stalled=True, reason="forced for test", delta=0.0, threshold=0.004, window=3, observed=4)


# --- P1: the annealed edit budget -------------------------------------------


def test_the_schedule_starts_at_the_max_and_ends_at_the_min():
    params = preset("workspace")
    assert edit_budget(0, params) == params.budget_max
    assert edit_budget(params.rounds, params) == params.budget_min


def test_the_schedule_only_ever_narrows_over_the_horizon():
    params = preset("coding")
    values = [edit_budget(t, params) for t in range(params.rounds + 1)]
    assert all(left >= right for left, right in zip(values, values[1:])), f"the budget widened mid-anneal: {values}"


def test_the_schedule_saturates_past_the_horizon_and_never_re_widens():
    """For ``t > T`` the raw cosine rises again — that must not reach ``b_t``."""
    params = preset("workspace")
    floor = edit_budget(params.rounds, params)
    assert floor == params.budget_min
    assert {edit_budget(params.rounds + k, params) for k in (1, 2, 5, 50)} == {floor}


def test_a_negative_round_is_refused_because_it_would_start_the_anneal_mid_descent():
    with pytest.raises(ValueError, match="round_index must be >= 0"):
        edit_budget(-1)
    with pytest.raises(ValueError, match="round_index must be an integer"):
        edit_budget(True)


def test_applying_the_budget_is_a_prefix_cut_with_the_drop_disclosed():
    params = RrsiParams(rounds=4, budget_min=1, budget_max=4)
    outcome = apply_edit_budget(["a", "b", "c", "d", "e"], params.rounds, params)
    assert outcome.kept == ("a",)
    assert outcome.dropped == ("b", "c", "d", "e")
    assert outcome.applied is True
    assert outcome.saturated is True
    assert "retained 1 of 5" in outcome.reason
    assert "saturated at b_min" in outcome.reason
    assert outcome.to_dict()["dropped_count"] == 4


def test_applying_the_budget_never_reorders_or_rewrites_an_edit():
    params = preset("workspace")
    outcome = apply_edit_budget(["first", "second", "third"], 0, params)
    assert outcome.kept == ("first", "second", "third")
    assert outcome.dropped == ()
    assert outcome.applied is False
    assert "retained all 3" in outcome.reason


def test_a_bare_string_is_refused_as_an_edit_list():
    with pytest.raises(ValueError, match="not a single string"):
        apply_edit_budget("abc", 0)


# --- P2: the credit-assignment ledger ---------------------------------------


def test_an_absent_ledger_is_empty_and_readable_not_corrupt(tmp_path):
    """Absent is not corrupt: a first run must not report a fault."""
    ledger = _ledger(tmp_path / "missing.jsonl")
    assert ledger.readable is True
    assert ledger.entries == ()
    status = ledger.status()
    assert status["count"] == 0
    assert status["load_error"] is None
    assert status["degraded"] is False


def test_an_unreadable_ledger_reports_unknown_never_empty(tmp_path):
    blocked = tmp_path / "rrsi_history.jsonl"
    blocked.mkdir()  # a directory at the ledger path: exists, cannot be read as text
    ledger = _ledger(blocked)
    assert ledger.readable is False
    assert ledger.load_error
    assert ledger.entries is None
    assert ledger.exercised_components() is None
    assert ledger.accepted_components() is None
    assert ledger.attempts() is None
    assert ledger.gain_window("prompt", window=4) is None
    assert ledger.unmeasured_deltas() is None
    status = ledger.status()
    assert status["count"] is None, "an unreadable source must never report a count of 0"
    assert status["readable"] is False
    disclosure = ledger.to_disclosure()
    assert disclosure["exercised_components"] is None
    assert disclosure["attempts"] is None


def test_appending_to_an_unreadable_ledger_reports_failure_not_success(tmp_path):
    blocked = tmp_path / "rrsi_history.jsonl"
    blocked.mkdir()
    ledger = _ledger(blocked)
    assert ledger.append(_entry("prompt", round_index=1, delta=None)) is False


def test_an_append_that_lands_survives_a_reload(tmp_path):
    path = tmp_path / "rsi" / "rrsi_history.jsonl"
    ledger = _ledger(path)
    entry = _entry("prompt", round_index=1, delta=0.5, accepted=True)
    assert ledger.append(entry) is True

    reloaded = _ledger(path)
    assert reloaded.readable is True
    assert len(reloaded.entries) == 1
    assert reloaded.entries[0].delta_score == 0.5
    assert reloaded.entries[0].component == "prompt"


def test_malformed_lines_are_skipped_and_counted_never_repaired(tmp_path):
    path = tmp_path / "rrsi_history.jsonl"
    good = _entry("prompt", round_index=1, delta=0.1)
    path.write_text(json.dumps(good.to_dict()) + "\n" + "{not json\n" + json.dumps({"round": 1}) + "\n", encoding="utf-8")
    ledger = _ledger(path)
    assert len(ledger.entries) == 1
    assert ledger.skipped_lines == 2
    assert ledger.status()["degraded"] is True


def test_a_ledger_record_must_name_a_component_of_K():
    with pytest.raises(ValueError, match="is not in K"):
        HistoryEntry(round=1, candidate_id="c", component="prompt_tuning", diff_hash="h", accepted=True)


def test_a_ledger_record_may_leave_a_delta_unmeasured_but_not_fake_it():
    HistoryEntry(round=1, candidate_id="c", component="prompt", diff_hash="h", accepted=False, delta_score=None)
    with pytest.raises(ValueError, match="delta_score"):
        HistoryEntry(round=1, candidate_id="c", component="prompt", diff_hash="h", accepted=False, delta_score="0.5")


def test_a_gain_window_measures_only_the_last_window_rounds(tmp_path):
    path = tmp_path / "rrsi_history.jsonl"
    ledger = _ledger(path)
    assert ledger.append(_entry("prompt", round_index=1, delta=0.5)) is True
    assert ledger.append(_entry("prompt", round_index=2, delta=-0.4)) is True
    assert ledger.append(_entry("prompt", round_index=3, delta=None, suffix="x")) is True
    assert ledger.append(_entry("skill", round_index=3, delta=0.2, accepted=True)) is True

    window = ledger.gain_window("prompt", window=2)
    assert window.top_round == 3
    assert window.floor_round == 2
    assert window.records == 2  # round 1 fell out of the window
    assert window.measured == 1  # round 3 carried no measured delta
    assert window.best == -0.4
    assert window.positive is False
    assert window.in_window is True

    assert ledger.accepted_components() == frozenset({"skill"})
    assert ledger.attempts("prompt") == 3


def test_a_component_with_no_measured_gain_in_window_is_not_positive(tmp_path):
    path = tmp_path / "rrsi_history.jsonl"
    ledger = _ledger(path)
    assert ledger.append(_entry("prompt", round_index=1, delta=0.5)) is True
    window = ledger.gain_window("memory", window=4)
    assert window.records == 0
    assert window.measured == 0
    assert window.best is None
    assert window.positive is False, "an absence of measurement must never read as a positive gain"


def test_a_gain_window_over_an_empty_ledger_has_no_rounds(tmp_path):
    window = _ledger(tmp_path / "missing.jsonl").gain_window("prompt", window=4)
    assert window.top_round == 0
    assert window.records == 0
    assert window.best is None
    with pytest.raises(ValueError, match="window must be >= 1"):
        _ledger(tmp_path / "missing.jsonl").gain_window("prompt", window=0)


def test_attempts_without_a_measured_delta_are_counted_separately(tmp_path):
    path = tmp_path / "rrsi_history.jsonl"
    ledger = _ledger(path)
    ledger.append(_entry("prompt", round_index=1, delta=0.5))
    ledger.append(_entry("skill", round_index=1, delta=None))
    assert ledger.unmeasured_deltas() == 1


# --- P3: stall-triggered structured exploration ------------------------------


def test_a_stall_never_declares_a_verdict_the_history_cannot_support():
    signal = detect_stall([0.5, 0.5, 0.5])
    assert signal.stalled is False
    assert signal.delta is None
    assert signal.observed == 3
    assert "4 required" in signal.reason


def test_the_stall_boundary_is_inclusive_at_the_noise_band():
    params = RrsiParams(noise_delta=0.25)
    at_band = detect_stall([0.5, 0.625, 0.75, 0.75], params)
    assert at_band.delta == 0.25
    assert at_band.stalled is True, "σ_t = 1[Ŝ_t − Ŝ_(t−w) ≤ δ] — equality is a stall"

    above = detect_stall([0.5, 0.75, 1.0, 1.0], params)
    assert above.delta == 0.5
    assert above.stalled is False


def test_a_bare_string_of_scores_is_refused():
    with pytest.raises(ValueError, match="not a string"):
        detect_stall("0.5 0.5")


def test_exploration_is_not_due_while_the_search_is_not_stalled(tmp_path):
    plan = plan_exploration(detect_stall([0.9, 0.91, 0.92, 0.99]), _ledger(tmp_path / "missing.jsonl"))
    assert plan.status == "not_stalled"
    assert plan.slots == 0
    assert plan.applied is False


def test_a_reserved_slot_points_at_an_untried_structural_component_first(tmp_path):
    ledger = _ledger(tmp_path / "rrsi_history.jsonl")
    ledger.append(_entry("prompt", round_index=1, delta=None))
    plan = plan_exploration(_stalled(), ledger)
    assert plan.status == "applied"
    assert plan.applied is True
    assert plan.slots == 1
    assert plan.untried_components[0] == "client_tool", "K_struct is filled first — ν only credits structural components"
    assert plan.reserved_components == ("client_tool",)
    # The priority list follows the declared order of K, not frozenset iteration
    # order — two readers must see the same sequence.
    assert plan.structural_priority[:4] == tuple(name for name in COMPONENTS if name in STRUCTURAL_COMPONENTS)


def test_an_unreadable_ledger_makes_exploration_unavailable_not_empty(tmp_path):
    blocked = tmp_path / "rrsi_history.jsonl"
    blocked.mkdir()
    plan = plan_exploration(_stalled(), _ledger(blocked))
    assert plan.status == "unavailable"
    assert plan.reserved_components == ()
    assert plan.untried_components == ()
    assert plan.applied is False
    assert plan.slots == 1, "the number of slots the stall reserves is still a fact worth reporting"
    assert "could not be read" in plan.reason


def test_plan_exploration_requires_a_real_stall_signal(tmp_path):
    with pytest.raises(ValueError, match="takes a StallSignal"):
        plan_exploration("stalled", _ledger(tmp_path / "missing.jsonl"))


# --- S4: L1 structural pruning ----------------------------------------------


def test_pruning_targets_both_the_measured_failure_and_the_never_measured(tmp_path):
    """``max ∅ = −∞`` folds both into ``B_t``; the report keeps them apart."""
    ledger = _ledger(tmp_path / "rrsi_history.jsonl")
    ledger.append(_entry("prompt", round_index=10, delta=0.4))  # positive in-window → productive
    ledger.append(_entry("skill", round_index=10, delta=-0.2))  # measured and non-positive → target
    ledger.append(_entry("memory", round_index=10, delta=None))  # exercised but never measured → target

    report = pruning_targets(ledger)
    assert report.status == "ok"
    assert report.available is True
    assert set(report.targets) == {"skill", "memory"}
    assert report.productive == ("prompt",)
    assert report.unmeasured == ("memory",)
    assert "prompt" not in report.untouched
    assert set(report.untouched) == set(COMPONENTS) - {"prompt", "skill", "memory"}
    assert "no measured" in report.reasons["memory"]
    assert "max ∅ = −∞" in report.reasons["memory"]
    assert "best measured ΔS" in report.reasons["skill"]


def test_a_component_whose_only_record_left_the_window_is_a_target(tmp_path):
    ledger = _ledger(tmp_path / "rrsi_history.jsonl")
    ledger.append(_entry("skill", round_index=1, delta=0.9))  # a great gain, four rounds ago
    ledger.append(_entry("prompt", round_index=10, delta=0.5))

    report = pruning_targets(ledger)  # prune_window default 4, top round 10 → floor 7
    assert report.status == "ok"
    assert report.targets == ("skill",)
    assert report.unmeasured == ("skill",), "out of window means no measured record, not a measured failure"
    assert "max ∅ = −∞" in report.reasons["skill"]


def test_never_exercised_components_are_never_targets(tmp_path):
    ledger = _ledger(tmp_path / "rrsi_history.jsonl")
    ledger.append(_entry("prompt", round_index=5, delta=-1.0))
    report = pruning_targets(ledger)
    assert "memory" not in report.targets
    assert "memory" in report.untouched


def test_an_unreadable_ledger_yields_no_target_list_at_all(tmp_path):
    blocked = tmp_path / "rrsi_history.jsonl"
    blocked.mkdir()
    report = pruning_targets(_ledger(blocked))
    assert report.status == "unavailable"
    assert report.available is False
    assert report.targets == ()
    assert report.untouched == ()
    assert report.considered == ()
    assert "could not be read" in report.reason
    assert "empty would read as" in report.reason


# --- Algorithm 1 composition --------------------------------------------------


def test_a_plan_is_a_constraint_and_never_a_result(tmp_path):
    plan = build_proposal_plan(1, round_scores=[0.5, 0.51, 0.52, 0.52], history=_ledger(tmp_path / "missing.jsonl"))
    assert plan.kind == "rrsi_proposal_plan"
    assert plan.usable is True
    assert plan.round_index == 1
    assert plan.horizon == plan.params.rounds
    assert "Nothing below is a score, a result, or an approval." in plan.instruction
    assert len(plan.instruction) <= MAX_INSTRUCTION_CHARS
    assert plan.constraints["edit_budget"] >= 1
    # `to_dict` reports health and constraints only — no score, no verdict.
    serialised = json.dumps(plan.to_dict())
    assert '"improvement"' not in serialised
    assert '"promoted"' not in serialised


def test_unmeasured_rounds_are_dropped_from_the_stall_series_never_zeroed(tmp_path):
    plan = build_proposal_plan(3, round_scores=[0.9, None, 0.91, None, 0.92], history=_ledger(tmp_path / "missing.jsonl"))
    assert plan.stall.observed == 3, "a round with no measurement supplies no point on the series"
    assert plan.stall.delta is None


def test_an_unreadable_ledger_makes_the_plan_unusable_and_says_so(tmp_path):
    blocked = tmp_path / "rrsi_history.jsonl"
    blocked.mkdir()
    plan = build_proposal_plan(1, round_scores=[], history=_ledger(blocked))
    assert plan.usable is False
    assert plan.history_available is False
    assert plan.history_error
    assert plan.pruning.status == "unavailable"
    # With no score series at all the stall indicator cannot support a verdict,
    # so exploration reports "not stalled" — it never guesses at a ledger it
    # could not read either.
    assert plan.exploration.status == "not_stalled"
    assert "could not be read" in plan.instruction
    assert "Do not treat that as an empty history" in plan.instruction
    assert "Pruning could not be evaluated" in plan.instruction
    assert "not permission to ignore it" in plan.instruction


def test_the_instruction_bounds_the_number_of_pruning_reasons(tmp_path):
    path = tmp_path / "rrsi_history.jsonl"
    ledger = _ledger(path)
    for index, component in enumerate(("control_flow", "config", "output_plumbing", "context_mgmt", "client_tool", "skill")):
        ledger.append(_entry(component, round_index=5, delta=-0.1, suffix=str(index)))
    plan = build_proposal_plan(2, round_scores=[0.5, 0.5, 0.5, 0.5], history=ledger)
    assert len(plan.pruning.targets) > MAX_REASONS
    assert "further target(s) truncated" in plan.instruction
    assert len(plan.instruction) <= MAX_INSTRUCTION_CHARS


def test_a_plan_refuses_a_round_index_it_cannot_describe():
    with pytest.raises(ValueError, match="round_index"):
        build_proposal_plan(-1, round_scores=[])
    with pytest.raises(ValueError, match="round_index"):
        build_proposal_plan(True, round_scores=[])


def test_applying_a_plan_is_the_budget_enforcement_point(tmp_path):
    plan = build_proposal_plan(0, round_scores=[], history=_ledger(tmp_path / "missing.jsonl"))
    outcome = plan.apply(["a", "b"])
    assert outcome.budget == plan.budget.budget
    assert outcome.kept == ("a", "b")
    assert outcome.dropped == ()
