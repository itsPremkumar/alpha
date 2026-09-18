"""Out-of-Band Secure Credential Vault.

Provides an isolated, in-memory, zero-leak repository for credentials, API tokens,
and secrets requested by autonomous agents. Secrets deposited here are injected
directly into execution environments without ever passing through prompt history,
transcripts, or checkpoint persistence.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CredentialRequest:
    """Represents a pending credential request awaiting human input."""

    thread_id: str
    key: str
    description: str
    reason: str
    created_at: float = field(default_factory=time.time)
    fulfilled: bool = False


@dataclass
class _VaultItem:
    value: str
    expires_at: float


class SecureCredentialVault:
    """Thread-safe, ephemeral in-memory vault for runtime secrets."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # thread_id -> {credential_key -> _VaultItem}
        self._vault: dict[str, dict[str, _VaultItem]] = {}
        # thread_id -> [CredentialRequest]
        self._pending_requests: dict[str, list[CredentialRequest]] = {}
        # Track known raw secret strings for automatic log redaction
        self._known_secrets: set[str] = set()

    def request_credential(
        self,
        thread_id: str,
        key: str,
        description: str,
        reason: str = "",
    ) -> CredentialRequest:
        """Register a requirement for a sensitive credential."""
        with self._lock:
            # Check if active unexpired credential already exists
            existing = self.get_credential(thread_id, key)
            if existing is not None:
                return CredentialRequest(
                    thread_id=thread_id,
                    key=key,
                    description=description,
                    reason=reason,
                    fulfilled=True,
                )

            req = CredentialRequest(
                thread_id=thread_id,
                key=key,
                description=description,
                reason=reason,
            )
            self._pending_requests.setdefault(thread_id, []).append(req)
            return req

    def deposit_credential(
        self,
        thread_id: str,
        key: str,
        value: str,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Securely deposit an out-of-band credential into the thread vault."""
        with self._lock:
            if not value:
                return
            thread_store = self._vault.setdefault(thread_id, {})
            thread_store[key] = _VaultItem(
                value=value,
                expires_at=time.time() + ttl_seconds,
            )
            if len(value) >= 6:
                self._known_secrets.add(value)

            # Mark matching pending requests as fulfilled
            if thread_id in self._pending_requests:
                for req in self._pending_requests[thread_id]:
                    if req.key == key:
                        req.fulfilled = True

    def get_credential(self, thread_id: str, key: str) -> str | None:
        """Retrieve active credential if present and not expired."""
        with self._lock:
            thread_store = self._vault.get(thread_id)
            if not thread_store:
                return None
            item = thread_store.get(key)
            if not item:
                return None
            if time.time() > item.expires_at:
                del thread_store[key]
                return None
            return item.value

    def has_credential(self, thread_id: str, key: str) -> bool:
        """Check whether an active credential exists."""
        return self.get_credential(thread_id, key) is not None

    def list_pending(self, thread_id: str) -> list[dict[str, Any]]:
        """List unfulfilled credential requests for a thread."""
        with self._lock:
            requests = self._pending_requests.get(thread_id, [])
            return [
                {
                    "thread_id": r.thread_id,
                    "key": r.key,
                    "description": r.description,
                    "reason": r.reason,
                    "created_at": r.created_at,
                    "fulfilled": r.fulfilled,
                }
                for r in requests
                if not r.fulfilled
            ]

    def inject_environment(self, thread_id: str, base_env: dict[str, str]) -> dict[str, str]:
        """Inject valid vault credentials into a child process environment copy."""
        with self._lock:
            env = dict(base_env)
            thread_store = self._vault.get(thread_id, {})
            now = time.time()
            for key, item in thread_store.items():
                if now <= item.expires_at:
                    env[key] = item.value
            return env

    def redact_text(self, text: str) -> str:
        """Redact known secret values from raw text streams or logs."""
        if not text:
            return ""
        with self._lock:
            redacted = text
            for secret in sorted(self._known_secrets, key=len, reverse=True):
                if secret in redacted:
                    redacted = redacted.replace(secret, "[REDACTED_SECRET]")
            return redacted

    def clear_thread(self, thread_id: str) -> None:
        """Purge all secrets and requests for a specific thread."""
        with self._lock:
            self._vault.pop(thread_id, None)
            self._pending_requests.pop(thread_id, None)


# Process-wide singleton
_GLOBAL_VAULT = SecureCredentialVault()


def get_credential_vault() -> SecureCredentialVault:
    """Return the global process credential vault instance."""
    return _GLOBAL_VAULT
