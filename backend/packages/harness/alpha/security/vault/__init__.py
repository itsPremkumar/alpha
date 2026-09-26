"""The password-blind credential vault.

The guarantee this package exists to provide: **the agent can use a credential
it can never read.**  That is a stronger property than sandboxing what a
downloaded skill may touch, because the secret never enters the model's reach
at all - not in a tool result, not in an error message, not in a log line, not
in a truncated frame, not in an exception repr.

The mechanism is a split between two sides:

* the **handle side** (everything the model can see) carries an opaque,
  unguessable identifier, the operation it is scoped to, the target it is scoped
  to, and an expiry.  Nothing else.
* the **secret side** lives only inside a :class:`PointOfUseInjector`, which
  receives the plaintext at the moment of use, puts it where the *target* needs
  it (an HTTP header, a subprocess environment entry), and returns only the
  operation's result.  The plaintext never crosses back.

Two-factor codes follow the same rule from the other direction: a code is
either derived inside the vault from a stored authenticator key, or typed by the
user into the user's own UI.  It is never surfaced to the model and never
accepted over the channel the model controls.
"""

from __future__ import annotations

from .errors import (
    HandleNotFound,
    PolicyOverrideRejected,
    RawSecretRefused,
    ScopeViolation,
    TwoFactorUnavailable,
    VaultError,
)
from .handles import SecretHandle, handle_fingerprint
from .inject import (
    EnvVarInjector,
    HttpBasicAuthInjector,
    HttpHeaderInjector,
    PointOfUseInjector,
    TwoFactorCodeInjector,
)
from .ledger import VaultLedger, VaultLedgerEntry
from .policy import VaultScope
from .store import HandleVault, get_vault
from .totp import Authenticator, UserCodeBroker, provisioning_uri, totp_at

__all__ = [
    "Authenticator",
    "EnvVarInjector",
    "HandleNotFound",
    "HandleVault",
    "HttpBasicAuthInjector",
    "HttpHeaderInjector",
    "PointOfUseInjector",
    "PolicyOverrideRejected",
    "RawSecretRefused",
    "ScopeViolation",
    "SecretHandle",
    "TwoFactorCodeInjector",
    "TwoFactorUnavailable",
    "UserCodeBroker",
    "VaultError",
    "VaultLedger",
    "VaultLedgerEntry",
    "VaultScope",
    "get_vault",
    "handle_fingerprint",
    "provisioning_uri",
    "totp_at",
]
