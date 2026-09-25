"""Config-plane contract for the two AppConfig-level autonomy subsystems.

`evolution_evidence` (the bar a proposed self-change must clear) and
`self_tuning` (the change protocol that may apply one) are the first
non-memory subsystems promoted into `AppConfig`. Both are default-OFF, and
both are security-relevant: one decides whether the agent may promote its own
change, the other decides whether it may rewrite its own configuration. So the
properties pinned here are the ones that keep them inert until an operator says
otherwise, plus the drift guard that keeps `config.example.yaml` honest.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from alpha.config.app_config import AppConfig

SECTIONS = ("evolution_evidence", "self_tuning")

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.yaml"


def _example() -> dict[str, Any]:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")) or {}
    assert isinstance(document, dict)
    return document


def _model_construct() -> Any:
    """Defaults without validation: AppConfig has required fields (sandbox)."""
    return AppConfig.model_construct()


def _fields(model: type) -> dict[str, tuple[str | None, Any]]:
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        msg = f"{model!r} is not a pydantic model"
        raise TypeError(msg)
    return {
        name: (info.description, info.get_default(call_default_factory=True))
        for name, info in model.model_fields.items()
    }


def _normalize(value: Any) -> Any:
    """Container representation is irrelevant; values are compared strictly."""
    if isinstance(value, tuple | list | set | frozenset):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _normalize(dataclasses.asdict(value))
    return value


@pytest.mark.parametrize("section", SECTIONS)
def test_section_exists_and_defaults_off(section: str) -> None:
    value = getattr(_model_construct(), section)
    assert value is not None
    assert value.enabled is False, f"{section} must be opt-in"


@pytest.mark.parametrize("section", SECTIONS)
def test_example_matches_schema_defaults(section: str) -> None:
    """The shipped example must equal the model's real defaults, key for key."""
    example_block = _example()[section]
    schema_block = {name: default for name, (_desc, default) in _fields(type(getattr(_model_construct(), section))).items()}
    assert set(example_block) == set(schema_block), (
        f"{section} keys differ between config.example.yaml and the schema: "
        f"example-only={sorted(set(example_block) - set(schema_block))} "
        f"schema-only={sorted(set(schema_block) - set(example_block))}"
    )
    for key, expected in schema_block.items():
        assert _normalize(example_block[key]) == _normalize(expected), (
            f"{section}.{key}: example={example_block[key]!r} schema={expected!r}"
        )


def test_no_autonomy_subsystem_ships_enabled() -> None:
    """A fresh install must not run self-evolution or self-configuration."""
    for section in SECTIONS:
        assert _example()[section]["enabled"] is False, (
            f"{section} must ship disabled; enabling it for every operator is a product decision"
        )


def test_self_tuning_protects_its_own_authority() -> None:
    """The protocol must be unable to widen its own permissions.

    A self-configuration system that can edit the list of things it is not
    allowed to edit is not a control at all, so the protected list must contain
    the protocol's own namespace plus the enforcement ceilings.
    """
    protected = set(_model_construct().self_tuning.protected_paths)
    for required in (
        "self_tuning",
        "auth",
        "authorization",
        "sandbox",
        "approval",
        "kill_switch",
        "token_budget",
        "loop_detection.hard_limit",
    ):
        assert required in protected, f"{required} must be a protected path"
    # And the same list is what ships in the example, so an operator reading
    # the config sees the real policy rather than a stale subset.
    assert set(_example()["self_tuning"]["protected_paths"]) == protected


def test_evolution_evidence_requires_gates_and_a_rollback() -> None:
    """A promotion must not be possible on measurements alone."""
    config = _model_construct().evolution_evidence
    required = set(config.required_gates)
    assert {"evaluator_integrity", "reproducibility", "rollback_path"} <= required, (
        f"integrity, reproducibility and rollback must be required gates; got {sorted(required)}"
    )
    assert config.enabled is False
    # Noise floors are what make "improved" mean something.
    assert config.noise_floors, "at least the primary metric needs a declared noise floor"
    assert float(config.noise_floors.get(config.primary_metric, 0)) >= 0
