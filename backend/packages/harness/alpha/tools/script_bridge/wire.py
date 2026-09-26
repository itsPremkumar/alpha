"""Parent<->script transport for the script bridge.

``AF_UNIX`` is unreliable on Windows, so the transport is **selected at
runtime**: a Unix domain socket where the platform has a working one, and a
loopback TCP socket everywhere else (which on this repo's host means Windows).

Both directions are newline-delimited JSON over a stream socket, with a
per-execution shared-secret token presented on the very first frame.  The token
is minted by the parent, never appears in a model-visible string, and exists to
stop another local process from driving the dispatcher.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .errors import TransportError

PROTOCOL_VERSION = 1

#: Loopback only.  The dispatcher must never be reachable off-host.
LOOPBACK_HOST = "127.0.0.1"


def unix_socket_supported() -> bool:
    """Return True when this platform has a usable ``AF_UNIX``.

    Windows 10 1803+ exposes ``AF_UNIX`` but the implementation is not
    reliable for the accept/connect/close cycle the bridge needs, so the
    transport falls back to loopback TCP there.
    """
    if os.name == "nt":
        return False
    if not hasattr(socket, "AF_UNIX"):
        return False
    probe: socket.socket | None = None
    path: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="alpha-sb-probe-") as tmp:
            path = os.path.join(tmp, "probe.sock")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.bind(path)
            probe.close()
            probe = None
        return True
    except (OSError, AttributeError, NotImplementedError):
        return False
    finally:
        if probe is not None:  # pragma: no cover - defensive
            try:
                probe.close()
            except OSError:
                pass


def transport_kind() -> str:
    """``"unix"`` or ``"tcp"`` - chosen at runtime, never hardcoded per-OS."""
    return "unix" if unix_socket_supported() else "tcp"


@dataclass(frozen=True)
class Endpoint:
    """Where the parent dispatcher is listening."""

    kind: str
    address: str
    token: str
    listener: Any = None

    def to_env(self) -> dict[str, str]:
        return {
            "ALPHA_SCRIPT_BRIDGE_SOCKET": self.address,
            "ALPHA_SCRIPT_BRIDGE_TOKEN": self.token,
        }


def new_token() -> str:
    """A fresh 256-bit shared secret for one execution."""
    return secrets.token_hex(32)


def make_endpoint(base_dir: str | os.PathLike[str] | None = None) -> Endpoint:
    """Bind a loopback listener and return its :class:`Endpoint`."""
    kind = transport_kind()
    token = new_token()
    if kind == "unix":
        root = tempfile.mkdtemp(prefix="alpha-sb-", dir=str(base_dir) if base_dir else None)
        address = os.path.join(root, "dispatch.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(address)
        os.chmod(address, stat.S_IRUSR | stat.S_IWUSR)
    else:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((LOOPBACK_HOST, 0))
        host, port = listener.getsockname()
        address = f"{host}:{port}"
    return Endpoint(kind=kind, address=address, token=token, listener=listener)  # type: ignore[call-arg]


def encode_frame(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def read_frames(sock: socket.socket, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield decoded frames until the peer closes or *limit* frames arrive."""
    buffer = b""
    seen = 0
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return
        buffer += chunk
        while b"\n" in buffer:
            raw, _, buffer = buffer.partition(b"\n")
            if not raw.strip():
                continue
            try:
                yield json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransportError(f"malformed frame: {exc}") from exc
            seen += 1
            if limit is not None and seen >= limit:
                return


def send_frame(sock: socket.socket, payload: dict[str, Any]) -> None:
    try:
        sock.sendall(encode_frame(payload))
    except OSError as exc:
        raise TransportError(f"send failed: {exc}") from exc


def connect(address: str, kind: str, *, timeout: float = 10.0) -> socket.socket:
    """Connect a child to the parent dispatcher."""
    try:
        if kind == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(address)
        else:
            host, _, port = address.rpartition(":")
            sock = socket.create_connection((host or LOOPBACK_HOST, int(port)), timeout=timeout)
    except (OSError, ValueError) as exc:
        raise TransportError(f"connect to dispatcher failed: {exc}") from exc
    return sock


def listen(endpoint: Endpoint, *, backlog: int = 8) -> socket.socket:
    """Put the bound socket into listen state."""
    listener = endpoint.listener  # type: ignore[attr-defined]
    try:
        listener.listen(backlog)
    except OSError as exc:
        raise TransportError(f"listen failed: {exc}") from exc
    return listener


def authenticate(frames: Iterator[dict[str, Any]], token: str) -> dict[str, Any]:
    """Consume and validate the handshake frame.

    The handshake is a *separate* frame from the first request so a socket that
    a third local process managed to open cannot smuggle a request in before
    the token check.
    """
    try:
        hello = next(frames)
    except StopIteration as exc:
        raise TransportError("dispatcher closed before handshake") from exc
    if not secrets.compare_digest(str(hello.get("token", "")), token):
        raise TransportError("dispatcher handshake rejected: bad token")
    if int(hello.get("protocol", 0)) != PROTOCOL_VERSION:
        raise TransportError(f"dispatcher handshake rejected: protocol {hello.get('protocol')!r} != {PROTOCOL_VERSION}")
    return hello
