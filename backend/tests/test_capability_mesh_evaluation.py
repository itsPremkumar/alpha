"""Tests for the capability evaluation harness and diversity exploration (plan §16/§17/§19, §13.2).

The load-bearing rules pinned here:

* rubric selection is a deterministic TABLE lookup by microtask kind, a miss is
  an honest refusal that names what WAS available, and two rubrics sharing an id
  under different versions is refused rather than silently picked (the
  comparability rule);
* a suite refuses a holdout case that also appears in its scored cases — plan
  §13.2 keeps the holdout separate so the router is never tuned to its own
  answers;
* an evaluation report is per CATEGORY and never a global leaderboard:
  ``global_rank_available`` is False and no global rank field exists at all
  (plan §4.1);
* ``routing_regret`` is None without a candidate set and None when no case is
  comparable, and ``cost_at_quality`` is None because ``evaluate()`` receives no
  cost data at all;
* exploration happens only when the exploration share has budget AND the
  incumbent's evidence is thin; with nothing measured on either side it returns
  False with a reason rather than burning a request for nothing.
"""

from __future__ import annotations

import pytest

from alpha.capability_mesh.evaluation import (
    THIN_INCUMBENT_FLOOR,
    EvaluationCase,
    EvaluationReport,
    EvaluationSuite,
    ExplorationDecision,
    Rubric,
    RubricSelection,
    evaluate,
    select_rubric,
    should_explore,
)


def _rubric(rubric_id: str = "code", *, version: str = "r1", **kwargs) -> Rubric:
    kwargs.setdefault("criteria", ("behavioural correctness", "test evidence"))
    kwargs.setdefault("dimension_weights", (("correctness", 0.7), ("conventions", 0.3)))
    kwargs.setdefault("hard_gates", ("must compile",))
    return Rubric(rubric_id=rubric_id, rubric_version=version, **kwargs)


def _case(case_id: str = "c1", *, task_category: str = "code", capability: str = "code_implementation") -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        task_category=task_category,
        capability=capability,
        expected_checks=("the patch applies",),
        tags=("regression",),
    )


def _suite(cases: list[EvaluationCase] | None = None, *, holdout: tuple[EvaluationCase, ...] = ()) -> EvaluationSuite:
    return EvaluationSuite(suite_id="suite-a", version="v1", cases=tuple(cases or [_case("c1"), _case("c2")]), holdout=holdout)


# ------------------------------------------------------------------- rubric


def test_rubric_requires_id_version_and_criteria():
    with pytest.raises(ValueError, match="rubric_id must be non-empty"):
        _rubric("")
    with pytest.raises(ValueError, match="rubric_version must be non-empty"):
        Rubric(rubric_id="code", rubric_version="", criteria=("something",))
    with pytest.raises(ValueError, match="criteria must be non-empty"):
        Rubric(rubric_id="code", rubric_version="r1", criteria=())


def test_rubric_rejects_empty_criterion_and_gate_entries():
    with pytest.raises(ValueError, match="criteria entries must be non-empty"):
        Rubric(rubric_id="code", rubric_version="r1", criteria=("ok", ""))
    with pytest.raises(ValueError, match="hard_gates entries must be non-empty"):
        Rubric(rubric_id="code", rubric_version="r1", criteria=("ok",), hard_gates=("",))


def test_rubric_weight_refusals():
    with pytest.raises(ValueError, match="finite number >= 0"):
        _rubric(dimension_weights=(("correctness", -0.1),))
    with pytest.raises(ValueError, match="finite number >= 0"):
        _rubric(dimension_weights=(("correctness", float("nan")),))
    with pytest.raises(ValueError, match="dimension name must be non-empty"):
        _rubric(dimension_weights=(("", 1.0),))
    with pytest.raises(ValueError, match="duplicate rubric dimension 'correctness'"):
        _rubric(dimension_weights=(("correctness", 0.5), ("correctness", 0.5)))
    with pytest.raises(ValueError, match="weights sum to 0.0"):
        _rubric(dimension_weights=(("correctness", 0.0),))


def test_rubric_round_trips_through_dict():
    rubric = _rubric()
    restored = Rubric.from_dict(rubric.to_dict())
    assert restored == rubric
    assert restored.dimension_weights == (("correctness", 0.7), ("conventions", 0.3))
    assert restored.hard_gates == ("must compile",)


# --------------------------------------------------------- rubric selection


def test_select_rubric_is_a_deterministic_table_lookup():
    available = {"code": _rubric("code"), "research": _rubric("research", version="r1")}
    first = select_rubric(microtask_kind="research", available=available)
    second = select_rubric(microtask_kind="research", available=available)
    assert first == second
    assert first.rubric.rubric_id == "research"
    assert "research" in first.reason
    assert first.profile_version == "r1"


def test_select_rubric_refusal_names_what_was_available():
    with pytest.raises(ValueError) as excinfo:
        select_rubric(microtask_kind="security-review", available={"code": _rubric("code"), "research": _rubric("research")})
    message = str(excinfo.value)
    assert "security-review" in message
    assert "code" in message and "research" in message


def test_select_rubric_refusal_with_nothing_available():
    with pytest.raises(ValueError, match=r"available kinds: \(none\)"):
        select_rubric(microtask_kind="code", available={})


def test_select_rubric_refuses_two_versions_of_one_rubric_id():
    available = {"code": _rubric("code", version="r1"), "legacy": _rubric("code", version="r2")}
    with pytest.raises(ValueError) as excinfo:
        select_rubric(microtask_kind="code", available=available)
    message = str(excinfo.value)
    assert "code" in message
    assert "r1" in message and "r2" in message


def test_select_rubric_allows_the_same_version_under_two_kinds():
    available = {"code": _rubric("code", version="r1"), "review": _rubric("code", version="r1")}
    assert select_rubric(microtask_kind="review", available=available).rubric.rubric_id == "code"


def test_select_rubric_refuses_bad_inputs():
    with pytest.raises(ValueError, match="microtask_kind must be non-empty"):
        select_rubric(microtask_kind="", available={"code": _rubric("code")})
    with pytest.raises(ValueError, match="must be Rubric rows"):
        select_rubric(microtask_kind="code", available={"code": "not-a-rubric"})  # type: ignore[dict-item]


def test_rubric_selection_requires_reason_and_profile_version():
    rubric = _rubric()
    with pytest.raises(ValueError, match="reason must be non-empty"):
        RubricSelection(rubric=rubric, reason="", profile_version="r1")
    with pytest.raises(ValueError, match="profile_version must be non-empty"):
        RubricSelection(rubric=rubric, reason="matched", profile_version="")
    with pytest.raises(ValueError, match="rubric must be a Rubric"):
        RubricSelection(rubric="nope", reason="matched", profile_version="r1")  # type: ignore[arg-type]


# ------------------------------------------------------------------- cases


def test_evaluation_case_requires_identity_and_expected_checks():
    with pytest.raises(ValueError, match="case_id must be non-empty"):
        _case("")
    with pytest.raises(ValueError, match="task_category must be non-empty"):
        _case("c1", task_category="")
    with pytest.raises(ValueError, match="capability must be non-empty"):
        _case("c1", capability="")
    with pytest.raises(ValueError, match="expected_checks must be non-empty"):
        EvaluationCase(case_id="c1", task_category="code", capability="code_implementation", expected_checks=())
    with pytest.raises(ValueError, match="expected_checks entries must be non-empty"):
        EvaluationCase(case_id="c1", task_category="code", capability="code_implementation", expected_checks=("",))
    with pytest.raises(ValueError, match="tags entries must be non-empty"):
        EvaluationCase(case_id="c1", task_category="code", capability="code_implementation", expected_checks=("ok",), tags=("",))


def test_evaluation_case_round_trips():
    case = _case()
    assert EvaluationCase.from_dict(case.to_dict()) == case


# ------------------------------------------------------------------- suite


def test_suite_requires_cases():
    with pytest.raises(ValueError, match="cases must be non-empty"):
        EvaluationSuite(suite_id="s", version="v1", cases=())
    with pytest.raises(ValueError, match="suite_id must be non-empty"):
        EvaluationSuite(suite_id="", version="v1", cases=(_case(),))


def test_suite_refuses_a_holdout_case_that_is_also_scored():
    with pytest.raises(ValueError) as excinfo:
        _suite([_case("c1"), _case("c2")], holdout=(_case("c2"),))
    message = str(excinfo.value)
    assert "c2" in message
    assert "holdout" in message


def test_suite_refuses_duplicate_case_ids():
    with pytest.raises(ValueError, match="duplicate case_id 'c1'"):
        _suite([_case("c1"), _case("c1")])
    with pytest.raises(ValueError, match="duplicate case_id 'h1'"):
        _suite([_case("c1")], holdout=(_case("h1"), _case("h1")))


def test_suite_round_trips_and_keeps_holdout_separate():
    suite = _suite([_case("c1")], holdout=(_case("h1", task_category="code"),))
    restored = EvaluationSuite.from_dict(suite.to_dict())
    assert restored == suite
    assert [case.case_id for case in restored.holdout] == ["h1"]


# ------------------------------------------------------------------ report


def test_report_refuses_to_claim_a_global_rank():
    with pytest.raises(ValueError) as excinfo:
        EvaluationReport(suite_id="s", per_category=(("code", 0.5),), global_rank_available=True, sample_count=1)
    assert "global rank" in str(excinfo.value)
    assert "leaderboard" in str(excinfo.value)


def test_report_exposes_per_category_and_no_global_rank_field():
    report = EvaluationReport(suite_id="s", per_category=(("code", 0.8), ("research", 0.4)), sample_count=4)
    assert report.global_rank_available is False
    document = report.to_dict()
    assert document["per_category"] == [["code", 0.8], ["research", 0.4]]
    assert "global_rank" not in document
    assert set(document) == {"suite_id", "per_category", "global_rank_available", "routing_regret", "cost_at_quality", "sample_count"}


def test_report_round_trips():
    report = EvaluationReport(suite_id="s", per_category=(("code", 0.5),), routing_regret=0.1, cost_at_quality=0.02, sample_count=2)
    assert EvaluationReport.from_dict(report.to_dict()) == report


def test_report_refusals():
    with pytest.raises(ValueError, match="routing_regret must be a finite number in \\[0, 1\\] or None"):
        EvaluationReport(suite_id="s", per_category=(("code", 0.5),), routing_regret=1.5, sample_count=1)
    with pytest.raises(ValueError, match="routing_regret must be a finite number in \\[0, 1\\] or None"):
        EvaluationReport(suite_id="s", per_category=(("code", 0.5),), routing_regret=-0.01, sample_count=1)
    with pytest.raises(ValueError, match="cost_at_quality must be a finite number >= 0 or None"):
        EvaluationReport(suite_id="s", per_category=(("code", 0.5),), cost_at_quality=-1.0, sample_count=1)
    with pytest.raises(ValueError, match=r"score for 'code' must be a finite number in \[0, 1\]"):
        EvaluationReport(suite_id="s", per_category=(("code", 2.0),), sample_count=1)
    with pytest.raises(ValueError, match="duplicate category 'code'"):
        EvaluationReport(suite_id="s", per_category=(("code", 0.5), ("code", 0.6)), sample_count=2)
    with pytest.raises(ValueError, match="sample_count must be >= 1 when per_category carries rows"):
        EvaluationReport(suite_id="s", per_category=(("code", 0.5),), sample_count=0)
    with pytest.raises(ValueError, match="sample_count must be 0 when per_category carries no rows"):
        EvaluationReport(suite_id="s", per_category=(), sample_count=2)


# ---------------------------------------------------------------- evaluate


def test_evaluate_reports_per_category_not_one_average():
    suite = _suite([_case("c1", task_category="code"), _case("c2", task_category="code"), _case("c3", task_category="research")])
    report = evaluate(suite, {"c1": 1.0, "c2": 0.0, "c3": 0.5})
    assert report.suite_id == "suite-a"
    assert report.per_category == (("code", 0.5), ("research", 0.5))
    assert report.sample_count == 3
    assert report.global_rank_available is False
    assert "global_rank" not in report.to_dict()


def test_evaluate_refuses_an_outcome_for_an_unknown_case():
    with pytest.raises(ValueError, match="not a case id in suite 'suite-a'"):
        evaluate(_suite(), {"nope": 0.5})


def test_evaluate_refuses_a_holdout_case_outcome_by_name():
    suite = _suite([_case("c1")], holdout=(_case("h1"),))
    with pytest.raises(ValueError) as excinfo:
        evaluate(suite, {"h1": 0.9})
    assert "HOLDOUT" in str(excinfo.value)
    assert "h1" in str(excinfo.value)


def test_evaluate_refuses_an_outcome_outside_the_unit_interval():
    with pytest.raises(ValueError, match="outcome 'c1' must be a finite number in \\[0, 1\\]"):
        evaluate(_suite(), {"c1": 1.4})


def test_evaluate_with_no_outcomes_reports_nothing_and_none_regret():
    report = evaluate(_suite(), {})
    assert report.per_category == ()
    assert report.sample_count == 0
    assert report.routing_regret is None
    assert report.cost_at_quality is None


def test_routing_regret_is_none_without_a_candidate_set():
    report = evaluate(_suite(), {"c1": 0.9, "c2": 0.8})
    assert report.routing_regret is None


def test_routing_regret_is_none_when_no_case_is_comparable():
    # A candidate set is supplied, but the caller measured no alternative
    # candidate, so there is nothing to be regretful about.
    report = evaluate(_suite(), {"c1": 0.9, "c2": 0.8}, candidates={"c1": "agent:beta"}, chosen="agent:alpha")
    assert report.routing_regret is None


def test_routing_regret_is_the_mean_gap_to_the_best_observed_candidate():
    report = evaluate(
        _suite(),
        {"c1": 0.6, "c2": 0.9, "c1::agent:beta": 0.9, "c2::agent:beta": 0.9},
        candidates={"c1": "agent:beta", "c2": "agent:beta"},
        chosen="agent:alpha",
    )
    assert report.routing_regret == pytest.approx(0.15)
    assert report.per_category == (("code", 0.75),)


def test_routing_regret_never_goes_negative_when_the_choice_beats_the_benchmark():
    report = evaluate(
        _suite(),
        {"c1": 1.0, "c2": 1.0, "c1::agent:beta": 0.2, "c2::agent:beta": 0.2},
        candidates={"c1": "agent:beta", "c2": "agent:beta"},
        chosen="agent:alpha",
    )
    assert report.routing_regret == 0.0


def test_evaluate_refuses_a_benchmark_key_for_an_unknown_case():
    with pytest.raises(ValueError, match="not a case id in suite 'suite-a'"):
        evaluate(_suite(), {"c1": 0.5, "nope::agent:beta": 0.9}, candidates={"c1": "agent:beta"}, chosen="agent:alpha")


def test_evaluate_refuses_a_non_suite_and_a_non_numeric_outcome():
    with pytest.raises(ValueError, match="evaluate\\(\\) expects an EvaluationSuite"):
        evaluate({"c1": 0.5}, {})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="outcome 'c1' must be a finite number in \\[0, 1\\]"):
        evaluate(_suite(), {"c1": "high"})  # type: ignore[dict-item]


# --------------------------------------------------------- should_explore


def test_exploration_is_refused_when_the_budget_is_gone():
    decision = should_explore(incumbent_score=0.2, challenger_score=0.9, remaining_budget_fraction=0.0)
    assert isinstance(decision, ExplorationDecision)
    assert decision.explore is False
    assert "budget" in decision.reason
    assert decision.remaining_budget_fraction == 0.0


def test_exploration_is_refused_when_the_remaining_budget_is_unknown():
    decision = should_explore(incumbent_score=0.2, challenger_score=0.9, remaining_budget_fraction=None)
    assert decision.explore is False
    assert "unknown" in decision.reason
    assert decision.remaining_budget_fraction is None


def test_exploration_is_refused_when_the_exploration_share_is_zero():
    decision = should_explore(incumbent_score=0.2, challenger_score=0.9, remaining_budget_fraction=0.8, exploration_budget_fraction=0.0)
    assert decision.explore is False
    assert "exploration budget share is 0" in decision.reason


def test_exploration_refuses_an_out_of_range_exploration_share():
    with pytest.raises(ValueError, match="exploration_budget_fraction must be a finite number in \\[0, 1\\]"):
        should_explore(incumbent_score=0.5, challenger_score=0.5, remaining_budget_fraction=0.5, exploration_budget_fraction=1.5)
    with pytest.raises(ValueError, match="remaining_budget_fraction must be a finite number in \\[0, 1\\] or None"):
        should_explore(incumbent_score=0.5, challenger_score=0.5, remaining_budget_fraction=-0.1)


def test_nothing_measured_does_not_burn_a_request():
    decision = should_explore(incumbent_score=None, challenger_score=None, remaining_budget_fraction=0.9)
    assert decision.explore is False
    assert "nothing measured" in decision.reason


def test_a_thin_incumbent_is_explored():
    decision = should_explore(incumbent_score=0.2, challenger_score=0.95, remaining_budget_fraction=0.9)
    assert decision.explore is True
    assert THIN_INCUMBENT_FLOOR.__class__ is float
    assert decision.reason


def test_an_unmeasured_incumbent_against_a_measured_challenger_is_explored():
    decision = should_explore(incumbent_score=None, challenger_score=0.4, remaining_budget_fraction=0.9)
    assert decision.explore is True
    assert "unmeasured" in decision.reason


def test_a_well_measured_incumbent_is_not_explored():
    decision = should_explore(incumbent_score=0.95, challenger_score=0.99, remaining_budget_fraction=0.9)
    assert decision.explore is False
    assert "0.95" in decision.reason


def test_scores_outside_the_unit_interval_are_refused():
    with pytest.raises(ValueError, match="incumbent_score must be a finite number in \\[0, 1\\] or None"):
        should_explore(incumbent_score=1.2, challenger_score=0.5, remaining_budget_fraction=0.5)
    with pytest.raises(ValueError, match="challenger_score must be a finite number in \\[0, 1\\] or None"):
        should_explore(incumbent_score=0.5, challenger_score=float("inf"), remaining_budget_fraction=0.5)


def test_exploration_decision_requires_a_reason():
    with pytest.raises(ValueError, match="reason must be non-empty"):
        ExplorationDecision(explore=True, reason="", remaining_budget_fraction=0.5)
