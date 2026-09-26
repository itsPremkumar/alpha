"""Two-factor codes, produced where the model cannot see them.

Two acceptable sources, and only two:

1. a **stored authenticator key**, from which the code is derived *inside the
   vault* and handed straight to the injector.  The model never sees a code and
   never sees the key.
2. the **user's own UI**, through :class:`UserCodeBroker`: the model can *ask*
   for a code, and the operator types it into their own surface.  The model is
   told a code is pending; it is never told the code.

A code the model supplies is refused, because the model controls that channel.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any

from .errors import TwoFactorUnavailable

#: RFC 6238 defaults.  30s step, 6 digits, SHA-1 - the universal interoperable
#: combination every authenticator app supports.
TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6


def generate_authenticator_key() -> str:
    """A fresh base32 authenticator secret.

    Returned **once**, to the operator's enrolment surface, so it can be shown
    as a QR provisioning URI.  It is never returned to the model.
    """
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def provisioning_uri(key: str, *, account: str, issuer: str) -> str:
    """The ``otpauth://`` URI an authenticator app scans.

    Built for the *operator's* UI.  ``account`` and ``issuer`` are labels, never
    secrets.
    """
    from urllib.parse import quote

    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={key}&issuer={quote(issuer)}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP_SECONDS}"


def totp_at(key: str, *, timestamp: float | None = None, step: int = TOTP_STEP_SECONDS) -> str:
    """Derive the current code from a stored key, inside the vault."""
    padded = key + "=" * ((8 - len(key) % 8) % 8)
    secret = base64.b32decode(padded, casefold=True)
    counter = int((time.time() if timestamp is None else timestamp) // step)
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


@dataclass(frozen=True)
class PendingChallenge:
    """A two-factor challenge awaiting the operator.  Carries no code."""

    challenge_id: str
    target: str
    created_at: float
    fulfilled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "target": self.target,
            "created_at": self.created_at,
            "fulfilled": self.fulfilled,
            "status": "awaiting_user" if not self.fulfilled else "fulfilled",
        }


class UserCodeBroker:
    """A one-shot channel from the operator's UI into the vault.

    The model can open a challenge and learn that it is pending.  Only the
    operator can fill it, and the filled code goes to the injector, never back to
    the model.
    """

    def __init__(self, *, ttl_seconds: float = 180.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, PendingChallenge] = {}
        self._codes: dict[str, tuple[float, str]] = {}

    def request(self, *, target: str) -> PendingChallenge:
        challenge = PendingChallenge(
            challenge_id=f"tfa_{secrets.token_urlsafe(12)}",
            target=target,
            created_at=time.time(),
        )
        with self._lock:
            self._pending[challenge.challenge_id] = challenge
        return challenge

    def supply_from_user_ui(self, challenge_id: str, code: str) -> PendingChallenge:
        """Operator-side entry point.  Called by the user's own surface only."""
        with self._lock:
            challenge = self._pending.get(challenge_id)
            if challenge is None:
                raise TwoFactorUnavailable("unknown two-factor challenge", challenge_id=challenge_id)
            if time.time() - challenge.created_at > self.ttl_seconds:
                self._pending.pop(challenge_id, None)
                raise TwoFactorUnavailable("two-factor challenge expired", challenge_id=challenge_id)
            if not (code.isdigit() and len(code) == TOTP_DIGITS):
                raise TwoFactorUnavailable("a two-factor code must be exactly six digits", challenge_id=challenge_id)
            self._codes[challenge_id] = (time.time() + self.ttl_seconds, code)
        return challenge

    def consume(self, challenge_id: str) -> str:
        """Vault-side consumption.  The code goes to the injector from here."""
        with self._lock:
            entry = self._codes.pop(challenge_id, None)
            self._pending.pop(challenge_id, None)
        if entry is None:
            raise TwoFactorUnavailable(
                "no operator-supplied code is available for this challenge; the model cannot supply one",
                challenge_id=challenge_id,
            )
        expires_at, code = entry
        if time.time() > expires_at:
            raise TwoFactorUnavailable("operator-supplied code expired", challenge_id=challenge_id)
        return code

    def status(self, challenge_id: str) -> dict[str, Any]:
        with self._lock:
            challenge = self._pending.get(challenge_id)
        if challenge is None:
            return {"challenge_id": challenge_id, "status": "consumed_or_unknown"}
        return challenge.to_dict()


class Authenticator:
    """Stored authenticator keys, per owner and per target.

    Keys live here, in the vault's process, and never leave it: the only thing
    that leaves is a derived code handed to an injector.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[tuple[str, str], str] = {}

    def enrol(self, *, owner: str, target: str, account: str, issuer: str) -> dict[str, Any]:
        """Create a key and return the operator-facing enrolment material.

        The returned mapping is for the user's UI.  It is not a tool result and
        must not be routed to the model.
        """
        key = generate_authenticator_key()
        with self._lock:
            self._keys[(owner, target)] = key
        return {
            "owner": owner,
            "target": target,
            "provisioning_uri": provisioning_uri(key, account=account, issuer=issuer),
            "digits": TOTP_DIGITS,
            "period_seconds": TOTP_STEP_SECONDS,
            "note": "show this in the operator UI; never to the model",
        }

    def has_key(self, *, owner: str, target: str) -> bool:
        with self._lock:
            return (owner, target) in self._keys

    def code_provider(self, *, owner: str, target: str) -> Any:
        """A callable the injector can use; closes over the key, returns a code."""

        def _provide(*, target: str | None = None, **_: Any) -> str:
            with self._lock:
                key = self._keys.get((owner, target or target))
            if key is None:
                raise TwoFactorUnavailable(
                    "no stored authenticator key for this owner and target",
                    owner=owner,
                    target=target,
                )
            return totp_at(key)

        return _provide
