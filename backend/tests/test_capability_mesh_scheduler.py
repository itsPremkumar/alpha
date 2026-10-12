"""Tests for the value-of-information scheduler (Feature I / plan §10).

The scheduler answers "what should happen NEXT", not "which model is
strongest". The load-bearing rules pinned here: the plan's expected-net-value
formula exactly, an UNPRICED prior that is *named* where it is used (never a
reassuring 0.0), an unavailable action refused by name, no re-asking of an
already settled point, a stopping decision that always names the criterion or
the risk that blocked it, and round-trips that re-validate.
"""

from __future__ import annotations

import math

import pytest

from alpha.capability_mesh.scheduler import (
    DEFAULT_UNRESOLVED_RISK_THRESHOLD,
    MAX_DESCRIPTION_CHARS,
    UNPRICED_PRIOR,
    ActionKind,
    CandidateAction,
    StopDecision,
    VoIEstimate,
    choose_next_action,
    expected_net_value,
    should_stop,
)


def _action(kind: ActionKind = ActionKind.RUN_TEST, description: str = "run the unit suite", **kwargs: object) -> CandidateAction:
    base: dict[str, object] = {
        "kind": kind,
        "description": description,
        "expected_risk_or_error_reduction": 0.4,
        "expected_coverage_gain": 0.25,
        "expected_decision_clarity": 0.15,
        "cost_usd": 0.02,
        "latency_seconds": 0.05,
        "new_failure_risk": 0.01,
    }
    base.update(kwargs)
    return CandidateAction(**base)  # type: ignore[arg-type]


class TestExpectedNetValue:
    def test_hand_computed_formula(self) -> None:
        # 0.40 + 0.25 + 0.15 - 0.02 - 0.05 - 0.01 = 0.72, by hand.
        estimate = expected_net_value(_action())
        assert estimate.expected_net_value == pytest.approx(0.72, abs=1e-9)
        assert estimate.speculative is False
        assert estimate.action.kind is ActionKind.RUN_TEST

    def test_components_are_the_observable_terms(self) -> None:
        components = expected_net_value(_action()).components
        assert components == pytest.approx(
            {
                "expected_risk_or_error_reduction": 0.40,
                "expected_coverage_gain": 0.25,
                "expected_decision_clarity": 0.15,
                "cost": -0.02,
                "latency": -0.05,
                "new_failure_risk": -0.01,
            },
            abs=1e-9,
        )

    def test_components_sum_to_the_estimate(self) -> None:
        estimate = expected_net_value(_action(cost_usd=None, latency_seconds=None))
        assert math.fsum(estimate.components.values()) == estimate.expected_net_value

    def test_unpriced_cost_uses_the_prior_and_names_the_substitution(self) -> None:
        estimate = expected_net_value(_action(cost_usd=None, latency_seconds=None))
        # 0.40 + 0.25 + 0.15 - 0.05 - 0.05 - 0.01 = 0.69, by hand.
        assert estimate.expected_net_value == pytest.approx(0.69, abs=1e-9)
        assert "cost" not in estimate.components
        assert estimate.components["cost_unpriced_prior"] == pytest.approx(-UNPRICED_PRIOR, abs=1e-9)
        assert estimate.components["latency_unpriced_prior"] == pytest.approx(-UNPRICED_PRIOR, abs=1e-9)
        assert estimate.speculative is True, "a substituted prior makes the number an estimate, not a measurement"

    def test_one_unpriced_term_leaves_the_other_measured(self) -> None:
        estimate = expected_net_value(_action(latency_seconds=None))
        assert "latency" not in estimate.components
        assert "latency_unpriced_prior" in estimate.components
        assert estimate.components["cost"] == pytest.approx(-0.02, abs=1e-9)
        assert estimate.speculative is True

    def test_a_purely_harmless_action_scores_above_a_purely_harmful_one(self) -> None:
        good = expected_net_value(_action(new_failure_risk=0.0))
        bad = expected_net_value(_action(new_failure_risk=1.0))
        assert good.expected_net_value - bad.expected_net_value == pytest.approx(1.0, abs=1e-9)

    def test_unavailable_action_is_refused_by_name(self) -> None:
        action = _action(description="read the upstream changelog", available=False, blocking_reason="the sandbox has no network")
        with pytest.raises(ValueError) as error:
            expected_net_value(action)
        assert "read the upstream changelog" in str(error.value)
        assert "upstream changelog" in str(error.value) or "the sandbox has no network" in str(error.value)

    def test_estimate_round_trips(self) -> None:
        estimate = expected_net_value(_action(cost_usd=None))
        assert VoIEstimate.from_dict(estimate.to_dict()) == estimate


class TestVoIEstimateHonesty:
    def _components(self, **overrides: float) -> dict[str, float]:
        base = {
            "expected_risk_or_error_reduction": 0.4,
            "expected_coverage_gain": 0.25,
            "expected_decision_clarity": 0.15,
            "cost": -0.02,
            "latency": -0.05,
            "new_failure_risk": -0.01,
        }
        base.update(overrides)
        return base

    def _estimate(self, components: dict[str, float], *, speculative: bool = False) -> VoIEstimate:
        return VoIEstimate(action=_action(), expected_net_value=math.fsum(components.values()), components=components, speculative=speculative)

    def test_components_that_do_not_produce_the_number_are_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            VoIEstimate(action=_action(), expected_net_value=0.99, components=self._components(), speculative=False)
        assert "expected_net_value" in str(error.value)

    def test_a_prior_component_without_the_speculative_flag_is_refused(self) -> None:
        components = self._components(cost=-UNPRICED_PRIOR)
        del components["cost"]
        components["cost_unpriced_prior"] = -UNPRICED_PRIOR
        with pytest.raises(ValueError) as error:
            self._estimate(components)
        assert "speculative" in str(error.value)

    def test_speculative_without_a_prior_component_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            self._estimate(self._components(), speculative=True)
        assert "speculative" in str(error.value)

    def test_a_missing_component_is_refused_by_name(self) -> None:
        components = self._components()
        del components["new_failure_risk"]
        with pytest.raises(ValueError) as error:
            self._estimate(components)
        assert "new_failure_risk" in str(error.value)

    def test_an_unknown_component_is_refused_by_name(self) -> None:
        components = self._components(vibes=0.5)
        with pytest.raises(ValueError) as error:
            self._estimate(components)
        assert "vibes" in str(error.value)

    def test_a_missing_cost_or_latency_term_is_refused(self) -> None:
        components = self._components()
        del components["cost"]
        with pytest.raises(ValueError) as error:
            self._estimate(components)
        assert "cost" in str(error.value)


class TestChooseNextAction:
    def test_picks_the_highest_net_value(self) -> None:
        weak = _action(description="ask the specialist", expected_risk_or_error_reduction=0.1)
        strong = _action(description="run the failing test", expected_risk_or_error_reduction=0.9)
        choice = choose_next_action([weak, strong])
        assert choice is not None
        assert choice.action.description == "run the failing test"

    def test_ties_keep_declaration_order(self) -> None:
        first = _action(description="first action")
        second = _action(description="second action")
        choice = choose_next_action([first, second])
        assert choice is not None
        assert choice.action.description == "first action"

    def test_unavailable_actions_are_skipped(self) -> None:
        unavailable = _action(description="read the primary source", expected_risk_or_error_reduction=0.9, available=False, blocking_reason="no network in this sandbox")
        available = _action(description="run the test", expected_risk_or_error_reduction=0.1)
        choice = choose_next_action([unavailable, available])
        assert choice is not None
        assert choice.action.description == "run the test"

    def test_settled_point_suppresses_a_re_ask(self) -> None:
        # The re-ask scores higher than anything else and is still refused: the
        # plan forbids asking agents to rephrase an already settled point.
        reask = _action(kind=ActionKind.ASK_SPECIALIST, description="ask the specialist to restate the rate limit is 100 req/s", expected_risk_or_error_reduction=0.95)
        other = _action(kind=ActionKind.RUN_TEST, description="run the integration test", expected_risk_or_error_reduction=0.2)
        choice = choose_next_action([reask, other], settled_points=("the rate limit is 100 req/s",))
        assert choice is not None
        assert choice.action.description == "run the integration test"

    def test_settled_matching_ignores_case_and_extra_whitespace(self) -> None:
        reask = _action(description="Please  restate:   the rate limit is 100 req/s")
        choice = choose_next_action([reask], settled_points=("The   Rate Limit is 100 req/s",))
        assert choice is None

    def test_none_when_nothing_remains(self) -> None:
        assert choose_next_action([]) is None
        assert choose_next_action([_action(available=False, blocking_reason="unreachable")]) is None
        assert choose_next_action([_action(description="settled already")], settled_points=("settled already",)) is None

    def test_a_non_action_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            choose_next_action(["run a test"])  # type: ignore[list-item]
        assert "run a test" in str(error.value)


class TestStopDecision:
    def test_stops_only_when_criteria_are_met_and_risk_is_low(self) -> None:
        decision = should_stop(unmet_criteria=(), unresolved_risk=0.1)
        assert decision.should_stop is True
        assert decision.unmet_criteria == ()
        assert decision.unresolved_risk == pytest.approx(0.1, abs=1e-9)
        assert decision.reason

    def test_reason_names_the_blocking_criterion(self) -> None:
        decision = should_stop(unmet_criteria=("the total is reproducible", "the source is primary"), unresolved_risk=0.0)
        assert decision.should_stop is False
        assert "the total is reproducible" in decision.reason
        assert "the source is primary" in decision.reason
        assert decision.unmet_criteria == ("the total is reproducible", "the source is primary")

    def test_reason_names_the_blocking_risk(self) -> None:
        decision = should_stop(unmet_criteria=(), unresolved_risk=0.5)
        assert decision.should_stop is False
        assert "0.500" in decision.reason
        assert str(DEFAULT_UNRESOLVED_RISK_THRESHOLD) in decision.reason

    def test_reason_names_both_blockers_when_both_apply(self) -> None:
        decision = should_stop(unmet_criteria=("the primary source is cited",), unresolved_risk=0.9)
        assert decision.should_stop is False
        assert "the primary source is cited" in decision.reason
        assert "0.900" in decision.reason

    def test_the_threshold_is_explicit_and_inclusive(self) -> None:
        assert should_stop(unmet_criteria=(), unresolved_risk=0.2, unresolved_risk_threshold=0.2).should_stop is True
        assert should_stop(unmet_criteria=(), unresolved_risk=0.2000001, unresolved_risk_threshold=0.2).should_stop is False

    def test_every_branch_records_an_auditable_reason(self) -> None:
        for unmet, risk in (((), 0.0), (("a",), 0.0), ((), 1.0), (("a", "b"), 0.7)):
            reason = should_stop(unmet_criteria=unmet, unresolved_risk=risk).reason
            assert reason.strip()
            assert reason.startswith(("stopping:", "not stopping:"))

    def test_out_of_range_risk_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            should_stop(unmet_criteria=(), unresolved_risk=1.4)
        assert "unresolved_risk" in str(error.value)

    def test_out_of_range_threshold_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            should_stop(unmet_criteria=(), unresolved_risk=0.1, unresolved_risk_threshold=-0.1)
        assert "unresolved_risk_threshold" in str(error.value)

    def test_an_empty_criterion_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            should_stop(unmet_criteria=("",), unresolved_risk=0.1)
        assert "unmet_criteria" in str(error.value)

    def test_round_trips(self) -> None:
        decision = should_stop(unmet_criteria=("a criterion",), unresolved_risk=0.4)
        assert StopDecision.from_dict(decision.to_dict()) == decision


class TestCandidateActionRefusals:
    def test_empty_description_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _action(description="")
        assert "description" in str(error.value)

    def test_oversized_description_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _action(description="x" * (MAX_DESCRIPTION_CHARS + 1))
        assert str(MAX_DESCRIPTION_CHARS) in str(error.value)

    def test_description_at_the_bound_is_accepted(self) -> None:
        assert _action(description="x" * MAX_DESCRIPTION_CHARS).description

    @pytest.mark.parametrize("field", ["expected_risk_or_error_reduction", "expected_coverage_gain", "expected_decision_clarity", "new_failure_risk"])
    def test_fraction_bounds_are_refused_not_clamped(self, field: str) -> None:
        with pytest.raises(ValueError) as error:
            _action(**{field: 1.5})
        assert field in str(error.value)

    def test_a_boolean_is_not_a_fraction(self) -> None:
        with pytest.raises(ValueError) as error:
            _action(expected_coverage_gain=True)
        assert "expected_coverage_gain" in str(error.value)

    def test_an_unavailable_action_must_name_its_blocker(self) -> None:
        with pytest.raises(ValueError) as error:
            _action(available=False)
        assert "blocking_reason" in str(error.value)

    def test_an_available_action_may_not_carry_a_blocker(self) -> None:
        with pytest.raises(ValueError) as error:
            _action(blocking_reason="nothing is wrong here")
        assert "blocking_reason" in str(error.value)

    def test_negative_cost_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _action(cost_usd=-0.01)
        assert "cost_usd" in str(error.value)

    def test_negative_latency_is_refused(self) -> None:
        with pytest.raises(ValueError) as error:
            _action(latency_seconds=-1.0)
        assert "latency_seconds" in str(error.value)

    def test_unknown_kind_is_refused_with_the_valid_names(self) -> None:
        with pytest.raises(ValueError) as error:
            CandidateAction(
                kind="think_harder",  # type: ignore[arg-type]
                description="d",
                expected_risk_or_error_reduction=0.1,
                expected_coverage_gain=0.1,
                expected_decision_clarity=0.1,
                new_failure_risk=0.1,
            )
        assert "think_harder" in str(error.value)
        assert ActionKind.STOP_AND_RETURN.value in str(error.value)

    def test_round_trips(self) -> None:
        action = _action(kind=ActionKind.SWITCH_MODEL_FAMILY, description="switch to a different family", cost_usd=None)
        assert CandidateAction.from_dict(action.to_dict()) == action


def test_every_planned_candidate_action_kind_exists() -> None:
    # The nine candidates listed by plan §10, in the plan's order.
    assert [kind.value for kind in ActionKind] == [
        "run_test",
        "read_primary_source",
        "ask_specialist",
        "inspect_file",
        "compare_alternatives",
        "switch_model_family",
        "create_counterexample",
        "request_missing_constraint",
        "stop_and_return",
    ]
