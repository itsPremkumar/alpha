"""The handle vault itself.

The store holds three things and returns only one of them:

* the plaintext, in ``_secrets`` - reachable only by :meth:`_perform`, which
  hands it straight to an injector and never returns it;
* a scope, in ``_entries``;
* an :class:`~alpha.security.vault.handles.SecretHandle`, which is what every
  public method returns.

There is deliberately no ``get_secret``.  A method that returns plaintext is a
method the model can call, so the method does not exist; asking for one raises
:class:`RawSecretRefused` with a reason instead of silently downgrading to
something weaker.
"""

from __future__ import annotations

import hmac
import threading
import time
from typing import Any

from .errors import (
    HandleNotFound,
    PolicyOverrideRejected,
    RawSecretRefused,
    ScopeViolation,
)
from .handles import SecretHandle
from .inject import PointOfUseInjector
from .ledger import VaultLedger
from .policy import VaultScope

#: Attributes a caller may try to set on a scope to widen it.  Named explicitly
#: so the rejection message can be specific.
UNOVERRIDABLE_POLICY_KEYS: frozenset[str] = frozenset({"operation", "target", "ttl_seconds", "owner", "expires_at", "skip_scope_check"})


class _Entry:
    __slots__ = ("scope", "owner", "expires_at", "label", "revoked", "uses")

    def __init__(self, scope: VaultScope, owner: str, expires_at: float, label: str) -> None:
        self.scope = scope
        self.owner = owner
        self.expires_at = expires_at
        self.label = label
        self.revoked = False
        self.uses = 0


class HandleVault:
    """Stores secrets; hands out handles."""

    def __init__(
        self,
        *,
        ledger: VaultLedger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._lock = threading.RLock()
        self._secrets: dict[str, str] = {}
        self._entries: dict[str, _Entry] = {}
        self.ledger = ledger or VaultLedger()
        self._clock = clock

    # -- deposit -----------------------------------------------------------
    def deposit(
        self,
        secret: str,
        *,
        operation: str,
        target: str,
        owner: str,
        ttl_seconds: float = 300.0,
        label: str = "",
        policy_overrides: dict[str, Any] | None = None,
    ) -> SecretHandle:
        """Store *secret* and return an opaque handle scoped to one use.

        Raises:
            PolicyOverrideRejected: a caller tried to widen the scope from the
                model channel.  Rejected outright, not clamped.
            ValueError: the scope is not least-privilege (see
                :class:`~alpha.security.vault.policy.VaultScope`).
        """
        if policy_overrides:
            bad = sorted(set(policy_overrides) & UNOVERRIDABLE_POLICY_KEYS)
            if bad:
                raise PolicyOverrideRejected(
                    f"vault policy cannot be overridden from the model channel; rejected keys: {bad}. Narrow the request instead.",
                    rejected=bad,
                )
        if not isinstance(secret, str) or not secret:
            raise ValueError("a vault deposit needs a non-empty secret string")
        scope = VaultScope(operation=operation, target=target, ttl_seconds=ttl_seconds)
        expires_at = scope.expires_at(now=self._clock())
        handle = SecretHandle(
            operation=scope.operation,
            target=scope.target,
            owner=owner,
            expires_at=expires_at,
            label=label,
        )
        with self._lock:
            self._secrets[handle.id] = secret
            self._entries[handle.id] = _Entry(scope, owner, expires_at, label)
        return handle

    # -- inspect (safe) ----------------------------------------------------
    def describe(self, handle: SecretHandle | str) -> dict[str, Any]:
        """Everything about a handle except the secret.  Always safe to return."""
        entry, resolved = self._resolve(handle)
        return {
            "handle": resolved.id,
            "fingerprint": resolved.fingerprint,
            "operation": entry.scope.operation,
            "target": entry.scope.target,
            "owner": entry.owner,
            "label": entry.label,
            "expires_at": entry.expires_at,
            "expired": self._clock() > entry.expires_at,
            "revoked": entry.revoked,
            "uses": entry.uses,
        }

    def list_handles(self, *, owner: str | None = None) -> list[dict[str, Any]]:
        """List handles.  Never values, never anything reversible."""
        now = self._clock()
        with self._lock:
            return [
                {
                    "handle": hid,
                    "fingerprint": SecretHandle(
                        operation=e.scope.operation,
                        target=e.scope.target,
                        owner=e.owner,
                        expires_at=e.expires_at,
                        label=e.label,
                        identifier=hid,
                    ).fingerprint,
                    "operation": e.scope.operation,
                    "target": e.scope.target,
                    "owner": e.owner,
                    "label": e.label,
                    "expires_at": e.expires_at,
                    "expired": now > e.expires_at,
                    "uses": e.uses,
                }
                for hid, e in self._entries.items()
                if owner is None or e.owner == owner
            ]

    # -- use ---------------------------------------------------------------
    def use(
        self,
        handle: SecretHandle | str,
        injector: PointOfUseInjector,
        *,
        operation: str,
        target: str,
        on_behalf_of: str | None = None,
        **inject_kwargs: Any,
    ) -> Any:
        """Perform *operation* against *target* with the secret, out of band.

        The plaintext is handed to the injector inside this frame and never
        returned.  Every outcome - success, denial, exception - is a ledger row.

        ``on_behalf_of`` is the identity performing the use.  When it is given
        and does not match the handle's owner the call is refused, so one
        profile cannot spend another profile's grant.  Omitting it is the
        operator-side path (an operator acting out of band); every
        model-initiated use must supply it.
        """
        for key in ("skip_scope_check", "bypass_scope", "override_scope", "force"):
            if key in inject_kwargs:
                self.ledger.record(
                    handle_fingerprint=_fingerprint_of(handle),
                    operation=str(operation),
                    target=str(target),
                    owner=_owner_of(handle),
                    outcome="denied",
                    detail=f"policy_override_attempt:{key}",
                )
                raise PolicyOverrideRejected(
                    f"'{key}' is not a supported argument: vault scope cannot be bypassed from the model channel",
                    rejected=[key],
                )
        entry, resolved = self._resolve(handle)
        if on_behalf_of is not None and not hmac.compare_digest(str(on_behalf_of), str(entry.owner)):
            self.ledger.record(
                handle_fingerprint=resolved.fingerprint,
                operation=operation,
                target=target,
                owner=entry.owner,
                outcome="denied",
                detail="owner_mismatch",
            )
            raise ScopeViolation(
                "this handle belongs to a different owner; a profile may not spend another profile's grant",
                operation=operation,
                target=target,
            )
        permitted, reason = entry.scope.permits(operation, target)
        if not permitted:
            self.ledger.record(
                handle_fingerprint=resolved.fingerprint,
                operation=operation,
                target=target,
                owner=entry.owner,
                outcome="denied",
                detail=reason,
            )
            raise ScopeViolation(reason, operation=operation, target=target)
        if getattr(injector, "operation", None) != operation:
            self.ledger.record(
                handle_fingerprint=resolved.fingerprint,
                operation=operation,
                target=target,
                owner=entry.owner,
                outcome="denied",
                detail=f"injector_operation_mismatch:{getattr(injector, 'operation', None)}",
            )
            raise ScopeViolation(
                f"injector declares operation {getattr(injector, 'operation', None)!r}, which is not the authorised {operation!r}",
                operation=operation,
            )
        with self._lock:
            secret = self._secrets[resolved.id]
        try:
            result = injector.inject(secret, target, **inject_kwargs)
        except Exception as exc:
            self.ledger.record(
                handle_fingerprint=resolved.fingerprint,
                operation=operation,
                target=target,
                owner=entry.owner,
                outcome="error",
                detail=f"{type(exc).__name__}",
            )
            # Re-raise with the plaintext removed.  An injector that fails is
            # exactly where a secret escapes: an HTTP client puts the credential
            # in the URL of its own exception message, and that message is on its
            # way to a log line and a tool result.  The original is kept as
            # ``__cause__`` for a debugger, never rendered into a result.
            raise self._sanitise(exc, secret, resolved, operation, target) from None
        with self._lock:
            live = self._entries.get(resolved.id)
            if live is not None:
                live.uses += 1
        self.ledger.record(
            handle_fingerprint=resolved.fingerprint,
            operation=operation,
            target=target,
            owner=entry.owner,
            outcome="succeeded",
        )
        return result

    # -- revoke ------------------------------------------------------------
    def revoke(self, handle: SecretHandle | str) -> bool:
        entry, resolved = self._resolve(handle)
        with self._lock:
            entry.revoked = True
            self._secrets.pop(resolved.id, None)
        self.ledger.record(
            handle_fingerprint=resolved.fingerprint,
            operation=entry.scope.operation,
            target=entry.scope.target,
            owner=entry.owner,
            outcome="denied",
            detail="revoked",
        )
        return True

    def revoke_owner(self, owner: str) -> int:
        count = 0
        for hid, entry in list(self._entries.items()):
            if entry.owner == owner:
                self.revoke(hid)
                count += 1
        return count

    # -- the method that does not exist ------------------------------------
    def get_secret(self, *_args: Any, **_kwargs: Any) -> str:
        """Always refuses.

        Present only so the refusal has a name and a reason.  A caller that
        reaches for the plaintext gets a typed error, never a value and never a
        silent downgrade to something weaker.
        """
        raise RawSecretRefused(
            "reading the raw secret is not supported: a vault handle authorises one operation on one target and nothing else. Use `use()` with an injector, or ask the operator to act out of band.",
            available_operations=sorted({e.scope.operation for e in self._entries.values()}),
        )

    # A second, differently named door to the same refusal, because a determined
    # caller will try the other obvious name.
    reveal = get_secret
    read_secret = get_secret
    peek = get_secret

    # -- internals ---------------------------------------------------------
    def _sanitise(
        self,
        exc: BaseException,
        secret: str,
        handle: SecretHandle,
        operation: str,
        target: str,
    ) -> Exception:
        """Return an exception of the same type whose text cannot carry the secret.

        Both the message *and* the args are scrubbed, because ``repr(exc)``,
        ``str(exc)`` and a traceback each read a different one.  Nothing here
        ever sees the plaintext except as a search term.
        """
        marker = "[REDACTED:secret]"
        message = str(exc).replace(secret, marker) if secret else str(exc)
        scrubbed_args = tuple((a.replace(secret, marker) if isinstance(a, str) and secret else a) for a in getattr(exc, "args", ()))
        try:
            new = type(exc)(*scrubbed_args) if scrubbed_args else type(exc)(message)
        except Exception:  # noqa: BLE001 - exotic exception constructors
            new = RuntimeError(f"{type(exc).__name__}: {message}")
        new.args = scrubbed_args or (message,)
        if secret:
            new.__dict__ = {k: (v.replace(secret, marker) if isinstance(v, str) else v) for k, v in getattr(exc, "__dict__", {}).items()}
        new.__notes__ = [  # type: ignore[attr-defined]
            f"raised by a vault point-of-use injector for {handle.fingerprint} ({operation} -> {target}); the credential was removed from this message"
        ]
        return new

    def _resolve(self, handle: SecretHandle | str) -> tuple[_Entry, SecretHandle]:
        identifier = handle.id if isinstance(handle, SecretHandle) else str(handle)
        with self._lock:
            entry = self._entries.get(identifier)
        if entry is None:
            raise HandleNotFound(
                "unknown or already-purged vault handle",
                fingerprint=_fingerprint_of(handle),
            )
        if entry.revoked:
            raise HandleNotFound("vault handle was revoked", fingerprint=_fingerprint_of(handle))
        if self._clock() > entry.expires_at:
            with self._lock:
                self._secrets.pop(identifier, None)
                self._entries.pop(identifier, None)
            raise HandleNotFound("vault handle expired; deposit a new one", fingerprint=_fingerprint_of(handle))
        resolved = handle
        if not isinstance(handle, SecretHandle):
            resolved = SecretHandle(
                operation=entry.scope.operation,
                target=entry.scope.target,
                owner=entry.owner,
                expires_at=entry.expires_at,
                label=entry.label,
                identifier=identifier,
            )
        return entry, resolved

    def _secrets_for_tests(self) -> int:
        """Count of stored plaintexts.  Lets a test assert nothing leaked out."""
        with self._lock:
            return len(self._secrets)


def _fingerprint_of(handle: SecretHandle | str) -> str:
    if isinstance(handle, SecretHandle):
        return handle.fingerprint
    from .handles import handle_fingerprint

    return handle_fingerprint(str(handle))


def _owner_of(handle: SecretHandle | str) -> str:
    return handle.owner if isinstance(handle, SecretHandle) else "unknown"


_GLOBAL_VAULT: HandleVault | None = None
_GLOBAL_LOCK = threading.Lock()


def get_vault() -> HandleVault:
    """The process-wide vault."""
    global _GLOBAL_VAULT
    with _GLOBAL_LOCK:
        if _GLOBAL_VAULT is None:
            _GLOBAL_VAULT = HandleVault()
        return _GLOBAL_VAULT


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
