from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alpha.config.model_config import ModelConfig


def _make_model(**overrides) -> ModelConfig:
    return ModelConfig(
        name="openai-responses",
        display_name="OpenAI Responses",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="gpt-5",
        **overrides,
    )


def test_responses_api_fields_are_declared_in_model_schema():
    assert "use_responses_api" in ModelConfig.model_fields
    assert "output_version" in ModelConfig.model_fields


def test_responses_api_fields_round_trip_in_model_dump():
    config = _make_model(
        api_key="$OPENAI_API_KEY",
        use_responses_api=True,
        output_version="responses/v1",
    )

    dumped = config.model_dump(exclude_none=True)

    assert dumped["use_responses_api"] is True
    assert dumped["output_version"] == "responses/v1"


def test_context_window_round_trips_when_positive():
    assert _make_model(context_window=128_000).context_window == 128_000


@pytest.mark.parametrize("context_window", [0, -1])
def test_context_window_rejects_non_positive_capacity(context_window):
    with pytest.raises(ValidationError, match="context_window"):
        _make_model(context_window=context_window)


def test_union_alpha_example_uses_verified_openrouter_contract():
    """The shipped baseline's offline contract.

    ``union-alpha`` is the Alpha-side *name* and is stable. The OpenRouter
    ``model`` field is a provider-side *slug*, which the provider can retire out
    from under us — ``stealth/union-alpha`` began answering HTTP 404 with the
    successor in its own message, and the fix was the slug only. So this pins
    the name, and pins the slug as the value that was actually verified against
    the live provider rather than as a value that merely parses.
    """
    example_path = Path(__file__).resolve().parents[2] / "config.example.yaml"
    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert data["models"], "The example must provide the Union Alpha model configuration"
    model = ModelConfig.model_validate(data["models"][0])
    assert model.name == "union-alpha"
    assert model.model == "unbiased/pareto"
    assert model.use == "langchain_openai:ChatOpenAI"
    assert model.context_window == 262144
    assert model.supports_vision is True
    assert model.supports_thinking is False
    assert model.supports_reasoning_effort is False
    assert model.use_responses_api is False
    assert model.fallbacks == []
    settings = model.model_dump()
    assert settings["base_url"] == "https://openrouter.ai/api/v1"
    assert settings["api_key"] == "$OPENROUTER_API_KEY"
    assert settings["max_tokens"] == 16384


def test_union_alpha_slug_agrees_across_both_model_namespaces():
    """`models[]` and `model_catalog[]` carry the same slug, and must move together.

    The two namespaces are merged first-wins **by name** and
    ``alpha.models.catalog_consistency`` only compares *capabilities* — it never
    reads a slug. So renaming the provider slug in `models[].model` and leaving
    `model_catalog.models[].model_id` behind is not a boot-time failure: the
    catalog still advertises a retired model in the Settings picker, and that
    entry synthesises its `ModelConfig` from `model_id`
    (``AppConfig.get_model_by_name``). This is the regression that keeps the two
    namespaces from drifting.
    """
    example_path = Path(__file__).resolve().parents[2] / "config.example.yaml"
    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))

    configured = ModelConfig.model_validate(data["models"][0])
    assert configured.name == "union-alpha"

    catalog = data["model_catalog"]
    assert catalog, "config.example.yaml must declare the bring-your-own-provider catalog"
    entries = [entry for provider in catalog for entry in provider.get("models", []) if entry.get("id") == configured.name]
    assert len(entries) == 1, f"expected exactly one catalog entry for {configured.name!r}, found {len(entries)}"
    assert entries[0]["model_id"] == configured.model, f"catalog slug {entries[0]['model_id']!r} disagrees with models[].model {configured.model!r} for {configured.name!r}; the provider slug must be updated in both namespaces at once"
