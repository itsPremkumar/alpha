from __future__ import annotations

import pytest

from alpha.reasoning.budget import (
    BudgetExhaustedError,
    BudgetLimits,
    ReasoningBudgetLedger,
)
from alpha.reasoning.models import BudgetDimension, StopReason
from alpha.reasoning.ttcs import (
    LeafStrategy,
    ProcessVerifierKind,
    TTCSAllocator,
    TTCSRefused,
    TTCSRegime,
    TTCSRequest,
    TTCSSettings,
    VerifierCapability,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def limits_for(dimension: BudgetDimension, value: float = 1.0) -> BudgetLimits:
    values = {f"max_{item.value}": 10.0 for item in BudgetDimension}
    values[f"max_{dimension.value}"] = value
    return BudgetLimits.model_validate(values)


@pytest.mark.parametrize("dimension", list(BudgetDimension))
def test_every_budget_dimension_is_hard_enforced(dimension: BudgetDimension) -> None:
    ledger = ReasoningBudgetLedger(limits_for(dimension))
    first_amount = 0.5 if dimension is BudgetDimension.WALL_CLOCK_SECONDS else 1.0
    second_amount = 0.6 if dimension is BudgetDimension.WALL_CLOCK_SECONDS else 1.0
    accepted = ledger.consume(dimension, first_amount, reason_code="first")
    refused = ledger.consume(dimension, second_amount, reason_code="second")

    assert accepted.allowed is True
    assert refused.allowed is False
    assert refused.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert ledger.remaining(dimension) <= second_amount
    assert ledger.snapshot().exhausted == [dimension]
    assert [entry.accepted for entry in ledger.accounting_trail] == [True, False]
    assert ledger.accounting_trail[-1].reason_code == "second"


def test_wall_clock_is_enforced_from_the_injected_clock() -> None:
    clock = FakeClock()
    ledger = ReasoningBudgetLedger(limits_for(BudgetDimension.WALL_CLOCK_SECONDS, 1.0), clock=clock)
    clock.value = 1.5
    status = ledger.status()
    assert status.exhausted is True
    assert status.exhausted_dimensions == (BudgetDimension.WALL_CLOCK_SECONDS,)
    assert ledger.remaining(BudgetDimension.WALL_CLOCK_SECONDS) == 0


def test_multi_dimension_reservation_is_atomic() -> None:
    ledger = ReasoningBudgetLedger(BudgetLimits(max_model_calls=2, max_tokens=5))
    admission = ledger.consume_many(
        {BudgetDimension.MODEL_CALLS: 1, BudgetDimension.TOKENS: 10},
        reason_code="atomic",
    )
    assert admission.allowed is False
    assert ledger.remaining(BudgetDimension.MODEL_CALLS) == 2
    assert ledger.remaining(BudgetDimension.TOKENS) == 5


def test_marginal_value_refuses_a_low_value_step_and_budget_can_veto() -> None:
    ledger = ReasoningBudgetLedger(limits_for(BudgetDimension.MODEL_CALLS, 1.0))
    low = ledger.continue_if_value(expected_information_gain=0.2, expected_cost=0.3, expected_risk=0.1)
    assert low.should_continue is False
    assert "does not exceed" in low.reason

    high = ledger.continue_if_value(expected_information_gain=0.8, expected_cost=0.3, expected_risk=0.1)
    assert high.should_continue is True

    ledger.consume(BudgetDimension.MODEL_CALLS, 1)
    exhausted = ledger.continue_if_value(expected_information_gain=5, expected_cost=0.1)
    assert exhausted.should_continue is False
    assert exhausted.budget_available is False
    assert "exhausted" in exhausted.reason


def test_require_raises_on_refusal_with_the_real_admission() -> None:
    ledger = ReasoningBudgetLedger(limits_for(BudgetDimension.TOOL_CALLS, 1.0))
    ledger.require(BudgetDimension.TOOL_CALLS, 1)
    with pytest.raises(BudgetExhaustedError) as excinfo:
        ledger.require(BudgetDimension.TOOL_CALLS, 1)
    assert excinfo.value.admission.allowed is False
    assert excinfo.value.admission.stop_reason is StopReason.BUDGET_EXHAUSTED


def test_budget_limits_clamp_and_disclose_instead_of_raising() -> None:
    limits = BudgetLimits(max_model_calls=-1, max_wall_clock_seconds=10_000_000)
    assert limits.max_model_calls == 0
    assert limits.max_wall_clock_seconds == 604_800
    assert len(limits.clamp_disclosures) == 2


def deterministic_prefix_verifier() -> VerifierCapability:
    return VerifierCapability(
        name="invariant-probe",
        kind=ProcessVerifierKind.DETERMINISTIC,
        is_step_level=True,
        is_independent=True,
        checks_invariants=True,
    )


def test_default_allocation_is_single_trajectory() -> None:
    ledger = ReasoningBudgetLedger(BudgetLimits(max_model_calls=2, max_tokens=100))
    allocator = TTCSAllocator(TTCSSettings())
    allocation = allocator.allocate(allocator.default_request(), ledger)
    assert allocation.admitted is True
    assert allocation.regime is TTCSRegime.SINGLE_TRAJECTORY
    assert allocation.samples == 1
    assert ledger.remaining(BudgetDimension.MODEL_CALLS) == 1


def test_leaf_level_enforces_its_sample_budget_and_reduction() -> None:
    ledger = ReasoningBudgetLedger(BudgetLimits(max_model_calls=2, max_tokens=1000))
    allocator = TTCSAllocator(TTCSSettings(max_samples=4))
    with pytest.raises(TTCSRefused, match="explicit"):
        allocator.allocate(TTCSRequest(regime=TTCSRegime.LEAF_LEVEL, requested_samples=2), ledger)
    refused = allocator.allocate(
        TTCSRequest(regime=TTCSRegime.LEAF_LEVEL, requested_samples=5, leaf_strategy=LeafStrategy.SELF_CONSISTENCY),
        ledger,
    )
    assert refused.admitted is False
    assert "max_samples" in refused.reason

    over_budget = allocator.allocate(
        TTCSRequest(regime=TTCSRegime.LEAF_LEVEL, requested_samples=3, leaf_strategy=LeafStrategy.MINIMUM_BAYES_RISK),
        ledger,
    )
    assert over_budget.admitted is False
    assert ledger.remaining(BudgetDimension.MODEL_CALLS) == 2

    admitted_ledger = ReasoningBudgetLedger(BudgetLimits(max_model_calls=2, max_tokens=1000))
    admitted = allocator.allocate(
        TTCSRequest(regime=TTCSRegime.LEAF_LEVEL, requested_samples=2, leaf_strategy=LeafStrategy.SELF_CONSISTENCY),
        admitted_ledger,
    )
    assert admitted.admitted is True
    assert admitted_ledger.remaining(BudgetDimension.MODEL_CALLS) == 0


def test_prefix_level_refuses_missing_or_fake_step_verifiers() -> None:
    ledger = ReasoningBudgetLedger(BudgetLimits(max_model_calls=4, max_branches=2, max_tokens=1000))
    allocator = TTCSAllocator(TTCSSettings())
    request = TTCSRequest(regime=TTCSRegime.PREFIX_LEVEL, requested_samples=2, branch_count=2)

    with pytest.raises(TTCSRefused, match="requires an injected"):
        allocator.allocate(request, ledger)
    with pytest.raises(TTCSRefused, match="not a process verifier"):
        allocator.allocate(
            request,
            ledger,
            verifier=VerifierCapability(
                name="cheap-self-rating",
                kind=ProcessVerifierKind.LLM_SELF_RATING,
                is_step_level=True,
                is_independent=True,
            ),
        )
    with pytest.raises(TTCSRefused, match="independent"):
        allocator.allocate(
            request,
            ledger,
            verifier=VerifierCapability(
                name="dependent-prm",
                kind=ProcessVerifierKind.TRAINED_PROCESS_REWARD_MODEL,
                is_step_level=True,
                calibration_ref="fixture-evaluation-v1",
            ),
        )

    allocation = allocator.allocate(request, ledger, verifier=deterministic_prefix_verifier())
    assert allocation.admitted is True
    assert allocation.branches == 2
    assert ledger.remaining(BudgetDimension.BRANCHES) == 0


def test_best_of_n_refuses_generator_self_rating() -> None:
    ledger = ReasoningBudgetLedger(BudgetLimits(max_model_calls=4, max_tokens=1000))
    allocator = TTCSAllocator(TTCSSettings())
    request = TTCSRequest(
        regime=TTCSRegime.LEAF_LEVEL,
        requested_samples=2,
        leaf_strategy=LeafStrategy.BEST_OF_N,
    )
    with pytest.raises(TTCSRefused, match="self-rating"):
        allocator.allocate(
            request,
            ledger,
            verifier=VerifierCapability(
                name="generator-self-rating",
                kind=ProcessVerifierKind.LLM_SELF_RATING,
                is_step_level=True,
            ),
        )


def test_regimes_cannot_exceed_the_shared_budget() -> None:
    ledger = ReasoningBudgetLedger(BudgetLimits(max_model_calls=3, max_branches=1, max_tokens=10_000))
    allocator = TTCSAllocator(TTCSSettings(max_samples=5))
    first = allocator.allocate(
        TTCSRequest(regime=TTCSRegime.LEAF_LEVEL, requested_samples=2, leaf_strategy=LeafStrategy.SELF_CONSISTENCY),
        ledger,
    )
    second = allocator.allocate(
        TTCSRequest(regime=TTCSRegime.LEAF_LEVEL, requested_samples=2, leaf_strategy=LeafStrategy.SELF_CONSISTENCY),
        ledger,
    )
    assert first.admitted is True
    assert second.admitted is False
    assert ledger.remaining(BudgetDimension.MODEL_CALLS) == 1
    assert ledger.snapshot().stop_reason is StopReason.BUDGET_EXHAUSTED
