"""Config-plane contract for the promoted memory subsystem types.

Every additive memory type (L1 plus the seven subsystem configs) is reachable
from the shared ``MemoryConfig`` schema, defaults to OFF, survives the config
loader, and — critically — is described in ``config.example.yaml`` with exactly
the schema defaults. That last guard is the one that keeps a new operator's
shipped config and the code's real defaults from drifting apart silently.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from alpha.config.memory_config import (
    MemoryConfig,
    get_memory_config,
    load_memory_config_from_dict,
    set_memory_config,
)

# section name -> the config model that owns it.
SECTIONS: dict[str, str] = {
    "l1": "L1MemoryConfig",
    "affective": "AffectiveConfig",
    "entities": "EntityConfig",
    "narrative": "NarrativeConfig",
    "prospective": "ProspectiveConfig",
    "social": "SocialConfig",
    "policy": "PolicyConfig",
    "fusion": "FusionConfig",
    "scenarios": "ScenarioConfig",
    "fabric": "FabricConfig",
    "evaluation": "EvaluationConfig",
}

_MISSING = object()

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.yaml"

# The ONLY sanctioned deviations between config.example.yaml and the schema
# defaults. L1 ships live for fresh installs (the typed working-memory pipeline
# is a product default); every other type is opt-in and must ship OFF. Adding an
# entry here is a product decision that needs its own commit and ledger row --
# the guard below exists so a change of default cannot happen by accident.
INTENTIONAL_EXAMPLE_OVERRIDES: dict[tuple[str, str], Any] = {
    ("l1", "enabled"): True,
}


def _example_memory() -> dict[str, Any]:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")) or {}
    memory = document.get("memory")
    assert isinstance(memory, dict), "config.example.yaml must have a mapping memory section"
    return memory


def _schema_defaults(section: str) -> dict[str, Any]:
    """Field defaults for one promoted section.

    Most subsystem configs are pydantic models; ``EvaluationConfig`` is a frozen
    stdlib dataclass (it validates in ``__post_init__``), so both shapes must be
    read the way their own type exposes them.
    """
    value = getattr(MemoryConfig(), section)
    if isinstance(value, BaseModel):
        return value.model_dump()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
    msg = f"memory.{section} exposes neither model_dump() nor dataclass fields"
    raise TypeError(msg)


@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_every_promoted_section_exists_and_defaults_off(section: str) -> None:
    config = MemoryConfig()
    value = getattr(config, section)
    assert value is not None
    assert value.enabled is False, f"memory.{section} must be opt-in"


@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_promoted_sections_are_shared_fields(section: str) -> None:
    """A section missing from _SHARED_FIELDS would be migrated into
    ``backend_config`` on load instead of staying a host field."""
    from alpha.config import memory_config as module

    assert section in module._SHARED_FIELDS
    assert MemoryConfig.model_fields[section].annotation is not None


@pytest.mark.parametrize("section", sorted(SECTIONS))
def test_loader_preserves_promoted_sections(section: str) -> None:
    original = get_memory_config()
    try:
        assert load_memory_config_from_dict({section: {"enabled": True}}) is None
        loaded = get_memory_config()
        assert getattr(loaded, section).enabled is True
        # The section must not have been swallowed by backend_config migration.
        assert section not in loaded.backend_config
    finally:
        set_memory_config(original)


def test_example_config_matches_schema_defaults() -> None:
    """config.example.yaml must ship the schema's real defaults, key for key,
    except for the explicitly sanctioned overrides above."""
    memory = _example_memory()
    seen_overrides: set[tuple[str, str]] = set()
    for section in SECTIONS:
        assert section in memory, f"config.example.yaml is missing memory.{section}"
        example_block = memory[section]
        schema_block = _schema_defaults(section)
        assert set(example_block) == set(schema_block), (
            f"memory.{section} keys differ between config.example.yaml and the schema: "
            f"example-only={sorted(set(example_block) - set(schema_block))} "
            f"schema-only={sorted(set(schema_block) - set(example_block))}"
        )
        for key, expected in schema_block.items():
            actual = example_block[key]
            override = INTENTIONAL_EXAMPLE_OVERRIDES.get((section, key), _MISSING)
            if override is _MISSING:
                assert actual == expected, f"memory.{section}.{key}: example={actual!r} schema={expected!r}"
                continue
            seen_overrides.add((section, key))
            assert actual == override, f"memory.{section}.{key}: example={actual!r} sanctioned={override!r}"
    assert seen_overrides == set(INTENTIONAL_EXAMPLE_OVERRIDES), (
        f"sanctioned overrides that are no longer deviations: {sorted(set(INTENTIONAL_EXAMPLE_OVERRIDES) - seen_overrides)}"
    )


def test_example_sections_are_documented_opt_in() -> None:
    """A shipped example must not silently enable a subsystem: the schema
    default is OFF, so any true value here is an intentional product choice and
    must be justified; anything else would surprise operators."""
    memory = _example_memory()
    enabled_in_example = {
        section: memory[section]["enabled"] for section in SECTIONS if memory[section].get("enabled")
    }
    expected_enabled = {section for (section, key), value in INTENTIONAL_EXAMPLE_OVERRIDES.items() if key == "enabled" and value is True}
    assert set(enabled_in_example) == expected_enabled, (
        f"enabled memory subsystems in config.example.yaml = {sorted(enabled_in_example)}, "
        f"sanctioned = {sorted(expected_enabled)}"
    )


def test_subsystem_internals_are_not_imported_by_the_config_plane() -> None:
    """Importing the shared schema must not drag subsystem engines in (the
    import-cycle guard: config -> subsystem internals -> config)."""
    import subprocess
    import sys

    code = (
        "import sys; import alpha.config.memory_config as m; "
        "bad=[n for n in sys.modules if n.startswith(('alpha.memory.fusion.pipeline',"
        "'alpha.memory.policy.loader','alpha.memory.social.system','alpha.memory.affective.memory',"
        "'alpha.memory.entities.memory','alpha.memory.narrative.memory','alpha.memory.prospective.store',"
        "'alpha.memory.scenarios.router','alpha.memory.fabric.store','alpha.memory.evaluation.runner',"
        "'alpha.agents.memory.l1.pipeline','alpha.agents.memory.manager'))]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"config import pulled subsystem internals: {result.stdout.strip()}"
