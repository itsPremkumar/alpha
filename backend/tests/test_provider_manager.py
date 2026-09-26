"""Tests for provider_manager, credentials persistence, and models endpoints."""

import os

from alpha.config.app_config import AppConfig
from alpha.models.provider_manager import (
    configure_provider,
    get_active_provider_models,
    get_providers_catalog,
    mask_secret,
)


def test_mask_secret():
    assert mask_secret(None) is None
    assert mask_secret("") is None
    assert mask_secret("short") == "****"
    assert mask_secret("sk-1234567890abcdef") == "sk-1...cdef"


def test_providers_catalog_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    catalog = get_providers_catalog()
    assert len(catalog) >= 15

    ids = [p["id"] for p in catalog]
    assert "ovhcloud" in ids
    assert "pollinations" in ids
    assert "llm7" in ids
    assert "gemini" in ids
    assert "groq" in ids
    assert "openrouter" in ids
    assert "sambanova" in ids
    assert "openai" in ids
    assert "anthropic" in ids
    assert "deepseek" in ids

    # Keyless providers are configured by default
    ovh = next(p for p in catalog if p["id"] == "ovhcloud")
    assert ovh["configured"] is True
    assert ovh["category"] == "keyless_free"

    # Gemini starts unconfigured if env is clear
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    catalog2 = get_providers_catalog()
    gemini = next(p for p in catalog2 if p["id"] == "gemini")
    assert gemini["configured"] is False


def test_configure_and_remove_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    # Configure Groq
    res = configure_provider(provider_id="groq", api_key="gsk_test_1234567890")
    assert res["success"] is True
    assert res["configured"] is True
    assert res["masked_key"] == "gsk_...7890"
    assert os.getenv("GROQ_API_KEY") == "gsk_test_1234567890"

    # Catalog shows configured
    catalog = get_providers_catalog()
    groq = next(p for p in catalog if p["id"] == "groq")
    assert groq["configured"] is True

    # Active provider models now contains Groq models
    models = get_active_provider_models()
    groq_models = [m for m in models if m["provider"] == "groq"]
    assert len(groq_models) >= 2

    # Remove Groq
    rem_res = configure_provider(provider_id="groq", remove=True)
    assert rem_res["success"] is True
    assert rem_res["configured"] is False
    assert os.getenv("GROQ_API_KEY") is None


def test_configure_custom_model(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))

    res = configure_provider(
        provider_id="custom",
        model_id="local-llama3",
        display_name="Local LLaMA 3",
        base_url="http://127.0.0.1:11434/v1",
        api_key="ollama",
    )
    assert res["success"] is True

    models = get_active_provider_models()
    custom_m = next((m for m in models if m["name"] == "local-llama3"), None)
    assert custom_m is not None
    assert custom_m["display_name"] == "Local LLaMA 3"


def test_dynamic_model_synthesis_in_app_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_active_key_12345")

    cfg = AppConfig.model_validate(
        {
            "sandbox": {"use": "alpha.sandbox.local:LocalSandbox", "base_dir": str(tmp_path)},
            "models": [],
        }
    )

    # Alpha-free auto router synthesizes
    alpha_free = cfg.get_model_config("alpha-free")
    assert alpha_free is not None
    assert alpha_free.name == "alpha-free"
    assert alpha_free.use == "alpha.models.free_router:ChatFreeLLM"

    # Specific free provider synthesizes
    ovh_llama = cfg.get_model_config("free:ovhcloud:Meta-Llama-3_3-70B-Instruct")
    assert ovh_llama is not None
    assert ovh_llama.model == "Meta-Llama-3_3-70B-Instruct"

    # Provider model from Groq synthesizes with key
    groq_m = cfg.get_model_config("groq-llama-3.3-70b")
    assert groq_m is not None
    assert groq_m.model == "llama-3.3-70b-versatile"
    assert groq_m.api_key == "gsk_active_key_12345"
