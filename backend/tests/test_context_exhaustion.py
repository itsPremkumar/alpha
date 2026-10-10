"""Unit tests for :mod:`alpha.runtime.context_exhaustion`.

The ladder is the answer to "what happens when the window fills", and the
invariants here are the ones that stop it from becoming a second, quietly
different recovery authority:

* **order is never reordered by availability** — an unavailable rung stays in
  the plan carrying its reason, so a plan of three available rungs cannot read
  as "the only three options";
* **a nominal or unknown reading declares no rungs at all** — compacting a
  thread that is comfortable spends a model call for nothing, and reporting
  ``over`` for an undeclared window would park healthy work on a lie;
* **``over`` gets the whole ladder; ``critical`` gets the cheap pre-emptive
  step** — escalating to a more expensive model for a thread that merely
  approached the ceiling is the wrong trade;
* **``fail_closed`` is the only always-available rung**, which is what makes
  ``first_available`` total;
* **the ladder decides, it does not do** — no branch here performs I/O.
"""

from __future__ import annotations

import pytest

from alpha.runtime.context_exhaustion import (
    CONTEXT_EXHAUSTION_LADDER,
    CONTEXT_WINDOW_STOP_REASONS,
    ContextExhaustionStep,
    escalation_candidate,
    plan_context_recovery,
)
from alpha.runtime.context_window import classify_context_pressure, resolve_context_window


def _pressure(band_tokens: int, *, declared: int | None = 200_000, reserves: tuple[int, int] = (4096, 2048)):
    from alpha.config.context_window_config import ContextWindowConfig

    spec = resolve_context_window(
        declared_input_window=declared,
        output_reserve=reserves[0],
        next_turn_reserve=reserves[1],
    )
    return classify_context_pressure(band_tokens, spec, **ContextWindowConfig().band_thresholds())


class TestLadderOrdering:
    def test_ladder_is_declared_least_destructive_first(self) -> None:
        assert CONTEXT_EXHAUSTION_LADDER == ("compact", "evict_tool_outputs", "escalate_window", "park", "fail_closed")

    def test_over_band_declares_every_rung(self) -> None:
        plan = plan_context_recovery(_pressure(199_000))
        assert [step.action for step in plan.steps] == list(CONTEXT_EXHAUSTION_LADDER)

    def test_critical_band_is_pre_emptive_not_full_ladder(self) -> None:
        # 170_000 / 193_792 ≈ 0.877, which is critical but not over.
        plan = plan_context_recovery(_pressure(170_000))
        assert [step.action for step in plan.steps] == ["compact", "fail_closed"]
        assert plan.reason == "step_pre_emptive"

    def test_nominal_and_unknown_declare_nothing(self) -> None:
        assert plan_context_recovery(_pressure(1_000)).steps == ()
        assert plan_context_recovery(_pressure(1_000, declared=None)).steps == ()

    def test_empty_plan_is_a_state_not_a_failure(self) -> None:
        plan = plan_context_recovery(_pressure(1_000))
        assert plan.empty is True
        assert plan.reason == "no_action_required"
        assert plan.first_available is None


class TestAvailabilityNeverReorders:
    def test_unavailable_rung_stays_in_place_with_its_reason(self) -> None:
        plan = plan_context_recovery(
            _pressure(199_000),
            compaction_available=False,
            eviction_available=False,
            escalation_available=False,
            park_available=True,
        )
        assert [step.action for step in plan.steps] == list(CONTEXT_EXHAUSTION_LADDER)
        assert plan.steps[0].available is False
        assert plan.steps[0].reason == "compaction_not_enabled"
        assert plan.steps[3].available is True
        # The skipped rungs are disclosed, not dropped.
        assert plan.unavailable_reasons == ("compaction_not_enabled", "eviction_not_enabled", "no_model_with_larger_window")

    def test_first_available_walks_the_ladder_in_order(self) -> None:
        plan = plan_context_recovery(_pressure(199_000), compaction_available=False, eviction_available=True)
        assert plan.first_available.action == "evict_tool_outputs"

    def test_every_recovery_rung_refused_falls_through_to_the_terminal_one(self) -> None:
        plan = plan_context_recovery(_pressure(199_000))
        # fail_closed is always available, so the plan can always be acted on.
        assert plan.first_available.action == "fail_closed"
        assert plan.steps[-1].action == "fail_closed"
        assert plan.steps[-1].available is True
        # And the four skipped rungs are still named, so the terminal outcome
        # is not mistaken for "there was nothing else to try".
        assert plan.unavailable_reasons == (
            "compaction_not_enabled",
            "eviction_not_enabled",
            "no_model_with_larger_window",
            "parking_not_available",
        )

    def test_fail_closed_is_the_only_always_available_rung(self) -> None:
        for kwargs in (
            {},
            {"compaction_available": True},
            {"park_available": True, "escalation_available": True},
        ):
            plan = plan_context_recovery(_pressure(199_000), **kwargs)
            always = [s.action for s in plan.steps if s.available]
            assert "fail_closed" in always


class TestStopReasons:
    def test_only_park_and_fail_closed_persist_a_stop_reason(self) -> None:
        assert CONTEXT_WINDOW_STOP_REASONS["park"] == "context_window_parked"
        assert CONTEXT_WINDOW_STOP_REASONS["fail_closed"] == "context_window_exhausted"

    def test_non_durable_rungs_carry_no_stop_reason(self) -> None:
        plan = plan_context_recovery(_pressure(199_000), compaction_available=True)
        for step in plan.steps:
            if step.action in ("compact", "evict_tool_outputs", "escalate_window"):
                assert step.stop_reason is None


class TestEscalationCandidate:
    def test_picks_the_largest_window_above_the_current_one(self) -> None:
        assert escalation_candidate(declared_windows={"a": 128_000, "b": 200_000, "c": 1_000_000}, current_window=128_000) == "b"

    def test_ties_break_by_name(self) -> None:
        assert escalation_candidate(declared_windows={"zebra": 200_000, "alpha": 200_000}, current_window=128_000) == "alpha"

    def test_equal_window_is_refused(self) -> None:
        # Routing to a same-sized window is a model switch with no headroom
        # bought, which is a cost the user pays for nothing.
        assert escalation_candidate(declared_windows={"a": 128_000, "b": 128_000}, current_window=128_000) is None

    def test_no_current_window_means_no_candidate(self) -> None:
        # Without the current size, "larger" is unanswerable.
        assert escalation_candidate(declared_windows={"a": 200_000}, current_window=None) is None

    def test_empty_catalog_means_no_candidate(self) -> None:
        assert escalation_candidate(declared_windows={}, current_window=128_000) is None


class TestStepShape:
    def test_available_reason_is_stable(self) -> None:
        assert ContextExhaustionStep(action="compact", available=True, reason="x").reason == "x"
        with pytest.raises(Exception):
            ContextExhaustionStep(action="compact", available=True, reason="x").action = "park"  # type: ignore[misc]
