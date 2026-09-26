"""Minimal child-side RPC client.  No alpha imports, no privileged imports.

Everything this module can do is send a frame on a loopback socket and read the
answer.  It cannot widen an allowlist, name a forbidden tool, reach MCP, or
bypass a limit: the parent re-derives all of that per call.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any

PROTOCOL_VERSION = 1
ENV_SOCKET = "ALPHA_SCRIPT_BRIDGE_SOCKET"
ENV_TOKEN = "ALPHA_SCRIPT_BRIDGE_TOKEN"
ENV_TRANSPORT = "ALPHA_SCRIPT_BRIDGE_TRANSPORT"
ENV_STUB_SHA = "ALPHA_SCRIPT_BRIDGE_STUB_SHA"

_CLIENT: ToolRpcClient | None = None


class BridgeUnavailable(RuntimeError):
    """The bridge transport is not configured in this process."""


def _send(sock: socket.socket, payload: dict[str, Any]) -> None:
    sock.sendall((json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8"))


class _Frames:
    """Buffered newline-delimited JSON reader that keeps leftover bytes."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = b""

    def __iter__(self):
        return self

    def __next__(self) -> dict[str, Any]:
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise StopIteration
            self._buffer += chunk
        raw, _, self._buffer = self._buffer.partition(b"\n")
        if not raw.strip():
            return self.__next__()
        return json.loads(raw.decode("utf-8"))


class ToolRpcClient:
    """One request/response pair per call over a long-lived socket."""

    def __init__(self, address: str, kind: str, token: str) -> None:
        self._address = address
        self._kind = kind
        self._token = token
        if kind == "unix":
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(60.0)
            self._sock.connect(address)
        else:
            host, _, port = address.rpartition(":")
            self._sock = socket.create_connection((host or "127.0.0.1", int(port)), timeout=60.0)
        self._seq = 0
        self._frames = _Frames(self._sock)
        _send(self._sock, {"op": "hello", "protocol": PROTOCOL_VERSION, "token": token})

    @property
    def calls_made(self) -> int:
        return self._seq

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self._seq += 1
        call_id = self._seq
        _send(
            self._sock,
            {"op": "tool_call", "id": call_id, "name": name, "arguments": arguments or {}},
        )
        for frame in self._frames:
            if frame.get("id") != call_id:
                continue
            if frame.get("op") == "tool_error":
                error = frame.get("error") or {}
                raise ToolCallRefused(
                    str(error.get("code") or "tool_call_failed"),
                    str(error.get("message") or "the dispatcher refused this tool call"),
                    error.get("detail") or {},
                )
            if frame.get("op") == "tool_result":
                return frame.get("value")
        raise ToolCallRefused(
            "transport_closed",
            "the dispatcher closed the connection before answering the tool call",
        )

    def close(self) -> None:
        try:
            _send(self._sock, {"op": "bye"})
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class ToolCallRefused(RuntimeError):
    """A tool call made from inside a script was refused.  Never downgraded."""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.reason = message
        self.detail = dict(detail or {})


def get_client() -> ToolRpcClient:
    """Return the process-wide RPC client, connecting on first use."""
    global _CLIENT
    if _CLIENT is None:
        address = os.environ.get(ENV_SOCKET, "")
        token = os.environ.get(ENV_TOKEN, "")
        kind = os.environ.get(ENV_TRANSPORT, "") or ("unix" if _is_socket_path(address) else "tcp")
        if not address or not token:
            raise BridgeUnavailable("script bridge transport is not configured; this module only works inside a script_bridge execution")
        _CLIENT = ToolRpcClient(address, kind, token)
    return _CLIENT


def _is_socket_path(address: str) -> bool:
    return bool(address) and os.path.exists(address) and ":" not in address


def reset_client() -> None:
    global _CLIENT
    if _CLIENT is not None:
        _CLIENT.close()
    _CLIENT = None


def verify_stub_fingerprint() -> str | None:
    """Return a drift reason when the loaded stub is not the one the parent declared."""
    from . import generated

    declared = os.environ.get(ENV_STUB_SHA, "")
    if declared and declared != generated.STUB_REGISTRY_SHA256:
        return f"the tool stub this script loaded was generated from a different tool registry than the dispatcher's ({generated.STUB_REGISTRY_SHA256[:12]}... vs {declared[:12]}...); refusing to run"
    return None
