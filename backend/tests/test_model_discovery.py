"""Regression coverage for provider model discovery.

Providers turn their catalogs over continuously, so the catalog must be fetched
rather than hardcoded — and a fetch must never be able to take down a settings
page, hammer a provider, or become an SSRF primitive. These tests pin the
normalization contract (the three context numbers stay separate, free detection
is computed from prices rather than trusted from a flag) and the operational
bounds (timeout, TTL, negative cache, egress screening, result cap).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from alpha.models import discovery
from alpha.models.discovery import DiscoveredModel, DiscoveryState

# A trimmed but structurally faithful OpenRouter ``/models`` entry.
OPENROUTER_ENTRY: dict[str, Any] = {
    "id": "anthropic/claude-opus-5.5",
    "canonical_slug": "anthropic/claude-opus-5.5-20260921",
    "name": "Anthropic: Claude Opus 5.5",
    "context_length": 1000000,
    "architecture": {"modality": "text+image+file->text", "input_modalities": ["text", "image", "file"]},
    "pricing": {
        "prompt": "0.000004",
        "completion": "0.00002",
        "input_cache_read": "0.0000002",
    },
    "top_provider": {"context_length": 1000000, "max_completion_tokens": 128000, "is_moderated": True},
    "supported_parameters": ["reasoning", "reasoning_effort", "tools", "response_format"],
    "reasoning": {"mandatory": True, "supported_efforts": ["max", "xhigh", "high"], "default_effort": "high"},
}


@pytest.fixture(autouse=True)
def _clear_cache(tmp_path):
    """Point the on-disk cache at tmp_path so runs cannot influence each other."""
    discovery.reset_discovery_cache()
    original = discovery._state_dir
    discovery._state_dir = lambda: tmp_path  # type: ignore[assignment]
    yield
    discovery._state_dir = original  # type: ignore[assignment]
    discovery.reset_discovery_cache()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_openrouter_entry_normalizes() -> None:
    (model,) = discovery._parse_openrouter({"data": [OPENROUTER_ENTRY]})
    assert model.id == "anthropic/claude-opus-5.5"
    assert model.name == "Anthropic: Claude Opus 5.5"
    assert model.context_length == 1000000
    assert model.endpoint_max_completion_tokens == 128000
    assert model.supports_vision() is True
    assert model.supports_thinking() is True
    assert model.reasoning_efforts == ["max", "xhigh", "high"]
    assert model.reasoning_default_effort == "high"
    assert "tools" in model.supported_parameters


def test_three_context_numbers_stay_distinct() -> None:
    """Collapsing these is the documented cause of wrong summarization thresholds."""
    payload = {"data": [dict(OPENROUTER_ENTRY, context_length=200000, top_provider={"context_length": 128000, "max_completion_tokens": 64000})]}
    (model,) = discovery._parse_openrouter(payload)
    assert model.context_length == 200000
    assert model.endpoint_context_length == 128000
    assert model.endpoint_max_completion_tokens == 64000


def test_prices_convert_from_per_token_to_per_million() -> None:
    (model,) = discovery._parse_openrouter({"data": [OPENROUTER_ENTRY]})
    assert model.input_price_per_million == pytest.approx(4.0)
    assert model.output_price_per_million == pytest.approx(20.0)
    assert model.input_cache_read_per_million == pytest.approx(0.2)


def test_free_requires_both_axes_zero() -> None:
    """A free-input/paid-output model is not free, and providers do label those ':free'."""
    free = {"id": "v/free", "pricing": {"prompt": "0", "completion": "0"}}
    half_paid = {"id": "v/half", "pricing": {"prompt": "0", "completion": "0.000001"}}
    models = {m.id: m for m in discovery._parse_openrouter({"data": [free, half_paid]})}
    assert models["v/free"].is_free is True
    assert models["v/half"].is_free is False


def test_minimal_openai_compatible_entry_leaves_unknowns_none() -> None:
    """ "The provider did not say" must stay distinguishable from "the provider said no"."""
    (model,) = discovery._parse_openrouter({"data": [{"id": "local-model"}]})
    assert model.id == "local-model"
    assert model.context_length is None
    assert model.endpoint_context_length is None
    assert model.input_price_per_million is None
    assert model.supports_vision() is False
    assert model.supports_thinking() is False
    assert model.is_free is False


def test_malformed_entries_are_skipped_not_fatal() -> None:
    payload = {"data": [{"id": "good"}, {"no_id": True}, "not-a-dict", None, {"id": "   "}]}
    models = discovery._parse_openrouter(payload)
    assert [m.id for m in models] == ["good"]


def test_missing_data_key_yields_empty_list() -> None:
    assert discovery._parse_openrouter({}) == []
    assert discovery._parse_openrouter({"data": None}) == []


def test_ollama_tags_normalize() -> None:
    payload = {
        "models": [
            {"name": "qwen3:32b", "details": {"families": ["qwen3", "clip"]}},
            {"model": "llama3.1:8b", "details": {"families": ["llama"]}},
            {"details": {}},
        ]
    }
    models = {m.id: m for m in discovery._parse_ollama(payload)}
    assert set(models) == {"qwen3:32b", "llama3.1:8b"}
    assert models["qwen3:32b"].supports_vision() is True
    assert models["llama3.1:8b"].supports_vision() is False
    # A local model costs the operator nothing in tokens.
    assert all(m.is_free for m in models.values())


# ---------------------------------------------------------------------------
# Freshness / caching
# ---------------------------------------------------------------------------


def test_freshness_uses_ttl_for_success_and_shorter_negative_ttl() -> None:
    ok = DiscoveryState(provider="p", ok=True, fetched_at=time.time() - 60)
    assert ok.is_fresh(ttl=3600) is True

    failed = DiscoveryState(provider="p", ok=False, fetched_at=time.time() - 60)
    # A failure is cached, but against the much shorter negative TTL so a down
    # provider is retried on a sane cadence rather than on every request.
    assert failed.is_fresh(ttl=3600) is True
    long_down = DiscoveryState(provider="p", ok=False, fetched_at=time.time() - (discovery.NEGATIVE_TTL_SECONDS + 60))
    assert long_down.is_fresh(ttl=3600) is False


def test_never_fetched_is_never_fresh() -> None:
    assert DiscoveryState(provider="p").is_fresh() is False


def test_unchanged_state_is_served_from_memory_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[str] = []

    def _fake_fetch(provider, base, key=None):
        calls.append(provider)
        return discovery._store(DiscoveryState(provider=provider, ok=True, fetched_at=time.time(), models=[{"id": "m"}]))

    # Isolate the on-disk cache so a persisted state from another test (or an
    # earlier run) cannot satisfy the first call and hide the fetch.
    monkeypatch.setattr(discovery, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(discovery, "fetch_models", _fake_fetch)
    monkeypatch.setattr(discovery, "configured_endpoint", lambda _p: ("https://example.invalid/v1", None))

    discovery.get_state("p")
    discovery.get_state("p")
    assert calls == ["p"]


def test_state_persists_across_cache_reset(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A restart must not re-fetch an unchanged provider."""
    monkeypatch.setattr(discovery, "_state_dir", lambda: tmp_path)
    discovery._store(DiscoveryState(provider="p", ok=True, fetched_at=time.time(), models=[{"id": "m"}]))

    discovery.reset_discovery_cache()
    recovered = discovery._read_state("p")
    assert recovered is not None
    assert [m["id"] for m in recovered.models] == ["m"]


# ---------------------------------------------------------------------------
# Operational bounds
# ---------------------------------------------------------------------------


def test_fetch_timeout_is_bounded() -> None:
    """A slow provider must never hold a Gateway response open indefinitely."""
    assert 0 < discovery.FETCH_TIMEOUT_SECONDS <= 30


def test_result_cap_is_bounded() -> None:
    assert 0 < discovery.MAX_MODELS_PER_PROVIDER <= 10_000


def test_egress_policy_blocks_metadata_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery must not become an SSRF probe against cloud metadata."""
    monkeypatch.setattr(discovery, "_resolve_endpoint", lambda p, b: "http://169.254.169.254/latest/meta-data/")
    state = discovery.fetch_models("openrouter", "https://example.invalid/v1")
    assert state.ok is False
    assert "egress policy" in (state.error or "")


def test_failure_is_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """One unreachable provider must not fail a settings page."""
    import httpx

    def _boom(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(discovery, "_screen", lambda _url: None)
    monkeypatch.setattr(httpx, "get", _boom)
    state = discovery.fetch_models("openrouter", "https://api.openrouter.ai/v1")
    assert state.ok is False
    assert "ConnectError" in (state.error or "")
    assert state.models == []


def test_provider_id_cannot_escape_the_cache_directory() -> None:
    """A hostile id is sanitized into a flat filename, never a traversal."""
    resolved = discovery._state_path("../../escape").resolve()
    assert resolved.parent == discovery._state_dir().resolve()
    assert resolved.name == "escape.json"


def test_unknown_provider_uses_default_adapter() -> None:
    """A gateway with no dedicated row still works via the OpenAI-compatible probe."""
    assert discovery._resolve_endpoint("some_new_gateway", "https://x.invalid/v1/") == "https://x.invalid/v1/models"
    assert discovery._resolve_endpoint("ollama", "http://localhost:11434") == "http://localhost:11434/api/tags"
    assert discovery._resolve_endpoint("litellm", "https://p.invalid") == "https://p.invalid/model/info"


def test_unreadable_cache_file_is_discarded(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(discovery, "_state_dir", lambda: tmp_path)
    (tmp_path / "p.json").write_text("{not json", encoding="utf-8")
    assert discovery._read_state("p") is None


def test_shipped_example_catalog_declares_discoverable_providers() -> None:
    """config.example.yaml must actually contain something to discover.

    This used to call ``load_models_catalog()`` with no argument and so asserted
    against whatever second model file happened to be resolved on the machine
    running the test -- the operator's own file, or nothing at all on a machine
    that had none. It claimed to check the shipped example template while never
    reading it, so it passed for an empty deployment and only became meaningful
    by accident. ``config.example.yaml`` is the only shipped template now, and the
    point of the test (the template must declare discoverable providers) is
    unchanged.
    """
    from pathlib import Path

    import yaml

    example = Path(__file__).resolve().parents[2] / "config.example.yaml"
    doc = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
    entries = doc.get("model_catalog") or []

    assert entries, "config.example.yaml must declare providers under `model_catalog:`"
    assert any(entry.get("base_url") for entry in entries), "at least one provider needs a base_url"
    assert any(entry["id"] in discovery.ADAPTERS for entry in entries), "the example should include a provider with a dedicated discovery adapter"


def test_discovered_model_dict_exposes_derived_capabilities() -> None:
    data = DiscoveredModel(id="m", input_modalities=["image"], reasoning_efforts=["high"]).to_dict()
    assert data["supports_vision"] is True
    assert data["supports_thinking"] is True
