"""Tests for the user-selectable decision policy (Feature N / plan §15).

The profile is a resolution TABLE, never intelligence: explicit rows,
unknown names refused with the valid list, bounds refused not clamped, the
caller's agent budget applied as a stated min(), and LOCAL disclosing that
the mesh cannot verify locality yet. Merging may only RAISE a fingerprint's
recency requirement.
"""

from __future__ import annotations

import pytest

from alpha.capability_mesh.fingerprint import Band, Pressure, Risk, SituationFingerprint
from alpha.capability_mesh.policy import (
    MAX_AGENT_COUNT,
    MIN_AGENT_COUNT,
    DecisionPolicy,
    Depth,
    PolicyProfile,
    merge_fingerprint_policy,
    resolve_policy,
)


def _fp(**kwargs: object) -> SituationFingerprint:
    base: dict[str, object] = {"task_type": "general", "risk_band": Risk.LOW, "context_pressure": Pressure.LOW}
    base.update(kwargs)
    return SituationFingerprint(**base)  # type: ignore[arg-type]


class TestPolicyTable:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("balanced", (4, True, True, Depth.SAMPLED, False)),
            ("highest_confidence", (6, True, True, Depth.FULL, True)),
            ("fast", (2, False, False, Depth.NONE, False)),
            ("cost_minimal", (2, False, False, Depth.SAMPLED, False)),
            ("local", (4, False, True, Depth.SAMPLED, False)),
            ("deep", (8, True, True, Depth.FULL, True)),
            ("code_safe", (5, False, True, Depth.FULL, False)),
            ("research_grade", (6, True, True, Depth.FULL, True)),
        ],
    )
    def test_every_policy_resolves_from_the_table(self, name: str, expected: tuple[int, bool, bool, Depth, bool]) -> None:
        # default_agents=64 so the budget never bites: this pins the table itself.
        profile = resolve_policy(name, default_agents=64)
        agents, challenger, verifier, depth, recency = expected
        assert profile.policy.value == name
        assert profile.agent_count == agents
        assert profile.wants_challenger is challenger
        assert profile.wants_verifier is verifier
        assert profile.verification_depth is depth
        assert profile.recency_required is recency
        assert profile.rationale  # every profile carries its stated reason

    def test_accepts_enum_members_too(self) -> None:
        assert resolve_policy(DecisionPolicy.DEEP, default_agents=64).policy is DecisionPolicy.DEEP

    def test_unknown_policy_refused_listing_the_valid_names(self) -> None:
        with pytest.raises(ValueError) as caught:
            resolve_policy("turbo")
        message = str(caught.value)
        assert "turbo" in message
        for name in DecisionPolicy:
            assert name.value in message


class TestAgentBudget:
    def test_default_agents_caps_the_policy_appetite(self) -> None:
        assert resolve_policy("deep", default_agents=3).agent_count == 3  # preference 8, budget 3
        assert resolve_policy("fast", default_agents=8).agent_count == 2  # preference 2, budget never raises it

    @pytest.mark.parametrize("bad", [0, -1, MAX_AGENT_COUNT + 1, True, 4.5])
    def test_default_agents_bounds_are_refused(self, bad: object) -> None:
        with pytest.raises(ValueError, match="default_agents"):
            resolve_policy("balanced", default_agents=bad)  # type: ignore[arg-type]


class TestPolicyProfile:
    def _profile(self, **kwargs: object) -> PolicyProfile:
        base: dict[str, object] = {
            "policy": DecisionPolicy.BALANCED,
            "agent_count": 4,
            "wants_challenger": True,
            "wants_verifier": True,
            "verification_depth": Depth.SAMPLED,
            "recency_required": False,
            "rationale": "a stated reason",
        }
        base.update(kwargs)
        return PolicyProfile(**base)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [0, MAX_AGENT_COUNT + 1, MIN_AGENT_COUNT - 1, True, 2.5])
    def test_agent_count_bounds_refused_not_clamped(self, bad: object) -> None:
        with pytest.raises(ValueError, match="agent_count"):
            self._profile(agent_count=bad)

    def test_empty_rationale_refused(self) -> None:
        with pytest.raises(ValueError, match="rationale"):
            self._profile(rationale="")

    def test_non_bool_flags_refused(self) -> None:
        with pytest.raises(ValueError, match="wants_challenger"):
            self._profile(wants_challenger="yes")
        with pytest.raises(ValueError, match="recency_required"):
            self._profile(recency_required=1)

    def test_unknown_depth_and_policy_refused_listing_allowed(self) -> None:
        with pytest.raises(ValueError, match="verification_depth"):
            self._profile(verification_depth="paranoid")
        with pytest.raises(ValueError, match="policy"):
            self._profile(policy="turbo")

    def test_local_discloses_that_locality_cannot_be_verified(self) -> None:
        rationale = resolve_policy("local", default_agents=64).rationale
        assert "local" in rationale
        assert "cannot verify" in rationale


class TestMergeFingerprintPolicy:
    def test_policy_name_lands_on_the_fingerprint(self) -> None:
        merged = merge_fingerprint_policy(_fp(complexity_band=Band.HIGH), resolve_policy("code_safe"))
        assert merged.user_policy == "code_safe"
        assert merged.complexity_band is Band.HIGH  # untouched
        assert merged.recency_required is False  # code_safe demands no recency

    def test_policy_recency_raises_the_fingerprint_requirement(self) -> None:
        merged = merge_fingerprint_policy(_fp(), resolve_policy("research_grade"))
        assert merged.recency_required is True

    def test_policy_never_lowers_an_existing_recency_requirement(self) -> None:
        # The task-level requirement is user-visible; a policy that does not
        # itself demand recency cannot know the task stops needing it.
        merged = merge_fingerprint_policy(_fp(recency_required=True), resolve_policy("fast"))
        assert merged.recency_required is True

    def test_original_fingerprint_is_never_mutated(self) -> None:
        original = _fp()
        merge_fingerprint_policy(original, resolve_policy("deep"))
        assert original.user_policy == "balanced"
        assert original.recency_required is False

    def test_argument_types_are_checked(self) -> None:
        with pytest.raises(ValueError, match="SituationFingerprint"):
            merge_fingerprint_policy("not a fingerprint", resolve_policy("fast"))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="PolicyProfile"):
            merge_fingerprint_policy(_fp(), "fast")  # type: ignore[arg-type]
