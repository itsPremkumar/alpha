"""Regression tests for the local Laya System One transport."""

from __future__ import annotations

import httpx
import pytest

from alpha.config.system_one_config import PROVIDER_LAYA, SystemOneConfig
from alpha.evaluation.system_one_calibration import DecisionRecord, calibration_report
from alpha.models.system_one import BooleanQuestion, ChoiceQuestion, SystemOneClient


def _attach(client: SystemOneClient, handler) -> httpx.AsyncClient:
    fake = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client._get_client = lambda: fake  # type: ignore[method-assign]
    return fake


def test_computer_action_is_opt_in_until_calibration():
    assert SystemOneConfig().enable_computer_action is False
    assert SystemOneConfig(enable_computer_action=True).enable_computer_action is True


def test_laya_provider_defaults_to_the_loopback_server():
    config = SystemOneConfig(provider=PROVIDER_LAYA)

    assert config.base_url == "http://127.0.0.1:8000"
    assert config.model == ""


def test_default_client_follows_hot_reloaded_app_config(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    current = SimpleNamespace(system_one=SystemOneConfig(provider="vercel-gateway", base_url="https://gateway.example/v1", model="jev"))
    monkeypatch.setattr("alpha.config.get_app_config", lambda: current)
    client = SystemOneClient()

    assert client.config.provider == "vercel-gateway"
    current.system_one = SystemOneConfig(provider=PROVIDER_LAYA, base_url="http://127.0.0.1:8000", model="english")
    assert client.config.provider == PROVIDER_LAYA
    assert client._endpoint() == "http://127.0.0.1:8000/v1/systemone"


def test_explicit_client_reload_switches_to_laya_transport():
    client = SystemOneClient(SystemOneConfig(provider="vercel-gateway", api_key="cloud-key"))

    client.reload(SystemOneConfig(provider=PROVIDER_LAYA, base_url="http://127.0.0.1:8000", model="english"))

    assert client._endpoint() == "http://127.0.0.1:8000/v1/systemone"
    assert client._boolean_key() == "noul"
    assert client._resolve_api_key() is None


def test_direct_typesafe_provider_gets_its_own_defaults():
    config = SystemOneConfig(provider="typesafe")

    assert config.base_url == "https://api.typesafe.ai/v1"
    assert config.model == "jev-latest"


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unsupported System One provider"):
        SystemOneConfig(provider="typo-provider")


def test_laya_uses_the_jev_compatible_endpoint_and_noul_vocabulary():
    client = SystemOneClient(
        SystemOneConfig(
            provider=PROVIDER_LAYA,
            base_url="http://127.0.0.1:8000",
            model="english",
        )
    )

    assert client._endpoint() == "http://127.0.0.1:8000/v1/systemone"
    assert client._boolean_key() == "noul"
    assert BooleanQuestion("urgent?").to_payload(client._boolean_key())["type"] == "noul"


def test_laya_does_not_require_an_api_key_for_a_loopback_server():
    client = SystemOneClient(
        SystemOneConfig(
            provider=PROVIDER_LAYA,
            base_url="http://127.0.0.1:8000",
            model="english",
            api_key=None,
        )
    )

    assert client._resolve_api_key() is None
    assert client.is_available() is True


def test_laya_keyless_non_loopback_is_rejected():
    client = SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, base_url="http://192.0.2.10:8000"))

    assert client.is_available() is False


def test_laya_does_not_reuse_a_cloud_gateway_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "cloud-secret")
    client = SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, api_key=None))

    assert client._resolve_api_key() is None


def test_laya_uses_only_its_own_environment_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "cloud-secret")
    monkeypatch.setenv("LAYA_API_KEY", "local-secret")
    client = SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, api_key=None))

    assert client._resolve_api_key() == "local-secret"


def test_laya_ignores_an_explicit_cloud_key_reference(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "cloud-secret")
    client = SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, api_key="$AI_GATEWAY_API_KEY"))

    assert client._resolve_api_key() is None


def test_direct_typesafe_does_not_reuse_a_gateway_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "cloud-secret")
    client = SystemOneClient(SystemOneConfig(provider="typesafe", api_key=None))

    assert client._resolve_api_key() is None


@pytest.mark.asyncio
async def test_laya_abstains_before_http_when_state_exceeds_checkpoint_budget():
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={}, request=_request)

    client = SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, model="english"))
    _attach(client, handler)

    result = await client.evaluate("x" * 12_001, {"q": BooleanQuestion("Is this urgent?")})

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_laya_abstains_before_http_when_serialized_payload_exceeds_budget():
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={}, request=_request)

    client = SystemOneClient(
        SystemOneConfig(
            provider=PROVIDER_LAYA,
            model="english",
            laya_max_state_chars=12_000,
            laya_max_request_chars=2_000,
        )
    )
    _attach(client, handler)
    result = await client.evaluate(
        {"text": "short state"},
        {"q": ChoiceQuestion("Which?", {f"option_{index}": "x" * 1_000 for index in range(20)})},
    )

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_laya_abstains_before_http_when_state_exceeds_budget():
    """The state-char bound must abstain on its own, not only the request bound."""
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={}, request=_request)

    client = SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, model="english", laya_max_state_chars=1_000))
    _attach(client, handler)
    result = await client.evaluate(
        {"text": "x" * 1_000},
        {"q": ChoiceQuestion("Which?", {f"option_{index}": "x" * 1_000 for index in range(20)})},
    )

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_laya_abstains_before_http_for_large_choice_spaces():
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={}, request=_request)

    client = SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, model="english"))
    _attach(client, handler)
    criteria = {f"option_{index}": f"option {index}" for index in range(21)}

    result = await client.evaluate("state", {"q": ChoiceQuestion("Which?", criteria)})

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_laya_validation_error_does_not_open_circuit():
    client = SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, model="english", max_retries=0, circuit_breaker_threshold=1))
    _attach(client, lambda request: httpx.Response(422, json={"detail": "bad question"}, request=request))

    assert await client.evaluate("state", {"q": BooleanQuestion("true?")}) is None
    assert client._consecutive_failures == 0
    assert client.is_available() is True


@pytest.mark.asyncio
async def test_laya_omits_authorization_when_local_server_has_no_key():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(
            200,
            json={
                "model": "laya-rl-agent",
                "answers": {"urgent": {"type": "noul", "noul": 0.91, "confidence": 0.91}},
                "usage": {"input_tokens": 8, "output_tokens": 0},
            },
            request=request,
        )

    client = SystemOneClient(
        SystemOneConfig(
            provider=PROVIDER_LAYA,
            base_url="http://127.0.0.1:8000",
            model="english",
            api_key=None,
        )
    )
    _attach(client, handler)

    result = await client.evaluate("The production service is down.", {"urgent": BooleanQuestion("Is this urgent?")})

    assert result is not None
    assert result.model == "laya-rl-agent"
    assert result.answers["urgent"].boolean == 0.91
    assert seen["authorization"] is None
    assert b'"type":"noul"' in seen["body"]


def test_old_calibration_records_without_provider_remain_readable(tmp_path):
    path = tmp_path / "old.jsonl"
    path.write_text(
        '{"ts": 1, "site": "guardrail", "tier": "read", "question_id": "q", "type": "boolean", "value": 0.9, "confidence": null, "threshold": 0.6, "latency_ms": 1, "model": "jev"}\n',
        encoding="utf-8",
    )

    from alpha.evaluation.system_one_calibration import load_records

    records = load_records(path)

    assert len(records) == 1
    assert records[0].provider == ""


def test_calibration_keeps_laya_separate_from_hosted_jev():
    records = [
        DecisionRecord(
            ts=1.0,
            site="guardrail",
            tier="read",
            question_id="q",
            type="boolean",
            value=0.9,
            confidence=None,
            threshold=0.6,
            latency_ms=20.0,
            model="laya-rl-agent",
            provider=PROVIDER_LAYA,
        ),
        DecisionRecord(
            ts=2.0,
            site="guardrail",
            tier="read",
            question_id="q",
            type="boolean",
            value=0.9,
            confidence=None,
            threshold=0.6,
            latency_ms=30.0,
            model="jev-latest",
            provider="typesafe",
        ),
    ]

    report = calibration_report(records)

    assert report.by_provider[PROVIDER_LAYA]["count"] == 1
    assert report.by_provider["typesafe"]["count"] == 1


@pytest.mark.asyncio
async def test_laya_sends_bearer_token_when_one_is_configured():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"answers": {"team": {"type": "choice", "choice": "billing", "confidence": 0.95}}},
            request=request,
        )

    client = SystemOneClient(
        SystemOneConfig(
            provider=PROVIDER_LAYA,
            base_url="http://127.0.0.1:8000",
            model="english",
            api_key="local-secret",
        )
    )
    _attach(client, handler)

    result = await client.evaluate("I need a refund.", {"team": ChoiceQuestion("Which team?", {"billing": "Refunds"})})

    assert result is not None
    assert seen["authorization"] == "Bearer local-secret"
