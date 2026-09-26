"""Typed, secret-free errors for the vault.

Every message in this module is written so that it *cannot* carry a secret: it
names a handle fingerprint, a scope, or a policy reason, never a value.  That is
deliberate, because an error message is the single most common way a secret
escapes a system that otherwise handles it correctly.
"""

from __future__ import annotations

from typing import Any


class VaultError(RuntimeError):
    """Base class for every vault failure."""

    code = "vault_error"

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = dict(detail)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class HandleNotFound(VaultError):
    """The handle is unknown, expired, revoked, or belongs to another owner."""

    code = "handle_not_found"


class ScopeViolation(VaultError):
    """A handle was used outside the operation or target it is scoped to."""

    code = "scope_violation"


class PolicyOverrideRejected(VaultError):
    """A caller tried to widen vault policy from inside the model channel."""

    code = "policy_override_rejected"


class RawSecretRefused(VaultError):
    """A caller asked to read the secret itself.  Refused, never downgraded."""

    code = "raw_secret_refused"


class TwoFactorUnavailable(VaultError):
    """A two-factor code was requested but cannot be produced safely."""

    code = "two_factor_unavailable"
