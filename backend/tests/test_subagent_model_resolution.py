from types import SimpleNamespace

import pytest

from alpha.subagents.config import SubagentConfig, resolve_subagent_model_name


def test_inherited_subagent_uses_configured_default_model():
    app_config = SimpleNamespace(
        models=[SimpleNamespace(name="first-model"), SimpleNamespace(name="selected-model")],
        default_model_name="selected-model",
    )

    assert resolve_subagent_model_name(SubagentConfig(name="worker", description="worker"), None, app_config=app_config) == "selected-model"


def test_inherited_subagent_without_default_or_models_fails_closed():
    app_config = SimpleNamespace(models=[], default_model_name=None)

    with pytest.raises(ValueError, match="No chat models are configured"):
        resolve_subagent_model_name(SubagentConfig(name="worker", description="worker"), None, app_config=app_config)


def test_explicit_subagent_model_precedes_parent_and_app_default():
    app_config = SimpleNamespace(models=[SimpleNamespace(name="first-model")], default_model_name="selected-model")

    assert resolve_subagent_model_name(SubagentConfig(name="worker", description="worker", model="explicit-model"), "parent-model", app_config=app_config) == "explicit-model"
