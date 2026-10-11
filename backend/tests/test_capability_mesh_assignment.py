"""Tests for roles and routing (plan §3.4): one PRIMARY per microtask, earned second opinions.

Acceptance criteria are required on every assignment; hard refusals cannot
be overridden by score; a challenger is earned (margin + evidence gate); a
verifier must be a different competence; the INTEGRATOR is never
auto-assigned (composition owns it); and every microtask is either assigned
or refused — never silently dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alpha.capability_mesh.assignment import (
    CHALLENGER_MARGIN,
    NO_ELIGIBLE_ENTITY,
    MicrotaskAssignment,
    Role,
    RoutingPlan,
    route,
)
from alpha.capability_mesh.decomposition import Decomposition, Microtask
from alpha.capability_mesh.fingerprint import Pressure, Risk, SituationFingerprint
from alpha.capability_mesh.models import CapabilityEstimate, CapabilityPassport, EntityKind, EvidenceLevel
from alpha.capability_mesh.policy import DecisionPolicy, resolve_policy

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)


def _fp() -> SituationFingerprint:
    return SituationFingerprint(task_type="general", risk_band=Risk.LOW, context_pressure=Pressure.LOW)


def _estimate(capability: str, *, level: EvidenceLevel = EvidenceLevel.UNMEASURED) -> CapabilityEstimate:
    if level is EvidenceLevel.UNMEASURED:
        return CapabilityEstimate(capability=capability, task_category="*", evidence_level=level)
    return CapabilityEstimate(capability=capability, task_category="*", quality_mean=0.9, uncertainty=0.1, sample_count=5, evidence_level=level, last_evaluated_at="2025-06-14T00:00:00+00:00", profile_version="p1")


def _measured_recently(capability: str) -> CapabilityEstimate:
    return CapabilityEstimate(
        capability=capability,
        task_category="*",
        quality_mean=0.9,
        uncertainty=0.1,
        sample_count=5,
        evidence_level=EvidenceLevel.RECENTLY_REVALIDATED,
        last_evaluated_at="2025-06-14T00:00:00+00:00",
        profile_version="p1",
    )


def _passport(entity_id: str, capabilities: tuple[str, ...] = (), *, level: EvidenceLevel = EvidenceLevel.UNMEASURED, enabled: bool = True) -> CapabilityPassport:
    return CapabilityPassport(entity_id=entity_id, entity_type=EntityKind.AGENT, version="v1", capabilities=tuple(_estimate(name, level=level) for name in capabilities), enabled=enabled)


def _microtask(microtask_id: str = "mt-1", *, capabilities: tuple[str, ...] = (), tools: tuple[str, ...] = (), checks: tuple[str, ...] = ("the check passes",), max_attempts: int = 1) -> Microtask:
    return Microtask(microtask_id=microtask_id, title=f"task {microtask_id}", summary="s", acceptance_checks=checks, required_capabilities=capabilities, required_tools=tools, max_attempts=max_attempts)


def _decomposition(*microtasks: Microtask) -> Decomposition:
    return Decomposition(fingerprint=_fp(), microtasks=tuple(microtasks), decomposer="heuristic_baseline", notes=("crude baseline",))


def _assignment(**kwargs: object) -> MicrotaskAssignment:
    base: dict[str, object] = {
        "microtask_id": "mt-1",
        "passport_id": "agent:a",
        "role": Role.PRIMARY,
        "fit_score": 0.5,
        "score_explanation": ("factor 0.5",),
        "expected_outputs": ("the deliverable",),
        "acceptance_checks": ("the check passes",),
    }
    base.update(kwargs)
    return MicrotaskAssignment(**base)  # type: ignore[arg-type]


class TestMicrotaskAssignment:
    def test_acceptance_checks_are_required(self) -> None:
        with pytest.raises(ValueError, match="acceptance_checks must be non-empty"):
            _assignment(acceptance_checks=())

    def test_expected_outputs_are_required(self) -> None:
        with pytest.raises(ValueError, match="expected_outputs must be non-empty"):
            _assignment(expected_outputs=())

    def test_max_attempts_refused_below_one(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            _assignment(max_attempts=0)

    def test_unknown_role_refused_listing_roles(self) -> None:
        with pytest.raises(ValueError, match="unknown role"):
            _assignment(role="boss")

    def test_fit_score_bounds(self) -> None:
        with pytest.raises(ValueError, match="fit_score"):
            _assignment(fit_score=1.5)

    def test_round_trip(self) -> None:
        assignment = _assignment(max_attempts=3)
        assert MicrotaskAssignment.from_dict(assignment.to_dict()) == assignment


class TestRoutingPlan:
    def test_two_primaries_for_one_microtask_refused(self) -> None:
        with pytest.raises(ValueError, match="2 PRIMARY"):
            RoutingPlan(fingerprint=_fp(), assignments=(_assignment(passport_id="agent:a"), _assignment(passport_id="agent:b")))

    def test_secondary_role_without_a_primary_refused(self) -> None:
        with pytest.raises(ValueError, match="no PRIMARY"):
            RoutingPlan(fingerprint=_fp(), assignments=(_assignment(role=Role.CHALLENGER),))

    def test_duplicate_row_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate assignment"):
            RoutingPlan(fingerprint=_fp(), assignments=(_assignment(), _assignment()))

    def test_assigned_and_refused_together_refused(self) -> None:
        from alpha.capability_mesh.fit import HardConstraintRefusal

        with pytest.raises(ValueError, match="BOTH assigned and refused"):
            RoutingPlan(fingerprint=_fp(), assignments=(_assignment(),), refusals=(HardConstraintRefusal(entity_id="agent:x", unmet=("disabled",), microtask_id="mt-1"),))

    def test_a_plan_that_routes_nothing_refused(self) -> None:
        with pytest.raises(ValueError, match="silent no-op"):
            RoutingPlan(fingerprint=_fp(), assignments=(), refusals=())

    def test_round_trip(self) -> None:
        plan = RoutingPlan(fingerprint=_fp(), assignments=(_assignment(),), refusals=(), notes=("a note",))
        assert RoutingPlan.from_dict(plan.to_dict()) == plan


class TestRoute:
    def test_primary_is_the_top_fit_and_copies_the_contract(self) -> None:
        measured = _passport("agent:m", ("code",), level=EvidenceLevel.OBSERVED)
        unmeasured = _passport("agent:u", ("code",))
        microtask = _microtask(capabilities=("code",), checks=("check one", "check two"), max_attempts=3)
        plan = route(_fp(), _decomposition(microtask), [unmeasured, measured], policy=DecisionPolicy.FAST, profile_version="p1", now=NOW)
        assert len(plan.assignments) == 1
        primary = plan.assignments[0]
        assert primary.role is Role.PRIMARY
        assert primary.passport_id == "agent:m"
        assert primary.acceptance_checks == ("check one", "check two")  # copied from the microtask
        assert primary.max_attempts == 3
        assert primary.score_explanation  # the fit factors travel with the assignment

    def test_equal_fit_gets_a_challenger_within_the_margin(self) -> None:
        # 3 required capabilities: the runner-up trails by exactly 0.125
        # (one unmeasured prior at 0.25 * COVERAGE_WEIGHT 0.5), inside the
        # CHALLENGER_MARGIN of 0.15.
        strong = _passport("agent:a", ("code", "review", "explain"), level=EvidenceLevel.OBSERVED)
        runner_up = CapabilityPassport(
            entity_id="agent:z",
            entity_type=EntityKind.AGENT,
            version="v1",
            capabilities=(_estimate("code", level=EvidenceLevel.OBSERVED), _estimate("review", level=EvidenceLevel.OBSERVED), _estimate("explain")),  # last one unmeasured
        )
        plan = route(_fp(), _decomposition(_microtask(capabilities=("code", "review", "explain"))), [strong, runner_up], policy=DecisionPolicy.BALANCED, profile_version="p1", now=NOW)
        roles = {(assignment.passport_id, assignment.role) for assignment in plan.assignments}
        assert ("agent:a", Role.PRIMARY) in roles
        assert ("agent:z", Role.CHALLENGER) in roles

    def test_no_challenger_beyond_the_margin(self) -> None:
        strong = _passport("agent:a", ("code", "review"), level=EvidenceLevel.OBSERVED)
        weak = _passport("agent:z", ("code", "review"))  # both priors: trails by 0.375 > 0.15
        plan = route(_fp(), _decomposition(_microtask(capabilities=("code", "review"))), [strong, weak], policy=DecisionPolicy.BALANCED, profile_version="p1", now=NOW)
        assert not any(assignment.role is Role.CHALLENGER for assignment in plan.assignments)
        assert any("beyond the challenger margin" in note for note in plan.notes)
        assert CHALLENGER_MARGIN == 0.15  # pinned

    def test_recently_revalidated_primary_gets_no_challenger(self) -> None:
        # Both candidates fit identically (margin trivially satisfied); the
        # evidence gate alone must withhold the challenger.
        leader = CapabilityPassport(entity_id="agent:a", entity_type=EntityKind.AGENT, version="v1", capabilities=(_measured_recently("code"), _measured_recently("review")))
        peer = _passport("agent:z", ("code", "review"), level=EvidenceLevel.OBSERVED)
        plan = route(_fp(), _decomposition(_microtask(capabilities=("code", "review"))), [leader, peer], policy=DecisionPolicy.BALANCED, profile_version="p1", now=NOW)
        assert {assignment.role for assignment in plan.assignments if assignment.passport_id == "agent:a"} == {Role.PRIMARY}
        assert not any(assignment.role is Role.CHALLENGER for assignment in plan.assignments)
        assert any("recently_revalidated" in note for note in plan.notes)

    def test_policy_without_challenger_appetite_adds_none(self) -> None:
        first = _passport("agent:a", ("code",))
        second = _passport("agent:z", ("code",))
        plan = route(_fp(), _decomposition(_microtask(capabilities=("code",))), [first, second], policy=DecisionPolicy.FAST, profile_version="p1", now=NOW)
        assert {assignment.role for assignment in plan.assignments} == {Role.PRIMARY}

    def test_verifier_is_the_best_different_competence(self) -> None:
        primary = _passport("agent:a", ("code",), level=EvidenceLevel.OBSERVED)
        specialist = _passport("agent:z", ("review",))  # knows a competence the primary's weakest ('code') lacks
        plan = route(_fp(), _decomposition(_microtask(capabilities=("code",))), [primary, specialist], policy=DecisionPolicy.CODE_SAFE, profile_version="p1", now=NOW)
        verifiers = [assignment for assignment in plan.assignments if assignment.role is Role.VERIFIER]
        assert [verifier.passport_id for verifier in verifiers] == ["agent:z"]
        assert any("verification evidence" in output for output in verifiers[0].expected_outputs)

    def test_no_verifier_when_nobody_differs(self) -> None:
        primary = _passport("agent:a", ("code",), level=EvidenceLevel.OBSERVED)
        same = _passport("agent:z", ("code",))  # knows ONLY the primary's weakest capability
        plan = route(_fp(), _decomposition(_microtask(capabilities=("code",))), [primary, same], policy=DecisionPolicy.CODE_SAFE, profile_version="p1", now=NOW)
        assert not any(assignment.role is Role.VERIFIER for assignment in plan.assignments)
        assert any("distinct" in note for note in plan.notes)

    def test_no_verifier_without_a_required_capability(self) -> None:
        only = _passport("agent:a", ("code",), level=EvidenceLevel.OBSERVED)
        plan = route(_fp(), _decomposition(_microtask()), [only], policy=DecisionPolicy.CODE_SAFE, profile_version="p1", now=NOW)
        assert not any(assignment.role is Role.VERIFIER for assignment in plan.assignments)
        assert any("no required capability for a verifier" in note for note in plan.notes)

    def test_integrator_is_never_auto_assigned_and_says_so(self) -> None:
        passports = [_passport("agent:a", ("code",), level=EvidenceLevel.OBSERVED), _passport("agent:z", ("review",))]
        plan = route(_fp(), _decomposition(_microtask(capabilities=("code",))), passports, policy=DecisionPolicy.HIGHEST_CONFIDENCE, profile_version="p1", now=NOW)
        assert all(assignment.role is not Role.INTEGRATOR for assignment in plan.assignments)
        assert any("integrator" in note for note in plan.notes)

    def test_uncovered_microtask_is_refused_and_named_never_dropped(self) -> None:
        disabled = [_passport("agent:a", enabled=False), _passport("agent:z", enabled=False)]
        microtask = _microtask(capabilities=("code",))
        plan = route(_fp(), _decomposition(microtask), disabled, policy=DecisionPolicy.BALANCED, profile_version="p1", now=NOW)
        assert plan.assignments == ()
        assert [refusal.entity_id for refusal in plan.refusals] == ["agent:a", "agent:z", NO_ELIGIBLE_ENTITY]
        assert all(refusal.microtask_id == "mt-1" for refusal in plan.refusals)
        assert any("left unassigned" in note for note in plan.notes)
        # The coverage invariant, checked from the outside too.
        covered = {assignment.microtask_id for assignment in plan.assignments} | {refusal.microtask_id for refusal in plan.refusals if refusal.microtask_id}
        assert covered == {"mt-1"}

    def test_empty_passport_pool_refuses_by_name(self) -> None:
        plan = route(_fp(), _decomposition(_microtask()), [], policy=DecisionPolicy.BALANCED, profile_version="p1", now=NOW)
        assert len(plan.refusals) == 1
        assert plan.refusals[0].entity_id == NO_ELIGIBLE_ENTITY
        assert plan.assignments == ()

    def test_every_microtask_gets_exactly_one_primary(self) -> None:
        first = _microtask("mt-1", capabilities=("code",))
        second = _microtask("mt-2", capabilities=("review",))
        passports = [_passport("agent:a", ("code", "review"), level=EvidenceLevel.OBSERVED)]
        plan = route(_fp(), _decomposition(first, second), passports, policy=DecisionPolicy.BALANCED, profile_version="p1", now=NOW)
        for microtask_id in ("mt-1", "mt-2"):
            primaries = [assignment for assignment in plan.assignments_for(microtask_id) if assignment.role is Role.PRIMARY]
            assert len(primaries) == 1

    def test_route_accepts_a_resolved_profile_object(self) -> None:
        passports = [_passport("agent:a", ("code",), level=EvidenceLevel.OBSERVED)]
        decomposition = _decomposition(_microtask(capabilities=("code",)))
        by_enum = route(_fp(), decomposition, passports, policy=DecisionPolicy.FAST, profile_version="p1", now=NOW)
        by_profile = route(_fp(), decomposition, passports, policy=resolve_policy("fast"), profile_version="p1", now=NOW)
        assert by_enum.to_dict() == by_profile.to_dict()

    def test_route_requires_a_profile_version(self) -> None:
        with pytest.raises(ValueError, match="profile_version"):
            route(_fp(), _decomposition(_microtask()), [], policy=DecisionPolicy.FAST, profile_version="")
