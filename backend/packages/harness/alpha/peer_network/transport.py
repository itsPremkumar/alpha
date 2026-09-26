"""Free, serverless transports used by the Alpha peer network.

HTTP is the durable fallback and WebSocket is the low-latency path.  Both are
ordinary open Internet/LAN protocols; no broker is required.  The transport
never logs bearer tokens and rejects unsafe endpoint shapes before making a
request.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

import httpx

from .models import PeerCard, PeerEnvelope, PeerPairRequest

logger = logging.getLogger(__name__)

MAX_ENDPOINT_LENGTH = 2048
MAX_RESPONSE_BYTES = 512 * 1024
_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata",
    "instance-data.ec2.internal",
}
_BLOCKED_HOSTS_SUFFIXES = (".internal", ".local.metadata")
_BLOCKED_ADDRESSES = {
    "169.254.169.254",  # AWS/GCP/Azure instance metadata
    "169.254.170.2",  # ECS task metadata
    "100.100.100.200",  # Alibaba Cloud metadata
}


class PeerTransportError(RuntimeError):
    """A remote peer could not be reached or rejected a request."""


class PeerNetworkDisabledError(PeerTransportError):
    """The local operator has not enabled the peer plane.

    Subclassed from ``PeerTransportError`` on purpose: that is the exception
    the Gateway's public ingress route already renders as a refusal (401 on
    ``/api/peer-network/inbound/messages``), so a closed plane needs no
    router-side special case and can never be mistaken for a delivered message.
    """


@dataclass(slots=True)
class TransportResult:
    transport: str
    status_code: int | None
    response: dict | None = None


def validate_endpoint(value: str, *, allowed_schemes: tuple[str, ...] = ("http", "https", "ws", "wss")) -> str:
    """Validate a user/discovery supplied endpoint without resolving DNS."""

    if not isinstance(value, str):
        raise PeerTransportError("Peer endpoint must be a string")
    value = value.strip()
    if not value or len(value) > MAX_ENDPOINT_LENGTH:
        raise PeerTransportError("Peer endpoint is empty or too long")
    if any(ord(char) < 32 for char in value):
        raise PeerTransportError("Peer endpoint contains control characters")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in allowed_schemes:
        raise PeerTransportError(f"Peer endpoint scheme must be one of {list(allowed_schemes)}")
    if not parsed.hostname or parsed.username or parsed.password:
        raise PeerTransportError("Peer endpoint must contain a host and no embedded credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PeerTransportError("Peer endpoint contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise PeerTransportError("Peer endpoint port is outside the valid range")
    host = parsed.hostname.rstrip(".").casefold()
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOSTS_SUFFIXES):
        raise PeerTransportError("Peer endpoint points at a blocked metadata host")
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_unspecified or address.is_multicast):
        raise PeerTransportError("Peer endpoint must not use an unspecified or multicast address")
    if address is not None and str(address) in _BLOCKED_ADDRESSES:
        raise PeerTransportError("Peer endpoint points at a blocked metadata address")
    return value


def _base_url(endpoint: str) -> str:
    endpoint = validate_endpoint(endpoint, allowed_schemes=("http", "https"))
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if path.endswith("/api/peer-network"):
        path = path[: -len("/api/peer-network")]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _http_endpoint(endpoint: str) -> str:
    parsed = urlsplit(validate_endpoint(endpoint, allowed_schemes=("http", "https")))
    path = parsed.path.rstrip("/")
    if path.endswith("/api/peer-network"):
        path = path[: -len("/api/peer-network")]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/api/peer-network/inbound/messages", "", ""))


def _pair_endpoint(endpoint: str) -> str:
    parsed = urlsplit(validate_endpoint(endpoint, allowed_schemes=("http", "https")))
    path = parsed.path.rstrip("/")
    if path.endswith("/api/peer-network"):
        path = path[: -len("/api/peer-network")]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/api/peer-network/remote/pair", "", ""))


def _card_endpoint(endpoint: str) -> str:
    parsed = urlsplit(validate_endpoint(endpoint, allowed_schemes=("http", "https")))
    path = parsed.path.rstrip("/")
    if path.endswith("/api/peer-network"):
        path = path[: -len("/api/peer-network")]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/.well-known/agent-card.json", "", ""))


def _card_fallback_endpoint(endpoint: str) -> str:
    parsed = urlsplit(validate_endpoint(endpoint, allowed_schemes=("http", "https")))
    path = parsed.path.rstrip("/")
    if path.endswith("/api/peer-network"):
        path = path[: -len("/api/peer-network")]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/api/peer-network/card", "", ""))


class PeerTransport:
    """HTTP-first transport with an optional WebSocket retry."""

    def __init__(self, *, timeout_seconds: float = 8.0):
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))

    async def fetch_card(self, endpoint: str) -> PeerCard:
        headers = {"Accept": "application/alpha-peer-card+json, application/json"}
        last_status: int | None = None
        for url in (_card_endpoint(endpoint), _card_fallback_endpoint(endpoint)):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                    response = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                raise PeerTransportError(f"Could not fetch peer Agent Card: {exc}") from exc
            last_status = response.status_code
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                raise PeerTransportError(f"Peer Agent Card returned HTTP {response.status_code}")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise PeerTransportError("Peer Agent Card exceeds the response size limit")
            try:
                raw = response.json()
                card = PeerCard.model_validate(raw)
                validate_endpoint(card.url, allowed_schemes=("http", "https"))
                if card.websocket_url:
                    validate_endpoint(card.websocket_url, allowed_schemes=("ws", "wss"))
                return card
            except (ValueError, TypeError) as exc:
                raise PeerTransportError("Peer Agent Card is not valid Alpha/A2A JSON") from exc
        raise PeerTransportError(f"Peer Agent Card returned HTTP {last_status or 404}")

    async def pair(self, endpoint: str, request: PeerPairRequest) -> dict:
        url = _pair_endpoint(endpoint)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.post(url, json=request.model_dump(mode="json"))
        except httpx.HTTPError as exc:
            raise PeerTransportError(f"Pairing request failed: {exc}") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise PeerTransportError("Pairing response exceeds the response size limit")
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:500]}
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            raise PeerTransportError(str(detail or f"Pairing returned HTTP {response.status_code}"))
        if not isinstance(body, dict):
            raise PeerTransportError("Pairing response was not an object")
        return body

    async def send(self, peer: dict, envelope: PeerEnvelope, token: str) -> TransportResult:
        if peer.get("trust") != "paired":
            raise PeerTransportError("Peer is discovered but not paired")
        if not token:
            raise PeerTransportError("No pairing credential is available for this peer")
        endpoint = str(peer.get("url") or "")
        errors: list[str] = []
        try:
            result = await self._send_http(endpoint, envelope, token)
            return result
        except PeerTransportError as exc:
            errors.append(str(exc))

        websocket_endpoint = peer.get("websocket_url")
        if websocket_endpoint:
            try:
                return await self._send_websocket(str(websocket_endpoint), envelope, token)
            except PeerTransportError as exc:
                errors.append(str(exc))
        raise PeerTransportError("; ".join(errors) or "No transport succeeded")

    async def _send_http(self, endpoint: str, envelope: PeerEnvelope, token: str) -> TransportResult:
        url = _http_endpoint(endpoint)
        headers = {
            "Content-Type": "application/alpha-a2a+json",
            "Accept": "application/json",
            "X-Alpha-Peer-Token": token,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.post(url, headers=headers, json=envelope.model_dump(mode="json"))
        except httpx.HTTPError as exc:
            raise PeerTransportError(f"HTTP delivery failed: {exc}") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise PeerTransportError("HTTP delivery response exceeds the response size limit")
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            raise PeerTransportError(str(detail or f"HTTP delivery returned {response.status_code}"))
        return TransportResult(transport="http", status_code=response.status_code, response=body if isinstance(body, dict) else None)

    async def _send_websocket(self, endpoint: str, envelope: PeerEnvelope, token: str) -> TransportResult:
        endpoint = validate_endpoint(endpoint, allowed_schemes=("ws", "wss"))
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:  # pragma: no cover - dependency is pinned in the app
            raise PeerTransportError("WebSocket transport is unavailable") from exc
        try:
            async with connect(
                endpoint,
                additional_headers={"X-Alpha-Peer-Token": token},
                open_timeout=self.timeout_seconds,
                close_timeout=self.timeout_seconds,
                max_size=2 * 1024 * 1024,
            ) as websocket:
                await websocket.send(json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False))
                response = await asyncio.wait_for(websocket.recv(), timeout=self.timeout_seconds)
        except Exception as exc:  # websockets has several version-specific exceptions
            raise PeerTransportError(f"WebSocket delivery failed: {exc}") from exc
        try:
            body = json.loads(response) if isinstance(response, str) else None
        except ValueError:
            body = None
        return TransportResult(transport="websocket", status_code=200, response=body if isinstance(body, dict) else None)


__all__ = [
    "MAX_ENDPOINT_LENGTH",
    "MAX_RESPONSE_BYTES",
    "PeerNetworkDisabledError",
    "PeerTransport",
    "PeerTransportError",
    "TransportResult",
    "validate_endpoint",
]
