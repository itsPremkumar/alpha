"""Tests for complementarity-aware greedy team selection (plan §4.3/§4.4/§4.5).

The five-step greedy selector: highest-fit PRIMARY, then best marginal
coverage adjusted for measured pair value (SPECULATIVE pairs at HALF value,
flagging the member), correlated-failure penalty and budget pressure (an
unpriced candidate is never assumed free), then an independent VERIFIER the
policy earns. Every member's reason carries its marginal number; the
independence note names shared providers/families or discloses a single
specialist; role variety alone is never diversity; ``max_team_size`` beyond
``MAX_TEAM_SIZE`` is refused by name, never clamped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alpha.capability_mesh.assignment import Role
from alpha.capability_mesh.complementarity import PAIR_MIN_SAMPLES, ComplementarityEvidence, PairEstimate, PairMemory
from alpha.capability_mesh.decomposition import Microtask
from alpha.capability_mesh.fingerprint import Pressure, Risk, SituationFingerprint
from alpha.capability_mesh.graph import CapabilityGraph, EdgeKind, GraphEdge, GraphNode, NodeKind
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, OutcomeKind
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel
from alpha.capability_mesh.policy import DecisionPolicy, Depth, PolicyProfile
from alpha.capability_mesh.team import MAX_TEAM_SIZE, RISK_PENALTY, UNPRICED_PENALTY, DiversityAxis, TeamMember, TeamPlan, select_team

PV = "p1"
NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

_ids = iter(range(1, 1_000_000))


def _estimate(capability: str, quality: float, *, samples: int = 10) -> CapabilityEstimate:
    return CapabilityEstimate(capability=capability, task_category="*", quality_mean=quality, sample_count=samples, evidence_level=EvidenceLevel.BENCHMARKED if samples >= 10 else EvidenceLevel.OBSERVED, profile_version=PV)


def _passport(entity_id: str, *, provider: str = "openrouter", family: str = "llama", capabilities: tuple[CapabilityEstimate, ...] = (), tools: tuple[str, ...] = (), enabled: bool = True) -> CapabilityPassport:
    return CapabilityPassport(entity_id=entity_id, entity_type=EntityKind.AGENT, version="v1", model_provider=provider, model_family=family, capabilities=capabilities, required_tools=tools, enabled=enabled)


def _profile(*, agent_count: int = 4, wants_verifier: bool = True) -> PolicyProfile:
    return PolicyProfile(policy=DecisionPolicy.BALANCED, agent_count=agent_count, wants_challenger=False, wants_verifier=wants_verifier, verification_depth=Depth.SAMPLED, recency_required=False, rationale="test profile")


def _memory_pricing(tmp_path: Path, costs: dict[str, float]) -> CapabilityMemory:
    memory = CapabilityMemory(tmp_path)
    for entity_id, cost in costs.items():
        for index in range(2):
            memory.record(
                CapabilityRecord(
                    record_id=f"r{next(_ids)}",
                    task_fingerprint="fp_0011223344556677",
                    task_category="*",
                    entity_id=entity_id,
                    capability="cost",
                    outcome=OutcomeKind.SELECTED,
                    cost_usd=cost,
                    profile_version=PV,
                    recorded_at=f"2025-06-0{index + 1}T00:00:00+00:00",
                )
            )
    return memory


def _ordering_passports() -> list[CapabilityPassport]:
    return [
        _passport("agent:alpha", provider="openrouter", family="llama", capabilities=(_estimate("code", 0.9), _estimate("docs", 0.2, samples=5))),
        _passport("agent:beta", provider="groq", family="llama", capabilities=(_estimate("docs", 0.9),)),
        _passport("agent:gamma", provider="local", family="phi", capabilities=(_estimate("code", 0.5, samples=5),)),
    ]


# ------------------------------------------------------------------ refusals


def test_select_team_requires_a_profile_version():
    with pytest.raises(ValueError, match="profile_version"):
        select_team(("docs",), _ordering_passports(), policy=_profile(), profile_version="")


def test_select_team_refuses_max_team_size_above_the_hard_ceiling():
    with pytest.raises(ValueError, match="MAX_TEAM_SIZE"):
        select_team(("docs",), _ordering_passports(), policy=_profile(), profile_version=PV, max_team_size=MAX_TEAM_SIZE + 1)


def test_select_team_refuses_zero_max_team_size():
    with pytest.raises(ValueError, match="max_team_size"):
        select_team(("docs",), _ordering_passports(), policy=_profile(), profile_version=PV, max_team_size=0)


def test_team_member_refuses_a_reason_without_its_marginal_number():
    with pytest.raises(ValueError, match="reason must be non-empty"):
        TeamMember(passport_id="agent:a", role=Role.PRIMARY, fit_score=0.9, marginal_value=0.5, added_cost_usd=None, diversity_axes=(), reason="", speculative=False)


def test_team_plan_refuses_a_missing_independence_note():
    with pytest.raises(ValueError, match="independence_note"):
        TeamPlan(members=(), uncovered_capabilities=(), independence_note="", total_predicted_cost=None, stops="no_candidates")


def test_team_plan_refuses_a_total_when_a_member_is_unpriced():
    member = TeamMember(passport_id="agent:a", role=Role.PRIMARY, fit_score=0.9, marginal_value=0.5, added_cost_usd=None, diversity_axes=(), reason="marginal 0.500", speculative=False)
    with pytest.raises(ValueError, match="unpriced"):
        TeamPlan(members=(member,), uncovered_capabilities=(), independence_note="single specialist; no independent verification", total_predicted_cost=1.0, stops="no_candidates")


def test_team_plan_refuses_a_total_that_contradicts_member_costs():
    member = TeamMember(passport_id="agent:a", role=Role.PRIMARY, fit_score=0.9, marginal_value=0.5, added_cost_usd=1.0, diversity_axes=(), reason="marginal 0.500", speculative=False)
    with pytest.raises(ValueError, match="total_predicted_cost"):
        TeamPlan(members=(member,), uncovered_capabilities=(), independence_note="single specialist; no independent verification", total_predicted_cost=2.0, stops="no_candidates")


# ------------------------------------------------------------ selection flow


def test_greedy_selection_orders_primary_gap_filler_verifier():
    plan = select_team(("code", "docs"), _ordering_passports(), policy=_profile(), profile_version=PV, now=NOW)
    assert [member.passport_id for member in plan.members] == ["agent:alpha", "agent:beta", "agent:gamma"]
    assert [member.role for member in plan.members] == [Role.PRIMARY, Role.CHALLENGER, Role.VERIFIER]
    assert plan.stops == "marginal_below_threshold"  # gamma adds no coverage; it arrives as the verifier instead
    assert plan.uncovered_capabilities == ()
    assert plan.total_predicted_cost is None  # nothing was priced; never a reassuring 0.0
    # Every reason carries its marginal value number (plan §4.5).
    assert "0.350" in plan.members[1].reason
    assert "0.000" in plan.members[2].reason


def test_diversity_axes_track_what_each_member_adds_not_roles():
    plan = select_team(("code", "docs"), _ordering_passports(), policy=_profile(), profile_version=PV, now=NOW)
    primary = plan.members[0]
    assert DiversityAxis.PROVIDER in primary.diversity_axes and DiversityAxis.MODEL_FAMILY in primary.diversity_axes
    beta = plan.members[1]
    assert DiversityAxis.PROVIDER in beta.diversity_axes  # groq is new
    assert DiversityAxis.MODEL_FAMILY not in beta.diversity_axes  # llama already on the team
    assert DiversityAxis.ENTITY_KIND not in beta.diversity_axes  # another agent: kind adds nothing
    gamma = plan.members[2]
    assert DiversityAxis.PROVIDER in gamma.diversity_axes and DiversityAxis.MODEL_FAMILY in gamma.diversity_axes
    # Role variety is never an axis: nothing here is diversity just because a role changed.
    assert DiversityAxis.VERIFICATION_METHOD not in beta.diversity_axes


def test_independence_note_spans_when_providers_differ():
    plan = select_team(("code", "docs"), _ordering_passports(), policy=_profile(), profile_version=PV, now=NOW)
    assert "independence reduced" not in plan.independence_note
    assert "openrouter" in plan.independence_note and "groq" in plan.independence_note


def test_independence_note_names_shared_provider_and_family():
    passports = [
        _passport("agent:alpha", provider="openrouter", family="llama", capabilities=(_estimate("docs", 0.5, samples=5), _estimate("code", 0.5, samples=5))),
        _passport("agent:beta", provider="openrouter", family="llama", capabilities=(_estimate("docs", 0.9),)),
    ]
    plan = select_team(("docs", "code"), passports, policy=_profile(wants_verifier=False), profile_version=PV, now=NOW)
    assert len(plan.members) == 2
    assert "independence reduced" in plan.independence_note
    assert "openrouter" in plan.independence_note and "llama" in plan.independence_note


def test_single_member_note_is_exact():
    plan = select_team(("docs",), _ordering_passports()[:1], policy=_profile(agent_count=1), profile_version=PV, now=NOW)
    assert len(plan.members) == 1
    assert plan.independence_note == "single specialist; no independent verification"
    assert plan.stops == "max_size"  # a one-agent budget left no room for anyone else


def test_no_verifier_slot_is_left_empty_not_faked():
    passports = [
        _passport("agent:alpha", provider="openrouter", family="llama", capabilities=(_estimate("code", 0.9), _estimate("docs", 0.2, samples=5))),
        _passport("agent:beta", provider="openrouter", family="llama", capabilities=(_estimate("docs", 0.9),)),
        _passport("agent:gamma", provider="openrouter", family="llama", capabilities=(_estimate("code", 0.5, samples=5),)),
    ]
    plan = select_team(("code", "docs"), passports, policy=_profile(), profile_version=PV, now=NOW)
    assert all(member.role is not Role.VERIFIER for member in plan.members)
    assert "no independent verifier" in plan.independence_note


def test_disabled_and_tool_missing_passports_never_enter_the_team():
    passports = [
        _passport("agent:alpha", capabilities=(_estimate("docs", 0.9),), enabled=False),
        _passport("agent:beta", capabilities=(_estimate("docs", 0.5, samples=5),), tools=("shell",)),
        _passport("agent:gamma", capabilities=(_estimate("docs", 0.7, samples=5),)),
    ]
    microtask = Microtask(microtask_id="mt-1", title="t", summary="s", acceptance_checks=("the check passes",), required_capabilities=("docs",), required_tools=("shell",))
    plan = select_team(microtask, passports, policy=_profile(wants_verifier=False), profile_version=PV, now=NOW)
    assert [member.passport_id for member in plan.members] == ["agent:beta"]


def test_no_eligible_candidates_reports_honestly():
    plan = select_team(("docs",), [], policy=_profile(), profile_version=PV, now=NOW)
    assert plan.members == ()
    assert plan.stops == "no_candidates"
    assert plan.uncovered_capabilities == ("docs",)


def test_accepts_a_fingerprint_and_a_microtask():
    passports = _ordering_passports()
    fingerprint = SituationFingerprint(task_type="general", risk_band=Risk.LOW, context_pressure=Pressure.LOW, required_capabilities=("docs",))
    from_fingerprint = select_team(fingerprint, passports, policy=_profile(wants_verifier=False), profile_version=PV, now=NOW)
    microtask = Microtask(microtask_id="mt-1", title="t", summary="s", acceptance_checks=("the check passes",), required_capabilities=("docs",))
    from_microtask = select_team(microtask, passports, policy=_profile(wants_verifier=False), profile_version=PV, now=NOW)
    assert [member.passport_id for member in from_fingerprint.members] == [member.passport_id for member in from_microtask.members]


# ------------------------------------------------------------- stop reasons


def _budget_passports() -> list[CapabilityPassport]:
    # alpha covers everything weakly (the broad primary); beta is the docs
    # specialist the budget has to decide about.
    return [
        _passport("agent:alpha", capabilities=(_estimate("docs", 0.5, samples=5), _estimate("code", 0.9))),
        _passport("agent:beta", capabilities=(_estimate("docs", 0.9),)),
    ]


def test_budget_stop_when_the_best_candidate_is_unaffordable(tmp_path: Path):
    memory = _memory_pricing(tmp_path, {"agent:alpha": 2.0, "agent:beta": 10.0})
    plan = select_team(("docs", "code"), _budget_passports(), policy=_profile(wants_verifier=False), profile_version=PV, memory=memory, budget_usd=5.0, now=NOW)
    assert [member.passport_id for member in plan.members] == ["agent:alpha"]  # the primary is always added
    assert plan.stops == "budget"


def test_affordable_candidate_is_added_within_budget(tmp_path: Path):
    memory = _memory_pricing(tmp_path, {"agent:alpha": 2.0, "agent:beta": 10.0})
    plan = select_team(("docs", "code"), _budget_passports(), policy=_profile(wants_verifier=False), profile_version=PV, memory=memory, budget_usd=100.0, now=NOW)
    assert [member.passport_id for member in plan.members] == ["agent:alpha", "agent:beta"]
    assert plan.total_predicted_cost == pytest.approx(12.0)


def test_marginal_threshold_stop():
    passports = [
        _passport("agent:alpha", capabilities=(_estimate("docs", 0.5, samples=5), _estimate("code", 0.5, samples=5))),
        _passport("agent:beta", capabilities=(_estimate("docs", 0.9),)),
        _passport("agent:gamma", capabilities=(_estimate("code", 0.9),)),
        _passport("agent:delta", capabilities=(_estimate("code", 0.51),)),
    ]
    plan = select_team(("docs", "code"), passports, policy=_profile(wants_verifier=False), profile_version=PV, marginal_threshold=0.05, now=NOW)
    # alpha (primary) + beta (+0.4/2) + gamma (+0.4/2); delta's +0.01/2 is below the threshold.
    assert [member.passport_id for member in plan.members] == ["agent:alpha", "agent:beta", "agent:gamma"]
    assert plan.stops == "marginal_below_threshold"


def test_max_size_stop_at_the_policy_ceiling():
    passports = _ordering_passports()
    plan = select_team(("code", "docs"), passports, policy=_profile(agent_count=2, wants_verifier=False), profile_version=PV, now=NOW)
    assert len(plan.members) == 2
    assert plan.stops == "max_size"


# ------------------------------------------------------- adjustments and honesty


def test_unpriced_candidate_cannot_assume_it_is_free(tmp_path: Path):
    memory = _memory_pricing(tmp_path, {"agent:gamma": 1.0})
    passports = [
        _passport("agent:alpha", capabilities=(_estimate("docs", 0.5, samples=5), _estimate("code", 0.5, samples=5))),
        _passport("agent:beta", capabilities=(_estimate("docs", 0.9),)),
        _passport("agent:gamma", capabilities=(_estimate("docs", 0.9),)),
    ]
    without_budget = select_team(("docs", "code"), passports, policy=_profile(wants_verifier=False), profile_version=PV, now=NOW)
    assert [member.passport_id for member in without_budget.members] == ["agent:alpha", "agent:beta"]  # identical fit; id tiebreak
    with_budget = select_team(("docs", "code"), passports, policy=_profile(wants_verifier=False), profile_version=PV, memory=memory, budget_usd=100.0, now=NOW)
    # beta's unpriced status cost it UNPRICED_PENALTY against gamma's 1.0/100 cost pressure.
    assert [member.passport_id for member in with_budget.members] == ["agent:alpha", "agent:gamma"]
    assert UNPRICED_PENALTY > 1.0 / 100.0


def test_speculative_pair_contributes_half_value_and_flags_the_member(tmp_path: Path):
    pair_memory = PairMemory(tmp_path)
    pair_memory.record(PairEstimate(entity_a="agent:alpha", entity_b="agent:beta", task_category="*", solo_a=0.5, solo_b=0.5, together=0.8, sample_count=0, evidence=ComplementarityEvidence.SPECULATIVE, profile_version=PV))
    passports = [
        _passport("agent:alpha", capabilities=(_estimate("docs", 0.5, samples=5),)),
        _passport("agent:beta", capabilities=(_estimate("docs", 0.5, samples=5),)),
    ]
    plan = select_team(("docs",), passports, policy=_profile(wants_verifier=False), profile_version=PV, pair_memory=pair_memory, now=NOW)
    beta = plan.members[1]
    # Measured synergy is 0.3; a speculative pair may contribute only HALF of it.
    assert beta.speculative is True
    assert "0.150" in beta.reason


def test_observed_pair_contributes_full_value_without_the_speculative_flag(tmp_path: Path):
    pair_memory = PairMemory(tmp_path)
    pair_memory.record(PairEstimate(entity_a="agent:alpha", entity_b="agent:beta", task_category="*", solo_a=0.5, solo_b=0.5, together=0.8, sample_count=PAIR_MIN_SAMPLES, evidence=ComplementarityEvidence.OBSERVED, profile_version=PV))
    passports = [
        _passport("agent:alpha", capabilities=(_estimate("docs", 0.5, samples=5),)),
        _passport("agent:beta", capabilities=(_estimate("docs", 0.5, samples=5),)),
    ]
    plan = select_team(("docs",), passports, policy=_profile(wants_verifier=False), profile_version=PV, pair_memory=pair_memory, now=NOW)
    beta = plan.members[1]
    assert beta.speculative is False
    assert "0.300" in beta.reason


def test_correlated_failure_risk_penalizes_a_redundant_pair():
    passports = [
        _passport("agent:alpha", capabilities=(_estimate("docs", 0.5, samples=5), _estimate("code", 0.5, samples=5))),
        _passport("agent:delta", capabilities=(_estimate("docs", 0.9),)),
        _passport("agent:epsilon", capabilities=(_estimate("docs", 0.9),)),
    ]
    graph = CapabilityGraph()
    for entity in ("agent:alpha", "agent:delta", "agent:epsilon"):
        graph.add_node(GraphNode(node_id=f"entity:{entity}", kind=NodeKind.ENTITY, label=entity))
    for entity in ("agent:alpha", "agent:delta"):  # delta fails exactly like alpha; epsilon does not
        graph.add_node(GraphNode(node_id="failure_mode:timeout", kind=NodeKind.FAILURE_MODE, label="timeout"))
        graph.add_edge(GraphEdge(source=f"entity:{entity}", target="failure_mode:timeout", kind=EdgeKind.OFTEN_MISSES, evidence_count=3, profile_version=PV))
    plan = select_team(("docs", "code"), passports, policy=_profile(wants_verifier=False), profile_version=PV, graph=graph, now=NOW)
    assert [member.passport_id for member in plan.members] == ["agent:alpha", "agent:epsilon"]
    assert RISK_PENALTY == 0.5


def test_measured_cost_travels_per_member(tmp_path: Path):
    memory = _memory_pricing(tmp_path, {"agent:alpha": 2.0, "agent:beta": 3.0})
    passports = [
        _passport("agent:alpha", capabilities=(_estimate("docs", 0.5, samples=5), _estimate("code", 0.9))),
        _passport("agent:beta", capabilities=(_estimate("docs", 0.9),)),
    ]
    plan = select_team(("docs", "code"), passports, policy=_profile(wants_verifier=False), profile_version=PV, memory=memory, now=NOW)
    assert [member.passport_id for member in plan.members] == ["agent:alpha", "agent:beta"]
    assert [member.added_cost_usd for member in plan.members] == [pytest.approx(2.0), pytest.approx(3.0)]
    assert plan.total_predicted_cost == pytest.approx(5.0)


def test_team_plan_round_trips():
    plan = select_team(("code", "docs"), _ordering_passports(), policy=_profile(), profile_version=PV, now=NOW)
    assert TeamPlan.from_dict(plan.to_dict()).to_dict() == plan.to_dict()
