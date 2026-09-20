import pytest

from alpha.models.failover import (
    ModelEndpointConfig,
    ModelFailoverChain,
)


def test_auth_key_rotation_on_rate_limit():
    primary = ModelEndpointConfig(
        provider="anthropic",
        model_name="claude-3-7-sonnet",
        api_keys=["sk-ant-key-1", "sk-ant-key-2"],
        priority=0,
        cooldown_seconds=30.0,
    )
    chain = ModelFailoverChain([primary])

    # Simulate key 1 failing with 429 RateLimit, key 2 succeeding
    def mock_call(endpoint: ModelEndpointConfig, key: str):
        if key == "sk-ant-key-1":
            raise RuntimeError("HTTP 429: rate limit exceeded")
        return f"Success with {endpoint.model_name} on {key}"

    result = chain.execute(mock_call)
    assert result == "Success with claude-3-7-sonnet on sk-ant-key-2"
    assert len(chain.failover_history) == 1
    assert chain.failover_history[0]["rate_limit"] is True
    assert primary._keys_health["sk-ant-key-1"].is_in_cooldown is True
    assert primary._keys_health["sk-ant-key-2"].is_in_cooldown is False


def test_provider_fallback_chain():
    p1 = ModelEndpointConfig(
        provider="anthropic",
        model_name="claude-3-7-sonnet",
        api_keys=["sk-ant-1"],
        priority=0,
    )
    p2 = ModelEndpointConfig(
        provider="openai",
        model_name="gpt-4o",
        api_keys=["sk-oai-1"],
        priority=1,
    )
    chain = ModelFailoverChain([p1, p2])

    def mock_call(endpoint: ModelEndpointConfig, key: str):
        if endpoint.provider == "anthropic":
            raise RuntimeError("500 Internal Server Error: anthropic overloaded")
        return f"Fallback to {endpoint.provider}/{endpoint.model_name}"

    res = chain.execute(mock_call)
    assert res == "Fallback to openai/gpt-4o"
    assert len(chain.failover_history) == 1
    assert chain.failover_history[0]["provider"] == "anthropic"


def test_all_candidates_exhausted_raises():
    p1 = ModelEndpointConfig(
        provider="local",
        model_name="qwen-coder",
        api_keys=["k1"],
        priority=0,
    )
    chain = ModelFailoverChain([p1])

    with pytest.raises(RuntimeError, match="All failover candidates exhausted"):
        chain.execute(lambda ep, k: (_ for _ in ()).throw(RuntimeError("Connection refused")))
