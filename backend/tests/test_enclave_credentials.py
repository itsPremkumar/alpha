
from alpha.security.enclave import CredentialRedactor, ScopedCredentialVault


def test_scoped_credential_vault_lease():
    vault = ScopedCredentialVault()

    # Issue lease with 1 second TTL
    lease = vault.issue_lease(service_name="github_api", scopes=["repo:read", "repo:status"], ttl_seconds=1)
    assert lease.lease_id.startswith("lease_")
    assert lease.is_valid is True

    # Check authorization
    assert vault.authorize(lease.lease_id, "repo:read") is True
    assert vault.authorize(lease.lease_id, "repo:write") is False  # Unauthorized scope

    # Revoke lease
    vault.revoke_lease(lease.lease_id)
    assert vault.authorize(lease.lease_id, "repo:read") is False


def test_credential_redactor():
    redactor = CredentialRedactor()

    # Redact OpenAI key
    text_with_key = "Using model with Authorization: Bearer sk-ant-api03-abcdef1234567890abcdef1234567890"
    cleaned = redactor.redact(text_with_key)
    assert "sk-ant-api03-" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned

    # Redact GitHub token
    text_gh = "git clone https://ghp_123456789012345678901234567890123456@github.com/org/repo"
    cleaned_gh = redactor.redact(text_gh)
    assert "ghp_" not in cleaned_gh
    assert "[REDACTED_SECRET]" in cleaned_gh

    # Redact hardcoded password
    text_pwd = "db_config = {'password': 'super_secret_password_123'}"
    cleaned_pwd = redactor.redact(text_pwd)
    assert "super_secret_password_123" not in cleaned_pwd
    assert "[REDACTED_SECRET]" in cleaned_pwd
