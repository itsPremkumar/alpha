"""The Alpha-to-Alpha connection string: one copyable, pasteable invite.

Why this module exists
----------------------
Pairing an Alpha used to mean copying a bare pairing code and typing a URL into
a second field, in the right order, on the other machine.  That is two copy
operations and two pastes, which is exactly the friction that makes a peer
network feel like work.

This module owns a single URI grammar that carries the whole connection
description, so the operator copies one string and the other side pastes one
string:

.. code-block:: text

    alpha://connect?v=1&a=<agent_id>&u=<url>&w=<ws_url>&n=<name>&e=<expiry>&ep=<epoch>&k=<pairing_code>

Five properties are load-bearing and each one is a decision, not an
implementation detail.

**1. Two copy modes, because "easy to share" and "safe to share" differ.**
``k=`` carries the pairing code, which is a *bearer credential*: whoever holds
it can pair.  ``build_invite(include_secret=False)`` omits it and yields an
address that is public metadata and safe to post anywhere.  The default is the
full invite because the common case is a private handoff between two people who
already know each other; the UI labels which one it copied.

**2. An invite expires.**  ``e=`` is an absolute epoch second and a claim older
than ``DEFAULT_TTL_SECONDS`` is refused.  A QR code shown on a laptop screen and
a code read aloud over a phone are both slow paths, so the default is 15 minutes
— long enough for a human handoff, short enough that a screenshot buried in a
chat log stops working on its own.  Clock skew between two laptops is real, so
refusal is ``now > expiry + CLOCK_SKEW_TOLERANCE_SECONDS`` rather than a bare
comparison.

**3. An invite is single use, enforced by the *issuer*.**  ``ep=`` is a
monotonically increasing per-installation counter.  This is the only mechanism
that actually works, and it is worth recording why the obvious one does not.

The intuitive approach — "rotate the pairing code when the invite is redeemed" —
does **not** work.  Redemption happens on the *redeemer's* machine, while the
credential that was actually exposed is the *issuer's* code, and the redeemer has
no authority to invalidate it.  Rotating the redeemer's own code would protect
something nobody exposed and leave the leaked string working.

So single-use is enforced where the exposure is.  ``accept_pair`` on the issuer
records the highest ``ep`` it has consumed and refuses any attempt whose epoch is
not strictly greater: the first redeemer wins, and the same screenshot replayed
afterwards is refused with a message that says it was already used.  A pairing
carrying no epoch — the classic manual code entry — bypasses the check entirely,
so that path is unchanged.

**4. Parsing is strict, because this is the one parser in the plane that
consumes attacker-controlled text.**  An unknown ``v`` is *refused* rather than
guessed, every field is length-bounded before use, and both endpoints go through
the existing :func:`validate_endpoint` SSRF/metadata/scheme guard — the same
guard the transport uses, so an invite cannot smuggle in an endpoint the
transport would have rejected.  Every refusal raises :class:`InviteError` naming
the offending field, because "invalid invite" tells an operator nothing about
which of seven fields to fix.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from .models import validate_agent_id
from .transport import PeerTransportError, validate_endpoint

INVITE_SCHEME = "alpha"
INVITE_HOST = "connect"
INVITE_VERSION = "1"
#: The literal a paste handler matches on before parsing anything. Exported so the
#: cross-language parity test can assert the TypeScript mirror agrees, since a
#: drift here makes every paste fall through as ordinary text.
INVITE_PREFIX_HINT = f"{INVITE_SCHEME}://{INVITE_HOST}"

#: How long a freshly minted invite stays redeemable.
DEFAULT_TTL_SECONDS = 900  # 15 minutes
#: A ceiling, not a clamp target: an over-long request is refused with the bound
#: named so the operator learns the limit instead of silently getting 24h.
MAX_TTL_SECONDS = 86_400  # 24 hours
#: Two laptops can disagree about the clock.  Refusing an invite that expired
#: five minutes ago on a peer whose clock runs slow would be a support ticket,
#: not a security control.
CLOCK_SKEW_TOLERANCE_SECONDS = 300

MAX_INVITE_LENGTH = 4096
MAX_NAME_LENGTH = 120

_FIELD_LABELS = {
    "v": "version",
    "a": "agent id",
    "u": "gateway address",
    "w": "websocket address",
    "n": "display name",
    "e": "expiry",
    "ep": "invite number",
    "k": "pairing code",
}


class InviteError(ValueError):
    """An invite string could not be parsed, or is no longer redeemable.

    Always names the offending field: this string is shown verbatim to an
    operator holding a string they pasted from a chat window.
    """


def _fail(field: str, reason: str) -> InviteError:
    return InviteError(f"{_FIELD_LABELS.get(field, field)}: {reason}")


@dataclass(frozen=True, slots=True)
class InviteClaims:
    """The parsed contents of one connection string.

    ``pairing_code`` is populated only for a full invite.  An address-only invite
    parses into a claim with ``pairing_code is None`` and is *not* redeemable —
    :meth:`require_secret` refuses it with a message that says why, rather than
    letting it fail later as an authentication error.
    """

    agent_id: str
    url: str
    websocket_url: str | None = None
    name: str | None = None
    expires_at: int | None = None
    #: The issuer's monotonic invite counter. Strictly-greater comparison on the
    #: issuer side is what makes a shared string single-use; see the module
    #: docstring for why rotating the code would not have achieved that.
    epoch: int | None = None
    pairing_code: str | None = None

    @property
    def has_secret(self) -> bool:
        return bool(self.pairing_code)

    def require_secret(self) -> str:
        """Return the pairing code, or refuse because this invite lacks one."""

        if not self.pairing_code:
            raise InviteError("This is an address-only invite and carries no pairing code. Ask your friend for a full invite, or enter their pairing code manually.")
        return self.pairing_code

    def is_expired(self, *, now: float | None = None) -> bool:
        """Whether the claim is past its expiry, allowing for clock skew.

        A claim with no expiry never expires on its own; the service still
        refuses it, because an invite minted by a build that predates the
        ``e=`` field is exactly the kind of stale artifact that should not be
        redeemed silently.
        """

        if self.expires_at is None:
            return False
        current = time.time() if now is None else now
        return current > (self.expires_at + CLOCK_SKEW_TOLERANCE_SECONDS)

    def to_uri(self) -> str:
        return _encode(self)

    def summary(self) -> dict[str, object]:
        """A non-secret projection for the paste-preview card.

        Deliberately excludes ``pairing_code``: this is what the UI renders back
        to the operator to confirm they pasted the right thing, so it must be
        safe to render in a region that also gets screenshotted for support.
        """

        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "url": self.url,
            "websocket_url": self.websocket_url,
            "expires_at": self.expires_at,
            "epoch": self.epoch,
            "has_pairing_code": self.has_secret,
        }


def _encode(claims: InviteClaims) -> str:
    params: list[tuple[str, str]] = [
        ("v", INVITE_VERSION),
        ("a", claims.agent_id),
        ("u", claims.url),
    ]
    if claims.websocket_url:
        params.append(("w", claims.websocket_url))
    if claims.name:
        params.append(("n", claims.name))
    if claims.expires_at is not None:
        params.append(("e", str(int(claims.expires_at))))
    if claims.epoch is not None:
        params.append(("ep", str(int(claims.epoch))))
    if claims.pairing_code:
        params.append(("k", claims.pairing_code))
    # `alpha://connect?…` — urlsplit reports netloc="connect", so the host is
    # part of the grammar rather than a placeholder.
    return f"{INVITE_SCHEME}://{INVITE_HOST}?{urlencode(params, quote_via=quote)}"


def build_invite(
    *,
    agent_id: str,
    url: str,
    websocket_url: str | None = None,
    name: str | None = None,
    include_secret: bool = True,
    pairing_code: str | None = None,
    epoch: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Mint one connection string for this installation.

    ``ttl_seconds`` above :data:`MAX_TTL_SECONDS` is **refused with the bound
    named**, never clamped — the same rule the rest of the plane follows for
    retention and participants, because a silently clamped limit reads to the
    operator as "that is how long invites last".
    """

    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
        raise InviteError(f"ttl_seconds: must be a whole number of seconds, got {type(ttl_seconds).__name__}")
    if ttl_seconds <= 0:
        raise InviteError(f"ttl_seconds: must be positive, got {ttl_seconds}")
    if ttl_seconds > MAX_TTL_SECONDS:
        raise InviteError(f"ttl_seconds: must be at most {MAX_TTL_SECONDS} seconds (24 hours), got {ttl_seconds}")

    try:
        agent_id = validate_agent_id(agent_id)
    except ValueError as exc:
        raise _fail("a", str(exc)) from exc

    try:
        url = validate_endpoint(url, allowed_schemes=("http", "https"))
    except PeerTransportError as exc:
        raise _fail("u", str(exc)) from exc

    if websocket_url:
        try:
            websocket_url = validate_endpoint(websocket_url, allowed_schemes=("ws", "wss"))
        except PeerTransportError as exc:
            raise _fail("w", str(exc)) from exc

    if name:
        name = name.strip()
        if len(name) > MAX_NAME_LENGTH:
            raise _fail("n", f"must be at most {MAX_NAME_LENGTH} characters, got {len(name)}")

    if include_secret:
        if not pairing_code:
            raise InviteError("pairing code: a full invite needs one; pass include_secret=False for an address-only invite")
        # Validated at redemption against the peer's own code; bounded here only
        # so a pathological value cannot ride along in a copied string.
        if len(pairing_code) > 256:
            raise _fail("k", f"must be at most 256 characters, got {len(pairing_code)}")
        if epoch is not None and (isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1):
            raise _fail("ep", f"must be a whole number of at least 1, got {epoch!r}")

    current = time.time() if now is None else now
    claims = InviteClaims(
        agent_id=agent_id,
        url=url,
        websocket_url=websocket_url,
        name=name,
        expires_at=int(current) + ttl_seconds,
        epoch=epoch if include_secret else None,
        pairing_code=pairing_code if include_secret else None,
    )
    return claims.to_uri()


def parse_invite(value: str) -> InviteClaims:
    """Parse a connection string, or raise :class:`InviteError` naming the field.

    This is the plane's only parser of fully attacker-controlled text, so it is
    written to fail rather than to repair: an unknown version, a missing
    endpoint, a duplicated field, or an endpoint that fails the SSRF guard are
    all refusals.  Nothing here reaches the network or touches the filesystem.
    """

    if not isinstance(value, str):
        raise InviteError(f"invite: must be text, got {type(value).__name__}")

    text = value.strip()
    if not text:
        raise InviteError("invite: nothing to parse")

    # A pasted invite frequently arrives wrapped in markdown link syntax or
    # quoted by a chat client. Stripping those is a pure convenience at the
    # edges; anything still unparseable below is refused.
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    if text.lower().startswith("alpha://connect") and "?" not in text:
        raise _fail("invite", "the connection string is missing its '?' query part")

    if len(text) > MAX_INVITE_LENGTH:
        raise InviteError(f"invite: longer than {MAX_INVITE_LENGTH} characters")

    parsed = urlsplit(text)
    if parsed.scheme.lower() != INVITE_SCHEME:
        raise InviteError(f"invite: must start with '{INVITE_SCHEME}://', got {parsed.scheme or 'nothing'!r}")
    if parsed.netloc.lower() != INVITE_HOST:
        raise InviteError(f"invite: must use the '{INVITE_SCHEME}://{INVITE_HOST}' form, got '{parsed.netloc}'")

    # parse_qsl keeps duplicates in order so a repeated field can be refused
    # rather than silently resolved to the last occurrence.
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    seen: dict[str, str] = {}
    for key, val in pairs:
        if key in seen:
            raise _fail(key, "appears more than once")
        seen[key] = val

    version = seen.get("v", "")
    if version != INVITE_VERSION:
        raise _fail("v", f"unsupported connection-string version {version or '(missing)'!r}; this Alpha speaks version {INVITE_VERSION}")

    unknown = sorted(set(seen) - set(_FIELD_LABELS))
    if unknown:
        # Refused rather than ignored: an unrecognised field may carry a meaning
        # a newer build depends on, and silently dropping it would produce a
        # connection that looks fine and is not.
        raise InviteError(f"invite: unrecognised field(s) {unknown}; this Alpha understands {sorted(_FIELD_LABELS)}")

    raw_agent_id = seen.get("a", "")
    if not raw_agent_id:
        raise _fail("a", "is required")
    try:
        agent_id = validate_agent_id(raw_agent_id)
    except ValueError as exc:
        raise _fail("a", str(exc)) from exc

    raw_url = seen.get("u", "")
    if not raw_url:
        raise _fail("u", "is required")
    try:
        url = validate_endpoint(raw_url, allowed_schemes=("http", "https"))
    except PeerTransportError as exc:
        raise _fail("u", str(exc)) from exc

    websocket_url = seen.get("w") or None
    if websocket_url:
        try:
            websocket_url = validate_endpoint(websocket_url, allowed_schemes=("ws", "wss"))
        except PeerTransportError as exc:
            raise _fail("w", str(exc)) from exc

    name = seen.get("n") or None
    if name:
        name = name.strip()
        if len(name) > MAX_NAME_LENGTH:
            raise _fail("n", f"must be at most {MAX_NAME_LENGTH} characters, got {len(name)}")

    expires_at: int | None = None
    raw_expiry = seen.get("e", "")
    if raw_expiry:
        try:
            expires_at = int(raw_expiry)
        except ValueError as exc:
            raise _fail("e", f"must be a whole number of seconds since the epoch, got {raw_expiry!r}") from exc
        if expires_at <= 0:
            raise _fail("e", f"must be a positive epoch second, got {expires_at}")

    pairing_code = seen.get("k") or None
    if pairing_code and len(pairing_code) > 256:
        raise _fail("k", f"must be at most 256 characters, got {len(pairing_code)}")

    epoch: int | None = None
    raw_epoch = seen.get("ep", "")
    if raw_epoch:
        try:
            epoch = int(raw_epoch)
        except ValueError as exc:
            raise _fail("ep", f"must be a whole number, got {raw_epoch!r}") from exc
        if epoch < 1:
            raise _fail("ep", f"must be at least 1, got {epoch}")

    return InviteClaims(
        agent_id=agent_id,
        url=url,
        websocket_url=websocket_url,
        name=name,
        expires_at=expires_at,
        epoch=epoch,
        pairing_code=pairing_code,
    )


__all__ = [
    "CLOCK_SKEW_TOLERANCE_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "INVITE_HOST",
    "INVITE_PREFIX_HINT",
    "INVITE_SCHEME",
    "INVITE_VERSION",
    "MAX_TTL_SECONDS",
    "InviteClaims",
    "InviteError",
    "build_invite",
    "parse_invite",
]
