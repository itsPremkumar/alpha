"""Tests for Phases A–G of the AGI/ASI plan.

Class-per-phase, mirroring the plan document:

* :class:`TestPhaseAPathway` — did the change arrive by the claimed route?
* :class:`TestPhaseBEvaluatorStability` — measure the verifier's own noise
* :class:`TestPhaseCDiversity` — did the action space survive?
* :class:`TestPhaseDBudgetProtocol` — are cross-generation claims falsifiable?
* :class:`TestPhaseEEvidenceLedger` — six subsystems, one verdict
* :class:`TestPhaseFLoopHealth` — is the loop working?
* :class:`TestPhaseGInvestigation` — what is worth investigating?
* :class:`TestGateIntegration` — A and C reach the promotion gate

The three load-bearing negative cases are pinned explicitly:

* an unexplained score gain is refused (``TestPhaseAPathway`` +
  ``TestGateIntegration``);
* a behaviour collapse overrides an improvement (``TestPhaseCDiversity`` +
  ``TestGateIntegration``);
* an unmatched-budget comparison **raises** rather than reporting a delta
  (``TestPhaseDBudgetProtocol``).
"""

from __future__ import annotations

import pytest

from alpha.intelligence.budget_protocol import (
    BudgetMismatchError,
    BudgetUnit,
    MatchedBudgetRun,
    compare_matched,
    is_comparable,
)
from alpha.intelligence.diversity import (
    BehaviorTrace,
    collapse_detected,
    compare_diversity,
    normalize_action,
)
from alpha.intelligence.evaluator_stability import (
    measure_noise_floor,
    resolve_noise_floor,
    stability_report,
)
from alpha.intelligence.evidence_ledger import (
    ConvergenceStatus,
    EvidenceLedger,
    Subsystem,
    SubsystemRecord,
    SubsystemVerdict,
)
from alpha.intelligence.investigation import (
    GapKind,
    InvestigationProposal,
    proposal_is_admissible,
    select_investigations,
)
from alpha.intelligence.loop_health import (
    MIN_OBSERVATIONS_FOR_REGIME,
    Regime,
    SaturationDetector,
    assess_loop_health,
)
from alpha.intelligence.pathway import (
    Mechanism,
    PathwayHypothesis,
    PathwayVerdict,
    assert_pathway,
    probe_registry,
)
from alpha.intelligence.regression import (
    CaseResult,
    Decision,
    EvaluationRun,
    evaluate_gate,
)

# ---------------------------------------------------------------------------
# Phase A
# ---------------------------------------------------------------------------


class TestPhaseAPathway:
    def test_registry_covers_every_testable_mechanism(self) -> None:
        registry = probe_registry()
        for mechanism in Mechanism:
            if mechanism is Mechanism.UNKNOWN:
                assert mechanism not in registry
                continue
            assert mechanism in registry, f"{mechanism.value} has no probe"

    def test_untestable_hypothesis_is_not_engaged(self) -> None:
        report = assert_pathway(PathwayHypothesis(), None, None)
        assert report.verdict is PathwayVerdict.NOT_ENGAGED
        assert "no testable mechanism" in report.reason

    def test_routing_change_must_change_decisions(self) -> None:
        hypothesis = PathwayHypothesis(mechanism=Mechanism.ROUTING)
        before = {"decisions": ["expert_a", "expert_b", "expert_c"]}
        after = {"decisions": ["expert_b", "expert_a", "expert_c"]}
        report = assert_pathway(hypothesis, before, after)
        assert report.verdict is PathwayVerdict.ENGAGED
        assert report.evidence.detail["changed"] == 2

    def test_untouched_routing_is_not_engaged(self) -> None:
        """The headline case: a routing edit that changed no decision did nothing."""
        report = assert_pathway(
            PathwayHypothesis(mechanism=Mechanism.ROUTING),
            {"decisions": ["a", "b"]},
            {"decisions": ["a", "b"]},
        )
        assert report.verdict is PathwayVerdict.NOT_ENGAGED

    def test_anomalous_improvement_is_detectable(self) -> None:
        report = assert_pathway(
            PathwayHypothesis(mechanism=Mechanism.ROUTING),
            {"decisions": ["a", "b"]},
            {"decisions": ["a", "b"]},
            score_improved=True,
        )
        assert report.verdict is PathwayVerdict.NOT_ENGAGED
        assert report.is_anomalous_improvement is True

    def test_unmeasurable_state_is_inconclusive_not_engaged(self) -> None:
        """A probe that cannot run must never return True."""
        report = assert_pathway(
            PathwayHypothesis(mechanism=Mechanism.ROUTING),
            {"no_decisions_key": 1},
            {"no_decisions_key": 2},
        )
        assert report.verdict is PathwayVerdict.INCONCLUSIVE
        assert report.evidence.engaged is None

    def test_missing_probe_is_inconclusive(self) -> None:
        report = assert_pathway(
            PathwayHypothesis(mechanism=Mechanism.ROUTING),
            {"decisions": []},
            {"decisions": []},
            probes={},
        )
        assert report.verdict is PathwayVerdict.INCONCLUSIVE
        assert "no probe is registered" in report.reason

    def test_raising_probe_is_inconclusive_not_a_crash(self) -> None:
        class Boom:
            mechanism = Mechanism.PROMPT

            def assert_engaged(self, before, after):
                raise RuntimeError("probe is broken")

        report = assert_pathway(
            PathwayHypothesis(mechanism=Mechanism.PROMPT),
            "a",
            "b",
            probes={Mechanism.PROMPT: Boom()},
        )
        assert report.verdict is PathwayVerdict.INCONCLUSIVE
        assert "RuntimeError" in report.reason

    def test_prompt_probe_compares_delivered_text(self) -> None:
        hypothesis = PathwayHypothesis(mechanism=Mechanism.PROMPT)
        assert assert_pathway(hypothesis, "hello", "hello world").verdict is PathwayVerdict.ENGAGED
        assert assert_pathway(hypothesis, "same", "same").verdict is PathwayVerdict.NOT_ENGAGED

    def test_tool_set_probe(self) -> None:
        hypothesis = PathwayHypothesis(mechanism=Mechanism.TOOL_SET)
        before = {"tools": ["bash", "read"]}
        assert assert_pathway(hypothesis, before, {"tools": ["bash", "read"]}).verdict is PathwayVerdict.NOT_ENGAGED
        after = {"tools": ["bash", "read", "write"]}
        report = assert_pathway(hypothesis, before, after)
        assert report.verdict is PathwayVerdict.ENGAGED
        assert report.evidence.detail["added"] == ["write"]

    def test_budget_probe(self) -> None:
        hypothesis = PathwayHypothesis(mechanism=Mechanism.BUDGET)
        base = {"max_depth": 5, "max_attempts": 3}
        assert assert_pathway(hypothesis, base, dict(base)).verdict is PathwayVerdict.NOT_ENGAGED
        changed = {"max_depth": 7, "max_attempts": 3}
        assert assert_pathway(hypothesis, base, changed).verdict is PathwayVerdict.ENGAGED

    def test_memory_policy_probe(self) -> None:
        hypothesis = PathwayHypothesis(mechanism=Mechanism.MEMORY_POLICY)
        before = {"retained": ["a", "b"]}
        after = {"retained": ["a", "c"]}
        report = assert_pathway(hypothesis, before, after)
        assert report.verdict is PathwayVerdict.ENGAGED
        assert report.evidence.detail == {"added": 1, "removed": 1}

    def test_retrieval_probe_detects_reordering(self) -> None:
        hypothesis = PathwayHypothesis(mechanism=Mechanism.RETRIEVAL)
        report = assert_pathway(hypothesis, {"retrieved": ["a", "b"]}, {"retrieved": ["b", "a"]})
        assert report.verdict is PathwayVerdict.ENGAGED

    def test_expert_mix_probe(self) -> None:
        hypothesis = PathwayHypothesis(mechanism=Mechanism.EXPERT_MIX)
        before = {"selected": ["e1", "e2"]}
        after = {"selected": ["e1", "e3"]}
        report = assert_pathway(hypothesis, before, after)
        assert report.verdict is PathwayVerdict.ENGAGED

    def test_hypothesis_serialisation(self) -> None:
        hypothesis = PathwayHypothesis(mechanism=Mechanism.ROUTING, detail="why")
        assert hypothesis.to_dict()["is_testable"] is True
        assert PathwayHypothesis().to_dict()["is_testable"] is False

    def test_report_serialisation(self) -> None:
        report = assert_pathway(PathwayHypothesis(mechanism=Mechanism.PROMPT), "a", "b")
        payload = report.to_dict()
        assert payload["verdict"] in {v.value for v in PathwayVerdict}
        assert payload["evidence"]["mechanism"] == "prompt"


# ---------------------------------------------------------------------------
# Phase B
# ---------------------------------------------------------------------------


class TestPhaseBEvaluatorStability:
    def test_wobbly_evaluator_gets_a_measured_floor(self) -> None:
        scores = iter([0.50, 0.60, 0.55])
        floor = measure_noise_floor(lambda: next(scores), repeats=3)
        assert floor.observed is True
        assert floor.floor > 0.0
        assert floor.range is not None and floor.range > 0

    def test_too_few_repeats_claims_nothing(self) -> None:
        """One sample has no spread, so no floor is claimed."""
        floor = measure_noise_floor(lambda: 0.5, repeats=1)
        assert floor.observed is False
        assert floor.samples == 1
        assert "below the" in floor.reason

    def test_identical_repeats_claim_nothing(self) -> None:
        """A deterministic-looking evaluator is not evidence of zero noise."""
        floor = measure_noise_floor(lambda: 0.5, repeats=4)
        assert floor.observed is False
        assert "byte-identical" in floor.reason

    def test_raising_probe_is_disclosed_not_swallowed(self) -> None:
        def boom():
            raise RuntimeError("evaluator crashed")

        floor = measure_noise_floor(boom, repeats=3)
        assert floor.observed is False
        assert "RuntimeError" in floor.reason
        assert floor.source == "unmeasured"

    def test_non_numeric_result_is_disclosed(self) -> None:
        floor = measure_noise_floor(lambda: object(), repeats=3)
        assert floor.observed is False
        assert "no comparable numeric score" in floor.reason

    def test_probe_returning_a_run_is_accepted(self) -> None:
        run = EvaluationRun.from_results([CaseResult("c", "git", True, 1.0)])
        floor = measure_noise_floor(lambda: run, repeats=2)
        assert floor.values[0] == 1.0

    def test_score_extraction_shapes(self) -> None:
        assert measure_noise_floor(lambda: 0.5, repeats=2).values == (0.5, 0.5)
        assert measure_noise_floor(lambda: True, repeats=2).values == (1.0, 1.0)
        assert measure_noise_floor(lambda: {"score": 0.25}, repeats=2).values == (0.25, 0.25)

    def test_invalid_repeats_refused(self) -> None:
        with pytest.raises(ValueError, match="repeats"):
            measure_noise_floor(lambda: 0.5, repeats=0)
        with pytest.raises(ValueError, match="min_repeats"):
            measure_noise_floor(lambda: 0.5, repeats=3, min_repeats=1)

    def test_max_of_both_can_only_tighten(self) -> None:
        """The asymmetry that stops measurement from becoming a way to lower the bar."""
        scores = iter([0.5, 0.7, 0.6])
        measured = measure_noise_floor(lambda: next(scores), repeats=3, declared=0.9)
        assert resolve_noise_floor(measured, 0.9, source="max_of_both") >= 0.9

    def test_declared_source_ignores_measurement(self) -> None:
        scores = iter([0.5, 0.7, 0.6])
        measured = measure_noise_floor(lambda: next(scores), repeats=3, declared=0.01)
        assert resolve_noise_floor(measured, 0.01, source="declared") == 0.01

    def test_measured_source_used_when_declared_is_absent(self) -> None:
        scores = iter([0.5, 0.7, 0.6])
        measured = measure_noise_floor(lambda: next(scores), repeats=3)
        assert resolve_noise_floor(measured, None, source="measured") == measured.floor

    def test_unmeasured_falls_back_to_declared(self) -> None:
        floor = measure_noise_floor(lambda: 0.5, repeats=1, declared=0.05)
        assert resolve_noise_floor(floor, 0.05, source="max_of_both") == 0.05

    def test_unknown_source_refused(self) -> None:
        floor = measure_noise_floor(lambda: 0.5, repeats=2)
        with pytest.raises(ValueError, match="noise floor source"):
            resolve_noise_floor(floor, 0.0, source="whatever")

    def test_report_floor_for_is_none_when_unobserved(self) -> None:
        floor = measure_noise_floor(lambda: 0.5, repeats=1, metric="task_success_rate")
        report = stability_report({floor.metric: floor})
        assert report.floor_for("task_success_rate") is None
        assert report.floor_for("never_measured") is None
        assert report.unobserved_metrics() == ["task_success_rate"]

    def test_report_serialisation(self) -> None:
        scores = iter([0.5, 0.7, 0.6])
        floor = measure_noise_floor(lambda: next(scores), repeats=3)
        payload = stability_report({floor.metric: floor}).to_dict()
        assert payload["all_observed"] is True
        assert "score" in payload["floors"]


# ---------------------------------------------------------------------------
# Phase C
# ---------------------------------------------------------------------------


class TestPhaseCDiversity:
    def test_volatile_arguments_collapse_to_one_behaviour(self) -> None:
        """A retry with a different request id is the SAME behaviour."""
        first = normalize_action("bash", {"cmd": "ls", "request_id": "abc123def456"})
        second = normalize_action("bash", {"cmd": "ls", "request_id": "zzz999yyy888"})
        assert first == second

    def test_different_command_values_are_different_behaviours(self) -> None:
        """Keeping only the arg KEYS would make these one behaviour, which is
        exactly backwards for an action-space measure."""
        assert normalize_action("bash", {"cmd": "ls"}) != normalize_action("bash", {"cmd": "rm -rf"})
        assert normalize_action("write", {"content": "a"}) != normalize_action("write", {"content": "b"})

    def test_path_is_an_instance_not_a_behaviour(self) -> None:
        """Reading a.py and reading b.py are the SAME behaviour; the file is the
        argument, not the act."""
        assert normalize_action("read", {"path": "a.py"}) == normalize_action("read", {"path": "b.py"})

    def test_numeric_values_collapse(self) -> None:
        assert normalize_action("x", {"limit": 10}) == normalize_action("x", {"limit": 999})

    def test_paths_collapse(self) -> None:
        """A file path is an instance, not a behaviour."""
        assert normalize_action("read", {"path": "/tmp/a.txt"}) == normalize_action("read", {"path": "/tmp/b.txt"})

    def test_different_tools_are_different_behaviours(self) -> None:
        assert normalize_action("bash", {}) != normalize_action("read", {})

    def test_different_arg_keys_are_different_behaviours(self) -> None:
        assert normalize_action("bash", {"cmd": "ls"}) != normalize_action("bash", {"script": "ls"})

    def test_positional_args_normalise(self) -> None:
        assert normalize_action("x", ["a", "b"]).arg_shape == "a,b"

    def test_coverage_drops_when_behaviours_disappear(self) -> None:
        before = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(10)])
        after = BehaviorTrace.from_calls([("bash", {"cmd": "c0"})])
        report = compare_diversity(before, after, absolute_floor=0.5, min_drop=0.1)
        assert report.coverage_before == 1.0
        assert report.coverage_after == pytest.approx(0.1)
        assert report.collapsed is True
        assert len(report.lost) == 9

    def test_coverage_is_measured_against_the_baseline_not_itself(self) -> None:
        """A trace's distinct-action count divided by its own distinct set is
        identically 1.0, so the comparison would report perfect coverage forever."""
        before = BehaviorTrace.from_calls([("bash", {"cmd": "a"}), ("read", {"path": "b"})])
        after = BehaviorTrace.from_calls([("bash", {"cmd": "a"})])
        report = compare_diversity(before, after, absolute_floor=0.0, min_drop=0.9)
        assert report.action_space_before == 2
        assert report.action_space_after == 1
        assert report.coverage_after == pytest.approx(0.5)

    def test_gain_does_not_lower_below_baseline(self) -> None:
        """Adding behaviours cannot reduce baseline coverage."""
        before = BehaviorTrace.from_calls([("bash", {"cmd": "a"})])
        after = BehaviorTrace.from_calls([("bash", {"cmd": "a"}), ("read", {"path": "x"})])
        report = compare_diversity(before, after)
        assert report.coverage_after == 1.0
        assert report.coverage_delta == 0.0

    def test_empty_baseline_is_inconclusive_not_reassuring(self) -> None:
        """Coverage is measured against the baseline, so no baseline means no verdict."""
        report = compare_diversity(
            BehaviorTrace.from_calls([]),
            BehaviorTrace.from_calls([("bash", {})]),
        )
        assert report.coverage_before is None
        assert report.coverage_after is None
        assert report.collapsed is False
        assert any("cannot be asserted or ruled out" in r for r in report.reasons)

    def test_no_collapse_when_coverage_holds(self) -> None:
        """Replacing a behaviour is a loss, but not a collapse while the
        baseline's coverage is largely retained."""
        before = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(5)])
        after = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(4)])
        report = compare_diversity(before, after, absolute_floor=0.5, min_drop=0.5)
        assert report.coverage_after == pytest.approx(0.8)
        assert report.collapsed is False

    def test_completely_replacing_behaviours_is_a_collapse(self) -> None:
        """Under this definition, doing nothing the old agent did IS a collapse —
        which is the correct reading of an action-space measure."""
        before = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(5)])
        after = BehaviorTrace.from_calls([("write", {"cmd": f"d{i}"}) for i in range(5)])
        report = compare_diversity(before, after)
        assert report.coverage_after == 0.0
        assert report.collapsed is True
        assert len(report.gained) == 5
        assert not report.retained

    def test_both_conditions_are_required(self) -> None:
        """A big drop that stays above the floor is not a collapse."""
        collapsed, reasons = collapse_detected(coverage_before=1.0, coverage_after=0.6, absolute_floor=0.5, min_drop=0.1)
        assert collapsed is False
        assert any("the absolute floor was not breached" in r for r in reasons)

        collapsed, reasons = collapse_detected(coverage_before=0.9, coverage_after=0.4, absolute_floor=0.5, min_drop=0.9)
        assert collapsed is False
        assert any("the drop was not material" in r for r in reasons)

        collapsed, reasons = collapse_detected(coverage_before=1.0, coverage_after=0.4, absolute_floor=0.5, min_drop=0.1)
        assert collapsed is True
        assert any("both clauses hold" in r for r in reasons)

    def test_unmeasured_baseline_does_not_wave_a_change_through(self) -> None:
        collapsed, reasons = collapse_detected(coverage_before=None, coverage_after=0.9, absolute_floor=0.5, min_drop=0.1)
        assert collapsed is False
        assert any("cannot be asserted or ruled out" in r for r in reasons)

    def test_noise_floor_makes_collapse_harder_to_declare(self) -> None:
        """A noisy coverage measurement should not license a claim on a small drop.

        Raising ``min_drop`` means a *bigger* observed drop is required, so noise
        makes collapse **less** likely, which is the conservative direction. The
        opposite (noise making collapse easier to assert) would let an unreliable
        measurement manufacture an emergency.
        """
        before = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(10)])
        after = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(6)])
        report = compare_diversity(before, after, absolute_floor=0.7, min_drop=0.05, noise_floor=0.0)
        strict = compare_diversity(before, after, absolute_floor=0.7, min_drop=0.05, noise_floor=0.9)
        assert report.collapsed is True
        assert strict.collapsed is False
        assert any("raised to" in reason for reason in strict.reasons)

    def test_noise_floor_is_never_lowered_by_measurement(self) -> None:
        """A tiny measured floor cannot undercut the configured minimum."""
        before = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(10)])
        after = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(6)])
        strict = compare_diversity(before, after, absolute_floor=0.7, min_drop=0.05, noise_floor=0.001)
        assert strict.collapsed is True, "the measured floor is added to min_drop, never subtracted"

    def test_both_conditions_still_required_with_noise(self) -> None:
        """Below-floor alone is not a collapse; a big drop alone is not either."""
        collapsed, reasons = collapse_detected(coverage_before=1.0, coverage_after=0.4, absolute_floor=0.5, min_drop=0.9)
        assert collapsed is False
        assert any("below the absolute floor" in r for r in reasons)
        collapsed, _ = collapse_detected(coverage_before=1.0, coverage_after=0.9, absolute_floor=0.5, min_drop=0.05)
        assert collapsed is False
        collapsed, _ = collapse_detected(coverage_before=1.0, coverage_after=0.4, absolute_floor=0.5, min_drop=0.05)
        assert collapsed is True

    def test_entropy_is_none_for_degenerate_traces(self) -> None:
        report = compare_diversity(
            BehaviorTrace.from_calls([("bash", {})]),
            BehaviorTrace.from_calls([("bash", {})]),
        )
        assert report.policy_entropy_before is None
        assert report.policy_entropy_after is None

    def test_entropy_computed_for_real_distribution(self) -> None:
        report = compare_diversity(
            BehaviorTrace.from_calls([("bash", {"cmd": "a"}), ("read", {"path": "b"})]),
            BehaviorTrace.from_calls([("bash", {"cmd": "a"}), ("read", {"path": "b"})]),
        )
        assert report.policy_entropy_before == pytest.approx(0.6931, abs=0.001)

    def test_gained_behaviours_recorded(self) -> None:
        before = BehaviorTrace.from_calls([("bash", {"cmd": "a"})])
        after = BehaviorTrace.from_calls([("bash", {"cmd": "a"}), ("read", {"path": "x"})])
        report = compare_diversity(before, after)
        assert report.gained
        assert not report.collapsed

    def test_serialisation(self) -> None:
        before = BehaviorTrace.from_calls([("bash", {"cmd": "a"}), ("read", {"path": "b"})])
        payload = compare_diversity(before, before).to_dict()
        for key in ("action_space_before", "coverage_delta", "collapsed", "reasons"):
            assert key in payload


# ---------------------------------------------------------------------------
# Phase D
# ---------------------------------------------------------------------------


class TestPhaseDBudgetProtocol:
    def test_identical_budgets_compare(self) -> None:
        left = MatchedBudgetRun("gen3", 3, 0.60, BudgetUnit(attempts=5, tools=10))
        right = MatchedBudgetRun("gen7", 7, 0.72, BudgetUnit(attempts=5, tools=10))
        result = compare_matched(left, right)
        assert result.better == "gen7"
        assert result.delta == pytest.approx(0.12)
        assert result.per_attempt_gain == pytest.approx(0.024)

    def test_mismatched_budget_raises_rather_than_reporting(self) -> None:
        """The mechanism that stops an unfalsifiable cross-generation claim."""
        left = MatchedBudgetRun("gen3", 3, 0.60, BudgetUnit(attempts=5, tools=10))
        right = MatchedBudgetRun("gen7", 7, 0.72, BudgetUnit(attempts=50, tools=10))
        with pytest.raises(BudgetMismatchError, match="attempts differ"):
            compare_matched(left, right)

    def test_tolerance_is_deliberate(self) -> None:
        left = MatchedBudgetRun("a", 1, 0.5, BudgetUnit(attempts=10, tools=0))
        right = MatchedBudgetRun("b", 2, 0.6, BudgetUnit(attempts=11, tools=0))
        assert is_comparable(left.budget, right.budget)[0] is False
        assert is_comparable(left.budget, right.budget, tolerance=0.2)[0] is True

    def test_unverified_run_cannot_be_compared(self) -> None:
        left = MatchedBudgetRun("a", 1, 0.5, BudgetUnit(attempts=2, tools=1))
        right = MatchedBudgetRun("b", 2, 0.6, BudgetUnit(attempts=2, tools=1), evidence_kind="unverified")
        with pytest.raises(BudgetMismatchError, match="unmeasured score"):
            compare_matched(left, right)

    def test_zero_attempt_run_refuses_efficiency_claim(self) -> None:
        left = MatchedBudgetRun("a", 1, 0.5, BudgetUnit(attempts=0, tools=0))
        right = MatchedBudgetRun("b", 2, 0.6, BudgetUnit(attempts=0, tools=0))
        with pytest.raises(BudgetMismatchError, match="per-attempt gain"):
            compare_matched(left, right)

    def test_token_instrumentation_mismatch_refused(self) -> None:
        left = MatchedBudgetRun("a", 1, 0.5, BudgetUnit(attempts=2, tools=1, tokens=100))
        right = MatchedBudgetRun("b", 2, 0.6, BudgetUnit(attempts=2, tools=1))
        with pytest.raises(BudgetMismatchError, match="instrumented on one side only"):
            compare_matched(left, right)

    def test_tie_reported_as_tie(self) -> None:
        left = MatchedBudgetRun("a", 1, 0.5, BudgetUnit(attempts=2, tools=1))
        right = MatchedBudgetRun("b", 2, 0.5, BudgetUnit(attempts=2, tools=1))
        assert compare_matched(left, right).better == "tie"

    def test_worse_candidate_named(self) -> None:
        left = MatchedBudgetRun("good", 1, 0.8, BudgetUnit(attempts=2, tools=1))
        right = MatchedBudgetRun("bad", 2, 0.3, BudgetUnit(attempts=2, tools=1))
        assert compare_matched(left, right).better == "good"

    def test_negative_budget_refused(self) -> None:
        with pytest.raises(ValueError, match="attempts"):
            BudgetUnit(attempts=-1, tools=0)

    def test_tokens_none_not_zero(self) -> None:
        assert BudgetUnit(attempts=1, tools=1).tokens is None
        assert BudgetUnit(attempts=1, tools=1, tokens=0).tokens == 0

    def test_serialisation(self) -> None:
        run = MatchedBudgetRun("g", 1, 0.5, BudgetUnit(attempts=2, tools=1))
        assert run.to_dict()["budget"]["total"] == 3


# ---------------------------------------------------------------------------
# Phase E
# ---------------------------------------------------------------------------


class TestPhaseEEvidenceLedger:
    def _ledger(self, *subsystems: Subsystem) -> EvidenceLedger:
        return EvidenceLedger(enabled_subsystems=subsystems)

    def test_all_pass_approves(self) -> None:
        ledger = self._ledger(Subsystem.AVO, Subsystem.INTELLIGENCE)
        for subsystem in (Subsystem.AVO, Subsystem.INTELLIGENCE):
            ledger.record("c1", SubsystemRecord(subsystem, SubsystemVerdict.PASS, "ok"))
        verdict = ledger.converge("c1")
        assert verdict.status is ConvergenceStatus.APPROVED
        assert verdict.is_approvable is True

    def test_absence_is_not_approval(self) -> None:
        """A subsystem that never looked has not passed."""
        ledger = self._ledger(Subsystem.AVO, Subsystem.INTELLIGENCE)
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.PASS, "ok"))
        verdict = ledger.converge("c1")
        assert verdict.status is ConvergenceStatus.PARTIAL
        assert verdict.unreconciled == ("intelligence",)
        assert verdict.is_approvable is False

    def test_a_single_rejection_is_final(self) -> None:
        """No 5-1 voting: a broken invariant is not outvoted."""
        ledger = self._ledger(*list(Subsystem))
        for subsystem in Subsystem:
            ledger.record("c1", SubsystemRecord(subsystem, SubsystemVerdict.PASS, "ok"))
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.REJECT, "invariant oracle failed"))
        verdict = ledger.converge("c1")
        assert verdict.status is ConvergenceStatus.REJECTED
        assert verdict.rejecting == ("avo",)
        assert "not outvoted" in verdict.reason

    def test_inconclusive_is_not_approval(self) -> None:
        ledger = self._ledger(Subsystem.AVO)
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.INCONCLUSIVE, "probe broke"))
        verdict = ledger.converge("c1")
        assert verdict.status is ConvergenceStatus.INCONCLUSIVE
        assert verdict.inconclusive == ("avo",)

    def test_rejection_beats_inconclusive(self) -> None:
        ledger = self._ledger(Subsystem.AVO, Subsystem.INTELLIGENCE)
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.REJECT, "no"))
        ledger.record("c1", SubsystemRecord(Subsystem.INTELLIGENCE, SubsystemVerdict.INCONCLUSIVE, "hmm"))
        assert ledger.converge("c1").status is ConvergenceStatus.REJECTED

    def test_disabled_subsystem_does_not_create_partial(self) -> None:
        ledger = self._ledger(Subsystem.AVO)
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.PASS, "ok"))
        verdict = ledger.converge("c1")
        assert verdict.status is ConvergenceStatus.APPROVED
        assert verdict.unreconciled == ()

    def test_later_verdict_replaces_earlier(self) -> None:
        """A subsystem that changed its mind must not leave both opinions on file."""
        ledger = self._ledger(Subsystem.AVO)
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.INCONCLUSIVE, "first look"))
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.PASS, "second look"))
        records = ledger.records_for("c1")
        assert len(records) == 1
        assert records[0].reason == "second look"
        assert ledger.converge("c1").status is ConvergenceStatus.APPROVED

    def test_empty_candidate_is_partial_not_approved(self) -> None:
        assert self._ledger(Subsystem.AVO).converge("nobody-looked").status is ConvergenceStatus.PARTIAL

    def test_empty_candidate_id_refused(self) -> None:
        with pytest.raises(ValueError, match="candidate_id"):
            self._ledger().record("  ", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.PASS))

    def test_serialisation_and_stats(self) -> None:
        ledger = self._ledger(Subsystem.AVO)
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.PASS, "ok"))
        payload = ledger.converge("c1").to_dict()
        assert payload["status"] == "APPROVED"
        assert payload["records"][0]["subsystem"] == "avo"
        assert ledger.stats()["records"] == 1
        assert ledger.clear("c1") == 1
        assert ledger.clear() == 0

    def test_configure_changes_quorum(self) -> None:
        ledger = self._ledger(Subsystem.AVO)
        ledger.record("c1", SubsystemRecord(Subsystem.AVO, SubsystemVerdict.PASS, "ok"))
        assert ledger.converge("c1").status is ConvergenceStatus.APPROVED
        ledger.configure([Subsystem.AVO, Subsystem.RSI_PROMOTION])
        assert ledger.converge("c1").status is ConvergenceStatus.PARTIAL


# ---------------------------------------------------------------------------
# Phase F
# ---------------------------------------------------------------------------


class TestPhaseFLoopHealth:
    def test_detector_tracks_consecutive_non_improving(self) -> None:
        detector = SaturationDetector(saturating_at=3)
        for _ in range(3):
            detector.observe(improved=False)
        assert detector.is_saturated is True
        detector.observe(improved=True)
        assert detector.consecutive_non_improving == 0
        assert detector.is_saturated is False

    def test_insufficient_data_is_a_regime_not_a_guess(self) -> None:
        detector = SaturationDetector()
        detector.observe(improved=True)
        detector.observe(improved=True)
        report = assess_loop_health(detector=detector)
        assert report.regime is Regime.INSUFFICIENT_DATA
        assert "fabricated reassurance" in " ".join(report.reasons)

    def test_minimum_observations_constant_is_respected(self) -> None:
        detector = SaturationDetector()
        for _ in range(MIN_OBSERVATIONS_FOR_REGIME):
            detector.observe(improved=True, gain=0.04)
        assert assess_loop_health(detector=detector).regime is Regime.IMPROVING

    def test_improving_requires_a_measured_gain(self) -> None:
        """Improvements with no measured magnitude are STABLE, not IMPROVING.

        ``improved=True`` says *that* something improved; without a gain figure
        there is no magnitude, and calling that "improving" would overstate what
        was measured.
        """
        detector = SaturationDetector(saturating_at=5)
        for _ in range(3):
            detector.observe(improved=True)
        assert detector.gain_trend is None
        assert assess_loop_health(detector=detector).regime is Regime.STABLE

    def test_stable_regime(self) -> None:
        detector = SaturationDetector(saturating_at=5)
        for _ in range(3):
            detector.observe(improved=False)
        report = assess_loop_health(detector=detector)
        assert report.regime is Regime.STABLE

    def test_saturation_named_distinctly_from_stable(self) -> None:
        detector = SaturationDetector(saturating_at=3)
        for _ in range(4):
            detector.observe(improved=False)
        report = assess_loop_health(detector=detector)
        assert report.regime is Regime.SATURATING
        assert "stop spending attempts" in report.recommended_action

    def test_unmeasured_noise_is_the_top_bottleneck(self) -> None:
        from alpha.intelligence.evaluator_stability import NoiseFloor

        detector = SaturationDetector(saturating_at=9)
        for _ in range(3):
            detector.observe(improved=True, gain=0.05)
        noise = NoiseFloor(metric="score", floor=0.0, samples=0, observed=False, reason="never measured")
        report = assess_loop_health(detector=detector, noise=noise)
        assert report.regime is Regime.INSUFFICIENT_DATA
        assert "noise is unmeasured" in report.bottleneck

    def test_noise_dominating_gain_is_saturation(self) -> None:
        from alpha.intelligence.evaluator_stability import NoiseFloor

        detector = SaturationDetector(saturating_at=99)
        for _ in range(3):
            detector.observe(improved=True, gain=0.001)
        noise = NoiseFloor(metric="score", floor=0.05, samples=3, observed=True, reason="measured")
        report = assess_loop_health(detector=detector, noise=noise)
        assert report.regime is Regime.SATURATING
        assert "evaluator cannot resolve" in report.bottleneck

    def test_pathway_misalignment_is_saturation(self) -> None:
        detector = SaturationDetector(saturating_at=99)
        for _ in range(3):
            detector.observe(improved=True, gain=0.05)
        unaligned = [
            assert_pathway(PathwayHypothesis(mechanism=Mechanism.ROUTING), {"decisions": ["a"]}, {"decisions": ["a"]}),
        ]
        report = assess_loop_health(detector=detector, pathway_reports=unaligned)
        assert report.regime is Regime.SATURATING
        assert "not arriving by the stated route" in report.bottleneck

    def test_subsystem_rejection_is_regression(self) -> None:
        from alpha.intelligence.evidence_ledger import ConvergedVerdict, ConvergenceStatus

        detector = SaturationDetector(saturating_at=99)
        for _ in range(3):
            detector.observe(improved=True, gain=0.05)
        convergence = ConvergedVerdict(
            candidate_id="c1",
            status=ConvergenceStatus.REJECTED,
            records=(),
            rejecting=("avo",),
            reason="invariant oracle failed",
        )
        report = assess_loop_health(detector=detector, convergence=convergence)
        assert report.regime is Regime.REGRESSING
        assert "avo" in report.bottleneck

    def test_collapse_is_regression(self) -> None:
        before = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(10)])
        after = BehaviorTrace.from_calls([("bash", {"cmd": "c0"}) for _ in range(10)])
        diversity = compare_diversity(before, after, absolute_floor=0.5, min_drop=0.1)
        detector = SaturationDetector(saturating_at=99)
        for _ in range(3):
            detector.observe(improved=True, gain=0.05)
        report = assess_loop_health(detector=detector, diversity=diversity)
        assert report.regime is Regime.REGRESSING

    def test_bottleneck_selection_is_severity_ordered(self) -> None:
        """A rejection outranks saturation, which outranks plain improvement."""
        from alpha.intelligence.evaluator_stability import NoiseFloor

        detector = SaturationDetector(saturating_at=2)
        for _ in range(4):
            detector.observe(improved=False, gain=0.0)
        noise = NoiseFloor(metric="score", floor=0.5, samples=3, observed=True, reason="measured")
        report = assess_loop_health(detector=detector, noise=noise)
        assert report.regime is Regime.SATURATING
        assert "evaluator cannot resolve" in report.bottleneck

    def test_contributions_name_the_phase_behind_each_field(self) -> None:
        detector = SaturationDetector(saturating_at=9)
        for _ in range(3):
            detector.observe(improved=True, gain=0.05)
        report = assess_loop_health(detector=detector)
        assert "phase_f_saturation" in report.contributions
        assert "phase_a_pathway_alignment" in report.contributions

    def test_serialisation(self) -> None:
        detector = SaturationDetector(saturating_at=3)
        for _ in range(3):
            detector.observe(improved=False)
        payload = assess_loop_health(detector=detector).to_dict()
        assert payload["regime"] == "saturating"
        assert payload["consecutive_non_improving"] == 3


# ---------------------------------------------------------------------------
# Phase G
# ---------------------------------------------------------------------------


def _proposal(question: str = "Does pathway evidence catch unexplained gains?", *, gain: float = 0.8, cost: int = 4, falsifiable: str = "reject the candidate when NOT_ENGAGED") -> InvestigationProposal:
    return InvestigationProposal(
        question=question,
        resolves_gap=GapKind.PATHWAY,
        expected_information_gain=gain,
        cost=BudgetUnit(attempts=cost, tools=0),
        falsifiable_by=falsifiable,
    )


class TestPhaseGInvestigation:
    def test_falsifiable_by_is_required(self) -> None:
        proposal = InvestigationProposal(
            question="Something interesting",
            resolves_gap=GapKind.OTHER,
            expected_information_gain=0.5,
            cost=BudgetUnit(attempts=1, tools=0),
            falsifiable_by="it would be nice",
        )
        ok, reason = proposal_is_admissible(proposal)
        assert ok is False
        assert "enthusiasm" in reason

    def test_hope_phrasing_is_not_a_falsification(self) -> None:
        proposal = _proposal(falsifiable="we should probably see gains")
        assert proposal.falsifiable is False
        assert proposal_is_admissible(proposal)[0] is False

    def test_measurement_phrasing_is_falsifiable(self) -> None:
        assert _proposal(falsifiable="reject when coverage drops below 0.5").falsifiable is True

    def test_information_gain_floor(self) -> None:
        ok, reason = proposal_is_admissible(_proposal(gain=0.05), min_information_gain=0.3)
        assert ok is False
        assert "below the floor" in reason

    def test_unverified_selection_is_refused(self) -> None:
        """Choosing what to investigate is the judgment agents are bad at."""
        from alpha.intelligence.evidence_ledger import ConvergedVerdict, ConvergenceStatus

        convergence = ConvergedVerdict(
            candidate_id="selection",
            status=ConvergenceStatus.PARTIAL,
            records=(),
            unreconciled=("avo",),
            reason="avo never evaluated",
        )
        ok, reason = proposal_is_admissible(_proposal(), convergence=convergence)
        assert ok is False
        assert "unverified self-assessment" in reason

    def test_verified_selection_is_admissible(self) -> None:
        from alpha.intelligence.evidence_ledger import ConvergedVerdict, ConvergenceStatus

        convergence = ConvergedVerdict(
            candidate_id="selection",
            status=ConvergenceStatus.APPROVED,
            records=(),
            reason="all enabled subsystems passed",
        )
        assert proposal_is_admissible(_proposal(), convergence=convergence)[0] is True

    def test_ranked_by_gain_per_budget(self) -> None:
        cheap_high = _proposal("cheap and valuable", gain=0.5, cost=1)
        expensive_low = _proposal("expensive and weak", gain=0.5, cost=20)
        selection = select_investigations([expensive_low, cheap_high], limit=2)
        assert selection.admitted[0].question == "cheap and valuable"

    def test_dry_run_admits_nothing(self) -> None:
        selection = select_investigations([_proposal()], admission=False)
        assert selection.admitted == ()
        assert any("admission is disabled" in reason for reason in selection.reasons)

    def test_limit_is_enforced(self) -> None:
        proposals = [_proposal(f"question {i}", cost=i + 1) for i in range(5)]
        assert len(select_investigations(proposals, limit=2).admitted) == 2

    def test_rejections_carry_their_reason(self) -> None:
        selection = select_investigations([_proposal(falsifiable="we should see gains")])
        assert selection.admitted == ()
        assert selection.rejected[0][1]

    def test_ordering_is_deterministic(self) -> None:
        proposals = [_proposal(f"q{i}", gain=0.5, cost=3) for i in range(5)]
        first = select_investigations(proposals, limit=5).admitted_questions
        second = select_investigations(list(reversed(proposals)), limit=5).admitted_questions
        assert first == second

    def test_empty_question_refused(self) -> None:
        with pytest.raises(ValueError, match="question"):
            InvestigationProposal(
                question="  ",
                resolves_gap=GapKind.OTHER,
                expected_information_gain=0.5,
                cost=BudgetUnit(attempts=1, tools=0),
                falsifiable_by="measure it",
            )

    def test_gain_out_of_range_refused(self) -> None:
        with pytest.raises(ValueError, match="expected_information_gain"):
            _proposal(gain=1.5)

    def test_negative_limit_refused(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            select_investigations([], limit=-1)

    def test_serialisation(self) -> None:
        selection = select_investigations([_proposal()])
        payload = selection.to_dict()
        assert payload["admitted"][0]["falsifiable"] is True


# ---------------------------------------------------------------------------
# Gate integration
# ---------------------------------------------------------------------------


def _run(passing: int, total: int) -> EvaluationRun:
    return EvaluationRun.from_results([CaseResult(f"c{i}", "reasoning", i < passing, 1.0 if i < passing else 0.0) for i in range(total)])


class TestGateIntegration:
    def test_engaged_pathway_does_not_block(self) -> None:
        pathway = assert_pathway(
            PathwayHypothesis(mechanism=Mechanism.ROUTING),
            {"decisions": ["a"]},
            {"decisions": ["b"]},
        )
        outcome = evaluate_gate(_run(4, 4), _run(2, 4), pathway=pathway)
        assert outcome.decision is Decision.PROMOTE
        assert outcome.gates["pathway_engaged"] is True

    def test_anomalous_improvement_is_rejected(self) -> None:
        """The headline case: score up, mechanism provably unchanged."""
        pathway = assert_pathway(
            PathwayHypothesis(mechanism=Mechanism.ROUTING),
            {"decisions": ["a", "b"]},
            {"decisions": ["a", "b"]},
            score_improved=True,
        )
        outcome = evaluate_gate(_run(4, 4), _run(2, 4), pathway=pathway)
        assert outcome.decision is Decision.REJECT
        assert "anomalous improvement" in outcome.reason
        assert outcome.gates["no_anomalous_improvement"] is False

    def test_inconclusive_pathway_is_rejected(self) -> None:
        pathway = assert_pathway(
            PathwayHypothesis(mechanism=Mechanism.ROUTING),
            {"unrelated": 1},
            {"unrelated": 2},
        )
        outcome = evaluate_gate(_run(4, 4), _run(2, 4), pathway=pathway)
        assert outcome.decision is Decision.REJECT
        assert "inconclusive" in outcome.reason

    def test_behaviour_collapse_overrides_an_improvement(self) -> None:
        """The one gate that can reject a candidate that scored better."""
        before = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(10)])
        after = BehaviorTrace.from_calls([("bash", {"cmd": "c0"}) for _ in range(10)])
        diversity = compare_diversity(before, after, absolute_floor=0.5, min_drop=0.1)
        outcome = evaluate_gate(_run(4, 4), _run(2, 4), diversity=diversity)
        assert outcome.decision is Decision.REJECT
        assert "behaviour collapse" in outcome.reason
        assert "overrides the score" in outcome.reason
        assert outcome.gates["no_behavior_collapse"] is False

    def test_noise_floor_is_named_in_the_collapse_reason(self) -> None:
        before = BehaviorTrace.from_calls([("bash", {"cmd": f"c{i}"}) for i in range(10)])
        after = BehaviorTrace.from_calls([("bash", {"cmd": "c0"}) for _ in range(10)])
        diversity = compare_diversity(before, after, absolute_floor=0.5, min_drop=0.1, noise_floor=0.2)
        outcome = evaluate_gate(_run(4, 4), _run(2, 4), diversity=diversity, noise_floor=0.2)
        assert "Noise floor applied: 0.2000" in outcome.reason

    def test_healthy_diversity_does_not_block(self) -> None:
        before = BehaviorTrace.from_calls([("bash", {"cmd": "a"}), ("read", {"path": "b"})])
        after = BehaviorTrace.from_calls([("bash", {"cmd": "a"}), ("read", {"path": "b"})])
        diversity = compare_diversity(before, after)
        assert evaluate_gate(_run(4, 4), _run(2, 4), diversity=diversity).decision is Decision.PROMOTE

    def test_pathway_gate_runs_before_the_improvement_check(self) -> None:
        """Pathway must precede strictly-better, because the anomaly test needs
        to know the score improved in order to be anomalous.

        So a candidate that is both flat AND un-engaged reports the pathway
        blocker — which is the more specific and more actionable of the two.
        """
        outcome = evaluate_gate(_run(4, 4), _run(4, 4), pathway=assert_pathway(PathwayHypothesis(mechanism=Mechanism.ROUTING), {"decisions": ["a"]}, {"decisions": ["a"]}))
        assert outcome.decision is Decision.REJECT
        assert "did not engage" in outcome.reason

    def test_absent_evidence_does_not_block(self) -> None:
        """No pathway supplied is 'not checked', which is not a refusal."""
        assert evaluate_gate(_run(4, 4), _run(2, 4)).decision is Decision.PROMOTE
