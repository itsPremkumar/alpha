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
    example_path = Path(__file__).resolve().parents[2] / "config.example.yaml"
    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert data["models"], "The example must provide the Union Alpha model configuration"
    model = ModelConfig.model_validate(data["models"][0])
    assert model.name == "union-alpha"
    assert model.model == "stealth/union-alpha"
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
