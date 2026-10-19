"""Configuration plane for ``config.yaml -> harness_improvement`` (guide §17).

Three properties decide this plane, and each has a test that would fail if a
later edit undid it:

1. **The shipped template is the schema.** ``config.example.yaml`` must carry
   exactly the ``HarnessImprovementConfig`` fields, with the schema's defaults,
   so a fresh install's first run is the documented conservative posture rather
   than an accidental one.
2. **The two declarations cannot drift.** The pydantic model is the only place
   keys are *declared*; the engine's ``HarnessImprovementSettings`` is built from
   its ``model_dump()`` and refuses unknown keys. A field added on one side
   without the other fails here, not at probe time.
3. **Bounds are refusals, not clamps**, and the feature is off until an operator
   turns it on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from alpha.config.app_config import AppConfig
from alpha.harness_improvement import HarnessImprovementSettings
from alpha.harness_improvement.config import (
    HarnessImprovementConfig,
    HarnessImprovementPromotionConfig,
    HarnessImprovementSandboxConfig,
)
from alpha.harness_improvement.engine import ImprovementRunError

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.yaml"


def _example_block() -> dict[str, Any]:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")) or {}
    block = document.get("harness_improvement")
    assert isinstance(block, dict), "config.example.yaml must carry a harness_improvement block"
    return block


def _app_config(**harness_improvement: Any) -> AppConfig:
    document: dict[str, Any] = {
        "sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"},
        "models": [{"name": "m", "use": "langchain_openai:ChatOpenAI", "model": "gpt-test"}],
    }
    if harness_improvement:
        document["harness_improvement"] = harness_improvement
    return AppConfig.model_validate(document)


# ---------------------------------------------------------------- defaults


def test_the_default_posture_is_the_guide_conservative_one() -> None:
    config = HarnessImprovementConfig()
    assert config.enabled is False
    assert config.mode == "sequential"
    assert config.max_steps == 1
    assert config.max_candidates_per_scope == 1
    assert config.max_concurrent_probes == 1
    assert config.max_wall_time_seconds == 300
    assert config.max_tool_calls_per_probe == 60
    assert config.max_model_turns_per_probe == 80
    assert config.max_diff_files == 8
    assert config.max_changed_bytes == 32768
    assert config.max_model_calls == 24
    assert config.max_cost_usd is None
    assert config.max_experience_entries_in_context == 5
    assert config.min_evidence_for_positive_lesson == 1
    assert config.model is None
    assert config.seed is None

    assert config.sandbox.isolate_filesystem is True
    assert config.sandbox.isolate_memory_writes is True
    assert config.sandbox.allow_external_side_effects is False
    assert config.sandbox.network_mode == "deny_by_default"
    assert config.sandbox.allow_candidate_middleware_changes is False
    assert config.sandbox.allow_candidate_guardrail_policy_changes is False

    assert config.promotion.automatic_global_promotion is False
    assert config.promotion.require_regression_suite is True
    assert config.promotion.require_human_approval_for_scripts is True
    assert config.promotion.require_human_approval_for_middleware is True
    assert config.promotion.require_human_approval_for_permissions is True
    assert config.promotion.keep_last_known_good is True


def test_only_sequential_mode_is_expressible_and_default_off() -> None:
    assert HarnessImprovementConfig().mode == "sequential"
    assert HarnessImprovementConfig(mode="sequential").mode == "sequential"
    with pytest.raises(ValidationError):
        HarnessImprovementConfig(mode="parallel")


# ---------------------------------------------------------------- template


def test_the_shipped_example_is_exactly_the_schema_defaults() -> None:
    block = _example_block()
    schema = HarnessImprovementConfig().model_dump()
    assert set(block) == set(schema), f"example keys differ from the schema: example-only={sorted(set(block) - set(schema))} schema-only={sorted(set(schema) - set(block))}"
    for key, expected in schema.items():
        assert block[key] == expected, f"harness_improvement.{key}: example={block[key]!r} schema={expected!r}"


def test_the_shipped_example_round_trips_into_engine_settings() -> None:
    config = HarnessImprovementConfig(**_example_block())
    settings = HarnessImprovementSettings.from_mapping(config.model_dump())
    assert settings.enabled is False
    assert settings.patch_policy().max_changed_bytes == 32768
    assert settings.promotion_policy().automatic_global_promotion is False


# ---------------------------------------------------------------- parity


def test_every_schema_field_has_exactly_one_engine_counterpart() -> None:
    """The declaration (pydantic) and the consumer (engine dataclass) cannot drift.

    ``from_mapping`` resolves exactly the fields the engine declares, and it
    refuses unknown keys — so a key present on only one side is a load-time
    failure. This test turns that runtime property into a structural one.
    """
    nested = {"sandbox", "promotion"}
    schema_keys = (set(HarnessImprovementConfig.model_fields) - nested) | set(HarnessImprovementSandboxConfig.model_fields) | set(HarnessImprovementPromotionConfig.model_fields)
    engine_keys = HarnessImprovementSettings._field_names()
    assert engine_keys == schema_keys, f"schema-only={sorted(schema_keys - engine_keys)} engine-only={sorted(engine_keys - schema_keys)}"
    assert HarnessImprovementSettings._sandbox_keys() == set(HarnessImprovementSandboxConfig.model_fields)
    assert HarnessImprovementSettings._promotion_keys() == set(HarnessImprovementPromotionConfig.model_fields)


def test_unknown_keys_are_refused_not_ignored() -> None:
    config = HarnessImprovementConfig()
    with pytest.raises(ImprovementRunError, match="unknown harness_improvement settings: max_step"):
        HarnessImprovementSettings.from_mapping({**config.model_dump(), "max_step": 2})
    with pytest.raises(ImprovementRunError, match="unknown harness_improvement settings: isolate"):
        HarnessImprovementSettings.from_mapping({**config.model_dump(), "sandbox": {"isolate": True}})
    with pytest.raises(ImprovementRunError, match="unknown harness_improvement settings: auto_promote"):
        HarnessImprovementSettings.from_mapping({**config.model_dump(), "promotion": {"auto_promote": True}})


def test_the_mapping_can_be_absent_or_nested_only() -> None:
    assert HarnessImprovementSettings.from_mapping(None).enabled is False
    nested_only = HarnessImprovementSettings.from_mapping({"sandbox": {"network_mode": "allow"}, "promotion": {"automatic_global_promotion": True}})
    assert nested_only.network_mode == "allow"
    assert nested_only.automatic_global_promotion is True
    assert nested_only.enabled is False


# ---------------------------------------------------------------- bounds


@pytest.mark.parametrize(
    "payload",
    [
        {"max_changed_bytes": 0},
        {"max_diff_files": 0},
        {"max_candidates_per_scope": 5},
        {"max_concurrent_probes": 0},
        {"max_wall_time_seconds": 29},
        {"max_tool_calls_per_probe": 0},
        {"max_model_turns_per_probe": 1001},
        {"max_model_calls": 0},
        {"max_experience_entries_in_context": 51},
        {"min_evidence_for_positive_lesson": 0},
        {"seed": -1},
    ],
)
def test_out_of_range_bounds_are_refused_not_clamped(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HarnessImprovementConfig(**payload)


def test_a_zero_cost_ceiling_is_not_a_real_ceiling() -> None:
    """``max_cost_usd`` is a positive ceiling or unstated; ``0`` would read as
    "spend nothing" while ``gt=0`` is what makes an unreported total honest."""
    with pytest.raises(ValidationError):
        HarnessImprovementConfig(max_cost_usd=0)
    assert HarnessImprovementConfig(max_cost_usd=1.5).max_cost_usd == 1.5
    assert HarnessImprovementConfig(max_cost_usd=None).max_cost_usd is None


def test_extra_keys_are_forbidden_at_every_level() -> None:
    with pytest.raises(ValidationError):
        HarnessImprovementConfig(modex="sequential")
    with pytest.raises(ValidationError):
        HarnessImprovementConfig(sandbox={"network": "allow"})
    with pytest.raises(ValidationError):
        HarnessImprovementConfig(promotion={"auto": True})


# ---------------------------------------------------------------- model name


def test_a_declared_model_must_exist_in_models() -> None:
    assert _app_config(model="m").harness_improvement.model == "m"
    with pytest.raises(ValidationError, match="harness_improvement.model: missing"):
        _app_config(model="missing")


def test_a_blank_model_name_is_refused():
    with pytest.raises(ValidationError, match="must be a non-empty model name"):
        _app_config(model="   ")


def test_a_model_name_is_not_checked_against_an_empty_models_list() -> None:
    """With no ``models[]`` there is nothing to validate against, matching every
    other model-name validator in ``AppConfig``."""
    config = AppConfig.model_validate({"sandbox": {"use": "alpha.sandbox.local:LocalSandboxProvider"}, "harness_improvement": {"model": "anything"}})
    assert config.harness_improvement.model == "anything"


# ---------------------------------------------------------------- wiring pins


def test_the_guardrail_gate_defaults_closed_and_travels_into_the_patch_policy() -> None:
    settings = HarnessImprovementSettings.from_mapping(HarnessImprovementConfig().model_dump())
    assert settings.patch_policy().allow_guardrail_policy_changes is False
    assert settings.patch_policy().allow_middleware_changes is False

    opened = HarnessImprovementConfig(sandbox={"allow_candidate_guardrail_policy_changes": True, "allow_candidate_middleware_changes": True})
    opened_settings = HarnessImprovementSettings.from_mapping(opened.model_dump())
    assert opened_settings.patch_policy().allow_guardrail_policy_changes is True
    assert opened_settings.patch_policy().allow_middleware_changes is True


def test_the_engine_settings_hash_the_whole_policy_not_just_a_flag() -> None:
    closed = HarnessImprovementSettings.from_mapping(HarnessImprovementConfig().model_dump())
    opened = HarnessImprovementSettings.from_mapping(HarnessImprovementConfig(sandbox={"allow_candidate_guardrail_policy_changes": True}).model_dump())
    assert closed.config_hash() != opened.config_hash(), "a policy change must move the config hash a run records"
