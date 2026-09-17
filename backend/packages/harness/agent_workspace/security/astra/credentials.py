from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent_workspace.security.astra.credentials")


@dataclass
class CredentialLease:
    """Ephemeral, time-bounded credential lease with scoped permissions."""
    lease_id: str
    service_name: str
    token_value: str
    allowed_scopes: list[str]
    expires_at: float
    created_at: float = field(default_factory=time.time)
    is_revoked: bool = False

    @property
    def is_valid(self) -> bool:
        return not self.is_revoked and time.time() < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        # Token value is always masked in telemetry and export
        masked = self.token_value[:4] + "..." + self.token_value[-4:] if len(self.token_value) > 8 else "***"
        return {
            "lease_id": self.lease_id,
            "service_name": self.service_name,
            "masked_token": masked,
            "allowed_scopes": self.allowed_scopes,
            "expires_at": self.expires_at,
            "is_valid": self.is_valid,
            "is_revoked": self.is_revoked,
        }


class ScopedCredentialVault:
    """
    Scoped Ephemeral Credential Vault inspired by OpenAI GPT-6 Astra.
    Issues short-lived leases with automatic time-to-live expiration and scope enforcement.
    """

    def __init__(self) -> None:
        self.leases: dict[str, CredentialLease] = {}

    def issue_lease(
        self,
        service_name: str,
        scopes: list[str],
        ttl_seconds: int = 300,
    ) -> CredentialLease:
        lease_id = f"lease_{secrets.token_hex(6)}"
        token_value = f"astra_ephem_{secrets.token_urlsafe(24)}"
        expires_at = time.time() + ttl_seconds

        lease = CredentialLease(
            lease_id=lease_id,
            service_name=service_name,
            token_value=token_value,
            allowed_scopes=scopes,
            expires_at=expires_at,
        )
        self.leases[lease_id] = lease
        logger.info(f"Issued credential lease {lease_id} for {service_name} (TTL: {ttl_seconds}s)")
        return lease

    def authorize(self, lease_id: str, required_scope: str) -> bool:
        """Validates that lease exists, is unexpired, unrevoked, and contains scope."""
        lease = self.leases.get(lease_id)
        if not lease:
            logger.warning(f"CREDENTIAL_REJECTED: Unknown lease '{lease_id}'.")
            return False

        if not lease.is_valid:
            logger.warning(f"CREDENTIAL_REJECTED: Lease '{lease_id}' is expired or revoked.")
            return False

        if required_scope not in lease.allowed_scopes and "*" not in lease.allowed_scopes:
            logger.warning(
                f"CREDENTIAL_REJECTED: Lease '{lease_id}' lacks required scope '{required_scope}'."
            )
            return False

        return True

    def revoke_lease(self, lease_id: str) -> bool:
        lease = self.leases.get(lease_id)
        if lease:
            lease.is_revoked = True
            logger.info(f"Revoked credential lease {lease_id}.")
            return True
        return False


class CredentialRedactor:
    """
    High-performance redaction engine that scrubs secrets, API keys, and passwords
    from prompts, tool outputs, and telemetry logs before transmission.
    """

    PATTERNS = [
        # OpenAI / Anthropic / Mistral / DeepSeek API keys
        r"\b(sk-[a-zA-Z0-9_-]{20,})\b",
        # GitHub Personal Access Tokens
        r"\b(ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{50,})\b",
        # AWS Access Key ID
        r"\b(AKIA[0-9A-Z]{16})\b",
        # Bearer tokens in headers
        r"(Bearer\s+)[a-zA-Z0-9_\-\.]{20,}",
        # Hardcoded password / secret assignment in code or env
        r"(?i)['\"]?(?:password|secret|api_key|token)['\"]?\s*[:=]\s*['\"][^'\"]{6,}['\"]",
    ]

    def __init__(self) -> None:
        self.compiled_regexes = [re.compile(p) for p in self.PATTERNS]

    def redact(self, text: str) -> str:
        if not text:
            return ""

        clean = text
        for rx in self.compiled_regexes:
            clean = rx.sub("[REDACTED_SECRET]", clean)
        return clean
