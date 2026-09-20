"""Unit tests for the Out-of-Band Secure Credential Shield."""

from __future__ import annotations

from alpha.security.credential_vault import SecureCredentialVault
from alpha.tools.builtins.credential_request_tool import request_secure_credential


def test_credential_vault_deposit_and_retrieve():
    vault = SecureCredentialVault()
    thread_id = "test-thread-101"

    assert vault.get_credential(thread_id, "API_KEY") is None
    assert vault.has_credential(thread_id, "API_KEY") is False

    vault.deposit_credential(thread_id, "API_KEY", "sk-secret-12345", ttl_seconds=100.0)

    assert vault.has_credential(thread_id, "API_KEY") is True
    assert vault.get_credential(thread_id, "API_KEY") == "sk-secret-12345"


def test_credential_vault_pending_fulfillment():
    vault = SecureCredentialVault()
    thread_id = "test-thread-102"

    req = vault.request_credential(
        thread_id=thread_id,
        key="DATABASE_URL",
        description="Postgres connection string",
        reason="Run database migration",
    )
    assert req.fulfilled is False

    pending = vault.list_pending(thread_id)
    assert len(pending) == 1
    assert pending[0]["key"] == "DATABASE_URL"

    # Deposit fulfills the request
    vault.deposit_credential(thread_id, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    assert len(vault.list_pending(thread_id)) == 0


def test_credential_vault_environment_injection():
    vault = SecureCredentialVault()
    thread_id = "test-thread-103"

    vault.deposit_credential(thread_id, "CUSTOM_AUTH_TOKEN", "token-xyz-789")

    base_env = {"PATH": "/bin:/usr/bin", "USER": "tester"}
    injected_env = vault.inject_environment(thread_id, base_env)

    assert injected_env["PATH"] == "/bin:/usr/bin"
    assert injected_env["CUSTOM_AUTH_TOKEN"] == "token-xyz-789"


def test_credential_vault_redaction():
    vault = SecureCredentialVault()
    thread_id = "test-thread-104"
    secret = "ghp_ultra_secret_personal_access_token_999"

    vault.deposit_credential(thread_id, "GH_TOKEN", secret)

    sample_log = f"Error connecting to api.github.com with token {secret} - failed with 403"
    redacted = vault.redact_text(sample_log)

    assert secret not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_request_secure_credential_tool():
    res = request_secure_credential.invoke(
        {
            "credential_key": "AWS_SECRET_ACCESS_KEY",
            "description": "AWS Secret Access Key for S3 access",
            "reason": "Uploading build artifacts to private S3 bucket",
            "thread_id": "thread-tool-test",
        }
    )
    assert "[CREDENTIAL_REQUEST_PENDING]" in res
    assert "AWS_SECRET_ACCESS_KEY" in res


def test_credential_vault_redaction_overlapping_secrets():
    vault = SecureCredentialVault()
    # Deposit shorter secret and longer secret containing shorter secret as substring
    vault.deposit_credential("t-sub", "SHORT", "secret_key")
    vault.deposit_credential("t-sub", "LONG", "secret_key_extended_production")

    log_msg = "Access token secret_key_extended_production used for auth."
    redacted = vault.redact_text(log_msg)
    assert "extended_production" not in redacted
    assert "secret_key" not in redacted
    assert "[REDACTED_SECRET]" in redacted
