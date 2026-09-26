"""Network Policy & SSRF Egress Filtering Guard inspired by OpenClaw.

Covers two egress surfaces:

* server-side fetches and tools, via :meth:`NetworkPolicyGuard.validate_url`;
* bring-your-own-model endpoints (``base_url`` supplied by an operator through
  ``POST /api/models/providers/configure`` or a config file), via
  :meth:`NetworkPolicyGuard.validate_model_endpoint`. That method delegates to
  the single shared implementation in
  :mod:`alpha.community.url_safety` so the model path and the web-tool path
  can never drift apart, and adds the endpoint-specific rules: a declared
  local tier (loopback OpenAI-compatible servers) and cloud metadata, which is
  refused in every tier.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class PolicyCheckResult:
    """Outcome of egress network policy evaluation."""

    allowed: bool
    reason: str
    hostname: str = ""
    ip_address: str | None = None


class NetworkPolicyGuard:
    """Guards outbound network requests against SSRF, internal probing, and cloud metadata access."""

    # Disallowed IP Networks (RFC 1918, RFC 3927 link-local, Loopback, Carrier Grade NAT, IPv6 ULA/Link-local)
    DISALLOWED_NETWORKS = [
        ipaddress.ip_network("127.0.0.0/8"),       # IPv4 loopback
        ipaddress.ip_network("10.0.0.0/8"),        # RFC 1918 class A
        ipaddress.ip_network("172.16.0.0/12"),     # RFC 1918 class B
        ipaddress.ip_network("192.168.0.0/16"),    # RFC 1918 class C
        ipaddress.ip_network("169.254.0.0/16"),    # Link-local / Cloud metadata (AWS, GCP, Azure)
        ipaddress.ip_network("100.64.0.0/10"),     # Carrier-grade NAT
        ipaddress.ip_network("0.0.0.0/8"),         # Current network
        ipaddress.ip_network("::1/128"),           # IPv6 loopback
        ipaddress.ip_network("fc00::/7"),          # IPv6 Unique Local
        ipaddress.ip_network("fe80::/10"),         # IPv6 Link-Local
    ]

    DISALLOWED_HOSTS = {
        "localhost",
        "metadata.google.internal",
        "metadata.internal",
        "instance-data",
    }

    ALLOWED_SCHEMES = {"http", "https"}

    def __init__(
        self,
        allowed_schemes: Sequence[str] | None = None,
        disallowed_hosts: Sequence[str] | None = None,
        allow_custom_ports: bool = True,
    ):
        self.allowed_schemes = set(allowed_schemes or self.ALLOWED_SCHEMES)
        self.disallowed_hosts = set(self.DISALLOWED_HOSTS)
        if disallowed_hosts:
            self.disallowed_hosts.update(disallowed_hosts)
        self.allow_custom_ports = allow_custom_ports

    def validate_ip(self, ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> PolicyCheckResult:
        """Evaluate if an IP address belongs to private or link-local subnets."""
        ip_str = str(ip_obj)
        for net in self.DISALLOWED_NETWORKS:
            if ip_obj in net:
                return PolicyCheckResult(
                    allowed=False,
                    reason=f"Access blocked: IP '{ip_str}' is inside forbidden subnet '{net}' (SSRF prevention).",
                    ip_address=ip_str,
                )
        return PolicyCheckResult(allowed=True, reason="IP allowed", ip_address=ip_str)

    def validate_url(self, url: str, resolve_dns: bool = False) -> PolicyCheckResult:
        """Inspect and validate an outbound URL against egress security policy."""
        try:
            parsed = urlparse(url)
        except Exception as e:
            return PolicyCheckResult(allowed=False, reason=f"Malformed URL: {e}")

        if not parsed.scheme or parsed.scheme.lower() not in self.allowed_schemes:
            return PolicyCheckResult(
                allowed=False,
                reason=f"Forbidden URL scheme '{parsed.scheme}'. Only {sorted(self.allowed_schemes)} are permitted.",
            )

        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return PolicyCheckResult(allowed=False, reason="Missing hostname in URL.")

        # Check hostname blacklist
        if hostname in self.disallowed_hosts or hostname.endswith(".local"):
            return PolicyCheckResult(
                allowed=False,
                reason=f"Access blocked: hostname '{hostname}' is on the SSRF forbidden host list.",
                hostname=hostname,
            )

        # Check if hostname is an IP literal
        try:
            ip_obj = ipaddress.ip_address(hostname)
            ip_res = self.validate_ip(ip_obj)
            if not ip_res.allowed:
                return ip_res
        except ValueError:
            # Not an IP literal, it's a domain name
            if resolve_dns:
                try:
                    resolved_ips = socket.getaddrinfo(hostname, None)
                    for info in resolved_ips:
                        ip_val = info[4][0]
                        ip_obj = ipaddress.ip_address(ip_val)
                        ip_res = self.validate_ip(ip_obj)
                        if not ip_res.allowed:
                            return PolicyCheckResult(
                                allowed=False,
                                reason=f"Access blocked: DNS resolution of '{hostname}' yielded forbidden IP '{ip_val}'.",
                                hostname=hostname,
                                ip_address=ip_val,
                            )
                except socket.gaierror:
                    # DNS resolution failed or unreachable
                    pass

        return PolicyCheckResult(allowed=True, reason="URL egress permitted", hostname=hostname)

    def is_url_allowed(self, url: str, resolve_dns: bool = False) -> bool:
        return self.validate_url(url, resolve_dns=resolve_dns).allowed

    def validate_model_endpoint(
        self,
        url: str,
        *,
        allow_loopback: bool = False,
        allowed_private_hosts: Sequence[str] | None = None,
        resolve_dns: bool = True,
    ) -> PolicyCheckResult:
        """Apply the shared model-endpoint egress policy to a model ``base_url``.

        A BYO endpoint is a long-lived, operator-writable egress target, so it
        is screened before it is ever persisted: non-http(s) schemes, embedded
        URL credentials, loopback/private hosts (unless the caller declares a
        local tier or an operator allowlist) and every cloud metadata endpoint
        are refused.

        The decision itself lives in :mod:`alpha.community.url_safety` - this
        is the ``NetworkPolicyGuard`` entry point for callers that already hold
        a guard instance, kept as a thin delegate so there is exactly one
        model-endpoint policy in the codebase. Imported lazily because the
        guard is also used from import-light contexts.
        """
        from alpha.community.url_safety import validate_model_endpoint_url

        reason = validate_model_endpoint_url(
            url,
            allow_loopback=allow_loopback,
            allowed_private_hosts=tuple(allowed_private_hosts or ()),
            resolve_dns=resolve_dns,
            action="route model traffic to",
        )
        hostname = ""
        try:
            hostname = (urlparse(url).hostname or "").lower()
        except Exception:  # malformed URL: the policy already refused it
            hostname = ""
        if reason is None:
            return PolicyCheckResult(allowed=True, reason="Model endpoint egress permitted", hostname=hostname)
        return PolicyCheckResult(allowed=False, reason=reason, hostname=hostname)


_global_net_guard = NetworkPolicyGuard()


def get_net_policy_guard() -> NetworkPolicyGuard:
    return _global_net_guard
