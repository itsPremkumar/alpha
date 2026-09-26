"""Shared URL safety checks for server-side web tools and model egress.

Two layers live here on purpose:

* :func:`validate_public_http_url` - the original web-tool screen, unchanged.
* :func:`validate_model_endpoint_url` - the screen applied to an
  operator-supplied model endpoint (``base_url`` / ``api_base``) before any
  model client may egress to it. It *reuses* the web-tool screen as its
  primary decision and only then applies the two endpoint-specific rules the
  web-tool path never needed: a caller-declared local tier (loopback
  OpenAI-compatible servers such as Ollama/LM Studio) and cloud-metadata
  endpoints, which are denied in every tier because reaching one is
  credential theft rather than a misconfiguration.

Nothing here is model-egress *routing*; it is the screen that decides whether
an endpoint may be configured at all. See
:func:`alpha.models.provider_manager.configure_provider` for the caller.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def resolve_host_addresses(hostname: str) -> list[ipaddress._BaseAddress]:
    """Resolve a hostname to all IP addresses for SSRF screening."""
    addresses: list[ipaddress._BaseAddress] = []
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError):
        return addresses
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    return addresses


def is_blocked_address(address: ipaddress._BaseAddress) -> bool:
    """Return True for addresses web tools should not reach by default."""
    return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified


def validate_public_http_url(
    url: str,
    *,
    allow_private_addresses: bool = False,
    action: str = "fetch",
    resolver: Callable[[str], list[ipaddress._BaseAddress]] | None = None,
) -> str | None:
    """Validate an http(s) URL before a server-side web tool fetches it.

    Returns an ``"Error: ..."`` string when the URL should be rejected, or
    ``None`` when the caller may proceed.  The check is intentionally conservative
    for self-hosted fetch/render services because those services run inside the
    deployment network and can otherwise reach cloud metadata or private hosts.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "Error: Only http:// and https:// URLs are supported"

    if allow_private_addresses:
        return None

    hostname = parsed.hostname
    if not hostname:
        return "Error: URL host could not be parsed"

    normalized_host = hostname.strip().rstrip(".").lower()
    if normalized_host in _BLOCKED_HOSTNAMES:
        return f"Error: Refusing to {action} a private or loopback address"

    try:
        literal_ip = ipaddress.ip_address(normalized_host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        candidates = [literal_ip]
    else:
        resolve = resolver or resolve_host_addresses
        candidates = resolve(hostname)
        if not candidates:
            return "Error: URL host could not be resolved"

    if any(is_blocked_address(addr) for addr in candidates):
        return f"Error: Refusing to {action} a private, loopback, or metadata address"
    return None


# ---------------------------------------------------------------------------
# Model endpoint policy (bring-your-own-model egress)
# ---------------------------------------------------------------------------

#: Cloud metadata endpoints. Denied in *every* tier and never relaxable by an
#: allowlist: a model client that can reach 169.254.169.254 is a
#: credential-theft primitive, not a local-inference convenience.
_METADATA_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_METADATA_ADDRESSES = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / GCP / OpenStack / DigitalOcean
        "100.100.100.200",  # Alibaba Cloud
        "192.0.0.192",  # Oracle Cloud
        "fd00:ec2::254",  # AWS IMDS over IPv6
        "fe80::a9fe:a9fe",  # AWS IMDS IPv6 link-local
    }
)

#: Hostnames that are loopback by name. ``*.localhost`` is reserved for
#: loopback by RFC 6761; bare ``localhost`` is the classic spelling.
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})
_LOOPBACK_HOST_SUFFIXES = (".localhost",)
#: mDNS / link-local naming: a LAN neighbour, not this host.
_PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".intranet", ".lan", ".home", ".corp")

#: Host classes returned by :func:`classify_model_endpoint_host`.
HOST_CLASS_METADATA = "metadata"
HOST_CLASS_LOOPBACK = "loopback"
HOST_CLASS_PRIVATE = "private"
HOST_CLASS_PUBLIC = "public"


class ModelEndpointBlockedError(ValueError):
    """A model endpoint URL was refused by the egress policy.

    Subclasses :class:`ValueError` so the Gateway's existing
    ``except ValueError -> 400`` arm reports it as a client error, while
    callers that care can catch the precise type. The message never contains
    URL userinfo (a credential could be embedded there) - see
    :func:`redact_url_userinfo`.
    """

    def __init__(self, url: str, reason: str) -> None:
        self.url = redact_url_userinfo(url)
        self.reason = reason
        super().__init__(f"Model endpoint refused: {reason} (endpoint: {self.url})")


def redact_url_userinfo(url: str) -> str:
    """Return *url* with any ``user:password@`` component removed.

    A refused endpoint is echoed back in error text, logs and API responses;
    stripping userinfo first means a secret embedded in a URL can never ride
    along with the diagnostic. Path, query and fragment are preserved so the
    redaction stays a faithful (usable) endpoint reference.
    """
    if not isinstance(url, str) or "@" not in url:
        return url if isinstance(url, str) else ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<unparsable endpoint>"
    if not parsed.netloc or "@" not in parsed.netloc:
        return url
    _, _, host = parsed.netloc.rpartition("@")
    return parsed._replace(netloc=host).geturl()


def is_cloud_metadata_address(address: ipaddress._BaseAddress) -> bool:
    """True for cloud metadata endpoints and the whole link-local metadata net.

    IPv4-mapped IPv6 (``::ffff:169.254.169.254``) is unwrapped first: without
    that, a mapped literal would slip past a naive string comparison and
    reach the metadata service of the host it was aimed at.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address) in _METADATA_ADDRESSES or bool(address.is_link_local)


def _extra_blocked_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    """The egress guard's forbidden subnets, reused (not re-listed) here.

    ``ipaddress.is_private`` misses ranges the shipped
    :class:`alpha.safety.net_policy.NetworkPolicyGuard` already forbids (CGNAT
    ``100.64.0.0/10`` is the notable one), so the model-endpoint classifier
    defers to that single list instead of keeping a second copy that can drift.
    Imported lazily and cached so this module keeps no import-time dependency
    on the safety package.
    """
    global _EXTRA_BLOCKED_NETWORKS
    if _EXTRA_BLOCKED_NETWORKS is None:
        from alpha.safety.net_policy import NetworkPolicyGuard

        _EXTRA_BLOCKED_NETWORKS = tuple(NetworkPolicyGuard.DISALLOWED_NETWORKS)
    return _EXTRA_BLOCKED_NETWORKS


_EXTRA_BLOCKED_NETWORKS: tuple[ipaddress._BaseNetwork, ...] | None = None


def classify_model_endpoint_address(address: ipaddress._BaseAddress) -> str:
    """Classify one resolved address for the model-endpoint policy."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if is_cloud_metadata_address(address):
        return HOST_CLASS_METADATA
    if address.is_loopback or address.is_unspecified:
        # 0.0.0.0 / :: resolves to *this* host, so it is loopback in effect.
        return HOST_CLASS_LOOPBACK
    if is_blocked_address(address) or any(address in net for net in _extra_blocked_networks()):
        return HOST_CLASS_PRIVATE
    return HOST_CLASS_PUBLIC


def classify_model_endpoint_host(hostname: str) -> str:
    """Classify a host *without* DNS: literal IPs and reserved names only.

    A name that is not a literal and not reserved returns ``"public"``, which
    means "unknown until resolved" - callers that need certainty pass a
    resolver. Keeping the two apart is what lets the read path screen a
    persisted endpoint without a DNS round trip on every config load.
    """
    normalized = (hostname or "").strip().rstrip(".").lower()
    if normalized in _METADATA_HOSTNAMES:
        return HOST_CLASS_METADATA
    if normalized in _LOOPBACK_HOSTNAMES or normalized.endswith(_LOOPBACK_HOST_SUFFIXES):
        return HOST_CLASS_LOOPBACK
    if normalized.endswith(_PRIVATE_HOST_SUFFIXES):
        return HOST_CLASS_PRIVATE
    try:
        return classify_model_endpoint_address(ipaddress.ip_address(normalized))
    except ValueError:
        return HOST_CLASS_PUBLIC


def _memoized_resolver(resolver: Callable[[str], list[ipaddress._BaseAddress]]) -> Callable[[str], list[ipaddress._BaseAddress]]:
    """Cache one resolution per host so a two-phase screen resolves once."""
    cache: dict[str, list[ipaddress._BaseAddress]] = {}

    def _resolve(hostname: str) -> list[ipaddress._BaseAddress]:
        if hostname not in cache:
            try:
                cache[hostname] = list(resolver(hostname))
            except (OSError, ValueError):
                cache[hostname] = []
        return cache[hostname]

    return _resolve


def validate_model_endpoint_url(
    url: str,
    *,
    allow_loopback: bool = False,
    allowed_private_hosts: Iterable[str] = (),
    resolve_dns: bool = True,
    action: str = "route model traffic to",
    resolver: Callable[[str], list[ipaddress._BaseAddress]] | None = None,
) -> str | None:
    """Screen an operator-supplied model endpoint before any egress to it.

    Returns an ``"Error: ..."`` string when the endpoint must be refused, or
    ``None`` when the caller may proceed. Tiers:

    * default (``allow_loopback=False``) - the endpoint must be a public
      http(s) host. Loopback, private, link-local and metadata addresses are
      refused.
    * local tier (``allow_loopback=True``) - loopback is permitted (Ollama,
      LM Studio, vLLM on this host), everything else stays refused unless the
      host is listed in *allowed_private_hosts* (an operator allowlist for a
      LAN gateway).

    Cloud metadata is refused in both tiers. Credentials embedded in the URL
    are always refused, so a secret can never travel in a ``base_url``.

    ``resolve_dns=False`` restricts the screen to what can be decided offline
    (scheme, userinfo, IP literals, reserved names). That is the right mode
    for re-screening an already-persisted endpoint on a hot config-load path;
    a DNS name is then reported as public and not resolved, which is stated
    here so no caller mistakes it for a full guarantee.
    """
    if not isinstance(url, str) or not url.strip():
        return "Error: endpoint URL is empty"
    candidate = url.strip()
    try:
        parsed = urlparse(candidate)
        parsed.port  # raises ValueError on a malformed port
    except ValueError:
        return f"Error: could not parse the {action} endpoint URL"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "Error: Only http:// and https:// endpoint URLs are supported"
    if parsed.username or parsed.password:
        return "Error: endpoint URL must not embed credentials; use the provider api_key field"
    hostname = parsed.hostname
    if not hostname:
        return "Error: endpoint URL host could not be parsed"

    normalized = hostname.strip().rstrip(".").lower()
    allowlist = {h.strip().rstrip(".").lower() for h in allowed_private_hosts if isinstance(h, str) and h.strip()}

    # Phase 1 - everything decidable without a network round trip.
    host_class = classify_model_endpoint_host(normalized)
    tier_error = _tier_rejection(host_class, normalized, allowlist, allow_loopback, action)
    if tier_error is not None:
        return tier_error
    if host_class != HOST_CLASS_PUBLIC:
        return None  # literal IP or reserved name: the offline class is conclusive
    if not resolve_dns:
        return None
    if normalized in allowlist:
        return None  # operator allowlisted this name outright

    # Phase 2 - reuse the web-tool screen as the primary decision, so the
    # model path and the web-tool path can never drift apart.
    resolve = _memoized_resolver(resolver or resolve_host_addresses)
    strict_error = validate_public_http_url(candidate, allow_private_addresses=False, action=action, resolver=resolve)
    if strict_error is None:
        return None

    # The strict screen refused. Relax only what the caller's tier allows,
    # re-classifying the *resolved* addresses so a metadata address can never
    # be relaxed away by an allowlist or by the local tier. Every resolved
    # address must pass; an unresolvable host keeps the strict refusal.
    resolved = resolve(hostname)
    if not resolved:
        return strict_error
    for address in resolved:
        if not _tier_permits(classify_model_endpoint_address(address), normalized, allowlist, allow_loopback):
            return strict_error
    return None


def _tier_permits(host_class: str, normalized_host: str, allowlist: set[str], allow_loopback: bool) -> bool:
    if host_class == HOST_CLASS_METADATA:
        return False
    if host_class == HOST_CLASS_LOOPBACK:
        return allow_loopback
    if host_class == HOST_CLASS_PRIVATE:
        return normalized_host in allowlist
    return True


def _tier_rejection(host_class: str, normalized_host: str, allowlist: set[str], allow_loopback: bool, action: str) -> str | None:
    if _tier_permits(host_class, normalized_host, allowlist, allow_loopback):
        return None
    if host_class == HOST_CLASS_METADATA:
        return f"Error: Refusing to {action} a cloud metadata address"
    if host_class == HOST_CLASS_LOOPBACK:
        return f"Error: Refusing to {action} a loopback address"
    return f"Error: Refusing to {action} a private address"


def assert_model_endpoint_url(
    url: str,
    *,
    allow_loopback: bool = False,
    allowed_private_hosts: Iterable[str] = (),
    resolve_dns: bool = True,
    action: str = "route model traffic to",
    resolver: Callable[[str], list[ipaddress._BaseAddress]] | None = None,
) -> str:
    """Validate a model endpoint and return its stripped form.

    Raises :class:`ModelEndpointBlockedError` when the endpoint is refused, so
    a caller cannot forget to check the screening result.
    """
    error = validate_model_endpoint_url(
        url,
        allow_loopback=allow_loopback,
        allowed_private_hosts=allowed_private_hosts,
        resolve_dns=resolve_dns,
        action=action,
        resolver=resolver,
    )
    if error is not None:
        raise ModelEndpointBlockedError(url, error.removeprefix("Error: "))
    return url.strip()
