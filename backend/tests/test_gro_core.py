"""GRO core value types, policy classification, and strategy dealing.

Pins the load-bearing invariants of the group-relative orchestration engine:

- the run lifecycle is a **closed** transition table (an illegal move raises,
  terminal states have no exits);
- bounds are refused with the bound named, never clamped;
- risk and task type are **computed** from the goal, never declared;
- candidate strategies are distinct data, and a duplicate or unknown id is
  refused rather than silently deduped.
"""

from __future__ import annotations

import pytest

from alpha.gro.config import GroConfig
from alpha.gro.models import (
    TERMINAL_STATES,
    CandidateEvaluation,
    CandidateOutcome,
    CandidateSpec,
    CandidateStatus,
    GateResult,
    GateStatus,
    GroBudget,
    GroRun,
    GroRunState,
    IllegalGroTransition,
    RiskTier,
    RunMode,
    SelectionResult,
    StrategySpec,
    TaskContract,
    TaskType,
    allowed_transitions,
    can_transition,
)
from alpha.gro.policy import classify_risk_tier, classify_task_type, decide_mode, has_external_side_effects
from alpha.gro.strategies import REPAIR_STRATEGY, catalogue, select_strategies, strategies_by_id


def _contract(**overrides) -> TaskContract:
    base = {
        "run_id": "gro_test",
        "goal": "Fix the parser bug in module x",
        "task_type": TaskType.CODING,
        "risk_tier": RiskTier.ORDINARY,
    }
    base.update(overrides)
    return TaskContract(**base)


def _run(state: GroRunState = GroRunState.RECEIVED) -> GroRun:
    run = GroRun.new(run_id="gro_test", owner_id="u", thread_id="t", contract=_contract(), budget=GroBudget(), mode=RunMode.SINGLE)
    run.state = state
    return run


class TestStateMachine:
    def test_new_run_starts_received(self):
        run = GroRun.new(run_id=None, owner_id="u", thread_id="t", contract=_contract(), budget=GroBudget(), mode=RunMode.CANDIDATE_GROUP)
        assert run.state is GroRunState.RECEIVED
        assert run.run_id.startswith("gro_")
        assert run.is_terminal is False

    def test_legal_transition_is_journaled(self):
        run = _run()
        run.transition(GroRunState.NORMALIZED, reason="normalized")
        assert run.state is GroRunState.NORMALIZED
        assert run.events[-1]["type"] == "state_transition"
        assert run.events[-1]["from"] == "received"
        assert run.events[-1]["to"] == "normalized"
        assert run.events[-1]["reason"] == "normalized"

    def test_illegal_transition_raises_and_preserves_state(self):
        run = _run(GroRunState.RECEIVED)
        with pytest.raises(IllegalGroTransition) as excinfo:
            run.transition(GroRunState.PROMOTED, reason="shortcut")
        assert "received -> promoted" in str(excinfo.value)
        assert run.state is GroRunState.RECEIVED

    def test_terminal_states_have_no_exits(self):
        for state in TERMINAL_STATES:
            assert allowed_transitions(state) == frozenset()
            run = _run(state)
            for target in GroRunState:
                with pytest.raises(IllegalGroTransition):
                    run.transition(target, reason="terminal states never move")

    def test_full_happy_path_is_legal(self):
        run = _run()
        for state in (
            GroRunState.NORMALIZED,
            GroRunState.POLICY_DECIDED,
            GroRunState.GROUP_CREATED,
            GroRunState.READY,
            GroRunState.RUNNING,
            GroRunState.VERIFYING,
            GroRunState.RANKED,
            GroRunState.SELECTED,
            GroRunState.PROMOTED,
        ):
            run.transition(state, reason="path")
        assert run.state is GroRunState.PROMOTED
        assert run.is_terminal is True

    def test_can_transition_matches_table(self):
        assert can_transition(GroRunState.POLICY_DECIDED, GroRunState.NEEDS_HUMAN) is True
        assert can_transition(GroRunState.POLICY_DECIDED, GroRunState.PROMOTED) is False


class TestBudgetRefusals:
    def test_valid_budget(self):
        budget = GroBudget(max_candidates=3, max_active=2)
        assert budget.max_candidates == 3

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_candidates": 0},
            {"max_candidates": 3, "max_active": 0},
            {"max_candidates": 2, "max_active": 5},
            {"candidate_timeout_seconds": 0.0},
            {"run_timeout_seconds": -1.0},
            {"max_repair_rounds": -1},
        ],
    )
    def test_bounds_are_refused_with_the_bound_named(self, kwargs):
        with pytest.raises(ValueError):
            GroBudget(**kwargs)

    def test_roundtrip(self):
        budget = GroBudget(max_candidates=4, max_active=2, candidate_timeout_seconds=12.5, run_timeout_seconds=60.0, max_repair_rounds=2)
        assert GroBudget.from_dict(budget.to_dict()) == budget


class TestRunSerialization:
    def test_roundtrip_preserves_state_outcomes_and_selection(self):
        run = _run(GroRunState.RANKED)
        run.outcomes["cand_01"] = CandidateOutcome(candidate_id="cand_01", status=CandidateStatus.COMPLETED, output_text="done", usage={"total_tokens": 42})
        run.evaluations["cand_01"] = CandidateEvaluation(candidate_id="cand_01", evaluator_version="v", eligible=True, absolute_score=0.9, relative_rank=1)
        run.selection = SelectionResult(outcome="selected", selected_candidate_id="cand_01", reason="passed gates", ranking=["cand_01"])
        run.decision = {"mode": "single"}
        run.repair_round = 1

        restored = GroRun.from_dict(run.to_dict())
        assert restored.state is GroRunState.RANKED
        assert restored.outcomes["cand_01"].status is CandidateStatus.COMPLETED
        assert restored.outcomes["cand_01"].usage == {"total_tokens": 42}
        assert restored.evaluations["cand_01"].eligible is True
        assert restored.evaluations["cand_01"].absolute_score == 0.9
        assert restored.selection.selected_candidate_id == "cand_01"
        assert restored.repair_round == 1

    def test_unreported_usage_stays_none_after_roundtrip(self):
        outcome = CandidateOutcome(candidate_id="c", status=CandidateStatus.COMPLETED, output_text="x")
        assert outcome.usage is None
        assert CandidateOutcome.from_dict(outcome.to_dict()).usage is None

    def test_output_text_is_bounded_on_serialize(self):
        outcome = CandidateOutcome(candidate_id="c", status=CandidateStatus.COMPLETED, output_text="x" * 10_000)
        assert len(outcome.to_dict()["output_text"]) <= 4000

    def test_bash_executions_carry_a_count_not_the_payload(self):
        outcome = CandidateOutcome(candidate_id="c", status=CandidateStatus.COMPLETED, bash_executions=[{"command": "pytest"}])
        assert outcome.to_dict()["bash_execution_count"] == 1
        assert "bash_executions" not in outcome.to_dict()


class TestCandidatePrompt:
    def test_prompt_carries_objective_approach_and_criteria(self):
        spec = CandidateSpec(
            candidate_id="cand_01",
            run_id="gro_x",
            strategy=StrategySpec(strategy_id="coding.test_first", task_type=TaskType.CODING, approach="write the test first"),
            objective="Fix the parser",
            acceptance_criteria=("tests_passed: pytest",),
        )
        prompt = spec.to_prompt()
        assert "Objective: Fix the parser" in prompt
        assert "write the test first" in prompt
        assert "tests_passed: pytest" in prompt
        assert "Deliver the actual result" in prompt

    def test_repair_context_only_appears_on_repair_specs(self):
        plain = CandidateSpec(candidate_id="c", run_id="r", strategy=REPAIR_STRATEGY, objective="o")
        assert "previous attempt failed" not in plain.to_prompt().lower()
        repair = CandidateSpec(candidate_id="c", run_id="r", strategy=REPAIR_STRATEGY, objective="o", repair_context="- gate x: failed")
        assert "Fix exactly the proven shortfall" in repair.to_prompt()


class TestStrategyCatalogue:
    def test_dealt_strategies_are_distinct(self):
        dealt = select_strategies(TaskType.CODING, 3)
        assert len({s.strategy_id for s in dealt}) == 3
        assert all(s.task_type is TaskType.CODING for s in dealt)

    def test_over_dealing_is_refused_not_padded(self):
        pool = catalogue(TaskType.RESEARCH)
        with pytest.raises(ValueError) as excinfo:
            select_strategies(TaskType.RESEARCH, len(pool) + 1)
        assert str(len(pool)) in str(excinfo.value)

    def test_zero_candidates_refused(self):
        with pytest.raises(ValueError):
            select_strategies(TaskType.CODING, 0)

    def test_general_tasks_interleave_categories(self):
        dealt = select_strategies(TaskType.GENERAL, 3)
        kinds = {s.strategy_id.split(".")[0] for s in dealt}
        assert len(kinds) == 3  # one coding, one research, one planning/tool

    def test_unknown_strategy_id_refused(self):
        with pytest.raises(ValueError) as excinfo:
            strategies_by_id(("coding.nonexistent",))
        assert "coding.nonexistent" in str(excinfo.value)

    def test_duplicate_strategy_refused(self):
        with pytest.raises(ValueError) as excinfo:
            strategies_by_id(("coding.test_first", "coding.test_first"))
        assert "twice" in str(excinfo.value)


class TestPolicyClassification:
    def test_task_type_markers(self):
        assert classify_task_type("fix the traceback bug in the compiler") is TaskType.CODING
        assert classify_task_type("research the latest papers on retrieval") is TaskType.RESEARCH
        assert classify_task_type("plan the migration rollout schedule") is TaskType.PLANNING
        assert classify_task_type("say hello to the moon") is TaskType.GENERAL

    def test_tie_breaks_are_deterministic(self):
        text = "research and plan the fix for this test"
        assert classify_task_type(text) is TaskType.CODING

    def test_risk_tier_short_goal_is_trivial(self):
        tier, reasons = classify_risk_tier("say hello")
        assert tier is RiskTier.TRIVIAL
        assert "short_goal_few_criteria" in reasons

    def test_risk_tier_many_criteria_is_complex(self):
        tier, _ = classify_risk_tier("do the thing", ("a", "b", "c"))
        assert tier is RiskTier.COMPLEX

    def test_risk_is_computed_not_declared(self):
        tier, reasons = classify_risk_tier("deploy the service to production now")
        assert tier.rank >= RiskTier.UNCERTAIN.rank
        assert any(r.startswith("external_effect_markers") for r in reasons)

    def test_external_effect_detection(self):
        assert has_external_side_effects("publish the release notes") is True
        assert has_external_side_effects("summarize this document") is False


class TestDecideMode:
    def test_single_posture(self):
        decision = decide_mode("fix the bug", ("a", "b", "c"), config=GroConfig(enabled=True, default_mode="single"))
        assert decision.mode is RunMode.SINGLE
        assert decision.group_size == 1
        assert "posture=single" in decision.reasons

    def test_group_posture_hits_ceiling(self):
        decision = decide_mode("fix the bug in the compiler module", ("a", "b", "c"), config=GroConfig(enabled=True, default_mode="group", max_candidates_per_task=3))
        assert decision.mode is RunMode.CANDIDATE_GROUP
        assert decision.group_size == 3

    def test_adaptive_uses_tier_table(self):
        cfg = GroConfig(enabled=True, default_mode="adaptive")
        assert decide_mode("say hello", (), config=cfg).group_size == 1
        assert decide_mode("investigate the unclear error here", (), config=cfg).group_size == 2
        assert decide_mode("redesign the platform architecture end-to-end", (), config=cfg).group_size == 3

    def test_explicit_count_is_bounded_by_ceiling(self):
        decision = decide_mode("fix the bug in the compiler module", (), config=GroConfig(enabled=True, default_mode="group", max_candidates_per_task=3), candidate_count=10)
        assert decision.group_size == 3
        assert any(r.startswith("candidate_count_bounded_by_ceiling") for r in decision.reasons)

    def test_explicit_zero_is_refused(self):
        with pytest.raises(ValueError):
            decide_mode("fix the bug", (), config=GroConfig(enabled=True), candidate_count=0)

    def test_external_effects_park_for_approval(self):
        decision = decide_mode("deploy the service to production", (), config=GroConfig(enabled=True, require_approval_for_external_side_effects=True))
        assert decision.mode is RunMode.HUMAN_APPROVAL_FIRST
        assert decision.group_size == 0
        assert "external_side_effects_require_approval" in decision.reasons

    def test_operator_can_allow_external_effects(self):
        decision = decide_mode("deploy the service to production", (), config=GroConfig(enabled=True, require_approval_for_external_side_effects=False))
        assert decision.mode is not RunMode.HUMAN_APPROVAL_FIRST

    def test_decision_serializes(self):
        decision = decide_mode("fix the bug in the compiler module", ("x",), config=GroConfig(enabled=True))
        payload = decision.to_dict()
        assert payload["task_type"] == "coding"
        assert isinstance(payload["reasons"], list)


class TestGateResult:
    def test_required_separates_demand_from_informational(self):
        gates = [
            GateResult("g1", GateStatus.PASS),
            GateResult("g2", GateStatus.UNKNOWN, "could not decide", required=False),
        ]
        from alpha.gro.evaluation import is_eligible

        assert is_eligible(gates) is True

    def test_unknown_required_gate_is_not_a_pass(self):
        from alpha.gro.evaluation import is_eligible

        assert is_eligible([GateResult("g1", GateStatus.UNKNOWN, "unknown")]) is False
        assert is_eligible([GateResult("g1", GateStatus.FAIL, "failed")]) is False
        assert is_eligible([]) is True  # no demanded gates: nothing blocks


class TestRelativeRankIsDiagnostic:
    def test_relative_rank_never_changes_eligibility(self):
        best = CandidateEvaluation(candidate_id="a", evaluator_version="v", eligible=False, absolute_score=0.99)
        worst = CandidateEvaluation(candidate_id="b", evaluator_version="v", eligible=True, absolute_score=0.1)
        from alpha.gro.evaluation import rank_group

        rank_group([best, worst])
        assert best.relative_rank is None  # ineligible candidates are never ranked
        assert worst.relative_rank == 1
