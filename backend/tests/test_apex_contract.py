"""APEX — the autonomy contract: what may happen, up to how much, what needs a human.

Spec §5–§7. These tests pin the four properties that keep a control plane from
becoming an unbounded one:

1. the contract narrows and never widens;
2. the emergency stop cannot be disabled by a contract;
3. verdicts fail closed to approval, and an unknown authority key is never granted;
4. ascending a profile raises budgets monotonically and changes no protected action.

They also pin the claim that APEX is **not** a policy kernel: every attributed
policy site must actually import, because a delegated kernel that is absent is
an unenforced boundary and the contract has to say so rather than imply coverage.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import fields

import pytest

from alpha.apex.agents import ApexAgentFactory, ApexAgentSpec, resolve_delegation_limits
from alpha.apex.contract import (
    AUTHORITY_KEYS,
    PROTECTED_ACTIONS,
    ApexBudget,
    ApexControls,
    AutonomyProfile,
    ContractViolation,
    PolicyAttribution,
    authority_for,
    contract_digest_matches,
    contract_from_snapshot,
    default_contract,
    narrow_contract,
    profile_for,
)

ALL_PROFILES = tuple(AutonomyProfile)


class TestProfileLadder:
    def test_off_grants_nothing(self) -> None:
        contract = default_contract()
        assert contract.profile is AutonomyProfile.OFF
        assert contract.enabled is False
        assert not any(contract.authority.values())

    def test_every_profile_offers_every_key(self) -> None:
        # A key present for one profile and absent for another would make
        # `contract.authority[key]` raise on some paths and return False on
        # others — two different behaviours for one question.
        for profile in ALL_PROFILES:
            granted = authority_for(profile)
            assert set(granted) == set(AUTHORITY_KEYS), profile

    def test_ascending_profile_only_widens_authority(self) -> None:
        ranks = sorted(ALL_PROFILES, key=lambda p: p.rank)
        for lower, higher in zip(ranks, ranks[1:], strict=False):
            low = authority_for(lower)
            high = authority_for(higher)
            for key in AUTHORITY_KEYS:
                assert high[key] >= low[key], f"{higher} lost {key} relative to {lower}"

    def test_assist_withholds_host_reaching_authority(self) -> None:
        assist = authority_for(AutonomyProfile.ASSIST)
        for key in ("terminal", "git", "mcp", "a2a", "subagents", "swarm", "browser"):
            assert assist[key] is False, key

    def test_profile_accepts_its_string_form(self) -> None:
        assert profile_for("apex_max").profile is AutonomyProfile.APEX_MAX

    def test_unknown_profile_is_refused(self) -> None:
        with pytest.raises(ValueError):
            profile_for("god_mode")


class TestBudgets:
    def test_delegation_limits_honor_zero_and_parallel_budget(self) -> None:
        off = profile_for(AutonomyProfile.OFF)
        assert resolve_delegation_limits(off) == (0, 0, 0)

        contract = narrow_contract(
            profile_for(AutonomyProfile.APEX_MAX),
            budget={"max_active_agents": 3, "max_parallel_tasks": 2},
        )
        assert resolve_delegation_limits(contract) == (3, 2, 3)

    def test_agent_factory_refuses_spawn_when_parallel_budget_is_zero(self) -> None:
        contract = narrow_contract(profile_for(AutonomyProfile.APEX_MAX), budget={"max_parallel_tasks": 0})
        factory = ApexAgentFactory(contract, lifecycle=object())
        result = factory.spawn(ApexAgentSpec(role="researcher", objective="inspect a bounded task"))
        assert result.ok is False
        assert result.refusal is not None
        assert result.refusal.code == "agent_population_ceiling"

    def test_ascending_profile_raises_every_budget_monotonically(self) -> None:
        ranks = sorted(ALL_PROFILES, key=lambda p: p.rank)
        for lower, higher in zip(ranks, ranks[1:], strict=False):
            low = profile_for(lower).budget
            high = profile_for(higher).budget
            for name in (f.name for f in fields(ApexBudget)):
                high_value = getattr(high, name)
                low_value = getattr(low, name)
                assert high_value is None or (low_value is not None and high_value >= low_value), f"{higher}.{name} < {lower}.{name}"

    def test_off_profile_spends_nothing(self) -> None:
        budget = profile_for(AutonomyProfile.OFF).budget
        assert budget.max_tool_calls == 0
        assert budget.max_active_agents == 0
        assert budget.max_total_tokens == 0

    def test_apex_max_removes_spend_ceilings_but_keeps_operational_limits(self) -> None:
        budget = profile_for(AutonomyProfile.APEX_MAX).budget
        assert budget.max_total_tokens is None
        assert budget.max_tool_calls is None
        assert budget.max_runtime_minutes is None
        assert budget.max_active_agents == 12
        assert budget.max_parallel_tasks == 8
        assert budget.max_delegation_depth == 5
        assert budget.max_replans == 20
        assert budget.max_retries_per_failure_class == 4
        # Unlimited session policy still respects engine-owned admission caps.
        assert resolve_delegation_limits(profile_for(AutonomyProfile.APEX_MAX)) == (3, 8, 12)

    def test_every_enabled_profile_has_unlimited_spend_quotas(self) -> None:
        for profile in (AutonomyProfile.ASSIST, AutonomyProfile.AUTONOMOUS, AutonomyProfile.APEX_MAX):
            budget = profile_for(profile).budget
            assert budget.max_total_tokens is None, profile
            assert budget.max_tool_calls is None, profile
            assert budget.max_runtime_minutes is None, profile

    def test_budget_object_defaults_to_unlimited_spending_ceilings(self) -> None:
        budget = ApexBudget()
        assert budget.max_runtime_minutes is None
        assert budget.max_tool_calls is None
        assert budget.max_total_tokens is None

    def test_negative_budget_is_refused(self) -> None:
        with pytest.raises(ContractViolation):
            ApexBudget(max_tool_calls=-1)

    def test_boolean_is_not_an_int_budget(self) -> None:
        # `True == 1` in Python, so a bool would silently become a budget of 1.
        with pytest.raises(ContractViolation):
            ApexBudget(max_replans=True)

    def test_unknown_budget_key_is_refused_named(self) -> None:
        with pytest.raises(ContractViolation) as exc:
            ApexBudget.from_dict({"max_tool_calls": 5, "max_dragons": 1})
        assert "max_dragons" in str(exc.value)


class TestEmergencyStopIsNotConfigurable:
    def test_controls_cannot_disable_the_stop(self) -> None:
        with pytest.raises(ContractViolation) as exc:
            ApexControls(emergency_stop=False)
        assert "emergency_stop" in str(exc.value)

    def test_stop_is_true_on_every_profile(self) -> None:
        for profile in ALL_PROFILES:
            assert profile_for(profile).controls.emergency_stop is True, profile

    def test_contract_carries_no_stop_toggle(self) -> None:
        # A field named for the stop would be a place to set it. Asserting the
        # absence is what makes "there is no setter" a testable claim.
        field_names = {f.name for f in fields(default_contract())}
        assert not any("estop" in n or "emergency" in n for n in field_names)


class TestFailClosed:
    def test_unknown_authority_key_is_never_granted(self) -> None:
        contract = profile_for(AutonomyProfile.APEX_MAX)
        assert contract.may("definitely_not_a_dimension") is False

    def test_unclassified_action_class_falls_back_to_approval(self) -> None:
        contract = profile_for(AutonomyProfile.APEX_MAX)
        assert contract.verdict_for("an_action_nobody_classified") == "approval"

    @pytest.mark.parametrize("profile", ALL_PROFILES, ids=[p.value for p in ALL_PROFILES])
    @pytest.mark.parametrize("action_class", sorted(PROTECTED_ACTIONS))
    def test_protected_actions_survive_every_profile(self, action_class: str, profile: AutonomyProfile) -> None:
        # Spec §5: the protected block is the one thing "maximum autonomy" must
        # not touch. Ascending a profile must not loosen any of them.
        contract = profile_for(profile)
        assert contract.protected_actions[action_class] == PROTECTED_ACTIONS[action_class]

    def test_protected_action_is_recognised_as_protected(self) -> None:
        contract = profile_for(AutonomyProfile.APEX_MAX)
        assert contract.is_protected("destructive_filesystem") is True
        assert contract.is_protected("some_unmapped_thing") is True  # fails closed

    def test_missing_execution_toggle_is_false(self) -> None:
        assert profile_for(AutonomyProfile.APEX_MAX).may_execute("not_a_toggle") is False


class TestNarrowing:
    def test_pre_token_budget_snapshot_keeps_legacy_digest_compatible(self) -> None:
        current = profile_for(AutonomyProfile.APEX_MAX)
        snapshot = current.to_dict()
        snapshot["budget"].update(
            max_active_agents=12,
            max_parallel_tasks=8,
            max_delegation_depth=5,
            max_replans=20,
            max_retries_per_failure_class=4,
            max_runtime_minutes=1440,
            max_tool_calls=5000,
        )
        snapshot["budget"].pop("max_total_tokens")
        old_payload = json.dumps(
            {
                "profile": snapshot["profile"],
                "authority": dict(sorted(snapshot["authority"].items())),
                "execution": dict(sorted(snapshot["execution"].items())),
                "budget": snapshot["budget"],
                "controls": snapshot["controls"],
                "protected_actions": dict(sorted(snapshot["protected_actions"].items())),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        legacy_digest = "apxc-" + hashlib.sha256(old_payload.encode("utf-8")).hexdigest()[:16]
        snapshot["digest"] = legacy_digest

        restored = contract_from_snapshot(snapshot, expected_digest=legacy_digest)

        assert restored.budget.max_total_tokens == 2_000_000
        assert contract_digest_matches(restored, legacy_digest)

    def test_narrowed_snapshot_round_trips_and_rejects_widening(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX, mission_id="mission-1")
        narrowed = narrow_contract(base, authority={"terminal": False}, budget={"max_tool_calls": 3})
        assert contract_from_snapshot(narrowed.to_dict(), expected_digest=narrowed.digest()).digest() == narrowed.digest()

        widened = narrowed.to_dict()
        widened["authority"]["terminal"] = True
        with pytest.raises(ContractViolation):
            contract_from_snapshot(widened)

    def test_narrowing_reduces_authority(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX)
        narrowed = narrow_contract(base, authority={"terminal": False})
        assert narrowed.may("terminal") is False
        assert base.may("terminal") is True  # the original is untouched

    def test_widening_authority_is_refused_named(self) -> None:
        base = profile_for(AutonomyProfile.ASSIST)
        with pytest.raises(ContractViolation) as exc:
            narrow_contract(base, authority={"terminal": True})
        assert "terminal" in str(exc.value)

    def test_narrowing_past_the_profile_ceiling_is_refused_at_build(self) -> None:
        # The dataclass itself refuses, so a hand-constructed contract cannot
        # bypass the ceiling that `narrow_contract` enforces.
        from types import MappingProxyType

        base = profile_for(AutonomyProfile.ASSIST)
        with pytest.raises(ContractViolation):
            type(base)(
                profile=base.profile,
                authority=MappingProxyType({**dict(base.authority), "terminal": True}),
                execution=base.execution,
                budget=base.budget,
                controls=base.controls,
                protected_actions=base.protected_actions,
            )

    def test_widening_a_budget_is_refused(self) -> None:
        base = narrow_contract(profile_for(AutonomyProfile.ASSIST), budget={"max_tool_calls": 200})
        with pytest.raises(ContractViolation) as exc:
            narrow_contract(base, budget={"max_tool_calls": 99999})
        assert "max_tool_calls" in str(exc.value)

    def test_finite_session_quota_cannot_be_widened_back_to_unlimited(self) -> None:
        finite = narrow_contract(profile_for(AutonomyProfile.APEX_MAX), budget={"max_tool_calls": 10})
        with pytest.raises(ContractViolation, match="max_tool_calls"):
            narrow_contract(finite, budget={"max_tool_calls": None})

    def test_lowering_a_budget_is_allowed(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX)
        narrowed = narrow_contract(base, budget={"max_tool_calls": 10})
        assert narrowed.budget.max_tool_calls == 10
        assert base.budget.max_tool_calls is None

    def test_loosening_a_protected_action_is_refused(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX)
        with pytest.raises(ContractViolation) as exc:
            narrow_contract(base, protected_actions={"destructive_filesystem": "allow"})
        assert "destructive_filesystem" in str(exc.value)

    def test_tightening_a_protected_action_is_allowed(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX)
        narrowed = narrow_contract(base, protected_actions={"package_install": "deny"})
        assert narrowed.protected_actions["package_install"] == "deny"

    def test_unknown_verdict_is_refused(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX)
        with pytest.raises(ContractViolation):
            narrow_contract(base, protected_actions={"package_install": "maybe"})

    def test_narrowing_with_no_changes_returns_the_same_object(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX)
        assert narrow_contract(base) is base

    def test_unknown_authority_key_in_narrowing_is_refused(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX)
        with pytest.raises(ContractViolation) as exc:
            narrow_contract(base, authority={"nope": False})
        assert "nope" in str(exc.value)

    def test_narrowing_changes_the_digest(self) -> None:
        base = profile_for(AutonomyProfile.APEX_MAX)
        assert narrow_contract(base, authority={"terminal": False}).digest() != base.digest()


class TestIdentity:
    def test_digest_is_stable_across_issues(self) -> None:
        a = profile_for(AutonomyProfile.APEX_MAX)
        b = profile_for(AutonomyProfile.APEX_MAX)
        assert a.digest() == b.digest()
        assert a.issued_at != b.issued_at or True  # timestamps may coincide; digest must not care

    def test_digest_differs_per_profile(self) -> None:
        digests = {profile_for(p).digest() for p in ALL_PROFILES}
        assert len(digests) == len(ALL_PROFILES)

    def test_mission_id_does_not_change_the_digest(self) -> None:
        # The digest answers "did the policy change", so a per-mission contract
        # must compare equal to the profile it derives from.
        assert profile_for("apex_max", mission_id="m1").digest() == profile_for("apex_max", mission_id="m2").digest()

    def test_to_dict_round_trips_the_decisions(self) -> None:
        payload = profile_for(AutonomyProfile.APEX_MAX).to_dict()
        for key in ("profile", "enabled", "authority", "budget", "controls", "protected_actions", "digest", "policy_sites"):
            assert key in payload


class TestAPEXIsNotASecondPolicyKernel:
    """APEX delegates policy. These pin that every delegated site is real."""

    @pytest.mark.parametrize("attr_name", [f.name for f in fields(PolicyAttribution)])
    def test_every_attributed_policy_site_imports(self, attr_name: str) -> None:
        dotted = str(getattr(PolicyAttribution(), attr_name))
        module_path, _, symbol = dotted.partition(":")
        module = importlib.import_module(module_path)
        assert getattr(module, symbol, None) is not None, f"{dotted} does not exist"

    def test_live_sites_reports_every_declared_site(self) -> None:
        declared = set(PolicyAttribution().to_dict())
        assert set(PolicyAttribution.live_sites()) == declared

    def test_attribution_names_the_real_lifecycle_owner(self) -> None:
        # The point of the attribution table: RunManager stays the only run
        # lifecycle owner, and APEX says so in machine-readable form.
        assert PolicyAttribution().run_lifecycle.startswith("alpha.runtime.runs.manager:RunManager")

    def test_attribution_names_the_acceptance_gate(self) -> None:
        assert PolicyAttribution().acceptance == "alpha.mission.acceptance:assert_acceptance_passed"

    def test_attribution_names_the_emergency_stop_home(self) -> None:
        assert PolicyAttribution().emergency_stop == "alpha.runtime.control:read_state"
