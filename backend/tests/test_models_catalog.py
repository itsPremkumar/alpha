"""Regression coverage for the dedicated ``models.yaml`` model catalog.

The catalog exists because model names used to be hand-maintained in a dozen
places and drifted: the shipped default declared ``union-alpha`` as
``supports_thinking: false`` in ``config.yaml`` and ``true`` in two other
catalogs, producing a thinking control the backend rejected. These tests pin
the properties that stop that class of bug returning:

* the file resolves the same way ``extensions_config.json`` does, so an operator
  can point at it explicitly and a missing file is loud rather than silent;
* ``config.yaml`` still overrides it, so an existing deployment is untouched;
* every declared routing name resolves against ``models[]`` at load;
* ``$VAR`` references resolve on load and the raw file is never rewritten.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alpha.config.app_config import AppConfig
from alpha.config.model_config import ModelConfig
from alpha.config.models_catalog import (
    MODELS_CATALOG_VERSION,
    MODELS_CONFIG_PATH_ENV,
    ModelsCatalog,
    get_models_catalog,
    load_models_catalog,
    read_raw_models_catalog,
    reset_models_catalog_cache,
    resolve_models_config_path,
)
from alpha.config.sandbox_config import SandboxConfig

MINIMAL = """
config_version: 1
default_model: flagship
models:
  - name: flagship
    display_name: Flagship
    use: langchain_openai:ChatOpenAI
    model: flagship-model
    api_key: $TEST_CATALOG_KEY
    context_window: 200000
    supports_thinking: true
  - name: cheap
    display_name: Cheap
    use: langchain_openai:ChatOpenAI
    model: cheap-model
    api_key: $TEST_CATALOG_KEY
    context_window: 32000
routing:
  categories:
    quick: [cheap]
    deep: [flagship, cheap]
  tiers:
    fast: [cheap]
    frontier: [flagship]
pricing:
  flagship-model:
    input: 3.0
    output: 15.0
"""


@pytest.fixture
def catalog_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(MINIMAL, encoding="utf-8")
    monkeypatch.setenv(MODELS_CONFIG_PATH_ENV, str(path))
    reset_models_catalog_cache()
    yield path
    reset_models_catalog_cache()


def test_env_var_resolves_the_catalog(catalog_file: Path) -> None:
    assert resolve_models_config_path() == catalog_file


def test_explicit_path_wins_over_env(catalog_file: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.yaml"
    other.write_text(MINIMAL, encoding="utf-8")
    assert resolve_models_config_path(str(other)) == other


def test_missing_explicit_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_models_config_path(str(tmp_path / "absent.yaml"))


def test_missing_env_path_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(MODELS_CONFIG_PATH_ENV, str(tmp_path / "absent.yaml"))
    with pytest.raises(FileNotFoundError):
        resolve_models_config_path()


def test_absent_catalog_is_optional(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No file anywhere is the legitimate 'configure via config.yaml only' case."""
    monkeypatch.delenv(MODELS_CONFIG_PATH_ENV, raising=False)
    monkeypatch.setattr(
        "alpha.config.models_catalog._existing_project_file",
        lambda _names: None,
    )
    monkeypatch.setattr(
        "alpha.config.models_catalog.Path.is_file",
        lambda self: False,
    )
    reset_models_catalog_cache()
    assert resolve_models_config_path() is None
    assert load_models_catalog().models == []


def test_env_var_references_resolve_on_load(catalog_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_CATALOG_KEY", "sk-resolved")
    reset_models_catalog_cache()
    catalog = load_models_catalog()
    assert catalog.models[0].api_key == "sk-resolved"


def test_unset_env_var_becomes_empty_string(catalog_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_CATALOG_KEY", raising=False)
    reset_models_catalog_cache()
    assert load_models_catalog().models[0].api_key == ""


def test_raw_file_keeps_var_reference(catalog_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The raw reader must never leak a resolved secret or lose the reference."""
    monkeypatch.setenv("TEST_CATALOG_KEY", "sk-resolved")
    reset_models_catalog_cache()
    raw = read_raw_models_catalog()
    assert raw["models"][0]["api_key"] == "$TEST_CATALOG_KEY"
    assert "sk-resolved" not in json.dumps(raw)


def test_content_change_reloads(catalog_file: Path) -> None:
    reset_models_catalog_cache()
    first = get_models_catalog()
    assert [m.name for m in first.models] == ["flagship", "cheap"]

    updated = MINIMAL.replace("default_model: flagship", "default_model: cheap")
    catalog_file.write_text(updated, encoding="utf-8")
    second = get_models_catalog()
    assert second.default_model == "cheap"


def test_unchanged_file_is_served_from_cache(catalog_file: Path) -> None:
    reset_models_catalog_cache()
    assert get_models_catalog() is get_models_catalog()


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(f"{MINIMAL}\nmystery_key: 1\n", encoding="utf-8")
    monkeypatch.setenv(MODELS_CONFIG_PATH_ENV, str(path))
    reset_models_catalog_cache()
    with pytest.raises(ValidationError):
        load_models_catalog()


def test_unknown_provider_category_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown provider category"):
        ModelsCatalog.model_validate(
            {
                "catalog": [
                    {
                        "id": "x",
                        "name": "X",
                        "category": "sort_of_free",
                    }
                ]
            }
        )


def test_empty_declared_route_is_rejected() -> None:
    """An empty chain would read as 'declared but unusable' and silently degrade."""
    with pytest.raises(ValidationError, match="no model names"):
        ModelsCatalog.model_validate({"routing": {"categories": {"quick": ["  "]}}})


def test_free_gateway_auth_header_must_be_pairs() -> None:
    with pytest.raises(ValidationError, match="auth_header"):
        ModelsCatalog.model_validate(
            {
                "free_gateways": [
                    {
                        "id": "g",
                        "base_url": "https://example.invalid/v1",
                        "auth_header": [["Authorization"]],
                    }
                ]
            }
        )


def test_version_mismatch_warns_but_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(MINIMAL.replace("config_version: 1", f"config_version: {MODELS_CATALOG_VERSION + 99}"), encoding="utf-8")
    monkeypatch.setenv(MODELS_CONFIG_PATH_ENV, str(path))
    reset_models_catalog_cache()
    with caplog.at_level("WARNING"):
        catalog = load_models_catalog()
    assert catalog.models
    assert "config_version" in caplog.text


def test_json_catalog_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON is a YAML subset, so a models.json needs no separate code path."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps(yaml.safe_load(MINIMAL)), encoding="utf-8")
    monkeypatch.setenv(MODELS_CONFIG_PATH_ENV, str(path))
    reset_models_catalog_cache()
    assert [m.name for m in load_models_catalog().models] == ["flagship", "cheap"]


def test_malformed_yaml_is_reported_with_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("models: [unclosed\n", encoding="utf-8")
    monkeypatch.setenv(MODELS_CONFIG_PATH_ENV, str(path))
    reset_models_catalog_cache()
    with pytest.raises(ValueError, match="not valid YAML"):
        load_models_catalog()


"""AppConfig integration for the ``models.yaml`` catalog.

Constructed directly rather than through ``AppConfig.from_file`` so the global
config cache is never touched; ``_apply_models_catalog`` is the exact overlay
``from_file`` invokes.
"""


def _config(**overrides) -> AppConfig:
    return AppConfig(sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"), **overrides)


def _apply(config: AppConfig) -> AppConfig:
    config._apply_models_catalog()
    return config


def _model(name: str, **overrides) -> ModelConfig:
    return ModelConfig(name=name, use="langchain_openai:ChatOpenAI", model=overrides.pop("model", name), **overrides)


def test_catalog_supplies_models_when_config_declares_none(catalog_file) -> None:
    config = _apply(_config())
    assert [m.name for m in config.models] == ["flagship", "cheap"]
    assert config.default_model_name == "flagship"


def test_config_yaml_overrides_catalog_entry_wholesale(catalog_file) -> None:
    """One file stays authoritative per name: replaced, never field-merged."""
    config = _apply(_config(models=[_model("flagship", model="different-model", context_window=999)]))

    flagship = config.get_model_config("flagship")
    assert flagship is not None
    assert flagship.model == "different-model"
    assert flagship.context_window == 999
    # The catalog-only entry survives alongside the override.
    assert config.get_model_config("cheap") is not None


def test_catalog_routing_is_merged_when_config_declares_none(catalog_file) -> None:
    config = _apply(_config())
    assert config.model_routing.categories == {"quick": ["cheap"], "deep": ["flagship", "cheap"]}
    assert config.model_routing.tiers == {"fast": ["cheap"], "frontier": ["flagship"]}


def test_routing_chains_are_validated_against_models(catalog_file) -> None:
    """A routing name that resolves to nothing must fail the load, not degrade.

    ``ValueError`` rather than ``ValidationError``: the overlay runs after
    ``model_validate``, so this is a plain config error. pydantic's
    ``ValidationError`` subclasses ``ValueError``, so callers can catch one.
    """
    with pytest.raises(ValueError, match="model_routing"):
        _apply(_config(model_routing={"categories": {"quick": ["does-not-exist"]}}))


def test_default_model_must_exist() -> None:
    with pytest.raises(ValidationError, match="default_model"):
        _config(default_model="ghost", models=[_model("real")])


def test_explicit_default_model_survives_list_reordering() -> None:
    """The whole point of ``default_model:`` — prepending must not switch models."""
    config = _config(default_model="second", models=[_model("first", model="a"), _model("second", model="b")])
    assert config.default_model_name == "second"


def test_positional_default_still_works_without_the_key() -> None:
    assert _config(models=[_model("only")]).default_model_name == "only"


def test_no_models_yields_no_default() -> None:
    assert _config().default_model_name is None


def test_blank_default_model_is_rejected() -> None:
    with pytest.raises(ValidationError, match="default_model"):
        _config(default_model="   ", models=[_model("only")])


def test_malformed_catalog_does_not_break_config_only_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``config.yaml``-only deployment must still boot if ``models.yaml`` is broken."""

    def _boom() -> ModelsCatalog:
        raise ValueError("bad yaml")

    monkeypatch.setattr("alpha.config.models_catalog.get_models_catalog", _boom)
    config = _apply(_config(models=[_model("only")]))
    assert config.default_model_name == "only"
