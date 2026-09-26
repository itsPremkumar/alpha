from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import socket
import ssl
import threading
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from alpha.community.aio_sandbox import network_proxy


def test_domain_matches_exact_and_leading_wildcard_only() -> None:
    assert network_proxy.domain_matches("pypi.org", "pypi.org")
    assert not network_proxy.domain_matches("evilpypi.org", "pypi.org")
    assert network_proxy.domain_matches("files.pythonhosted.org", "*.pythonhosted.org")
    assert not network_proxy.domain_matches("pythonhosted.org", "*.pythonhosted.org")


def test_address_is_public_rejects_host_private_link_local_and_metadata() -> None:
    for address in (
        "127.0.0.1",
        "10.0.0.2",
        "172.16.0.2",
        "192.168.1.2",
        "169.254.169.254",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fec0::1",
        "fe80::1",
        "ff0e::1",
    ):
        assert not network_proxy.address_is_public(address)
    assert network_proxy.address_is_public("8.8.8.8")
    assert not network_proxy.address_is_public("198.18.1.5")
    assert network_proxy.address_is_public("198.18.1.5", allow_synthetic_dns=True)


def test_policy_denial_and_temporary_or_sandbox_grants(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(network_proxy, "POLICY_DB", tmp_path / "policy.sqlite3")
    monkeypatch.setenv("AGENT_WORKSPACE_NETWORK_MODE", "allowlist")
    monkeypatch.setenv("AGENT_WORKSPACE_ALLOW_DOMAINS_JSON", json.dumps(["pypi.org"]))

    assert network_proxy.policy_allows("pypi.org", 443, now=100)
    assert not network_proxy.policy_allows("example.com", 443, now=100)

    temporary = network_proxy.record_denial("example.com", 443, "CONNECT")
    assert network_proxy.decide(temporary, "allow_temporary", ttl=60)
    assert network_proxy.policy_allows("example.com", 443)

    sandbox = network_proxy.record_denial("files.example.net", 443, "CONNECT")
    assert network_proxy.decide(sandbox, "allow_sandbox", ttl=60)
    assert network_proxy.policy_allows("files.example.net", 443, now=10**12)


def test_pending_events_are_consumed_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(network_proxy, "POLICY_DB", tmp_path / "policy.sqlite3")
    request_id = network_proxy.record_denial("example.com", 443, "CONNECT")

    events = network_proxy.pending_events()

    assert events == [
        {
            "request_id": request_id,
            "host": "example.com",
            "port": 443,
            "method": "CONNECT",
            "created_at": events[0]["created_at"],
        }
    ]
    assert network_proxy.pending_events() == []


def test_pending_events_surface_only_one_destination_per_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(network_proxy, "POLICY_DB", tmp_path / "policy.sqlite3")
    first = network_proxy.record_denial("one.example", 443, "CONNECT")
    network_proxy.record_denial("two.example", 443, "CONNECT")

    assert [event["request_id"] for event in network_proxy.pending_events()] == [first]
    # The sibling is superseded so a retry can create a fresh approvable event.
    assert network_proxy.pending_events() == []
    fresh = network_proxy.record_denial("two.example", 443, "CONNECT")
    assert [event["request_id"] for event in network_proxy.pending_events()] == [fresh]


def test_pending_events_supersede_every_sibling_without_a_batch_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(network_proxy, "POLICY_DB", tmp_path / "policy.sqlite3")
    request_ids = [network_proxy.record_denial(f"host-{index}.example", 443, "CONNECT") for index in range(17)]

    assert [event["request_id"] for event in network_proxy.pending_events()] == [request_ids[0]]
    assert network_proxy.pending_events() == []


def test_deny_pending_events_atomically_denies_every_unsurfaced_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(network_proxy, "POLICY_DB", tmp_path / "policy.sqlite3")
    for index in range(17):
        network_proxy.record_denial(f"host-{index}.example", 443, "CONNECT")

    assert network_proxy.deny_pending_events() == 17
    assert network_proxy.pending_events() == []

    with network_proxy._connect_db() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE decision = 'deny'").fetchone() == (17,)


def test_deny_pending_events_preserves_an_already_surfaced_user_decision(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(network_proxy, "POLICY_DB", tmp_path / "policy.sqlite3")
    surfaced = network_proxy.record_denial("interactive.example", 443, "CONNECT")
    assert [event["request_id"] for event in network_proxy.pending_events()] == [surfaced]
    unsurfaced = network_proxy.record_denial("scheduled.example", 443, "CONNECT")

    assert network_proxy.deny_pending_events() == 1
    assert network_proxy.decide(surfaced, "allow_temporary", ttl=60)
    assert network_proxy.decide(unsurfaced, "deny", ttl=60)


def test_pending_events_claims_old_unsurfaced_denial_on_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(network_proxy, "POLICY_DB", tmp_path / "policy.sqlite3")
    now = [100.0]
    monkeypatch.setattr(network_proxy.time, "time", lambda: now[0])
    request_id = network_proxy.record_denial("late.example", 443, "CONNECT")

    now[0] = 105.0
    assert network_proxy.record_denial("late.example", 443, "CONNECT") == request_id
    assert [event["request_id"] for event in network_proxy.pending_events()] == [request_id]


@pytest.mark.anyio
async def test_resolve_public_fails_closed_when_dns_contains_private_answer(monkeypatch) -> None:
    loop = __import__("asyncio").get_running_loop()

    async def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    assert await network_proxy.resolve_public("example.com", 443) is None


@pytest.mark.anyio
async def test_resolve_public_returns_every_validated_answer_and_open_retries(monkeypatch) -> None:
    loop = asyncio.get_running_loop()
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
    ]

    async def fake_getaddrinfo(*_args, **_kwargs):
        return answers

    attempts: list[str] = []
    connected = (MagicMock(), MagicMock())

    async def fake_open_connection(host: str, _port: int, *, family: int):
        attempts.append(host)
        assert family == socket.AF_INET
        if host == "8.8.8.8":
            raise OSError("first address unavailable")
        return connected

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(network_proxy.asyncio, "open_connection", fake_open_connection)

    resolved = await network_proxy.resolve_public("example.com", 443)

    assert resolved == (
        (socket.AF_INET, ("8.8.8.8", 443)),
        (socket.AF_INET, ("1.1.1.1", 443)),
    )
    assert await network_proxy._open_public(resolved, 443) is connected
    assert attempts == ["8.8.8.8", "1.1.1.1"]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["isolated", "allowlist"])
async def test_denied_destination_is_rejected_without_dns_resolution(tmp_path, monkeypatch, mode: str) -> None:
    monkeypatch.setattr(network_proxy, "POLICY_DB", tmp_path / "policy.sqlite3")
    monkeypatch.setenv("AGENT_WORKSPACE_NETWORK_MODE", mode)
    monkeypatch.setenv("AGENT_WORKSPACE_ALLOW_DOMAINS_JSON", json.dumps(["allowed.example"]))
    monkeypatch.delenv("AGENT_WORKSPACE_RECORD_DENIALS", raising=False)
    resolutions: list[tuple[str, int]] = []

    async def fake_resolve_public(host: str, port: int):
        resolutions.append((host, port))
        return None

    monkeypatch.setattr(network_proxy, "resolve_public", fake_resolve_public)

    proxy = await asyncio.start_server(network_proxy.handle_proxy, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(b"CONNECT denied.example:443 HTTP/1.1\r\nHost: denied.example:443\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()

        assert b"403 Forbidden" in response
        assert resolutions == []
    finally:
        proxy.close()
        await proxy.wait_closed()


@pytest.mark.anyio
async def test_tls_client_hello_sni_is_extracted_for_connect_enforcement() -> None:
    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    context = ssl.create_default_context()
    tls = context.wrap_bio(incoming, outgoing, server_side=False, server_hostname="pypi.org")
    with pytest.raises(ssl.SSLWantReadError):
        tls.do_handshake()

    reader = __import__("asyncio").StreamReader()
    wire = outgoing.read()
    reader.feed_data(wire)
    reader.feed_eof()

    parsed = await network_proxy._read_tls_client_hello(reader)

    assert parsed == ("pypi.org", wire)


def test_http_request_framing_rejects_ambiguous_or_duplicate_lengths() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        fields = network_proxy._parse_http_header_fields(["Content-Length: 4", "Transfer-Encoding: chunked"])
        network_proxy._http_request_body_framing(fields)
    with pytest.raises(ValueError, match="one non-negative"):
        fields = network_proxy._parse_http_header_fields(["Content-Length: 4", "Content-Length: 4"])
        network_proxy._http_request_body_framing(fields)


@pytest.mark.anyio
async def test_chunked_request_body_rejects_non_hex_size() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"+1\r\na\r\n0\r\n\r\n")
    reader.feed_eof()
    writer = MagicMock()

    with pytest.raises(ValueError, match="Invalid chunk size"):
        await network_proxy._copy_chunked_request_body(reader, writer)


@pytest.mark.anyio
async def test_http_proxy_relays_exactly_one_request_per_connection(monkeypatch) -> None:
    received: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = await reader.readuntil(b"\r\n\r\n")
        body = await reader.readexactly(4)
        received.set_result(header + body)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]

    async def fake_resolve_public(_host: str, _port: int):
        return (socket.AF_INET, ("127.0.0.1", upstream_port))

    async def fake_open_public(_resolved, _port: int):
        return await asyncio.open_connection("127.0.0.1", upstream_port)

    monkeypatch.setattr(network_proxy, "resolve_public", fake_resolve_public)
    monkeypatch.setattr(network_proxy, "_open_public", fake_open_public)
    monkeypatch.setattr(network_proxy, "policy_allows", lambda host, port: (host, port) == ("allowed.example", 80))

    proxy = await asyncio.start_server(network_proxy.handle_proxy, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(b"POST http://allowed.example/first HTTP/1.1\r\nHost: allowed.example\r\nContent-Length: 4\r\nConnection: keep-alive\r\n\r\ndataGET http://denied.example/second HTTP/1.1\r\nHost: denied.example\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()

        upstream_request = await asyncio.wait_for(received, timeout=2)
        assert b"POST /first HTTP/1.1" in upstream_request
        assert b"Connection: close" in upstream_request
        assert b"Connection: keep-alive" not in upstream_request
        assert upstream_request.endswith(b"data")
        assert b"denied.example" not in upstream_request
        assert b"200 OK" in response
    finally:
        proxy.close()
        upstream.close()
        await proxy.wait_closed()
        await upstream.wait_closed()


@pytest.mark.anyio
async def test_sandbox_api_relay_requires_per_sandbox_token(monkeypatch) -> None:
    received: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    async def upstream_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = await reader.readuntil(b"\r\n\r\n")
        received.set_result(header)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    monkeypatch.setenv(network_proxy.RELAY_TOKEN_ENV, "test-relay-token")
    monkeypatch.setenv("AGENT_WORKSPACE_SANDBOX_TARGET", f"127.0.0.1:{upstream_port}")
    relay = await asyncio.start_server(network_proxy.handle_relay, "127.0.0.1", 0)
    relay_port = relay.sockets[0].getsockname()[1]
    try:
        denied_reader, denied_writer = await asyncio.open_connection("127.0.0.1", relay_port)
        denied_writer.write(b"GET /v1/sandbox HTTP/1.1\r\nHost: sandbox\r\n\r\n")
        await denied_writer.drain()
        denied_response = await asyncio.wait_for(denied_reader.read(), timeout=2)
        denied_writer.close()
        await denied_writer.wait_closed()

        assert b"403 Forbidden" in denied_response
        assert not received.done()

        reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
        writer.write(b"GET /v1/sandbox HTTP/1.1\r\nHost: sandbox\r\n" + f"{network_proxy.RELAY_AUTH_HEADER}: test-relay-token\r\n\r\n".encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()

        assert b"200 OK" in response
        assert network_proxy.RELAY_AUTH_HEADER.encode() in await asyncio.wait_for(received, timeout=2)
    finally:
        relay.close()
        upstream.close()
        await relay.wait_closed()
        await upstream.wait_closed()


@pytest.mark.anyio
async def test_sandbox_api_relay_rejects_non_ascii_token(monkeypatch) -> None:
    monkeypatch.setenv(network_proxy.RELAY_TOKEN_ENV, "test-relay-token")
    relay = await asyncio.start_server(network_proxy.handle_relay, "127.0.0.1", 0)
    relay_port = relay.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
        writer.write(b"GET /v1/sandbox HTTP/1.1\r\nHost: sandbox\r\n" + network_proxy.RELAY_AUTH_HEADER.encode() + b": \xff\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()

        assert b"403 Forbidden" in response
    finally:
        relay.close()
        await relay.wait_closed()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "malformed_header",
    [
        b"Host : denied.example",
        b"Transfer-Encoding : chunked",
        b"Bad(Header): value",
    ],
)
async def test_http_proxy_rejects_ambiguous_field_names_before_policy_check(monkeypatch, malformed_header: bytes) -> None:
    policy_checks: list[tuple[str, int]] = []

    def fake_policy_allows(host: str, port: int) -> bool:
        policy_checks.append((host, port))
        return False

    monkeypatch.setattr(network_proxy, "policy_allows", fake_policy_allows)

    proxy = await asyncio.start_server(network_proxy.handle_proxy, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(b"GET http://allowed.example/ HTTP/1.1\r\nHost: allowed.example\r\n" + malformed_header + b"\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        writer.close()
        await writer.wait_closed()

        assert b"400 Bad Request" in response
        assert policy_checks == []
    finally:
        proxy.close()
        await proxy.wait_closed()


class _ScriptedReader:
    """StreamReader stand-in that replays a fixed script of read outcomes."""

    def __init__(self, script: list[bytes | BaseException | None]) -> None:
        self._script = list(script)

    async def read(self, _limit: int) -> bytes:
        outcome = self._script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is None:
            # A peer that never sends another byte and never resets.
            await asyncio.Event().wait()
        return outcome


def _relay_writer() -> MagicMock:
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return writer


@pytest.mark.anyio
async def test_relay_direction_absorbs_a_non_connection_error_and_reports_the_failure() -> None:
    writer = _relay_writer()

    # An OSError is not a ConnectionError, so this is the failure mode that used
    # to escape the direction and abandon the opposite one.
    assert await network_proxy._relay(_ScriptedReader([OSError("injected relay failure")]), writer) is False
    assert writer.close.call_count == 1
    assert writer.wait_closed.await_count == 1


@pytest.mark.anyio
async def test_relay_direction_ends_a_stalled_read_on_its_deadline(monkeypatch) -> None:
    monkeypatch.setattr(network_proxy, "RELAY_IDLE_TIMEOUT_SECONDS", 0.05)
    writer = _relay_writer()

    assert await network_proxy._relay(_ScriptedReader([b"payload", None]), writer) is False
    assert writer.write.call_args_list == [call(b"payload")]
    assert writer.close.call_count == 1


@pytest.mark.anyio
async def test_relay_direction_reports_a_clean_half_close_as_success() -> None:
    writer = _relay_writer()

    assert await network_proxy._relay(_ScriptedReader([b"one", b"two", b""]), writer) is True
    assert writer.write.call_args_list == [call(b"one"), call(b"two")]
    assert writer.close.call_count == 1


@pytest.mark.anyio
async def test_relay_direction_still_propagates_cancellation() -> None:
    writer = _relay_writer()
    stalled = _ScriptedReader([None])

    direction = asyncio.create_task(network_proxy._relay(stalled, writer))
    await asyncio.sleep(0)
    direction.cancel()

    with pytest.raises(asyncio.CancelledError):
        await direction
    # Cancellation is how the owning relay tears the pair down, so the socket
    # must still be released on the way out.
    assert writer.close.call_count == 1


class _DrainFailingWriter:
    """Upstream writer whose second drain fails with a non-ConnectionError."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self.drains = 0

    def __getattr__(self, name: str):
        return getattr(self._writer, name)

    async def drain(self) -> None:
        self.drains += 1
        if self.drains > 1:
            raise OSError("injected upstream write-buffer failure")
        await self._writer.drain()


class _StalledClientWriter:
    """Client-facing writer whose buffer stops draining after the first write.

    This is the ordinary way a relay direction stalls: the client stopped
    reading, so the socket it writes to never accepts more. Nothing the *other*
    direction does can release it, because it is blocked on a different socket
    entirely.
    """

    def __init__(self, writer: asyncio.StreamWriter, stalled: asyncio.Event) -> None:
        self._writer = writer
        self._stalled = stalled
        self.drains = 0

    def __getattr__(self, name: str):
        return getattr(self._writer, name)

    async def drain(self) -> None:
        self.drains += 1
        if self.drains > 1:
            self._stalled.set()
            await asyncio.Event().wait()
        await self._writer.drain()


class _FloodingUpstream(asyncio.Protocol):
    """Upstream that answers with more bytes than one relay write can carry."""

    def __init__(self, connected: asyncio.Event, chunk: bytes, chunks: int) -> None:
        self._connected = connected
        self._chunk = chunk
        self._chunks = chunks

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        for _ in range(self._chunks):
            transport.write(self._chunk)
        self._connected.set()

    def data_received(self, _data: bytes) -> None:
        pass


@pytest.mark.anyio
async def test_failed_relay_direction_leaves_no_task_and_no_half_open_socket(monkeypatch) -> None:
    """One direction failing must never leave the other direction running.

    A non-ConnectionError OSError used to escape a relay direction and
    propagate out of the gather that joined them, which abandoned the opposite
    direction. Here that direction is stalled writing to a client that stopped
    reading, so it cannot unwind on its own: the abandoned task held a live
    socket that nothing owned, and the client never saw the end of the
    connection. The load-bearing assertion is therefore on the surviving task
    set, not on a return value.
    """
    loop = asyncio.get_running_loop()
    upstream_connected = asyncio.Event()
    client_write_stalled = asyncio.Event()
    # Socket pairs rather than listeners: a listening socket has the event loop
    # recreate a per-connection acceptor task, and this test measures the task
    # set the proxy itself leaves behind.
    client_sock, relay_sock = socket.socketpair()
    upstream_proxy_sock, upstream_peer_sock = socket.socketpair()
    client_reader, client_writer = await asyncio.open_connection(sock=client_sock)
    relay_reader, relay_writer = await asyncio.open_connection(sock=relay_sock)
    real_open_connection = asyncio.open_connection
    peer_transport: list[asyncio.Transport] = []

    async def open_connection_to_the_paired_upstream(_host, _port, **_kwargs):
        if not peer_transport:
            transport, _protocol = await loop.connect_accepted_socket(lambda: _FloodingUpstream(upstream_connected, b"A" * 16_384, 64), upstream_peer_sock)
            peer_transport.append(transport)
        reader, writer = await real_open_connection(sock=upstream_proxy_sock)
        return reader, _DrainFailingWriter(writer)

    monkeypatch.setattr(network_proxy.asyncio, "open_connection", open_connection_to_the_paired_upstream)
    monkeypatch.setenv(network_proxy.RELAY_TOKEN_ENV, "test-relay-token")
    monkeypatch.setenv("AGENT_WORKSPACE_SANDBOX_TARGET", "127.0.0.1:1")

    baseline = set(asyncio.all_tasks())
    try:
        handler = asyncio.create_task(network_proxy.handle_relay(relay_reader, _StalledClientWriter(relay_writer, client_write_stalled)))
        client_writer.write(b"GET /v1/sandbox HTTP/1.1\r\nHost: sandbox\r\n" + f"{network_proxy.RELAY_AUTH_HEADER}: test-relay-token\r\n\r\n".encode())
        await client_writer.drain()
        # Wait until the upstream is connected and the opposite direction is
        # genuinely stuck, so the failure below cannot race the setup.
        await asyncio.wait_for(upstream_connected.wait(), timeout=10)
        await asyncio.wait_for(client_write_stalled.wait(), timeout=10)
        # A body byte so the client -> upstream direction has work to do, and
        # therefore a drain that fails.
        client_writer.write(b"\x00")
        await client_writer.drain()

        outcome = await asyncio.wait_for(asyncio.gather(handler, return_exceptions=True), timeout=10)

        leaked = set(asyncio.all_tasks()) - baseline
        survivors = sorted(f"{task.get_name()}:{getattr(task.get_coro(), '__qualname__', task.get_coro())}" for task in leaked)
        assert leaked == set(), f"the failed relay direction left {len(leaked)} task(s) running: {survivors}"
        assert outcome == [None]
        # The stalled direction is only released by cancelling it, so the
        # teardown has to be deliberate rather than a side effect of a peer
        # that happens to hang up.
        assert relay_writer.is_closing()
        while await asyncio.wait_for(client_reader.read(), timeout=10):
            pass
    finally:
        for transport in peer_transport:
            transport.close()
        for sock in (client_sock, relay_sock, upstream_proxy_sock, upstream_peer_sock):
            sock.close()


class _StubListener:
    """Minimal ``asyncio.Server`` stand-in for the listener teardown test."""

    def __init__(self, name: str, *, failure: BaseException | None = None) -> None:
        self.name = name
        self._failure = failure
        self.closed = False
        self.serve_forever_cancelled = False
        self.serve_forever_finished = False

    async def __aenter__(self) -> _StubListener:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    async def serve_forever(self) -> None:
        try:
            if self._failure is not None:
                raise self._failure
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.serve_forever_cancelled = True
            raise
        finally:
            self.serve_forever_finished = True


@pytest.mark.anyio
async def test_dying_listener_tears_down_the_surviving_listener(monkeypatch) -> None:
    """The two listeners must live and die as one process-lifetime pair.

    ``gather`` propagates the first listener's failure but leaves the other
    listener's serve_forever task running and unowned, so the sidecar kept
    accepting on a port it could no longer serve, and the orphan outlived the
    teardown. The first failure still has to reach ``asyncio.run`` so the
    sidecar exits non-zero.
    """
    bound: list[_StubListener] = []

    async def fake_start_server(_handler, _host, _port, **_kwargs) -> _StubListener:
        bound.append(_StubListener(f"listener-{len(bound)}", failure=OSError("relay listener failed") if len(bound) == 1 else None))
        return bound[-1]

    monkeypatch.setattr(network_proxy.asyncio, "start_server", fake_start_server)
    monkeypatch.setattr(network_proxy, "_connect_db", MagicMock)

    baseline = set(asyncio.all_tasks())
    with pytest.raises(OSError):
        await asyncio.wait_for(network_proxy.serve(), timeout=10)

    leaked = set(asyncio.all_tasks()) - baseline
    assert leaked == set(), f"listener teardown left {len(leaked)} task(s) running: {sorted(task.get_name() for task in leaked)}"
    # The first listener to fail must not leave the other's serve_forever task
    # running, and neither listener may keep its socket after the teardown.
    assert all(listener.serve_forever_finished for listener in bound)
    assert [listener.serve_forever_cancelled for listener in bound] == [True, False]
    assert all(listener.closed for listener in bound)


@pytest.mark.anyio
async def test_policy_decisions_never_run_on_the_event_loop_thread(monkeypatch) -> None:
    """The proxy's policy database work must not stall the loop it runs on.

    Every decision opens the SQLite policy database, switches it to WAL and
    creates tables, and SQLite serialises writers against the host-side CLI
    that records or resolves approvals. Run inline, one contended decision
    freezes the single event loop that also serves every other connection in
    this sidecar.
    """
    loop_thread = threading.get_ident()
    policy_threads: list[int] = []
    denial_threads: list[int] = []

    def fake_policy_allows(_host: str, _port: int) -> bool:
        policy_threads.append(threading.get_ident())
        return False

    def fake_record_denial(_host: str, _port: int, _method: str) -> str:
        denial_threads.append(threading.get_ident())
        return "request-id"

    monkeypatch.setattr(network_proxy, "policy_allows", fake_policy_allows)
    monkeypatch.setattr(network_proxy, "record_denial", fake_record_denial)
    monkeypatch.setenv("AGENT_WORKSPACE_RECORD_DENIALS", "1")

    proxy = await asyncio.start_server(network_proxy.handle_proxy, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(b"CONNECT denied.example:443 HTTP/1.1\r\nHost: denied.example:443\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=10)
        writer.close()
        await writer.wait_closed()

        assert b"403 Forbidden" in response
        assert b"request-id" in response
        assert policy_threads and all(ident != loop_thread for ident in policy_threads)
        assert denial_threads and all(ident != loop_thread for ident in denial_threads)
    finally:
        proxy.close()
        await proxy.wait_closed()


def test_no_socket_await_in_the_proxy_lacks_a_deadline() -> None:
    """Every blocking socket wait must be wrapped in a deadline.

    A proxy serves every sandbox connection from one event loop, so a single
    unbounded read, drain or resolver call freezes all of them, and the work
    looks like nothing but networking. ``serve_forever`` is the same class of
    unbounded wait and must have exactly one owner, the teardown that cancels
    it.
    """
    source = pathlib.Path(network_proxy.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    bounded = {id(argument) for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "wait_for" for argument in (*node.args, *node.keywords)}
    unbounded: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr not in {"drain", "wait_closed", "read", "readuntil", "readexactly", "getaddrinfo"}:
            continue
        if id(node) not in bounded:
            unbounded.append(f"line {node.lineno}: {node.func.attr}")
    serve_forever_calls = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "serve_forever"]

    assert unbounded == []
    assert len(serve_forever_calls) == 1, f"serve_forever must have a single owning teardown, found it on lines {serve_forever_calls}"
