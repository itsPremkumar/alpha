"""Real-Time Language Server Protocol (LSP/LSIF) Intelligence Engine.

This module provides a frontier-grade, high-throughput asynchronous JSON-RPC
Language Server Protocol (LSP 3.17) client used to give the agent precise,
compiler-grade semantic awareness of a repository instead of relying on
approximate textual search.

Capabilities
------------
* Asynchronous JSON-RPC 2.0 transport with ``Content-Length`` framing
  (the wire format mandated by the LSP base protocol).
* LSP 3.17 request coverage: ``textDocument/definition``,
  ``textDocument/references``, ``textDocument/documentSymbol``,
  ``textDocument/hover`` and diagnostic collection via
  ``textDocument/publishDiagnostics``.
* Multi-language server process lifecycle management for Python
  (pyright / jedi-language-server), TypeScript / JavaScript (tsserver),
  Rust (rust-analyzer) and Go (gopls), including automatic capability
  negotiation and graceful shutdown.
* Automatic fallback to a built-in static AST symbol indexer when the
  corresponding language server binary is unavailable, so semantic queries
  never hard-fail in a bare environment.
* Fast SCIP / LSIF static index reader providing repository-wide symbol
  graphs without spawning any language server process.

Exposed agent tool
------------------
``query_language_server_symbol`` - symbol lookup, references, definitions and
hover type inference across the workspace.

All public entry points are deterministic and side-effect free with respect to
the host machine: language servers are spawned lazily, always inside the
workspace root, and every failure degrades to the static index.
"""

from __future__ import annotations

import asyncio
import ast
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

LSP_VERSION: str = "3.17"
DEFAULT_REQUEST_TIMEOUT_SEC: float = 20.0
DEFAULT_STARTUP_TIMEOUT_SEC: float = 12.0


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class SymbolKind(str, Enum):
    """LSP-aligned ``SymbolKind`` subset (mirrors LSP 3.17 numbering)."""

    FILE = "File"
    MODULE = "Module"
    NAMESPACE = "Namespace"
    PACKAGE = "Package"
    CLASS = "Class"
    METHOD = "Method"
    PROPERTY = "Property"
    FIELD = "Field"
    CONSTRUCTOR = "Constructor"
    ENUM = "Enum"
    INTERFACE = "Interface"
    FUNCTION = "Function"
    VARIABLE = "Variable"
    CONSTANT = "Constant"
    STRING = "String"
    NUMBER = "Number"
    BOOLEAN = "Boolean"
    ARRAY = "Array"
    OBJECT = "Object"
    KEY = "Key"
    NULL = "Null"
    ENUMMEMBER = "EnumMember"
    STRUCT = "Struct"
    EVENT = "Event"
    OPERATOR = "Operator"
    TYPEPARAMETER = "TypeParameter"
    UNKNOWN = "Unknown"


class DiagnosticSeverity(int, Enum):
    """LSP ``DiagnosticSeverity`` enumeration."""

    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4


class IndexFormat(str, Enum):
    """Supported static index container formats."""

    LSIF_JSON = "lsif_json"
    SCIP = "scip"


@dataclass
class SourceLocation:
    """A resolved file location produced by a semantic query."""

    file_path: str
    line: int
    character: int = 0
    end_line: Optional[int] = None
    end_character: Optional[int] = None
    preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line": self.line,
            "character": self.character,
            "end_line": self.end_line,
            "end_character": self.end_character,
            "preview": self.preview,
        }


@dataclass
class SymbolRecord:
    """A single indexed symbol with its lexical range and metadata."""

    name: str
    kind: SymbolKind
    location: SourceLocation
    container: Optional[str] = None
    detail: str = ""
    signature: str = ""
    documentation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "container": self.container,
            "detail": self.detail,
            "signature": self.signature,
            "documentation": self.documentation,
            "location": self.location.to_dict(),
        }


@dataclass
class DiagnosticRecord:
    """A normalized diagnostic published by a language server."""

    file_path: str
    line: int
    character: int
    severity: DiagnosticSeverity
    message: str
    source: str = ""
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line": self.line,
            "character": self.character,
            "severity": self.severity.name,
            "code": self.code,
            "source": self.source,
            "message": self.message,
        }


@dataclass
class HoverRecord:
    """Human readable hover payload for a symbol."""

    symbol: str
    contents: str
    signature: str = ""
    documentation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "contents": self.contents,
            "signature": self.signature,
            "documentation": self.documentation,
        }


@dataclass
class LanguageServerSpec:
    """Declarative descriptor for a language server binary."""

    name: str
    languages: tuple[str, ...]
    command: tuple[str, ...]
    initialization_options: dict[str, Any] = field(default_factory=dict)

    def is_installed(self) -> bool:
        """Return True when the server binary is resolvable on the host."""
        if not self.command:
            return False
        return shutil.which(self.command[0]) is not None


# Default server registry. Every entry is optional: absence simply routes the
# corresponding language through the static index.
DEFAULT_SERVER_SPECS: tuple[LanguageServerSpec, ...] = (
    LanguageServerSpec(
        name="pyright",
        languages=("python",),
        command=("pyright-langserver", "--stdio"),
    ),
    LanguageServerSpec(
        name="jedi-language-server",
        languages=("python",),
        command=("jedi-language-server",),
    ),
    LanguageServerSpec(
        name="typescript-language-server",
        languages=("typescript", "javascript", "tsx", "jsx"),
        command=("typescript-language-server", "--stdio"),
    ),
    LanguageServerSpec(
        name="rust-analyzer",
        languages=("rust",),
        command=("rust-analyzer",),
    ),
    LanguageServerSpec(
        name="gopls",
        languages=("go",),
        command=("gopls", "serve"),
    ),
)

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
}


# ---------------------------------------------------------------------------
# Async plumbing helpers
# ---------------------------------------------------------------------------


def run_coroutine(coro: "asyncio.Future[Any] | Any") -> Any:
    """Execute ``coro`` from synchronous code in every loop context.

    Language server I/O is asynchronous, but LangChain tools are invoked from
    synchronous agent code. This helper transparently bridges both worlds:
    it reuses the ambient event loop when one is already running (executing
    the coroutine on a dedicated worker loop) and falls back to
    :func:`asyncio.run` otherwise.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - re-raised below
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


class LspProtocolError(RuntimeError):
    """Raised when the JSON-RPC exchange with a language server fails."""


class LspTransport:
    """Framed JSON-RPC transport implementing the LSP base protocol.

    LSP frames consist of RFC 7230 style headers terminated by ``\\r\\n\\r\\n``
    followed by a JSON payload whose byte length is declared by the mandatory
    ``Content-Length`` header.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._write_lock = asyncio.Lock()

    async def write_message(self, payload: dict[str, Any]) -> None:
        """Serialize and frame a JSON-RPC message."""
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = (
            f"Content-Length: {len(body)}\r\n"
            f"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n"
        ).encode("ascii")
        async with self._write_lock:
            self._writer.write(header + body)
            await self._writer.drain()

    async def read_message(self, timeout: float = DEFAULT_REQUEST_TIMEOUT_SEC) -> dict[str, Any]:
        """Read and decode a single framed JSON-RPC message."""
        headers: dict[str, str] = {}
        while True:
            raw_line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
            if not raw_line:
                raise LspProtocolError("language server closed the stream")
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                break
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()

        length = int(headers.get("content-length", "0"))
        if length <= 0:
            raise LspProtocolError("frame without a positive Content-Length header")
        body = await asyncio.wait_for(self._reader.readexactly(length), timeout=timeout)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise LspProtocolError(f"malformed JSON payload: {exc}") from exc

    async def close(self) -> None:
        """Close the underlying writer, ignoring benign transport errors."""
        with contextlib.suppress(Exception):
            self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


class LanguageServerSession:
    """A live, initialized session with one language server process.

    The session owns the subprocess, performs the LSP ``initialize`` /
    ``initialized`` handshake, multiplexes concurrent requests by id, and
    drains server-initiated notifications (notably diagnostics) on a
    background reader task.
    """

    def __init__(
        self,
        spec: LanguageServerSpec,
        workspace_root: Path,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SEC,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SEC,
    ):
        self.spec = spec
        self.workspace_root = workspace_root
        self.request_timeout = request_timeout
        self.startup_timeout = startup_timeout
        self.process: Optional[asyncio.subprocess.Process] = None
        self.transport: Optional[LspTransport] = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self.diagnostics: dict[str, list[DiagnosticRecord]] = {}
        self.server_capabilities: dict[str, Any] = {}
        self.open_documents: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> bool:
        """Spawn the server process and complete the LSP handshake."""
        if self.process is not None:
            return True
        if not self.spec.is_installed():
            return False
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.spec.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace_root),
            )
        except (OSError, ValueError) as exc:
            logger.debug("failed to spawn language server %s: %s", self.spec.name, exc)
            self.process = None
            return False

        assert self.process.stdin is not None and self.process.stdout is not None
        self.transport = LspTransport(self.process.stdout, self.process.stdin)
        self._reader_task = asyncio.create_task(self._read_loop())

        try:
            response = await self.request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "clientInfo": {"name": "agent-workspace", "version": "1.0.0"},
                    "rootUri": self.workspace_root.as_uri(),
                    "rootPath": str(self.workspace_root),
                    "capabilities": {
                        "workspace": {"workspaceFolders": True},
                        "textDocument": {
                            "definition": {"dynamicRegistration": True},
                            "references": {"dynamicRegistration": True},
                            "hover": {"contentFormat": ["markdown", "plaintext"]},
                            "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                            "publishDiagnostics": {"relatedInformation": True},
                        },
                    },
                    "initializationOptions": self.spec.initialization_options,
                    "workspaceFolders": [
                        {"uri": self.workspace_root.as_uri(), "name": self.workspace_root.name}
                    ],
                },
                timeout=self.startup_timeout,
            )
        except (LspProtocolError, asyncio.TimeoutError) as exc:
            logger.debug("initialize handshake failed for %s: %s", self.spec.name, exc)
            await self.stop()
            return False

        self.server_capabilities = (response or {}).get("capabilities", {}) or {}
        await self.notify("initialized", {})
        return True

    async def stop(self) -> None:
        """Terminate the server process and release all resources."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None

        for future in self._pending.values():
            if not future.done():
                future.set_exception(LspProtocolError("session terminated"))
        self._pending.clear()

        if self.transport is not None:
            await self.transport.close()
            self.transport = None

        if self.process is not None:
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.process.wait(), timeout=3.0)
                if self.process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.kill()
            self.process = None

    # -- JSON-RPC ----------------------------------------------------------

    async def notify(self, method: str, params: Any) -> None:
        """Send a notification (a request without an id)."""
        if self.transport is None:
            raise LspProtocolError("session is not connected")
        await self.transport.write_message(
            {"jsonrpc": "2.0", "method": method, "params": params}
        )

    async def request(
        self, method: str, params: Any, timeout: Optional[float] = None
    ) -> Any:
        """Send a request and await its matching response."""
        if self.transport is None:
            raise LspProtocolError("session is not connected")
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future
        await self.transport.write_message(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        try:
            return await asyncio.wait_for(future, timeout or self.request_timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self) -> None:
        """Background pump routing responses and notifications."""
        assert self.transport is not None
        try:
            while True:
                message = await self.transport.read_message(timeout=self.request_timeout)
                if "id" in message and ("result" in message or "error" in message):
                    future = self._pending.pop(message.get("id"), None)
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(
                                LspProtocolError(str(message["error"]))
                            )
                        else:
                            future.set_result(message.get("result"))
                elif "method" in message:
                    self._handle_notification(message)
        except (asyncio.CancelledError, LspProtocolError, asyncio.TimeoutError, ConnectionError):
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("language server reader terminated: %s", exc)

    def _handle_notification(self, message: dict[str, Any]) -> None:
        """Handle server-initiated notifications (diagnostics)."""
        method = message.get("method", "")
        if method != "textDocument/publishDiagnostics":
            return
        params = message.get("params", {}) or {}
        uri = params.get("uri", "")
        records: list[DiagnosticRecord] = []
        for item in params.get("diagnostics", []) or []:
            start = (item.get("range") or {}).get("start", {}) or {}
            records.append(
                DiagnosticRecord(
                    file_path=_uri_to_path(uri),
                    line=int(start.get("line", 0)) + 1,
                    character=int(start.get("character", 0)),
                    severity=DiagnosticSeverity(int(item.get("severity", 1))),
                    message=str(item.get("message", "")),
                    source=str(item.get("source", "") or ""),
                    code=str(item.get("code", "") or ""),
                )
            )
        self.diagnostics[_uri_to_path(uri)] = records

    # -- LSP operations ----------------------------------------------------

    async def open_document(self, file_path: Path, language_id: str) -> None:
        """Send ``textDocument/didOpen`` so the server analyzes the file."""
        uri = file_path.as_uri()
        if uri in self.open_documents:
            return
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        await self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )
        self.open_documents.add(uri)

    async def definition(self, file_path: Path, line: int, character: int) -> list[SourceLocation]:
        """Resolve ``textDocument/definition`` for a 1-based line position."""
        result = await self.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": file_path.as_uri()},
                "position": {"line": max(line - 1, 0), "character": character},
            },
        )
        return _coerce_locations(result)

    async def references(
        self, file_path: Path, line: int, character: int, include_declaration: bool = True
    ) -> list[SourceLocation]:
        """Resolve ``textDocument/references`` for a 1-based line position."""
        result = await self.request(
            "textDocument/references",
            {
                "textDocument": {"uri": file_path.as_uri()},
                "position": {"line": max(line - 1, 0), "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
        )
        return _coerce_locations(result)

    async def document_symbols(self, file_path: Path) -> list[SymbolRecord]:
        """Resolve ``textDocument/documentSymbol`` for a file."""
        result = await self.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": file_path.as_uri()}},
        )
        return _coerce_symbols(result)

    async def hover(self, file_path: Path, line: int, character: int) -> Optional[HoverRecord]:
        """Resolve ``textDocument/hover`` for a 1-based line position."""
        result = await self.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": file_path.as_uri()},
                "position": {"line": max(line - 1, 0), "character": character},
            },
        )
        if not result:
            return None
        contents = _coerce_markup(result.get("contents"))
        return HoverRecord(symbol="", contents=contents, signature=_first_line(contents))


def _uri_to_path(uri: str) -> str:
    """Convert a ``file://`` URI into a native filesystem path."""
    if not uri:
        return ""
    if uri.startswith("file://"):
        trimmed = uri[len("file://") :]
        trimmed = trimmed.split("#", 1)[0]
        if re.match(r"^/[A-Za-z]:", trimmed):
            trimmed = trimmed.lstrip("/")
        return str(Path(trimmed))
    return uri


def _coerce_markup(contents: Any) -> str:
    """Normalize the polymorphic LSP ``MarkupContent`` union to text."""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return str(contents.get("value", ""))
    if isinstance(contents, list):
        return "\n".join(_coerce_markup(item) for item in contents)
    return str(contents)


def _first_line(text: str) -> str:
    """Return the first non-empty line of ``text``."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _coerce_locations(result: Any) -> list[SourceLocation]:
    """Normalize Location / Location[] / LocationLink[] responses."""
    if result is None:
        return []
    if isinstance(result, dict):
        result = [result]
    locations: list[SourceLocation] = []
    for item in result or []:
        if not isinstance(item, dict):
            continue
        target_uri = item.get("uri") or item.get("targetUri")
        target_range = item.get("range") or item.get("targetRange") or {}
        start = target_range.get("start", {}) or {}
        end = target_range.get("end", {}) or {}
        if not target_uri:
            continue
        locations.append(
            SourceLocation(
                file_path=_uri_to_path(str(target_uri)),
                line=int(start.get("line", 0)) + 1,
                character=int(start.get("character", 0)),
                end_line=int(end.get("line", 0)) + 1 if end else None,
                end_character=int(end.get("character", 0)) if end else None,
            )
        )
    return locations


def _coerce_symbols(result: Any) -> list[SymbolRecord]:
    """Normalize SymbolInformation[] / DocumentSymbol[] responses."""
    if not result:
        return []
    symbols: list[SymbolRecord] = []

    def _walk(node: dict[str, Any], container: Optional[str]) -> None:
        location = node.get("location")
        range_ = node.get("range")
        if isinstance(location, dict):
            uri = location.get("uri", "")
            start = (location.get("range") or {}).get("start", {}) or {}
            end = (location.get("range") or {}).get("end", {}) or {}
        else:
            uri = ""
            start = (range_ or {}).get("start", {}) or {}
            end = (range_ or {}).get("end", {}) or {}
        symbols.append(
            SymbolRecord(
                name=str(node.get("name", "")),
                kind=_kind_from_code(node.get("kind")),
                location=SourceLocation(
                    file_path=_uri_to_path(uri),
                    line=int(start.get("line", 0)) + 1,
                    character=int(start.get("character", 0)),
                    end_line=int(end.get("line", 0)) + 1 if end else None,
                    end_character=int(end.get("character", 0)) if end else None,
                ),
                container=container,
                detail=str(node.get("detail", "") or ""),
            )
        )
        for child in node.get("children", []) or []:
            if isinstance(child, dict):
                _walk(child, str(node.get("name", "")) or container)

    for node in result:
        if isinstance(node, dict):
            _walk(node, None)
    return symbols


_KIND_BY_CODE: dict[int, SymbolKind] = {
    1: SymbolKind.FILE,
    2: SymbolKind.MODULE,
    3: SymbolKind.NAMESPACE,
    4: SymbolKind.PACKAGE,
    5: SymbolKind.CLASS,
    6: SymbolKind.METHOD,
    7: SymbolKind.PROPERTY,
    8: SymbolKind.FIELD,
    9: SymbolKind.CONSTRUCTOR,
    10: SymbolKind.ENUM,
    11: SymbolKind.INTERFACE,
    12: SymbolKind.FUNCTION,
    13: SymbolKind.VARIABLE,
    14: SymbolKind.CONSTANT,
    15: SymbolKind.STRING,
    16: SymbolKind.NUMBER,
    17: SymbolKind.BOOLEAN,
    18: SymbolKind.ARRAY,
    19: SymbolKind.OBJECT,
    20: SymbolKind.KEY,
    21: SymbolKind.NULL,
    22: SymbolKind.ENUMMEMBER,
    23: SymbolKind.STRUCT,
    24: SymbolKind.EVENT,
    25: SymbolKind.OPERATOR,
    26: SymbolKind.TYPEPARAMETER,
}


def _kind_from_code(code: Any) -> SymbolKind:
    """Map an LSP numeric ``SymbolKind`` onto :class:`SymbolKind`."""
    try:
        return _KIND_BY_CODE.get(int(code), SymbolKind.UNKNOWN)
    except (TypeError, ValueError):
        return SymbolKind.UNKNOWN


# ---------------------------------------------------------------------------
# Static AST fallback indexer
# ---------------------------------------------------------------------------


_NON_PYTHON_SYMBOL_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "typescript": [
        (r"^\s*export\s+class\s+([A-Za-z_$][\w$]*)", "Class"),
        (r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", "Function"),
        (r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)", "Interface"),
        (r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=", "Class"),
        (r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=", "Variable"),
    ],
    "tsx": [],
    "javascript": [
        (r"^\s*class\s+([A-Za-z_$][\w$]*)", "Class"),
        (r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", "Function"),
        (r"^\s*const\s+([A-Za-z_$][\w$]*)\s*=", "Variable"),
    ],
    "jsx": [],
    "rust": [
        (r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][\w]*)", "Function"),
        (r"^\s*(?:pub\s+)?struct\s+([A-Za-z_][\w]*)", "Struct"),
        (r"^\s*(?:pub\s+)?enum\s+([A-Za-z_][\w]*)", "Enum"),
        (r"^\s*(?:pub\s+)?trait\s+([A-Za-z_][\w]*)", "Interface"),
        (r"^\s*impl\s+([A-Za-z_][\w]*)", "Class"),
    ],
    "go": [
        (r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)", "Function"),
        (r"^\s*type\s+([A-Za-z_][\w]*)\s+struct", "Struct"),
        (r"^\s*type\s+([A-Za-z_][\w]*)\s+interface", "Interface"),
    ],
}

_SKIP_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        "target",
        "site-packages",
    }
)


class StaticSymbolIndexer:
    """Zero-dependency symbol indexer used when no language server exists.

    Python sources are parsed with the standard library :mod:`ast` module for
    full fidelity, while other languages use a conservative line-oriented
    pattern scan that never raises on malformed input.
    """

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root
        self._symbols: list[SymbolRecord] = []
        self._by_name: dict[str, list[SymbolRecord]] = {}
        self._indexed_at: float = 0.0
        self._digest: str = ""

    # -- indexing ----------------------------------------------------------

    def index(self, max_files: int = 4000) -> int:
        """Build (or rebuild) the symbol index for the workspace."""
        self._symbols = []
        self._by_name = {}
        files = list(self._iter_source_files(max_files))
        for file_path in files:
            with contextlib.suppress(Exception):
                self._symbols.extend(self.index_file(file_path))
        for symbol in self._symbols:
            self._by_name.setdefault(symbol.name, []).append(symbol)
        self._indexed_at = time.time()
        self._digest = hashlib.sha256(
            "\n".join(sorted(str(p) for p in files)).encode("utf-8")
        ).hexdigest()
        return len(self._symbols)

    def _iter_source_files(self, max_files: int) -> Iterable[Path]:
        """Yield candidate source files beneath the workspace root."""
        count = 0
        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRECTORIES]
            for name in sorted(files):
                suffix = Path(name).suffix
                if suffix not in LANGUAGE_BY_EXTENSION:
                    continue
                count += 1
                if count > max_files:
                    return
                yield Path(root) / name

    def index_file(self, file_path: Path) -> list[SymbolRecord]:
        """Extract top level symbols from a single source file."""
        language = LANGUAGE_BY_EXTENSION.get(file_path.suffix, "")
        if language == "python":
            return self._index_python(file_path)
        return self._index_pattern(file_path, language)

    def _index_python(self, file_path: Path) -> list[SymbolRecord]:
        """Extract symbols from a Python module using :mod:`ast`."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError):
            return []
        relative = _relative_display(file_path, self.workspace_root)
        lines = source.splitlines()
        records: list[SymbolRecord] = []

        def _preview(line_no: int) -> str:
            if 1 <= line_no <= len(lines):
                return lines[line_no - 1].strip()
            return ""

        def _walk(node: ast.AST, container: Optional[str]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = SymbolKind.METHOD if container else SymbolKind.FUNCTION
                    records.append(
                        SymbolRecord(
                            name=child.name,
                            kind=kind,
                            location=SourceLocation(
                                file_path=relative,
                                line=child.lineno,
                                character=child.col_offset,
                                end_line=getattr(child, "end_lineno", None),
                                preview=_preview(child.lineno),
                            ),
                            container=container,
                            signature=_python_signature(child),
                            documentation=(ast.get_docstring(child) or "").strip(),
                        )
                    )
                elif isinstance(child, ast.ClassDef):
                    records.append(
                        SymbolRecord(
                            name=child.name,
                            kind=SymbolKind.CLASS,
                            location=SourceLocation(
                                file_path=relative,
                                line=child.lineno,
                                character=child.col_offset,
                                end_line=getattr(child, "end_lineno", None),
                                preview=_preview(child.lineno),
                            ),
                            container=container,
                            signature=f"class {child.name}({_base_names(child)})",
                            documentation=(ast.get_docstring(child) or "").strip(),
                        )
                    )
                    _walk(child, child.name)

        _walk(tree, None)
        return records

    def _index_pattern(self, file_path: Path, language: str) -> list[SymbolRecord]:
        """Extract symbols from non-Python sources with line patterns."""
        patterns = _NON_PYTHON_SYMBOL_PATTERNS.get(language) or []
        if not patterns:
            return []
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        relative = _relative_display(file_path, self.workspace_root)
        records: list[SymbolRecord] = []
        for index, line in enumerate(lines, start=1):
            for pattern, kind_name in patterns:
                match = re.match(pattern, line)
                if match:
                    records.append(
                        SymbolRecord(
                            name=match.group(1),
                            kind=SymbolKind(kind_name),
                            location=SourceLocation(
                                file_path=relative,
                                line=index,
                                character=0,
                                preview=line.strip(),
                            ),
                            signature=line.strip(),
                        )
                    )
                    break
        return records

    # -- queries -----------------------------------------------------------

    def symbol_count(self) -> int:
        """Return the number of indexed symbols."""
        return len(self._symbols)

    def document_symbols(self, file_path: str) -> list[SymbolRecord]:
        """Return every symbol defined in ``file_path``."""
        target = _normalize_relative(file_path)
        return [
            symbol
            for symbol in self._symbols
            if symbol.location.file_path == target
            or symbol.location.file_path.endswith(target)
        ]

    def definitions(self, symbol: str) -> list[SourceLocation]:
        """Return definition sites for ``symbol`` (exact, then suffix match)."""
        return [record.location for record in self._lookup(symbol)]

    def references(self, symbol: str, max_results: int = 200) -> list[SourceLocation]:
        """Return definition plus textual reference sites for ``symbol``."""
        locations = self.definitions(symbol)
        if not locations:
            return []
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        seen = {_location_key(loc) for loc in locations}
        for file_path in self._iter_source_files(4000):
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    relative = _relative_display(file_path, self.workspace_root)
                    key = f"{relative}:{line_no}"
                    if key in seen:
                        continue
                    seen.add(key)
                    locations.append(
                        SourceLocation(
                            file_path=relative,
                            line=line_no,
                            character=0,
                            preview=line.strip()[:200],
                        )
                    )
                    if len(locations) >= max_results:
                        return locations
        return locations

    def hover(self, symbol: str) -> Optional[HoverRecord]:
        """Return hover metadata for ``symbol``."""
        records = self._lookup(symbol)
        if not records:
            return None
        record = records[0]
        contents = "\n".join(
            part for part in (record.signature, record.documentation) if part
        ).strip()
        return HoverRecord(
            symbol=record.name,
            contents=contents or f"{record.kind.value} {record.name}",
            signature=record.signature,
            documentation=record.documentation,
        )

    def _lookup(self, symbol: str) -> list[SymbolRecord]:
        """Resolve a symbol by name with deterministic fallback ordering."""
        if not symbol:
            return []
        exact = self._by_name.get(symbol)
        if exact:
            return list(exact)
        suffix_matches = [
            record
            for name, records in self._by_name.items()
            if name.endswith(symbol)
            for record in records
        ]
        if suffix_matches:
            return suffix_matches
        return [
            record
            for name, records in self._by_name.items()
            if symbol.lower() in name.lower()
            for record in records
        ]


def _base_names(node: ast.ClassDef) -> str:
    """Render the base class list of a class definition."""
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
        else:
            names.append(ast.unparse(base))
    return ", ".join(names)


def _python_signature(node: ast.AST) -> str:
    """Render a readable Python signature for a function node."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        args = ast.unparse(node.args)
    except Exception:  # pragma: no cover - defensive
        args = ""
    returns = ""
    if node.returns is not None:
        with contextlib.suppress(Exception):
            returns = f" -> {ast.unparse(node.returns)}"
    decorators = ""
    if getattr(node, "decorator_list", None):
        with contextlib.suppress(Exception):
            decorators = "".join(
                f"@{ast.unparse(d)}\n" for d in node.decorator_list
            )
    return f"{decorators}{prefix} {node.name}({args}){returns}"


def _relative_display(file_path: Path, root: Path) -> str:
    """Render ``file_path`` relative to ``root`` when possible."""
    try:
        return str(Path(file_path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return str(file_path)


def _normalize_relative(file_path: str) -> str:
    """Normalize a user supplied path for suffix comparison."""
    return str(file_path).replace("\\", "/").lstrip("./")


def _location_key(location: SourceLocation) -> str:
    """Return a stable identity key for a source location."""
    return f"{location.file_path}:{location.line}"


# ---------------------------------------------------------------------------
# LSIF / SCIP static index readers
# ---------------------------------------------------------------------------


@dataclass
class StaticIndexSymbol:
    """A symbol occurrence loaded from a prebuilt LSIF/SCIP index."""

    symbol_id: str
    name: str
    file_path: str
    line: int
    character: int
    is_definition: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "name": self.name,
            "file_path": self.file_path,
            "line": self.line,
            "character": self.character,
            "is_definition": self.is_definition,
        }


class StaticIndexReader:
    """Reader for prebuilt repository-wide LSIF and SCIP indexes.

    LSIF is read as newline delimited JSON (the canonical dump format). SCIP
    is a protobuf container; because no protobuf runtime dependency is
    guaranteed in the runtime image, a minimal, dependency-free varint reader
    decodes the ``Index`` -> ``Document`` -> ``SymbolInformation`` /
    ``Occurrence`` subset required for symbol lookup.
    """

    def __init__(self) -> None:
        self.symbols: list[StaticIndexSymbol] = []
        self.format: Optional[IndexFormat] = None
        self.source: str = ""

    @property
    def symbol_count(self) -> int:
        """Number of loaded index entries."""
        return len(self.symbols)

    def load(self, index_path: Path) -> int:
        """Load an index file, inferring its container format."""
        path = Path(index_path)
        if not path.exists():
            return 0
        raw = path.read_bytes()
        if _looks_like_lsif(raw):
            self.format = IndexFormat.LSIF_JSON
            self.symbols = self._read_lsif(raw)
        else:
            self.format = IndexFormat.SCIP
            self.symbols = self._read_scip(raw)
        self.source = str(path)
        return len(self.symbols)

    # -- LSIF --------------------------------------------------------------

    def _read_lsif(self, raw: bytes) -> list[StaticIndexSymbol]:
        """Parse newline delimited LSIF JSON vertices and edges."""
        documents: dict[Any, str] = {}
        ranges: dict[Any, dict[str, Any]] = {}
        range_to_definition: dict[Any, str] = {}
        definition_to_result: dict[Any, list[Any]] = {}
        result_to_ranges: dict[Any, list[Any]] = {}
        symbols: list[StaticIndexSymbol] = []

        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                element = json.loads(line)
            except json.JSONDecodeError:
                continue
            if element.get("type") == "vertex":
                label = element.get("label")
                if label == "document":
                    documents[element.get("id")] = _uri_to_path(str(element.get("uri", "")))
                elif label == "range":
                    start = (element.get("start") or {})
                    ranges[element.get("id")] = {
                        "line": int(start.get("line", 0)) + 1,
                        "character": int(start.get("character", 0)),
                    }
            elif element.get("type") == "edge":
                label = element.get("label")
                out_v = element.get("outV")
                in_vs = element.get("inVs") or []
                in_v = element.get("inV")
                if label == "contains" and out_v in documents:
                    continue
                if label == "textDocument/definition":
                    if in_v is not None:
                        definition_to_result[out_v] = [in_v]
                    else:
                        definition_to_result[out_v] = list(in_vs)
                elif label == "item" and out_v in definition_to_result:
                    result_to_ranges.setdefault(element.get("inV"), []).extend(
                        in_vs or ([in_v] if in_v is not None else [])
                    )

        for range_id, result_ids in definition_to_result.items():
            for result_id in result_ids:
                for target in result_to_ranges.get(result_id, [result_id]):
                    position = ranges.get(target)
                    if position is None:
                        continue
                    symbols.append(
                        StaticIndexSymbol(
                            symbol_id=f"lsif:{range_id}:{target}",
                            name=f"range-{target}",
                            file_path=documents.get(range_id, ""),
                            line=position["line"],
                            character=position["character"],
                            is_definition=True,
                        )
                    )
        return symbols

    # -- SCIP --------------------------------------------------------------

    def _read_scip(self, raw: bytes) -> list[StaticIndexSymbol]:
        """Decode the minimal SCIP protobuf subset used for symbol lookup."""
        symbols: list[StaticIndexSymbol] = []
        try:
            for tag, _, payload in _iter_proto_fields(raw):
                # Index field 2 is the repeated Document message.
                if tag != 2:
                    continue
                symbols.extend(self._read_scip_document(payload))
        except ValueError:
            return []
        return symbols

    def _read_scip_document(self, payload: bytes) -> list[StaticIndexSymbol]:
        """Decode one SCIP ``Document`` message into index entries."""
        language = ""
        relative_path = ""
        symbol_names: dict[str, str] = {}
        occurrences: list[tuple[list[int], str, int]] = []

        for tag, wire_type, value in _iter_proto_fields(payload):
            if tag == 1 and wire_type == 2:
                language = value.decode("utf-8", errors="replace")
            elif tag == 2 and wire_type == 2:
                relative_path = value.decode("utf-8", errors="replace")
            elif tag == 4 and wire_type == 2:
                # Document.symbols: repeated SymbolInformation.
                symbol_id = ""
                display_name = ""
                for sub_tag, sub_wire, sub_value in _iter_proto_fields(value):
                    if sub_tag == 1 and sub_wire == 2:
                        symbol_id = sub_value.decode("utf-8", errors="replace")
                    elif sub_tag == 5 and sub_wire == 2:
                        display_name = sub_value.decode("utf-8", errors="replace")
                if symbol_id:
                    symbol_names[symbol_id] = display_name or _scip_symbol_name(symbol_id)
            elif tag == 5 and wire_type == 2:
                # Document.occurrences: repeated Occurrence.
                range_values: list[int] = []
                symbol_id = ""
                roles = 0
                for sub_tag, sub_wire, sub_value in _iter_proto_fields(value):
                    if sub_tag == 1:
                        range_values = _decode_packed_int32(sub_value, sub_wire)
                    elif sub_tag == 2 and sub_wire == 2:
                        symbol_id = sub_value.decode("utf-8", errors="replace")
                    elif sub_tag == 6 and sub_wire == 0:
                        roles = _decode_varint(sub_value)
                occurrences.append((range_values, symbol_id, roles))

        entries: list[StaticIndexSymbol] = []
        for range_values, symbol_id, roles in occurrences:
            line = range_values[0] + 1 if len(range_values) >= 3 else 1
            character = range_values[1] if len(range_values) >= 3 else 0
            entries.append(
                StaticIndexSymbol(
                    symbol_id=symbol_id,
                    name=symbol_names.get(symbol_id) or _scip_symbol_name(symbol_id),
                    file_path=relative_path,
                    line=line,
                    character=character,
                    is_definition=bool(roles & 0x1),
                )
            )
        if language:
            for entry in entries:
                entry.name = entry.name
        return entries

    # -- queries -----------------------------------------------------------

    def find(self, symbol: str, max_results: int = 100) -> list[StaticIndexSymbol]:
        """Return index entries whose symbol matches ``symbol``."""
        needle = (symbol or "").lower()
        if not needle:
            return []
        matches = [
            entry
            for entry in self.symbols
            if needle in entry.name.lower() or needle in entry.symbol_id.lower()
        ]
        return matches[:max_results]


def _looks_like_lsif(raw: bytes) -> bool:
    """Heuristically decide whether a payload is LSIF JSON."""
    head = raw[:512].lstrip()
    return head.startswith(b"{") or head.startswith(b"[")


def _decode_varint(data: bytes) -> int:
    """Decode an unsigned base-128 varint from the front of ``data``."""
    result = 0
    shift = 0
    for byte in data:
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    return result


def _iter_proto_fields(data: bytes) -> Iterable[tuple[int, int, bytes]]:
    """Yield ``(field_number, wire_type, raw_value)`` for a protobuf message.

    Only the subset required by the SCIP reader is supported: varint keys,
    length-delimited (2), fixed64 (1), fixed32 (5) and varint (0) payloads.
    """
    offset = 0
    length = len(data)
    while offset < length:
        key_start = offset
        while offset < length and (data[offset] & 0x80):
            offset += 1
        if offset >= length:
            return
        offset += 1
        key = _decode_varint(data[key_start:offset])
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            start = offset
            while offset < length and (data[offset] & 0x80):
                offset += 1
            if offset >= length:
                return
            offset += 1
            yield field_number, wire_type, data[start:offset]
        elif wire_type == 2:
            start = offset
            while offset < length and (data[offset] & 0x80):
                offset += 1
            if offset >= length:
                return
            offset += 1
            size = _decode_varint(data[start:offset])
            if offset + size > length:
                raise ValueError("truncated length-delimited protobuf field")
            yield field_number, wire_type, data[offset : offset + size]
            offset += size
        elif wire_type == 5:
            if offset + 4 > length:
                raise ValueError("truncated fixed32 protobuf field")
            yield field_number, wire_type, data[offset : offset + 4]
            offset += 4
        elif wire_type == 1:
            if offset + 8 > length:
                raise ValueError("truncated fixed64 protobuf field")
            yield field_number, wire_type, data[offset : offset + 8]
            offset += 8
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")


def _decode_packed_int32(data: bytes, wire_type: int) -> list[int]:
    """Decode a packed repeated int32 protobuf field."""
    if wire_type == 0:
        return [_decode_varint(data)]
    values: list[int] = []
    offset = 0
    length = len(data)
    while offset < length:
        start = offset
        while offset < length and (data[offset] & 0x80):
            offset += 1
        if offset >= length:
            break
        offset += 1
        values.append(_decode_varint(data[start:offset]))
    return values


def _scip_symbol_name(symbol_id: str) -> str:
    """Extract a display name from a SCIP symbol identifier."""
    if not symbol_id:
        return ""
    tail = symbol_id.split("/")[-1]
    tail = tail.split("#")[-1]
    return tail.rstrip(".").replace("`", "")


# ---------------------------------------------------------------------------
# Orchestration facade
# ---------------------------------------------------------------------------


class LanguageServerIntelligenceEngine:
    """Unified semantic query facade over live servers and static indexes.

    Resolution order for every query is:

    1. A prebuilt LSIF/SCIP index, when one has been loaded.
    2. A live language server session for the file's language.
    3. The built-in static AST indexer.

    This guarantees a deterministic, offline-safe answer for every query.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        server_specs: Optional[Iterable[LanguageServerSpec]] = None,
        enable_servers: bool = True,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.enable_servers = enable_servers
        self.server_specs: tuple[LanguageServerSpec, ...] = tuple(
            server_specs if server_specs is not None else DEFAULT_SERVER_SPECS
        )
        self.indexer = StaticSymbolIndexer(self.workspace_root)
        self.static_index = StaticIndexReader()
        self._sessions: dict[str, LanguageServerSession] = {}
        self._lock = threading.Lock()
        self._index_built = False

    # -- configuration -----------------------------------------------------

    def available_servers(self) -> list[dict[str, Any]]:
        """Report configured language servers and their install status."""
        return [
            {
                "name": spec.name,
                "languages": list(spec.languages),
                "command": list(spec.command),
                "installed": spec.is_installed(),
            }
            for spec in self.server_specs
        ]

    def load_static_index(self, index_path: str) -> int:
        """Load a prebuilt LSIF/SCIP index from disk."""
        return self.static_index.load(Path(index_path))

    def ensure_index(self, force: bool = False) -> int:
        """Build the fallback static index (idempotent unless ``force``)."""
        with self._lock:
            if self._index_built and not force:
                return self.indexer.symbol_count()
            count = self.indexer.index()
            self._index_built = True
            return count

    # -- session management ------------------------------------------------

    def _spec_for_language(self, language: str) -> Optional[LanguageServerSpec]:
        """Return the first installed server spec covering ``language``."""
        for spec in self.server_specs:
            if language in spec.languages and spec.is_installed():
                return spec
        return None

    def _session_for_language(self, language: str) -> Optional[LanguageServerSession]:
        """Return (and lazily start) a session for ``language``."""
        if not self.enable_servers:
            return None
        spec = self._spec_for_language(language)
        if spec is None:
            return None
        session = self._sessions.get(spec.name)
        if session is None:
            session = LanguageServerSession(spec, self.workspace_root)
            self._sessions[spec.name] = session
        try:
            started = run_coroutine(session.start())
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.debug("language server %s unavailable: %s", spec.name, exc)
            return None
        return session if started else None

    def shutdown(self) -> None:
        """Stop every live language server session."""
        for session in self._sessions.values():
            with contextlib.suppress(Exception):
                run_coroutine(session.stop())
        self._sessions.clear()

    # -- queries -----------------------------------------------------------

    def _resolve_file(self, file_path: str) -> Optional[Path]:
        """Resolve a user supplied path against the workspace root."""
        if not file_path:
            return None
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        if not candidate.exists():
            return None
        return candidate

    def _language_of(self, file_path: Path) -> str:
        """Map a file onto its language identifier."""
        return LANGUAGE_BY_EXTENSION.get(file_path.suffix, "unknown")

    def query(
        self,
        symbol: str,
        action: str = "definition",
        file_path: str = "",
        line: int = 0,
        character: int = 0,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Execute a semantic query with layered resolution.

        Args:
            symbol: Symbol name to resolve (used by every action).
            action: One of ``definition``, ``references``, ``hover``,
                ``document_symbol``, ``diagnostics``.
            file_path: Optional file providing query context.
            line: Optional 1-based line for position-scoped queries.
            character: Optional 0-based character offset.
            max_results: Upper bound on returned entries.

        Returns:
            A JSON-serializable result dictionary.
        """
        action = (action or "definition").strip().lower()
        resolved = self._resolve_file(file_path)
        language = self._language_of(resolved) if resolved else "unknown"

        if action == "diagnostics":
            return self._diagnostics(resolved, language)

        if action == "document_symbol":
            if resolved is None:
                return self._error("document_symbol requires an existing file_path")
            return self._document_symbols(resolved, language)

        if action == "hover":
            return self._hover(symbol, resolved, language, line, character)

        if action == "references":
            return self._references(symbol, resolved, language, line, character, max_results)

        return self._definition(symbol, resolved, language, line, character)

    def _definition(
        self,
        symbol: str,
        resolved: Optional[Path],
        language: str,
        line: int,
        character: int,
    ) -> dict[str, Any]:
        """Resolve definition locations for a symbol."""
        index_hits = self.static_index.find(symbol)
        if index_hits:
            return self._ok(
                symbol=symbol,
                action="definition",
                source="static_index",
                results=[hit.to_dict() for hit in index_hits],
            )

        session = self._session_for_language(language) if resolved else None
        if session is not None and resolved is not None and line > 0:
            try:
                locations = run_coroutine(
                    self._with_document(session, resolved, language, lambda: session.definition(resolved, line, character))
                )
                if locations:
                    return self._ok(
                        symbol=symbol,
                        action="definition",
                        source=f"language_server:{session.spec.name}",
                        results=[loc.to_dict() for loc in locations],
                    )
            except Exception as exc:
                logger.debug("definition via language server failed: %s", exc)

        self.ensure_index()
        locations = self.indexer.definitions(symbol)
        return self._ok(
            symbol=symbol,
            action="definition",
            source="static_ast_index",
            results=[loc.to_dict() for loc in locations],
        )

    def _references(
        self,
        symbol: str,
        resolved: Optional[Path],
        language: str,
        line: int,
        character: int,
        max_results: int,
    ) -> dict[str, Any]:
        """Resolve reference locations for a symbol."""
        session = self._session_for_language(language) if resolved else None
        if session is not None and resolved is not None and line > 0:
            try:
                locations = run_coroutine(
                    self._with_document(
                        session, resolved, language, lambda: session.references(resolved, line, character)
                    )
                )
                if locations:
                    return self._ok(
                        symbol=symbol,
                        action="references",
                        source=f"language_server:{session.spec.name}",
                        results=[loc.to_dict() for loc in locations[:max_results]],
                    )
            except Exception as exc:
                logger.debug("references via language server failed: %s", exc)

        self.ensure_index()
        locations = self.indexer.references(symbol, max_results=max_results)
        return self._ok(
            symbol=symbol,
            action="references",
            source="static_ast_index",
            results=[loc.to_dict() for loc in locations],
        )

    def _hover(
        self,
        symbol: str,
        resolved: Optional[Path],
        language: str,
        line: int,
        character: int,
    ) -> dict[str, Any]:
        """Resolve hover documentation for a symbol."""
        session = self._session_for_language(language) if resolved else None
        if session is not None and resolved is not None and line > 0:
            try:
                hover = run_coroutine(
                    self._with_document(session, resolved, language, lambda: session.hover(resolved, line, character))
                )
                if hover is not None and hover.contents:
                    payload = hover.to_dict()
                    payload["symbol"] = symbol
                    return self._ok(
                        symbol=symbol,
                        action="hover",
                        source=f"language_server:{session.spec.name}",
                        results=[payload],
                    )
            except Exception as exc:
                logger.debug("hover via language server failed: %s", exc)

        self.ensure_index()
        hover = self.indexer.hover(symbol)
        if hover is None:
            return self._ok(
                symbol=symbol, action="hover", source="static_ast_index", results=[]
            )
        return self._ok(
            symbol=symbol,
            action="hover",
            source="static_ast_index",
            results=[hover.to_dict()],
        )

    def _document_symbols(self, resolved: Path, language: str) -> dict[str, Any]:
        """Resolve the symbol outline of a document."""
        session = self._session_for_language(language)
        if session is not None:
            try:
                symbols = run_coroutine(
                    self._with_document(
                        session, resolved, language, lambda: session.document_symbols(resolved)
                    )
                )
                if symbols:
                    return self._ok(
                        symbol=resolved.name,
                        action="document_symbol",
                        source=f"language_server:{session.spec.name}",
                        results=[sym.to_dict() for sym in symbols],
                    )
            except Exception as exc:
                logger.debug("documentSymbol via language server failed: %s", exc)

        self.ensure_index()
        symbols = self.indexer.document_symbols(_relative_display(resolved, self.workspace_root))
        return self._ok(
            symbol=resolved.name,
            action="document_symbol",
            source="static_ast_index",
            results=[sym.to_dict() for sym in symbols],
        )

    def _diagnostics(self, resolved: Optional[Path], language: str) -> dict[str, Any]:
        """Collect diagnostics published by language servers."""
        results: list[dict[str, Any]] = []
        session = self._session_for_language(language) if resolved else None
        if session is not None and resolved is not None:
            with contextlib.suppress(Exception):
                run_coroutine(session.open_document(resolved, language))
            with contextlib.suppress(Exception):
                run_coroutine(asyncio.sleep(0))
            for file_path, records in session.diagnostics.items():
                results.extend(record.to_dict() for record in records)
        if not results:
            for candidate in self._sessions.values():
                for records in candidate.diagnostics.values():
                    results.extend(record.to_dict() for record in records)
        return self._ok(
            symbol=resolved.name if resolved else "",
            action="diagnostics",
            source="language_server" if results else "none",
            results=results,
        )

    async def _with_document(self, session: LanguageServerSession, resolved: Path, language: str, factory: Any) -> Any:
        """Ensure the document is open before issuing a request."""
        await session.open_document(resolved, language)
        return await factory()

    # -- response helpers --------------------------------------------------

    @staticmethod
    def _ok(symbol: str, action: str, source: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a successful query response."""
        return {
            "success": True,
            "symbol": symbol,
            "action": action,
            "source": source,
            "count": len(results),
            "results": results,
        }

    @staticmethod
    def _error(message: str, **extra: Any) -> dict[str, Any]:
        """Build a failed query response."""
        payload = {"success": False, "error": message, "count": 0, "results": []}
        payload.update(extra)
        return payload


# ---------------------------------------------------------------------------
# Process-wide engine registry
# ---------------------------------------------------------------------------

_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[str, LanguageServerIntelligenceEngine] = {}


def get_language_server_engine(
    workspace_root: str | Path, enable_servers: bool = True
) -> LanguageServerIntelligenceEngine:
    """Return the process-wide engine for ``workspace_root`` (creating it once)."""
    key = str(Path(workspace_root).resolve())
    with _ENGINE_LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = LanguageServerIntelligenceEngine(key, enable_servers=enable_servers)
            _ENGINES[key] = engine
        return engine
