"""Bring-your-own-model egress policy, credential storage and endpoint schema.

Covers the three properties a BYO endpoint must have to be safe:

* an endpoint that points model egress at cloud metadata, loopback (outside the
  declared local tier), or an internal RFC 1918 host is **refused** before it
  can be persisted, by the shared SSRF guard in ``alpha.community.url_safety``
  (reached through ``configure_provider`` and the Gateway route);
* provider keys are stored in an encrypted envelope, and a pre-existing
  plaintext file is migrated (on first read and on demand);
* the configuration schema accepts ``api_key_env`` / ``kind`` / ``headers`` so a
  key can come from the environment, a local endpoint can be declared, and
  extra request headers can be supplied.

Everything here is offline: endpoints are IP literals, reserved names, or a
DNS name resolved through an injected resolver, so no test touches the network
or a real provider.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.community.url_safety import (
    ModelEndpointBlockedError,
    assert_model_endpoint_url,
    classify_model_endpoint_host,
    redact_url_userinfo,
    validate_model_endpoint_url,
)
from alpha.models import provider_manager
from alpha.models.provider_manager import (
    CREDENTIALS_FILE_NAME,
    configure_provider,
    credentials_storage_status,
    get_providers_catalog,
    load_credentials_file,
    migrate_plaintext_credentials_file,
    resolve_provider_api_key,
    validate_endpoint_headers,
)
from app.gateway.routers import models as models_router

# Endpoints that must never be reachable through a model client, in any tier.
METADATA_ENDPOINTS = [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "https://169.254.169.254/computeMetadata/v1/",
    "http://100.100.100.200/latest/meta-data/",
    "http://192.0.0.192/opc/v2/instance/",
    "http://[fd00:ec2::254]/latest/meta-data/",
    "http://[::ffff:169.254.169.254]/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://metadata.internal/latest/meta-data/",
]

# Internal/loopback endpoints refused for a provider on the default remote tier.
LOOPBACK_ENDPOINTS = [
    "http://127.0.0.1:11434/v1",
    "http://127.5.5.5:8000/v1",
    "http://[::1]:8080/v1",
    "http://localhost:11434/v1",
    "http://ollama.localhost:11434/v1",
    "http://0.0.0.0:8000/v1",
]

# Internal endpoints refused even for a declared local endpoint, unless the
# operator explicitly allowlisted the host.
PRIVATE_ENDPOINTS = [
    "http://10.1.2.3:8080/v1",
    "http://172.16.5.4/v1",
    "http://192.168.1.50:8000/v1",
    "http://[fd00::1]/v1",
    "http://[::ffff:10.0.0.1]/v1",
    "http://100.64.1.7/v1",
    "http://ollama.box.local:11434/v1",
]

PROVIDERS = ["openrouter", "openai", "groq", "custom", "gemini"]


@pytest.fixture(autouse=True)
def _isolated_runtime_home(tmp_path, monkeypatch):
    """Every test gets its own runtime home so no real credential store is touched."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.delenv(provider_manager.PRIVATE_HOST_ALLOWLIST_ENV, raising=False)
    for env in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "CUSTOM_LLM_API_KEY", "ALPHA_TEST_PROVIDER_KEY"):
        monkeypatch.delenv(env, raising=False)
    return tmp_path


def _credentials_file(tmp_path) -> Path:
    return tmp_path / "runtime-home" / "models" / CREDENTIALS_FILE_NAME


def _write_legacy_plaintext(tmp_path, payload: dict) -> None:
    path = _credentials_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _resolve_to(*addresses: str):
    """Resolver stub standing in for DNS (``url_safety`` calls it with the host)."""
    resolved = [ipaddress.ip_address(a) for a in addresses]
    return lambda _hostname: list(resolved)


# ---------------------------------------------------------------------------
# Metadata endpoints: refused in every tier, for every provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", METADATA_ENDPOINTS)
@pytest.mark.parametrize("kind", [None, "remote", "local"])
def test_metadata_endpoint_is_refused_in_every_tier(url, kind, tmp_path):
    assert validate_model_endpoint_url(url) is not None
    assert validate_model_endpoint_url(url, allow_loopback=True) is not None
    if kind is not None:
        with pytest.raises(ModelEndpointBlockedError):
            configure_provider(provider_id="openrouter", api_key="sk-test-value-123456", base_url=url, kind=kind)


@pytest.mark.parametrize("provider_id", PROVIDERS)
def test_configure_provider_refuses_metadata_endpoint_and_persists_nothing(provider_id, tmp_path):
    with pytest.raises(ModelEndpointBlockedError) as exc_info:
        configure_provider(provider_id=provider_id, api_key="sk-test-value-123456", base_url="http://169.254.169.254/latest/meta-data/")
    assert "metadata" in str(exc_info.value).lower()
    # Fail closed: the refused request must not leave a half-configured provider.
    assert load_credentials_file()["providers"] == {}
    assert not _credentials_file(tmp_path).exists()


def test_metadata_literal_hidden_behind_ipv4_mapped_ipv6_is_refused():
    assert "metadata" in validate_model_endpoint_url("http://[::ffff:169.254.169.254]/v1").lower()
    assert classify_model_endpoint_host("::ffff:169.254.169.254") == "metadata"


# ---------------------------------------------------------------------------
# Loopback: refused by default, permitted only by the declared local tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", LOOPBACK_ENDPOINTS)
def test_loopback_endpoint_is_refused_for_a_remote_provider(url, tmp_path):
    with pytest.raises(ModelEndpointBlockedError) as exc_info:
        configure_provider(provider_id="groq", api_key="gsk_test_123456", base_url=url)
    assert "loopback" in str(exc_info.value).lower()
    assert load_credentials_file()["providers"] == {}


@pytest.mark.parametrize("url", LOOPBACK_ENDPOINTS)
def test_loopback_endpoint_is_permitted_only_for_a_declared_local_endpoint(url):
    assert validate_model_endpoint_url(url) is not None
    assert validate_model_endpoint_url(url, allow_loopback=True) is None


def test_shipped_local_endpoint_flow_still_works_for_the_custom_provider(tmp_path):
    """``custom`` is the documented local OpenAI-compatible provider."""
    result = configure_provider(
        provider_id="custom",
        model_id="local-llama3",
        display_name="Local LLaMA 3",
        base_url="http://127.0.0.1:11434/v1",
        api_key="ollama",
    )
    assert result["base_url"] == "http://127.0.0.1:11434/v1"
    assert result["kind"] == "local"
    stored = load_credentials_file()["providers"]["custom"]
    assert stored["kind"] == "local"
    assert stored["base_url"] == "http://127.0.0.1:11434/v1"


def test_local_tier_still_cannot_reach_metadata(tmp_path):
    with pytest.raises(ModelEndpointBlockedError):
        configure_provider(
            provider_id="custom",
            model_id="sneaky",
            base_url="http://169.254.169.254/latest/meta-data/",
            kind="local",
        )


def test_unknown_kind_is_rejected_instead_of_defaulting(tmp_path):
    with pytest.raises(ValueError, match="Unknown provider kind"):
        configure_provider(provider_id="groq", api_key="gsk_test_123456", base_url="https://api.groq.com/openai/v1", kind="sideways")


# ---------------------------------------------------------------------------
# Private hosts: refused in both tiers, allowlistable by the operator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", PRIVATE_ENDPOINTS)
def test_private_endpoint_is_refused_in_both_tiers(url):
    assert validate_model_endpoint_url(url) is not None
    assert validate_model_endpoint_url(url, allow_loopback=True) is not None


def test_operator_allowlist_permits_a_private_host_but_never_metadata(tmp_path, monkeypatch):
    allowlist = ["10.1.2.3", "192.168.1.50"]
    assert validate_model_endpoint_url("http://10.1.2.3:8080/v1", allow_loopback=True, allowed_private_hosts=allowlist) is None
    assert validate_model_endpoint_url("http://192.168.1.50:8000/v1", allowed_private_hosts=allowlist) is None
    # Not allowlisted.
    assert validate_model_endpoint_url("http://172.16.5.4/v1", allow_loopback=True, allowed_private_hosts=allowlist) is not None
    # An allowlist can never lift the metadata refusal.
    assert "metadata" in validate_model_endpoint_url("http://169.254.169.254/v1", allow_loopback=True, allowed_private_hosts=allowlist).lower()

    # The allowlist is read from the environment by the provider manager.
    monkeypatch.setenv(provider_manager.PRIVATE_HOST_ALLOWLIST_ENV, " 10.1.2.3 , 192.168.1.50 ")
    assert provider_manager.private_host_allowlist() == ("10.1.2.3", "192.168.1.50")
    result = configure_provider(
        provider_id="custom",
        api_key="ollama",
        base_url="http://10.1.2.3:8080/v1",
        kind="local",
    )
    assert result["base_url"] == "http://10.1.2.3:8080/v1"
    with pytest.raises(ModelEndpointBlockedError):
        configure_provider(provider_id="custom", api_key="ollama", base_url="http://172.16.5.4/v1", kind="local")


# ---------------------------------------------------------------------------
# DNS-backed screens (injected resolver, no network)
# ---------------------------------------------------------------------------


def test_dns_name_resolving_to_a_private_address_is_refused():
    error = validate_model_endpoint_url("https://gateway.example.com/v1", resolver=_resolve_to("10.0.0.9"))
    assert error is not None and "private" in error.lower()


def test_dns_name_resolving_to_metadata_is_refused_even_with_the_local_tier():
    error = validate_model_endpoint_url("https://sneaky.example.com/v1", allow_loopback=True, resolver=_resolve_to("169.254.169.254"))
    assert error is not None and "metadata" in error.lower()


def test_dns_name_resolving_to_public_is_accepted():
    assert validate_model_endpoint_url("https://gateway.example.com/v1", resolver=_resolve_to("93.184.216.34")) is None


def test_unresolvable_host_fails_closed():
    assert validate_model_endpoint_url("https://nowhere.invalid/v1", resolver=lambda _h: []) is not None
    with pytest.raises(ModelEndpointBlockedError):
        configure_provider(provider_id="groq", api_key="gsk_test_123456", base_url="https://nowhere.invalid/v1")


def test_offline_screen_skips_dns_but_still_catches_literals(tmp_path):
    """The read-path screen must not resolve names, yet still refuse literals."""
    assert validate_model_endpoint_url("https://gateway.example.com/v1", resolve_dns=False) is None
    assert validate_model_endpoint_url("http://169.254.169.254/v1", resolve_dns=False) is not None
    assert validate_model_endpoint_url("http://10.0.0.9/v1", resolve_dns=False) is not None


def test_public_literal_endpoint_is_accepted(tmp_path):
    result = configure_provider(provider_id="groq", api_key="gsk_test_123456", base_url="https://93.184.216.34/openai/v1")
    assert result["base_url"] == "https://93.184.216.34/openai/v1"
    assert result["kind"] == "remote"


# ---------------------------------------------------------------------------
# Scheme / credential hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://ftp.example.com/v1", "gopher://example.com", "not-a-url", ""])
def test_non_http_endpoint_is_refused(url):
    assert validate_model_endpoint_url(url) is not None


def test_endpoint_url_may_not_embed_credentials():
    error = validate_model_endpoint_url("https://user:sk-secret-value@evil.example.com/v1")
    assert error is not None and "credential" in error.lower()
    with pytest.raises(ModelEndpointBlockedError) as exc_info:
        configure_provider(provider_id="groq", api_key="gsk_test_123456", base_url="https://user:sk-secret-value@evil.example.com/v1")
    # The refusal text must not carry the embedded secret.
    assert "sk-secret-value" not in str(exc_info.value)
    assert exc_info.value.url == "https://evil.example.com/v1"
    assert redact_url_userinfo("https://user:pw@host/v1") == "https://host/v1"


def test_assert_helper_returns_the_screened_endpoint():
    assert assert_model_endpoint_url("  https://93.184.216.34/v1  ") == "https://93.184.216.34/v1"


# ---------------------------------------------------------------------------
# Persisted endpoints are re-screened on read
# ---------------------------------------------------------------------------


def test_persisted_poisoned_endpoint_is_dropped_on_load(tmp_path, caplog):
    _write_legacy_plaintext(
        tmp_path,
        {
            "providers": {
                "openrouter": {"api_key": "sk-persisted-123456", "base_url": "http://169.254.169.254/latest/meta-data/"},
                "custom": {"api_key": "ollama", "base_url": "http://127.0.0.1:11434/v1"},
            },
            "custom_models": [
                {"id": "sneaky", "model": "sneaky", "provider": "openrouter", "base_url": "http://10.0.0.5/v1", "api_key": "k"}
            ],
        },
    )
    with caplog.at_level("WARNING"):
        data = load_credentials_file()
    assert "base_url" not in data["providers"]["openrouter"]
    assert data["providers"]["custom"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert "base_url" not in data["custom_models"][0]
    assert "Ignoring persisted endpoint" in caplog.text
    assert "169.254.169.254" in caplog.text


# ---------------------------------------------------------------------------
# Credential storage at rest
# ---------------------------------------------------------------------------


def test_provider_key_is_not_stored_in_plaintext(tmp_path):
    secret = "sk-live-looking-value-0123456789"
    configure_provider(provider_id="openrouter", api_key=secret, base_url="https://openrouter.ai/api/v1")

    raw = _credentials_file(tmp_path).read_text(encoding="utf-8")
    assert secret not in raw
    assert CREDENTIALS_FILE_NAME.endswith(".json")
    envelope = json.loads(raw)
    assert envelope["schema"] == provider_manager.CREDENTIALS_SCHEMA
    assert envelope["version"] == provider_manager.CREDENTIALS_VERSION
    assert envelope["encryption"] in {"dpapi-user", "fernet-file", "none"}

    status = credentials_storage_status()
    assert status["encrypted_at_rest"] is True
    assert status["plaintext_on_disk"] is False
    assert status["protection"] in {"dpapi-user", "fernet-file"}
    # Round-trips: the same secret is recoverable in memory for the client.
    assert load_credentials_file()["providers"]["openrouter"]["api_key"] == secret


def test_credential_store_reports_honest_protection_when_nothing_is_written(tmp_path):
    status = credentials_storage_status()
    assert status["exists"] is False
    assert status["encrypted_at_rest"] is False
    assert status["plaintext_on_disk"] is False
    assert status["protection"] == "none"


def test_legacy_plaintext_file_is_migrated_on_first_read(tmp_path):
    secret = "sk-legacy-plaintext-value-1234567890"
    _write_legacy_plaintext(tmp_path, {"providers": {"openrouter": {"api_key": secret, "base_url": "https://openrouter.ai/api/v1"}}, "custom_models": []})

    status = credentials_storage_status()
    assert status["legacy_plaintext_pending_migration"] is True
    assert status["protection"] == "plaintext"

    data = load_credentials_file()  # implicit migration
    assert data["providers"]["openrouter"]["api_key"] == secret

    raw = _credentials_file(tmp_path).read_text(encoding="utf-8")
    assert secret not in raw
    migrated = credentials_storage_status()
    assert migrated["encrypted_at_rest"] is True
    assert migrated["legacy_plaintext_pending_migration"] is False
    # And the migrated file still serves the key.
    assert load_credentials_file()["providers"]["openrouter"]["api_key"] == secret


def test_explicit_migration_is_idempotent(tmp_path):
    _write_legacy_plaintext(tmp_path, {"providers": {"groq": {"api_key": "gsk-legacy-123456789"}}, "custom_models": []})
    assert migrate_plaintext_credentials_file() is True
    first = _credentials_file(tmp_path).read_text(encoding="utf-8")
    assert migrate_plaintext_credentials_file() is False
    assert _credentials_file(tmp_path).read_text(encoding="utf-8") == first
    assert "gsk-legacy-123456789" not in first
    assert load_credentials_file()["providers"]["groq"]["api_key"] == "gsk-legacy-123456789"
    assert migrate_plaintext_credentials_file() is False  # no file to migrate is a no-op


def test_unreadable_envelope_does_not_crash_or_leak(tmp_path, monkeypatch):
    configure_provider(provider_id="openrouter", api_key="sk-test-value-123456", base_url="https://openrouter.ai/api/v1")

    def _boom(_payload: str, _backend: str) -> bytes:
        raise ValueError("simulated wrong-user key")

    monkeypatch.setattr(provider_manager, "_decrypt_payload", _boom)
    assert load_credentials_file() == {"providers": {}, "custom_models": []}


# ---------------------------------------------------------------------------
# Schema: api_key_env / kind / headers
# ---------------------------------------------------------------------------


def test_api_key_env_keeps_the_secret_out_of_the_file_and_the_body(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_TEST_PROVIDER_KEY", "sk-from-the-environment-9876543210")
    result = configure_provider(
        provider_id="openrouter",
        api_key_env="ALPHA_TEST_PROVIDER_KEY",
        base_url="https://openrouter.ai/api/v1",
    )
    assert result["api_key_env"] == "ALPHA_TEST_PROVIDER_KEY"
    assert result["masked_key"] == "sk-f...3210"

    stored = load_credentials_file()["providers"]["openrouter"]
    assert stored["api_key_env"] == "ALPHA_TEST_PROVIDER_KEY"
    assert "api_key" not in stored
    raw = _credentials_file(tmp_path).read_text(encoding="utf-8")
    assert "sk-from-the-environment-9876543210" not in raw
    # Resolved from the environment at use time, and synced to the provider env var.
    assert resolve_provider_api_key(stored) == "sk-from-the-environment-9876543210"
    assert load_credentials_file()["providers"]["openrouter"] == stored
    provider_manager.sync_all_persisted_credentials_to_env()
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "sk-from-the-environment-9876543210"

    entry = next(p for p in get_providers_catalog() if p["id"] == "openrouter")
    assert entry["api_key_env"] == "ALPHA_TEST_PROVIDER_KEY"
    assert entry["configured"] is True
    assert entry["kind"] == "remote"


def test_api_key_env_keeps_the_secret_out_of_a_custom_model_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_TEST_PROVIDER_KEY", "sk-env-custom-model-1234567890")
    configure_provider(
        provider_id="custom",
        api_key_env="ALPHA_TEST_PROVIDER_KEY",
        base_url="http://127.0.0.1:11434/v1",
        model_id="local-llama3",
    )
    stored = load_credentials_file()
    assert stored["providers"]["custom"]["api_key_env"] == "ALPHA_TEST_PROVIDER_KEY"
    model_entry = stored["custom_models"][0]
    assert model_entry["api_key_env"] == "ALPHA_TEST_PROVIDER_KEY"
    assert model_entry["api_key"] == ""
    assert "sk-env-custom-model-1234567890" not in _credentials_file(tmp_path).read_text(encoding="utf-8")
    # The key still reaches the client: it is synced to the provider's env var,
    # which AppConfig falls back to for a custom model with no stored literal.
    provider_manager.sync_all_persisted_credentials_to_env()
    import os

    assert os.environ["CUSTOM_LLM_API_KEY"] == "sk-env-custom-model-1234567890"


def test_api_key_env_must_be_set_and_well_formed(tmp_path):
    with pytest.raises(ValueError, match="is not set in the environment"):
        configure_provider(provider_id="openrouter", api_key_env="ALPHA_TEST_ABSENT_KEY", base_url="https://openrouter.ai/api/v1")
    with pytest.raises(ValueError, match="not a valid environment variable name"):
        configure_provider(provider_id="openrouter", api_key_env="not a name!", base_url="https://openrouter.ai/api/v1")


def test_endpoint_headers_are_accepted_but_framing_headers_are_not():
    cleaned = validate_endpoint_headers({"HTTP-Referer": "https://alpha.example", "X-Title": "Alpha"})
    assert cleaned == {"HTTP-Referer": "https://alpha.example", "X-Title": "Alpha"}
    for forbidden in ("Host", "host", "Content-Length", "Transfer-Encoding", "Connection", "Proxy-Authorization"):
        with pytest.raises(ValueError, match="not allowed"):
            validate_endpoint_headers({forbidden: "x"})
    with pytest.raises(ValueError, match="CR, LF or NUL"):
        validate_endpoint_headers({"X-Bad": "value\r\nInjected: 1"})
    with pytest.raises(ValueError, match="non-empty"):
        validate_endpoint_headers({"  ": "x"})
    with pytest.raises(ValueError, match="must be an object"):
        validate_endpoint_headers(["X-A", "b"])


def test_configured_headers_are_persisted_and_disclosed_as_names_only(tmp_path):
    secret_header = "Bearer internal-gateway-token-value"
    configure_provider(
        provider_id="custom",
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        kind="local",
        headers={"HTTP-Referer": "https://alpha.example", "X-Internal-Auth": secret_header},
    )
    stored = load_credentials_file()["providers"]["custom"]
    assert stored["headers"]["X-Internal-Auth"] == secret_header
    entry = next(p for p in get_providers_catalog() if p["id"] == "custom")
    assert entry["header_names"] == ["HTTP-Referer", "X-Internal-Auth"]
    # Values are never echoed by the catalog.
    assert secret_header not in json.dumps(entry)


# ---------------------------------------------------------------------------
# Gateway route
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(models_router.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:11434/v1", "http://192.168.10.5/v1"])
def test_gateway_configure_route_rejects_internal_endpoints_with_400(url):
    response = _client().post("/api/models/providers/configure", json={"provider": "openrouter", "api_key": "sk-test-123456", "base_url": url})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Model endpoint refused" in detail
    assert "sk-test-123456" not in detail


def test_gateway_configure_route_rejects_bad_headers_and_kind():
    client = _client()
    bad_header = client.post(
        "/api/models/providers/configure",
        json={"provider": "openrouter", "api_key": "sk-test-123456", "base_url": "https://openrouter.ai/api/v1", "headers": {"Host": "evil.example.com"}},
    )
    assert bad_header.status_code == 400
    # An out-of-schema kind never reaches the policy layer at all.
    bad_kind = client.post(
        "/api/models/providers/configure",
        json={"provider": "openrouter", "api_key": "sk-test-123456", "base_url": "https://openrouter.ai/api/v1", "kind": "sideways"},
    )
    assert bad_kind.status_code == 422


def test_gateway_configure_route_accepts_public_endpoint_with_env_key(monkeypatch):
    monkeypatch.setenv("ALPHA_TEST_PROVIDER_KEY", "sk-route-env-value-1234567890")
    response = _client().post(
        "/api/models/providers/configure",
        json={"provider": "openrouter", "api_key_env": "ALPHA_TEST_PROVIDER_KEY", "base_url": "https://openrouter.ai/api/v1", "headers": {"X-Title": "Alpha"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["api_key_env"] == "ALPHA_TEST_PROVIDER_KEY"
    assert body["header_names"] == ["X-Title"]
    assert body["kind"] == "remote"


def test_gateway_configure_route_accepts_local_endpoint_for_custom_provider():
    response = _client().post(
        "/api/models/providers/configure",
        json={"provider": "custom", "api_key": "ollama", "base_url": "http://127.0.0.1:11434/v1", "model_id": "local-llama3", "kind": "local"},
    )
    assert response.status_code == 200
    assert response.json()["base_url"] == "http://127.0.0.1:11434/v1"


def test_gateway_credentials_storage_route_discloses_protection(tmp_path):
    _write_legacy_plaintext(tmp_path, {"providers": {"groq": {"api_key": "gsk-legacy-123456789"}}, "custom_models": []})
    response = _client().get("/api/models/providers/credentials-storage")
    assert response.status_code == 200
    body = response.json()
    assert body["migrated_plaintext_on_request"] is True
    assert body["encrypted_at_rest"] is True
    assert body["protection"] in {"dpapi-user", "fernet-file"}
    assert "gsk-legacy-123456789" not in json.dumps(body)
