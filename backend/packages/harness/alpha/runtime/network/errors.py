"""Turn a transport failure into a claim about the *link*, not about the work.

The distinction this module exists to make
-----------------------------------------
Almost every provider failure looks like a network failure if you only look at
the exception text: a 503 from a gateway, a DNS failure, a refused socket and a
TLS handshake error all surface as an opaque ``ConnectionError`` subclass. But
they call for completely different runtime responses, and conflating them is how
a provider outage parks every session as "waiting for network".

So a failure is classified on two axes:

* **What broke** -- :class:`NetworkFailureKind`, for diagnostics.
* **Does it prove the link is down** -- :func:`proves_link_unavailable`, which
  is the only question the monitor may act on.

The second axis is where the honesty lives. A ``TIMEOUT`` does *not* prove the
link is down: a saturated link, a cold TLS path, or a slow provider all produce
one, and flipping global state to ``OFFLINE`` on those would park healthy work.
Only DNS failure, connection refused, and explicit unreachable errors are
treated as evidence of a genuinely absent route. ``UNKNOWN`` is evidence of
nothing at all and never downgrades connectivity.

This module is stdlib-only and dependency-free by design: it is imported by
error-classification paths that must stay cheap and must not drag in the
runtime.
"""

from __future__ import annotations

import errno
import socket
import ssl
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "DEFINITIVE_LINK_FAILURE_KINDS",
    "NetworkFailure",
    "NetworkFailureKind",
    "classify_network_error",
    "proves_link_unavailable",
]


class NetworkFailureKind(StrEnum):
    """What actually broke at the transport layer."""

    DNS_FAILURE = "dns_failure"
    CONNECTION_REFUSED = "connection_refused"
    TIMEOUT = "timeout"
    TLS_FAILURE = "tls_failure"
    HOST_UNREACHABLE = "host_unreachable"
    NETWORK_UNREACHABLE = "network_unreachable"
    CONNECTION_RESET = "connection_reset"
    ADDRESS_UNREACHABLE = "address_unreachable"
    ADDRESS_IN_USE = "address_in_use"
    BROKEN_PIPE = "broken_pipe"
    UNKNOWN = "unknown"


#: Kinds that are genuine evidence of an absent route. Everything else is a
#: signal about *something*, and must not move global connectivity state.
DEFINITIVE_LINK_FAILURE_KINDS: Final[frozenset[NetworkFailureKind]] = frozenset(
    {
        NetworkFailureKind.DNS_FAILURE,
        NetworkFailureKind.CONNECTION_REFUSED,
        NetworkFailureKind.HOST_UNREACHABLE,
        NetworkFailureKind.NETWORK_UNREACHABLE,
        NetworkFailureKind.ADDRESS_UNREACHABLE,
    }
)

# errno -> kind. Only definite route absences and unambiguous transport errors
# appear here; an unmapped errno classifies as UNKNOWN rather than guessing.
_ERRNO_KINDS: Final[dict[int, NetworkFailureKind]] = {
    errno.ECONNREFUSED: NetworkFailureKind.CONNECTION_REFUSED,
    errno.ENETUNREACH: NetworkFailureKind.NETWORK_UNREACHABLE,
    errno.ENETDOWN: NetworkFailureKind.NETWORK_UNREACHABLE,
    errno.EHOSTUNREACH: NetworkFailureKind.HOST_UNREACHABLE,
    errno.EHOSTDOWN: NetworkFailureKind.HOST_UNREACHABLE,
    errno.EADDRNOTAVAIL: NetworkFailureKind.ADDRESS_UNREACHABLE,
    errno.EADDRINUSE: NetworkFailureKind.ADDRESS_IN_USE,
    errno.ECONNRESET: NetworkFailureKind.CONNECTION_RESET,
    errno.ECONNABORTED: NetworkFailureKind.CONNECTION_RESET,
    errno.EPIPE: NetworkFailureKind.BROKEN_PIPE,
}

# Exception class names, matched MRO-agnostically. Names rather than isinstance()
# so this module imports nothing heavy and cannot create an import cycle with a
# provider SDK.
_EXCEPTION_KINDS: Final[tuple[tuple[str, NetworkFailureKind], ...]] = (
    ("gaierror", NetworkFailureKind.DNS_FAILURE),
    ("TimeoutError", NetworkFailureKind.TIMEOUT),
    ("ConnectTimeout", NetworkFailureKind.TIMEOUT),
    ("ReadTimeout", NetworkFailureKind.TIMEOUT),
    ("WriteTimeout", NetworkFailureKind.TIMEOUT),
    ("PoolTimeout", NetworkFailureKind.TIMEOUT),
    ("ConnectError", NetworkFailureKind.CONNECTION_REFUSED),
    ("SSLError", NetworkFailureKind.TLS_FAILURE),
    ("SSLCertVerificationError", NetworkFailureKind.TLS_FAILURE),
    ("ProxyError", NetworkFailureKind.CONNECTION_REFUSED),
    ("NewConnectionError", NetworkFailureKind.CONNECTION_REFUSED),
    ("ClosedPoolError", NetworkFailureKind.CONNECTION_REFUSED),
    ("RemoteProtocolError", NetworkFailureKind.CONNECTION_RESET),
)


@dataclass(frozen=True, slots=True)
class NetworkFailure:
    """A classified transport failure.

    ``kind`` describes the mechanism; ``proves_link_down`` is the only field a
    caller may act on to change connectivity state. ``detail`` is a short,
    already-redacted string -- never the raw exception message, which can carry
    a URL with a query token.
    """

    kind: NetworkFailureKind
    proves_link_down: bool
    detail: str = ""
    exception_type: str = ""

    @property
    def is_definitive(self) -> bool:
        """Alias for :attr:`proves_link_down`, spelled for call-site clarity."""
        return self.proves_link_down

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "proves_link_down": self.proves_link_down,
            "detail": self.detail,
            "exception_type": self.exception_type,
        }


def proves_link_unavailable(failure: NetworkFailure) -> bool:
    """Return True only when *failure* is evidence of an absent route."""
    return failure.proves_link_down


def classify_network_error(exc: BaseException) -> NetworkFailure:
    """Classify *exc* (and its cause chain) into a :class:`NetworkFailure`.

    The whole ``__cause__``/``__context__`` chain is walked because provider SDKs
    wrap the transport error several layers deep, and the interesting errno or
    class name is usually on the innermost frame. A cycle-safe walk is used
    because a badly behaved exception graph must not hang the classifier.

    **Precedence runs strongest-first, not outermost-first.** A chain such as
    ``RuntimeError from TimeoutError from gaierror`` is classified as
    ``DNS_FAILURE`` -- a *definitive* signal anywhere in the chain beats a
    non-definitive one, because a name that does not resolve genuinely proves
    the route is unusable while a timeout only suggests it. Reading outermost
    first would report a timeout and therefore refuse to conclude anything,
    throwing away the one piece of real evidence in the chain. A timeout that
    is all there is stays a timeout, and stays non-definitive.
    """
    definitive: NetworkFailureKind | None = None
    first_known: NetworkFailureKind | None = None
    first_detail = ""
    exception_type = type(exc).__name__

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        resolved = _classify_one(current)
        if resolved is not None:
            kind, detail = resolved
            if first_known is None:
                first_known, first_detail = kind, detail
            if kind in DEFINITIVE_LINK_FAILURE_KINDS and definitive is None:
                definitive = kind

        cause = current.__cause__ or current.__context__
        if cause is not None:
            pending.append(cause)
        if current.__context__ is not None and current.__context__ is not cause:
            pending.append(current.__context__)

    kind = definitive or first_known or NetworkFailureKind.UNKNOWN
    detail = first_detail if kind is first_known else _detail_for(definitive)
    return NetworkFailure(
        kind=kind,
        proves_link_down=kind in DEFINITIVE_LINK_FAILURE_KINDS,
        detail=detail,
        exception_type=exception_type,
    )


def _detail_for(kind: NetworkFailureKind | None) -> str:
    """A short, mechanism-only detail string; never the raw exception message."""
    return kind.value if kind is not None else ""


def _classify_one(exc: BaseException) -> tuple[NetworkFailureKind, str] | None:
    """Classify a single exception node, or return None if it says nothing."""
    # An errno-bearing OSError is the strongest signal available.
    err = getattr(exc, "errno", None)
    if isinstance(err, int):
        mapped = _ERRNO_KINDS.get(err)
        if mapped is not None:
            return mapped, f"errno={err}"

    for klass in type(exc).__mro__:
        for name, kind in _EXCEPTION_KINDS:
            if klass.__name__ == name:
                return kind, name

    # A bare socket.gaierror has errno unset on some platforms, so name-match it.
    if isinstance(exc, socket.gaierror):
        return NetworkFailureKind.DNS_FAILURE, "socket.gaierror"
    if isinstance(exc, ssl.SSLError):
        return NetworkFailureKind.TLS_FAILURE, "ssl.SSLError"
    return None
