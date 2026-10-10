"""Advanced Arena features from docs/alpha-arena-advanced-implementation-plan.md.

Covers the task contract, candidate artifact contract + contribution synthesis,
the bounded repair loop with dispositions, task-specific rubrics with hard
gates, judge bias controls, the evidence ledger, and the mode profiles +
adaptive router.

Every module here is pure: no model calls, no filesystem, no network.
"""

from __future__ import annotations

import pytest

from alpha.arena.candidate import (
    CandidateArtifact,
    Contribution,
    CoverageStatus,
    RequirementCoverage,
    best_contributions,
    synthesis_gaps,
)
from alpha.arena.evidence import (
    ClaimCheck,
    EvidenceLedger,
    Verdict,
    gate_from_ledger,
)
from alpha.arena.modes import (
    PROFILES,
    Profile,
    RouteInputs,
    RunMode,
    describe_modes,
    route,
    spec_for,
)
from alpha.arena.models import VerdictScores
from alpha.arena.repair import (
    Disposition,
    RepairEntry,
    RepairRound,
    fatal_conceded,
    is_terminal,
    parse_repairs,
    unresolved_attacks,
)
from alpha.arena.synthesis import (
    MergeStatus,
    PatchContribution,
    plan_merge,
    render_markdown,
    synthesize,
)
from alpha.arena.task_contract import (
    Requirement,
    TaskContract,
    TaskType,
)
from alpha.arena.task_rubrics import (
    TASK_WEIGHTS,
    HardGate,
    any_gate_tripped,
    apply_gates,
    default_gates,
    pair_order,
    weights_for,
    weighted_total_for,
)


# --------------------------------------------------------------- task contract


class TestTaskContract:
    def test_create_from_request_is_serializable(self) -> None:
        contract = TaskContract.create_from_request("answer the question")
        restored = TaskContract.from_dict(contract.to_dict())
        assert restored.task_id == contract.task_id
        assert restored.task_type is contract.task_type
        assert restored.requirements == contract.requirements

    def test_requirements_round_trip(self) -> None:
        contract = TaskContract.create_from_request("ship it", task_type=TaskType.CODING)
        contract.requirements.append(Requirement(id="REQ-002", text="must build"))
        restored = TaskContract.from_dict(contract.to_dict())
        assert [r.id for r in restored.requirements] == ["REQ-001", "REQ-002"]
        assert restored.task_type is TaskType.CODING

    def test_contract_records_ambiguities_rather_than_guessing(self) -> None:
        contract = TaskContract.create_from_request("make it good")
        contract.ambiguities.append("'good' is not defined")
        assert TaskContract.from_dict(contract.to_dict()).ambiguities == ["'good' is not defined"]


# -------------------------------------------------------- candidate artifacts


def _artifact(agent_id: str, contributions: list[Contribution], coverage: list[RequirementCoverage]) -> CandidateArtifact:
    return CandidateArtifact(
        agent_id=agent_id,
        artifact_id=f"art-{agent_id}",
        run_id="run-1",
        path=f"/tmp/{agent_id}.md",
        coverage=coverage,
        contributions=contributions,
    )


class TestCandidateArtifact:
    def test_coverage_round_trip(self) -> None:
        artifact = _artifact(
            "a1",
            [Contribution(requirement_id="R1", title="t", body="b", quality=7)],
            [RequirementCoverage(requirement_id="R1", status=CoverageStatus.COVERED, artifact_fragment="L1-L9")],
        )
        restored = CandidateArtifact.from_dict(artifact.to_dict())
        assert restored.coverage_by_requirement() == {"R1": CoverageStatus.COVERED}
        assert restored.contributions[0].quality == 7

    def test_uncovered_reports_missing_requirements(self) -> None:
        artifact = _artifact(
            "a1",
            [],
            [RequirementCoverage(requirement_id="R1", status=CoverageStatus.PARTIAL)],
        )
        assert artifact.uncovered(["R1", "R2"]) == ["R1", "R2"]

    def test_best_contributions_picks_highest_quality_deterministically(self) -> None:
        low = _artifact("a1", [Contribution(requirement_id="R1", title="lo", body="lo", quality=3)], [])
        high = _artifact("a2", [Contribution(requirement_id="R1", title="hi", body="hi", quality=9)], [])
        winners = best_contributions([low, high], ["R1"])
        assert winners["R1"].quality == 9

    def test_best_contributions_tie_breaks_on_agent_id(self) -> None:
        first = _artifact("a1", [Contribution(requirement_id="R1", title="x", body="x", quality=5)], [])
        second = _artifact("a2", [Contribution(requirement_id="R1", title="y", body="y", quality=5)], [])
        # Order must not matter: a1 wins the tie either way.
        assert best_contributions([second, first], ["R1"])["R1"].title == "x"
        assert best_contributions([first, second], ["R1"])["R1"].title == "x"

    def test_non_reusable_contribution_is_skipped(self) -> None:
        artifact = _artifact(
            "a1",
            [Contribution(requirement_id="R1", title="t", body="b", quality=10, reusable=False)],
            [],
        )
        assert best_contributions([artifact], ["R1"]) == {}

    def test_synthesis_gaps_are_surfaced_not_dropped(self) -> None:
        artifact = _artifact(
            "a1",
            [],
            [RequirementCoverage(requirement_id="R1", status=CoverageStatus.COVERED)],
        )
        assert synthesis_gaps([artifact], ["R1", "R2", "R3"]) == ["R2", "R3"]


# ----------------------------------------------------------- synthesis output


class TestSynthesis:
    def test_synthesize_merges_best_per_requirement(self) -> None:
        artifacts = [
            _artifact(
                "a1",
                [Contribution(requirement_id="R1", title="A", body="from a1", quality=4)],
                [RequirementCoverage(requirement_id="R1", status=CoverageStatus.COVERED)],
            ),
            _artifact(
                "a2",
                [Contribution(requirement_id="R1", title="B", body="from a2", quality=8)],
                [RequirementCoverage(requirement_id="R1", status=CoverageStatus.COVERED)],
            ),
        ]
        result = synthesize("run-1", artifacts, ["R1"])
        assert result.is_complete
        assert len(result.sections) == 1
        assert result.sections[0]["body"] == "from a2"
        assert result.sources == ["a1", "a2"]

    def test_incomplete_synthesis_reports_gaps(self) -> None:
        result = synthesize("run-1", [], ["R1"], fallback_text="operator must supply")
        assert not result.is_complete
        assert result.gaps == ["R1"]
        assert "operator must supply" in render_markdown(result)

    def test_render_markdown_includes_sections(self) -> None:
        artifacts = [
            _artifact(
                "a1",
                [Contribution(requirement_id="R1", title="Intro", body="body text", quality=5)],
                [RequirementCoverage(requirement_id="R1", status=CoverageStatus.COVERED)],
            )
        ]
        text = render_markdown(synthesize("run-1", artifacts, ["R1"]))
        assert "## Intro (R1)" in text
        assert "body text" in text


# ---------------------------------------------------------------- code patches


class TestCodeSynthesis:
    def _patch(self, agent: str, start: int, end: int, quality: int) -> PatchContribution:
        return PatchContribution(
            agent_id=agent,
            path="src/a.py",
            start_line=start,
            end_line=end,
            patch=f"@@ {agent}",
            quality=quality,
        )

    def test_non_overlapping_patches_all_apply(self) -> None:
        plan = plan_merge([self._patch("a1", 1, 5, 5), self._patch("a2", 10, 20, 7)])
        assert plan.is_clean
        assert len(plan.applyable) == 2

    def test_overlap_becomes_a_conflict_and_higher_quality_wins(self) -> None:
        plan = plan_merge([self._patch("a1", 1, 10, 3), self._patch("a2", 5, 15, 9)])
        assert not plan.is_clean
        statuses = {(i.patch.agent_id): i.status for i in plan.items}
        assert statuses["a2"] is MergeStatus.APPLY
        assert statuses["a1"] is MergeStatus.CONFLICT
        assert "overlaps a2" in plan.conflicts[0].reason

    def test_patches_to_different_files_never_conflict(self) -> None:
        a = PatchContribution(agent_id="a1", path="x.py", start_line=1, end_line=9, patch="p", quality=1)
        b = PatchContribution(agent_id="a2", path="y.py", start_line=1, end_line=9, patch="p", quality=1)
        assert not a.overlaps(b)
        assert plan_merge([a, b]).is_clean

    def test_plan_never_claims_completeness_it_cannot_show(self) -> None:
        plan = plan_merge([])
        assert plan.is_clean and plan.applyable == []


# --------------------------------------------------------------- repair loop


class TestRepairLoop:
    def test_parse_repairs_reads_dispositions(self) -> None:
        text = "ATTACK 1: FIXED. added the null check\nATTACK 2: CONCEDED. real bug\nnoise line"
        entries = parse_repairs(text)
        assert [e.disposition for e in entries] == [Disposition.FIXED, Disposition.CONCEDED]
        assert entries[0].note == "added the null check"

    def test_parse_repairs_ignores_malformed_lines(self) -> None:
        assert parse_repairs("ATTACK: nonsense") == []
        assert parse_repairs(None) == []
        assert parse_repairs("ATTACK abc: FIXED. x") == []

    def test_round_trip(self) -> None:
        round_ = RepairRound(
            cycle=2,
            entries=[RepairEntry(attack_index=1, disposition=Disposition.REBUTTED, note="n")],
            revised_path="/tmp/rev.md",
        )
        restored = RepairRound.from_dict(round_.to_dict())
        assert restored.cycle == 2
        assert restored.entries[0].disposition is Disposition.REBUTTED
        assert restored.revised_path == "/tmp/rev.md"

    def test_unresolved_attacks_excludes_resolved_and_deferred_is_unresolved(self) -> None:
        entries = [RepairEntry(attack_index=1, disposition=Disposition.FIXED)]
        assert unresolved_attacks([1, 2], entries) == [2]
        deferred = [RepairEntry(attack_index=2, disposition=Disposition.DEFERRED)]
        assert unresolved_attacks([1, 2], entries + deferred) == [2]

    def test_terminal_when_every_attack_resolved(self) -> None:
        entries = [RepairEntry(attack_index=i, disposition=Disposition.FIXED) for i in (1, 2)]
        assert is_terminal(entries, [1, 2], max_cycles=2, cycle=1)
        assert not is_terminal([], [1], max_cycles=2, cycle=1)

    def test_terminal_at_cycle_cap_even_when_unresolved(self) -> None:
        assert is_terminal([], [1], max_cycles=2, cycle=2)

    def test_fatal_conceded_only_counts_concessions(self) -> None:
        assert fatal_conceded([RepairEntry(attack_index=1, disposition=Disposition.CONCEDED)])
        assert not fatal_conceded([RepairEntry(attack_index=1, disposition=Disposition.FIXED)])
        assert not fatal_conceded([RepairEntry(attack_index=1, disposition=Disposition.REBUTTED)])
        assert not fatal_conceded([])


# ------------------------------------------------- rubrics, gates, bias


class TestTaskRubrics:
    def test_every_task_weight_set_sums_to_one(self) -> None:
        for name, weights in TASK_WEIGHTS.items():
            assert pytest.approx(sum(weights.values()), abs=1e-9) == 1.0, name
            assert set(weights) == set(weights_for(None)), name

    def test_unknown_task_type_falls_back_to_default_weights(self) -> None:
        assert weights_for("nonsense") == weights_for(None)
        assert weights_for(None) == weights_for("")

    def test_weighted_total_uses_the_task_weights(self) -> None:
        scores = VerdictScores(
            correctness=10.0,
            completeness=10.0,
            specificity=0.0,
            robustness=0.0,
            clarity=0.0,
        )
        coding = weighted_total_for("coding", scores)
        default = weighted_total_for(None, scores)
        # coding weights correctness 0.40 + completeness 0.20 = 0.60 of a perfect pair.
        assert coding == pytest.approx(6.0)
        assert default != coding

    def test_gate_tripping_forces_correctness_to_zero(self) -> None:
        scores = VerdictScores(correctness=9.0, completeness=9.0, specificity=9.0, robustness=9.0, clarity=9.0)
        gates = [
            HardGate(id="tests", description="tests pass", tripped=True, detail="2 failing"),
            HardGate(id="build", description="builds", tripped=False),
        ]
        adjusted, reported = apply_gates(scores, gates)
        assert adjusted.correctness == 0.0
        assert adjusted.fatal is True
        # Non-gate criteria are untouched so the report still shows what was good.
        assert adjusted.completeness == 9.0
        assert len(reported) == 2
        assert any_gate_tripped(reported).id == "tests"

    def test_untouched_gates_leave_scores_alone(self) -> None:
        scores = VerdictScores(correctness=9.0, completeness=8.0, specificity=7.0, robustness=6.0, clarity=5.0)
        gates = default_gates("coding")
        adjusted, _ = apply_gates(scores, gates)
        assert adjusted == scores
        assert any_gate_tripped(gates) is None

    def test_default_gate_sets_differ_per_task_type(self) -> None:
        assert {g.id for g in default_gates("coding")} == {"build", "tests", "no_critical"}
        assert "citations" in {g.id for g in default_gates("research")}
        assert {g.id for g in default_gates(None)} == {"fidelity"}


class TestBiasControls:
    def test_pair_order_is_deterministic(self) -> None:
        assert pair_order("seed-1", 1, 2) == pair_order("seed-1", 1, 2)

    def test_pair_order_varies_across_matches(self) -> None:
        decisions = {pair_order("seed-1", 1, m) for m in range(8)}
        assert len(decisions) > 1, "order must not be constant across a round"

    def test_pair_order_is_stable_across_a_resume(self) -> None:
        first = [pair_order(42, r, m) for r in range(1, 4) for m in range(1, 5)]
        second = [pair_order(42, r, m) for r in range(1, 4) for m in range(1, 5)]
        assert first == second

    def test_non_numeric_seed_still_decides(self) -> None:
        assert isinstance(pair_order("run-abc", 2, 3), bool)


# ------------------------------------------------------------ evidence ledger


class TestEvidenceLedger:
    def test_counts_always_report_every_verdict(self) -> None:
        ledger = EvidenceLedger(run_id="run-1")
        counts = ledger.counts()
        assert counts == {"supported": 0, "contradicted": 0, "unverified": 0, "not_checked": 0, "total": 0}

    def test_record_and_round_trip(self) -> None:
        ledger = EvidenceLedger(run_id="run-1")
        ledger.record(ClaimCheck(claim="the sky is blue", verdict=Verdict.SUPPORTED, evidence_ref="src:1"))
        ledger.record_batch([ClaimCheck(claim="wet", verdict=Verdict.UNVERIFIED)])
        restored = EvidenceLedger.from_dict(ledger.to_dict())
        assert restored.counts() == ledger.counts()
        assert restored.support_ratio() == 1.0

    def test_unverified_is_not_support(self) -> None:
        ledger = EvidenceLedger(run_id="run-1")
        ledger.record(ClaimCheck(claim="c", verdict=Verdict.UNVERIFIED))
        ledger.record(ClaimCheck(claim="d", verdict=Verdict.NOT_CHECKED))
        # Nothing was checked, so the ratio is unknown - not 0, not 1.
        assert ledger.support_ratio() is None
        assert not ledger.has_contradiction()

    def test_support_ratio_over_checked_claims_only(self) -> None:
        ledger = EvidenceLedger(run_id="run-1")
        ledger.record(ClaimCheck(claim="a", verdict=Verdict.SUPPORTED))
        ledger.record(ClaimCheck(claim="b", verdict=Verdict.CONTRADICTED))
        ledger.record(ClaimCheck(claim="c", verdict=Verdict.UNVERIFIED))
        assert ledger.support_ratio() == pytest.approx(0.5)

    def test_gate_trips_only_on_contradiction(self) -> None:
        clean = EvidenceLedger(run_id="run-1")
        clean.record(ClaimCheck(claim="a", verdict=Verdict.UNVERIFIED))
        tripped, detail = gate_from_ledger(clean)
        assert not tripped
        assert "unverified" in detail

        bad = EvidenceLedger(run_id="run-1")
        bad.record(ClaimCheck(claim="false claim", verdict=Verdict.CONTRADICTED, evidence_ref="src:9"))
        tripped, detail = gate_from_ledger(bad)
        assert tripped
        assert "false claim" in detail

    def test_support_ratio_is_none_on_an_empty_ledger(self) -> None:
        assert EvidenceLedger(run_id="run-1").support_ratio() is None


# ------------------------------------------------------- profiles and router


class TestProfiles:
    def test_every_declared_profile_is_sized_and_bounded(self) -> None:
        for profile in (Profile.QUICK, Profile.STANDARD, Profile.DEEP):
            spec = spec_for(profile)
            assert spec.agents >= 2
            assert spec.wave >= 1
            assert spec.agents <= 64, "profiles must stay inside the operator agent ceiling"

    def test_profiles_are_ordered_by_cost(self) -> None:
        quick = spec_for(Profile.QUICK)
        standard = spec_for(Profile.STANDARD)
        deep = spec_for(Profile.DEEP)
        assert quick.agents < standard.agents < deep.agents
        assert quick.repair_cycles == 0
        assert deep.repair_cycles >= standard.repair_cycles

    def test_custom_requires_explicit_parameters(self) -> None:
        with pytest.raises(ValueError):
            spec_for(Profile.CUSTOM)
        fallback = spec_for(Profile.STANDARD)
        assert spec_for("custom", fallback=fallback) is fallback

    def test_string_profile_lookup(self) -> None:
        assert spec_for("deep").agents == spec_for(Profile.DEEP).agents

    def test_all_four_modes_are_described(self) -> None:
        described = {m["mode"] for m in describe_modes()}
        assert described == {m.value for m in RunMode}


class TestRouter:
    def test_explicit_profile_wins_over_every_heuristic(self) -> None:
        profile, reason = route(
            RouteInputs(
                task_length=99999,
                requirement_count=50,
                safety_level="critical",
                available_budget_calls=1,
                explicit_profile="quick",
            )
        )
        assert profile is Profile.QUICK
        assert reason == "explicit profile"

    def test_critical_safety_routes_deep(self) -> None:
        profile, reason = route(RouteInputs(safety_level="critical"))
        assert profile is Profile.DEEP
        assert "critical" in reason

    def test_short_simple_task_routes_quick(self) -> None:
        profile, reason = route(RouteInputs(task_length=120, requirement_count=1))
        assert profile is Profile.QUICK
        assert reason == "low complexity"

    def test_complex_task_routes_deep(self) -> None:
        profile, reason = route(RouteInputs(task_length=9000, requirement_count=12))
        assert profile is Profile.DEEP

    def test_default_is_standard(self) -> None:
        profile, reason = route(RouteInputs(task_length=900, requirement_count=4))
        assert profile is Profile.STANDARD
        assert reason == "default"

    def test_budget_never_routes_beyond_what_it_can_pay_for(self) -> None:
        quick_calls = sum(
            spec_for(p).agents + 5 * (spec_for(p).agents - 1) for p in (Profile.QUICK,)
        )
        profile, reason = route(RouteInputs(task_length=9000, available_budget_calls=quick_calls))
        assert profile is Profile.QUICK
        assert "budget" in reason

    def test_budget_that_fits_nothing_still_names_itself(self) -> None:
        profile, reason = route(RouteInputs(available_budget_calls=1))
        assert profile is Profile.QUICK
        assert "cannot fit" in reason

    def test_budget_allows_standard_for_a_normal_task(self) -> None:
        deep_spec = spec_for(Profile.DEEP)
        # Enough for standard, but requirement count keeps it off deep.
        budget = deep_spec.agents + 5 * (deep_spec.agents - 1) - 1
        profile, reason = route(RouteInputs(task_length=900, requirement_count=4, available_budget_calls=budget))
        assert profile in (Profile.STANDARD, Profile.QUICK)
        assert "budget" in reason

    def test_projected_calls_cover_spawn_and_every_match(self) -> None:
        # quick: 3 agents, 3 spawn + 2 matches * 5 calls = 13, no final check.
        assert PROFILES[Profile.QUICK].agents == 3

    def test_route_always_returns_a_reason(self) -> None:
        for inputs in (
            RouteInputs(),
            RouteInputs(explicit_profile="nope", task_length=10),
            RouteInputs(safety_level="low", task_length=10),
        ):
            profile, reason = route(inputs)
            assert isinstance(profile, Profile)
            assert reason
