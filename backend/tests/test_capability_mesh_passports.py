"""Tests for registry-derivation helpers: passports built from what exists.

Phase A derives passports from harness registries the cheap way: the caller
injects the known names, the helper validates against them, and every
estimate it emits is UNMEASURED with sample_count=0 — the mesh never claims a
measurement it does not have.
"""

from __future__ import annotations

import pytest

from alpha.capability_mesh.models import EntityKind, EvidenceLevel
from alpha.capability_mesh.passports import (
    WILDCARD_TASK_CATEGORY,
    agent_passport,
    model_passport,
    skill_passport,
    tool_passport,
)
from alpha.capability_mesh.store import PassportStore


class TestToolPassport:
    def test_shape(self) -> None:
        passport = tool_passport("bash", version="v1", required_tools=["bash"])
        assert passport.entity_id == "tool:bash"
        assert passport.entity_type is EntityKind.TOOL
        assert passport.version == "v1"
        assert passport.required_tools == ("bash",)
        assert passport.enabled is True

    def test_capability_is_use_prefixed_and_unmeasured(self) -> None:
        passport = tool_passport("bash", version="v1")
        assert len(passport.capabilities) == 1
        estimate = passport.capabilities[0]
        assert estimate.capability == "use:bash"
        assert estimate.task_category == WILDCARD_TASK_CATEGORY
        assert estimate.evidence_level is EvidenceLevel.UNMEASURED
        assert estimate.sample_count == 0
        assert estimate.quality_mean is None
        assert estimate.uncertainty is None

    def test_known_tool_names_accepts_a_registered_tool(self) -> None:
        passport = tool_passport("bash", version="v1", known_tool_names=frozenset({"bash", "read"}))
        assert passport.entity_id == "tool:bash"

    def test_unknown_tool_is_refused_when_the_registry_is_provided(self) -> None:
        with pytest.raises(ValueError, match="time_machine") as excinfo:
            tool_passport("time_machine", version="v1", known_tool_names=frozenset({"bash", "read"}))
        assert "time_machine" in str(excinfo.value)

    def test_no_registry_means_no_lookup(self) -> None:
        """Injection is the seam; the helper never imports the tool graph."""
        assert tool_passport("whatever", version="v1").entity_id == "tool:whatever"

    def test_empty_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="version"):
            tool_passport("bash", version="")

    def test_empty_tool_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="tool_name"):
            tool_passport("", version="v1")

    def test_overall_evidence_level_is_unmeasured(self) -> None:
        assert tool_passport("bash", version="v1").overall_evidence_level() is EvidenceLevel.UNMEASURED


class TestModelPassport:
    def test_carries_provider_and_model_id(self) -> None:
        passport = model_passport("openrouter", "vendor/model-x", version="cfg-7")
        assert passport.entity_id == "model:openrouter/vendor/model-x"
        assert passport.entity_type is EntityKind.MODEL_CONFIG
        assert passport.model_provider == "openrouter"
        assert passport.model_id == "vendor/model-x"
        assert passport.version == "cfg-7"

    def test_one_unmeasured_estimate_per_capability(self) -> None:
        passport = model_passport("openrouter", "vendor/model-x", version="cfg-7", capabilities=["implement", "review"])
        assert [e.capability for e in passport.capabilities] == ["implement", "review"]
        for estimate in passport.capabilities:
            assert estimate.evidence_level is EvidenceLevel.UNMEASURED
            assert estimate.sample_count == 0
            assert estimate.task_category == WILDCARD_TASK_CATEGORY

    def test_no_capabilities_means_no_estimates(self) -> None:
        assert model_passport("p", "m", version="v1").capabilities == ()

    def test_duplicate_capabilities_collapse(self) -> None:
        passport = model_passport("p", "m", version="v1", capabilities=["implement", "implement"])
        assert [e.capability for e in passport.capabilities] == ["implement"]

    def test_empty_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="version"):
            model_passport("p", "m", version="")

    def test_overall_evidence_level_is_unmeasured(self) -> None:
        passport = model_passport("p", "m", version="v1", capabilities=["implement"])
        assert passport.overall_evidence_level() is EvidenceLevel.UNMEASURED


class TestSkillPassport:
    def test_shape(self) -> None:
        passport = skill_passport("pdf-crunch", version="s3", required_tools=["bash"])
        assert passport.entity_id == "skill:pdf-crunch"
        assert passport.entity_type is EntityKind.SKILL_BUNDLE
        assert passport.required_tools == ("bash",)
        assert len(passport.capabilities) == 1
        assert passport.capabilities[0].capability == "use:pdf-crunch"
        assert passport.capabilities[0].evidence_level is EvidenceLevel.UNMEASURED
        assert passport.capabilities[0].sample_count == 0

    def test_empty_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="version"):
            skill_passport("pdf-crunch", version="")

    def test_empty_skill_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="skill_name"):
            skill_passport("", version="v1")


class TestAgentPassport:
    def test_shape(self) -> None:
        passport = agent_passport("tester", version="a1", capabilities=["test_design"], required_tools=["bash"])
        assert passport.entity_id == "agent:tester"
        assert passport.entity_type is EntityKind.AGENT
        assert passport.version == "a1"
        assert passport.required_tools == ("bash",)
        assert len(passport.capabilities) == 1
        estimate = passport.capabilities[0]
        assert estimate.capability == "test_design"
        assert estimate.evidence_level is EvidenceLevel.UNMEASURED
        assert estimate.sample_count == 0
        assert estimate.quality_mean is None

    def test_no_capabilities_is_allowed(self) -> None:
        """An agent with unknown capability is honest, not broken."""
        passport = agent_passport("tester", version="a1")
        assert passport.capabilities == ()
        assert passport.overall_evidence_level() is EvidenceLevel.UNMEASURED

    def test_empty_version_is_refused(self) -> None:
        with pytest.raises(ValueError, match="version"):
            agent_passport("tester", version="")

    def test_empty_agent_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="agent_id"):
            agent_passport("", version="v1")

    def test_every_derived_passport_is_json_safe_for_the_store(self, tmp_path) -> None:
        store = PassportStore(tmp_path)
        passports = [
            agent_passport("tester", version="a1", capabilities=["test_design"]),
            model_passport("p", "m", version="v1", capabilities=["implement"]),
            skill_passport("s", version="v1"),
            tool_passport("bash", version="v1", known_tool_names=frozenset({"bash"})),
        ]
        store.replace_all(passports)
        assert {p.entity_id for p in store.list()} == {"agent:tester", "model:p/m", "skill:s", "tool:bash"}
