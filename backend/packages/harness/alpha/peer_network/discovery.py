"""Peer discovery providers for the Alpha network.

UDP broadcast is always available and has no third-party dependency.  mDNS is
supported when the free, actively maintained ``zeroconf`` package is present;
without it the service reports the provider as unavailable instead of
pretending that LAN discovery succeeded.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import socket
from typing import Any, Protocol

from .models import PeerCard, validate_agent_id

logger = logging.getLogger(__name__)

MDNS_SERVICE_TYPE = "_alpha._tcp.local."


class DiscoveryConsumer(Protocol):
    def observe_discovery(self, payload: dict[str, Any], *, source: str) -> PeerCard | None: ...

    def card(self) -> PeerCard: ...


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, consumer: DiscoveryConsumer, *, port: int):
        self.consumer = consumer
        self.port = port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > 64 * 1024:
            return
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        self.consumer.observe_discovery(payload, source=f"udp:{addr[0]}:{addr[1]}")
        if self.transport is None:
            return
        try:
            card = self.consumer.card().discovery_dict()
            self.transport.sendto(json.dumps(card, separators=(",", ":")).encode("utf-8"), addr)
        except (OSError, ValueError):
            logger.debug("Could not answer peer discovery beacon", exc_info=True)

    def error_received(self, exc: Exception) -> None:
        logger.debug("UDP discovery socket error: %s", exc)


class UdpDiscovery:
    """Broadcast-based LAN discovery with bounded packet sizes."""

    def __init__(
        self,
        consumer: DiscoveryConsumer,
        *,
        port: int = 8743,
        interval_seconds: float = 8.0,
        bind_host: str = "0.0.0.0",
    ):
        self.consumer = consumer
        self.port = max(1, min(int(port), 65535))
        self.interval_seconds = max(2.0, min(float(interval_seconds), 300.0))
        self.bind_host = bind_host
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._transport is not None and self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self.consumer, port=self.port),
                local_addr=(self.bind_host, self.port),
                family=socket.AF_INET,
                allow_broadcast=True,
            )
        except OSError as exc:
            self.last_error = str(exc)
            logger.warning("Alpha UDP peer discovery could not bind: %s", exc)
            return
        self._transport = transport  # type: ignore[assignment]
        self._task = asyncio.create_task(self._run(), name="alpha-peer-udp-discovery")
        await self.broadcast_once()

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._transport:
            self._transport.close()
            self._transport = None

    async def broadcast_once(self) -> None:
        if self._transport is None:
            return
        data = json.dumps(self.consumer.card().discovery_dict(), separators=(",", ":")).encode("utf-8")
        targets = {"255.255.255.255"}
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                address = info[4][0]
                if not address.startswith("127."):
                    targets.add(address)
        except OSError:
            pass
        for target in targets:
            try:
                self._transport.sendto(data, (target, self.port))
            except OSError as exc:
                self.last_error = str(exc)
                logger.debug("UDP discovery broadcast to %s failed: %s", target, exc)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                await self.broadcast_once()


class MdnsDiscovery:
    """Optional standards-based mDNS provider backed by ``zeroconf``."""

    def __init__(self, consumer: DiscoveryConsumer, *, port: int = 8001):
        self.consumer = consumer
        self.port = port
        self._zeroconf: Any = None
        self._browser: Any = None
        self._service_info: Any = None
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        try:
            import zeroconf  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def running(self) -> bool:
        return self._zeroconf is not None

    def start(self) -> None:
        if self.running:
            return
        try:
            from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
        except ImportError:
            self.last_error = "optional dependency 'zeroconf' is not installed"
            return

        owner = self

        class Listener(ServiceListener):
            def add_service(self, zc: Any, type_: str, name: str) -> None:
                info = zc.get_service_info(type_, name)
                owner._consume(info)

            def remove_service(self, zc: Any, type_: str, name: str) -> None:
                return None

            def update_service(self, zc: Any, type_: str, name: str) -> None:
                self.add_service(zc, type_, name)

        try:
            self._zeroconf = Zeroconf()
            base = self.consumer.card().url.rstrip("/")
            from urllib.parse import urlsplit

            parsed = urlsplit(base)
            server = parsed.hostname or "alpha.local"
            try:
                addresses = [socket.inet_aton(server)]
            except OSError:
                addresses = [socket.inet_aton("127.0.0.1")]
            properties = {
                b"agent_id": self.consumer.card().agent_id.encode("utf-8"),
                b"protocol": b"alpha-a2a",
                b"version": self.consumer.card().version.encode("utf-8"),
            }
            self._service_info = ServiceInfo(
                MDNS_SERVICE_TYPE,
                f"Alpha {self.consumer.card().agent_id}.{MDNS_SERVICE_TYPE}",
                addresses=addresses,
                port=self.port,
                properties=properties,
                server=f"{server}.",
            )
            self._zeroconf.register_service(self._service_info)
            self._browser = ServiceBrowser(self._zeroconf, MDNS_SERVICE_TYPE, Listener())
        except Exception as exc:  # optional provider must never break the Gateway
            self.last_error = str(exc)
            logger.info("Alpha mDNS peer discovery is unavailable: %s", exc)
            self.stop()

    def stop(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                self._browser.cancel()
            self._browser = None
        if self._zeroconf is not None:
            with contextlib.suppress(Exception):
                self._zeroconf.close()
            self._zeroconf = None
        self._service_info = None

    def _consume(self, info: Any) -> None:
        if info is None:
            return
        addresses = getattr(info, "addresses", None) or []
        if not addresses:
            return
        if isinstance(addresses[0], bytes):
            addresses = [socket.inet_ntoa(address) for address in addresses]
        if not addresses:
            return
        properties = getattr(info, "properties", None) or {}
        decoded: dict[str, str] = {}
        for key, value in properties.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8", "replace")
            if isinstance(value, bytes):
                value = value.decode("utf-8", "replace")
            decoded[str(key)] = str(value)
        agent_id = decoded.get("agent_id", "")
        try:
            agent_id = validate_agent_id(agent_id)
        except ValueError:
            # Do not let an arbitrary mDNS instance name become an identity or
            # path fragment. The service will still be visible as a bounded
            # discovered card, but it must be paired explicitly.
            digest = hashlib.sha256(str(info.name).encode("utf-8", "replace")).hexdigest()[:8]
            agent_id = f"mdns-{digest}"
        port = getattr(info, "port", self.port)
        card = PeerCard(
            agent_id=agent_id,
            name=f"mDNS peer {agent_id}",
            description="Discovered through the local mDNS Alpha service.",
            version=decoded.get("version", "unknown"),
            url=f"http://{addresses[0]}:{port}",
            websocket_url=f"ws://{addresses[0]}:{port}/api/peer-network/ws",
            capabilities=["mDNS"],
            supports=["mdns", "http", "websocket"],
        )
        self.consumer.observe_discovery(card.discovery_dict(), source="mdns")


__all__ = ["MDNS_SERVICE_TYPE", "MdnsDiscovery", "UdpDiscovery"]
