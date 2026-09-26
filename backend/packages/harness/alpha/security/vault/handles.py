"""The opaque handle: everything the model is allowed to see about a secret.

A :class:`SecretHandle` is deliberately impoverished.  It has a random
identifier, the one operation it authorises, the one target it authorises, an
expiry, and a non-reversible fingerprint for logs.  It has no field, no
property, no ``__dict__`` entry and no ``__repr__`` fragment that could hold,
derive or leak the secret it stands for.

``dataclasses`` is avoided on purpose: a generated ``__init__`` with a
``secret`` parameter would be one refactor away from accepting a secret into an
object the model can print.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

_HANDLE_PREFIX = "vaultref"
_HANDLE_BYTES = 24


def _mint() -> str:
    return f"{_HANDLE_PREFIX}_{secrets.token_urlsafe(_HANDLE_BYTES)}"


class SecretHandle:
    """An opaque, unforgeable reference to a secret held by the vault."""

    __slots__ = ("_id", "_operation", "_target", "_owner", "_expires_at", "_label", "_fingerprint")

    def __init__(
        self,
        *,
        operation: str,
        target: str,
        owner: str,
        expires_at: float,
        label: str = "",
        identifier: str | None = None,
    ) -> None:
        object.__setattr__(self, "_id", identifier or _mint())
        object.__setattr__(self, "_operation", operation)
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_expires_at", float(expires_at))
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_fingerprint", handle_fingerprint(self._id))

    # -- identity ----------------------------------------------------------
    @property
    def id(self) -> str:
        """The opaque identifier.  Safe to log, safe to show the model."""
        return self._id

    @property
    def fingerprint(self) -> str:
        """A short, non-reversible tag for logs and ledger rows."""
        return self._fingerprint

    # -- scope -------------------------------------------------------------
    @property
    def operation(self) -> str:
        return self._operation

    @property
    def target(self) -> str:
        return self._target

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def expires_at(self) -> float:
        return self._expires_at

    @property
    def label(self) -> str:
        """A non-secret human label chosen by the operator."""
        return self._label

    # -- everything a model can see ----------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self._id,
            "fingerprint": self._fingerprint,
            "operation": self._operation,
            "target": self._target,
            "owner": self._owner,
            "expires_at": self._expires_at,
            "label": self._label,
        }

    def __repr__(self) -> str:
        return f"SecretHandle({self._fingerprint} op={self._operation!r} target={self._target!r})"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretHandle):
            return NotImplemented
        return hmac.compare_digest(self._id, other._id)

    def __hash__(self) -> int:
        return hash(self._id)

    def __reduce__(self):
        raise TypeError("SecretHandle is not serialisable: a pickled handle would outlive the vault entry it points at and travel through channels that have no business holding a credential reference")

    def __getstate__(self):
        raise TypeError("SecretHandle cannot be pickled or copied")

    def __setattr__(self, name: str, value: Any) -> None:  # pragma: no cover - defensive
        raise AttributeError("SecretHandle is immutable")

    def __delattr__(self, name: str) -> None:  # pragma: no cover - defensive
        raise AttributeError("SecretHandle is immutable")


def handle_fingerprint(identifier: str) -> str:
    """A short, one-way tag for a handle id.

    Keyed with a per-process salt so a fingerprint cannot be correlated across
    restarts, and truncated so it cannot be used as a handle.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.sha256(salt + identifier.encode("utf-8")).hexdigest()
    return f"fp_{digest[:12]}"
