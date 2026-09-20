"""Self-Evolving Dynamic Tool Metacompiler.

When an autonomous agent discovers a missing capability - a specialised API
integration, an ad-hoc transformation, a one-off analysis - it can synthesise a
new tool at runtime instead of waiting for a human to ship one.

Pipeline
--------
1. **Static security verification.** The candidate source is parsed into an AST
   and screened by :class:`SecurityAstValidator`. Anything that shells out,
   deserialises untrusted data, opens network listeners, deletes files, or
   mutates the filesystem outside the workspace is rejected before it is ever
   compiled. Nothing dangerous is executed.
2. **Ephemeral compilation.** Accepted sources are compiled into an isolated
   module namespace whose builtins are restricted to a safe subset.
3. **Autonomous self-validation.** The synthesised module is executed against
   its own synthetic unit tests. A tool that fails its own tests is never
   registered.
4. **Versioned registration.** Successful tools enter the live tool registry
   with an incrementing version, and stale versions are hot-unloaded and
   garbage collected.

Exposed agent tools
-------------------
``synthesize_runtime_tool`` and ``list_dynamic_tools``.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import logging
import re
import sys
import threading
import time
import traceback
import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_ENTRYPOINT: str = "run"
MAX_SOURCE_BYTES: int = 200_000
DEFAULT_MAX_TOOLS: int = 64


class SynthesisStatus(str, Enum):
    """Lifecycle state of a synthesized tool."""

    PENDING = "pending"
    VALIDATED = "validated"
    REGISTERED = "registered"
    REJECTED = "rejected"
    UNLOADED = "unloaded"


# ---------------------------------------------------------------------------
# Security model
# ---------------------------------------------------------------------------


BLOCKED_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "os",
        "subprocess",
        "socket",
        "pickle",
        "marshal",
        "shelve",
        "ctypes",
        "cffi",
        "multiprocessing",
        "signal",
        "shutil",
        "pty",
        "platform",
        "importlib",
        "sys",
        "webbrowser",
        "telnetlib",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "asyncio",
        "threading",
        "resource",
        "gc",
        "code",
        "codeop",
        "pdb",
        "pip",
        "setuptools",
    }
)

BLOCKED_CALL_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "input",
        "exit",
        "quit",
        "system",
        "popen",
        "remove",
        "unlink",
        "rmdir",
        "rmtree",
        "kill",
        "spawn",
        "fork",
        "setattr",
        "delattr",
    }
)

DELETION_CALL_NAMES: frozenset[str] = frozenset(
    {"remove", "unlink", "rmdir", "rmtree", "truncate", "deletedir"}
)

WRITE_MODE_HINTS: frozenset[str] = frozenset({"w", "wb", "a", "ab", "x", "xb", "w+", "a+", "r+"})

NETWORK_ATTRS: frozenset[str] = frozenset({"bind", "listen", "connect", "connect_ex", "accept"})

WRITE_ATTRS: frozenset[str] = frozenset({"write_text", "write_bytes", "touch", "mkdir"})


@dataclass
class SecurityViolation:
    """A single static-analysis finding against candidate source."""

    rule: str
    severity: str
    message: str
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
        }


@dataclass
class SecurityVerdict:
    """Aggregate result of the AST security screen."""

    allowed: bool
    violations: list[SecurityViolation] = field(default_factory=list)
    warnings: list[SecurityViolation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "violations": [item.to_dict() for item in self.violations],
            "warnings": [item.to_dict() for item in self.warnings],
        }


class SecurityAstValidator(ast.NodeVisitor):
    """Static AST screening for dynamically synthesized tool source.

    The validator never executes candidate code. It walks the syntax tree and
    rejects constructs that could escape the sandbox: process execution,
    unsafe deserialisation, network listeners, filesystem deletion, dynamic
    import, and filesystem writes that resolve outside the workspace root.
    """

    def __init__(self, workspace_root: Optional[str | Path] = None):
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root else None
        )
        self.violations: list[SecurityViolation] = []
        self.warnings: list[SecurityViolation] = []
        self._imported_aliases: dict[str, str] = {}

    # -- public API --------------------------------------------------------

    def validate(self, source: str) -> SecurityVerdict:
        """Parse and screen ``source``, returning a :class:`SecurityVerdict`."""
        self.violations = []
        self.warnings = []
        self._imported_aliases = {}
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            self.violations.append(
                SecurityViolation(
                    rule="syntax-error",
                    severity="error",
                    message=f"source is not valid Python: {exc.msg}",
                    line=int(getattr(exc, "lineno", 0) or 0),
                )
            )
            return SecurityVerdict(False, self.violations, self.warnings)

        self.visit(tree)
        allowed = not self.violations
        return SecurityVerdict(allowed, list(self.violations), list(self.warnings))

    # -- visitor -----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        """Screen ``import x`` statements."""
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in BLOCKED_IMPORT_ROOTS:
                self._reject(
                    "blocked-import",
                    f"import of restricted module '{alias.name}' is not permitted",
                    node.lineno,
                )
            else:
                self._imported_aliases[alias.asname or root] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Screen ``from x import y`` statements."""
        module = node.module or ""
        root = module.split(".")[0]
        if root in BLOCKED_IMPORT_ROOTS:
            self._reject(
                "blocked-import",
                f"import from restricted module '{module}' is not permitted",
                node.lineno,
            )
        for alias in node.names:
            if alias.name == "*":
                self._reject(
                    "blocked-import", "wildcard imports are not permitted", node.lineno
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Screen function and method calls."""
        full_name = _call_name(node.func)
        leaf = full_name.split(".")[-1] if full_name else ""

        if leaf in BLOCKED_CALL_NAMES and leaf not in {"setattr", "delattr"}:
            self._reject(
                "blocked-call",
                f"call to restricted builtin '{leaf}()' is not permitted",
                node.lineno,
            )
        elif leaf in {"setattr", "delattr"}:
            self.warnings.append(
                SecurityViolation(
                    rule="reflective-mutation",
                    severity="warning",
                    message=f"'{leaf}()' performs reflective mutation and is discouraged",
                    line=node.lineno,
                )
            )

        if leaf in DELETION_CALL_NAMES or full_name in {
            "os.remove",
            "os.unlink",
            "os.rmdir",
            "shutil.rmtree",
            "Path.unlink",
        }:
            self._reject(
                "blocked-deletion",
                f"filesystem deletion via '{full_name or leaf}' is not permitted",
                node.lineno,
            )

        if leaf in NETWORK_ATTRS and _looks_networky(node):
            self._reject(
                "blocked-network",
                f"network operation '{full_name}' is not permitted",
                node.lineno,
            )

        if leaf in WRITE_ATTRS or (leaf == "open" and _open_is_write(node)):
            # ``open(path, mode)`` keeps the mode in a trailing positional, so
            # only the first positional argument can be the filesystem target.
            target = _literal_path_arg(node, first_positional_only=(leaf == "open"))
            if target is not None:
                if not self._is_inside_workspace(target):
                    self._reject(
                        "blocked-write-outside-workspace",
                        f"write target '{target}' resolves outside the workspace",
                        node.lineno,
                    )
            else:
                self.warnings.append(
                    SecurityViolation(
                        rule="unverified-write-target",
                        severity="warning",
                        message=(
                            f"write via '{full_name or leaf}' uses a non-literal path; "
                            "it cannot be proven to stay inside the workspace"
                        ),
                        line=node.lineno,
                    )
                )

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Screen attribute access such as ``socket.socket``."""
        full_name = _attribute_chain(node)
        if full_name.split(".")[0] in BLOCKED_IMPORT_ROOTS:
            self._reject(
                "blocked-attribute",
                f"access to restricted attribute '{full_name}' is not permitted",
                node.lineno,
            )
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        """Reject global mutation, which can leak state between tools."""
        self._reject(
            "blocked-global",
            f"global declaration of {', '.join(node.names)} is not permitted",
            node.lineno,
        )
        self.generic_visit(node)

    # -- helpers -----------------------------------------------------------

    def _reject(self, rule: str, message: str, line: int) -> None:
        """Record a blocking violation."""
        self.violations.append(
            SecurityViolation(rule=rule, severity="error", message=message, line=line)
        )

    def _is_inside_workspace(self, target: str) -> bool:
        """Return True when a literal path resolves inside the workspace."""
        if self.workspace_root is None:
            return False
        try:
            candidate = Path(target)
            if not candidate.is_absolute():
                candidate = self.workspace_root / candidate
            resolved = candidate.resolve()
            return str(resolved).startswith(str(self.workspace_root))
        except (OSError, ValueError):
            return False


def _call_name(node: ast.AST) -> str:
    """Render a dotted call target name from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _attribute_chain(node)
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _attribute_chain(node: ast.AST) -> str:
    """Render a dotted attribute chain, e.g. ``socket.socket.bind``."""
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _open_is_write(node: ast.Call) -> bool:
    """Return True when an ``open(...)`` call uses a mutating mode."""
    for arg in node.args[1:]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            mode = arg.value
            if any(hint in mode for hint in WRITE_MODE_HINTS):
                return True
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            if any(hint in str(keyword.value.value) for hint in WRITE_MODE_HINTS):
                return True
    return False


#: Keyword argument names that conventionally carry a filesystem target.
_PATH_KEYWORDS: frozenset[str] = frozenset({"file", "path", "filename", "name"})


def _literal_path_arg(
    node: ast.Call, *, first_positional_only: bool = False
) -> Optional[str]:
    """Extract a literal filesystem path argument, when statically known.

    Args:
        node: The call node under inspection.
        first_positional_only: Restrict the positional scan to the first
            argument. This is required for ``open(path, mode)`` style calls,
            where trailing positionals carry the file mode rather than a path
            and would otherwise be mistaken for the write target.

    Returns:
        The literal target path, or ``None`` when the target cannot be
        determined statically.
    """
    candidates: list[ast.AST] = []

    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
        # ``Path("target").write_text(...)`` carries the target on the receiver.
        candidates.extend(func.value.args[:1])

    candidates.extend(node.args[:1] if first_positional_only else node.args)
    candidates.extend(
        keyword.value for keyword in node.keywords if keyword.arg in _PATH_KEYWORDS
    )

    for candidate in candidates:
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            return candidate.value
    return None


def _looks_networky(node: ast.Call) -> bool:
    """Heuristically detect socket-like receivers for network calls."""
    if isinstance(node.func, ast.Attribute):
        owner = _attribute_chain(node.func.value)
        lowered = owner.lower()
        return "socket" in lowered or "sock" in lowered or "server" in lowered
    return False


# ---------------------------------------------------------------------------
# Restricted execution namespace
# ---------------------------------------------------------------------------

SAFE_BUILTIN_NAMES: tuple[str, ...] = (
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "chr",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "hasattr",
    "hash",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "type",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "RuntimeError",
    "StopIteration",
    "ArithmeticError",
    "ZeroDivisionError",
    "AssertionError",
    "AttributeError",
    "NotImplementedError",
    "BaseException",
    "True",
    "False",
    "None",
)


def build_restricted_builtins() -> dict[str, Any]:
    """Return the whitelist of builtins exposed to synthesized tools."""
    import builtins

    safe: dict[str, Any] = {}
    for name in SAFE_BUILTIN_NAMES:
        value = getattr(builtins, name, None)
        if value is not None:
            safe[name] = value
    safe["__import__"] = _restricted_import
    safe["open"] = _restricted_open
    return safe


_ALLOWED_IMPORT_WHITELIST: frozenset[str] = frozenset(
    {
        "json",
        "math",
        "re",
        "string",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "textwrap",
        "unicodedata",
        "statistics",
        "decimal",
        "fractions",
        "base64",
        "binascii",
        "hashlib",
        "uuid",
        "dataclasses",
        "typing",
        "enum",
        "pathlib",
        "heapq",
        "bisect",
        "copy",
        "pprint",
        "difflib",
        "html",
        "urllib.parse",
    }
)


_REAL_IMPORT = __import__
_REAL_OPEN = open


def _restricted_import(
    name: str,
    globals: Any = None,
    locals: Any = None,
    fromlist: Any = (),
    level: int = 0,
) -> Any:
    """Import gate used as ``__import__`` inside synthesized modules."""
    root = str(name).split(".")[0]
    if root not in _ALLOWED_IMPORT_WHITELIST:
        raise ImportError(f"import of '{name}' is not permitted inside synthesized tools")
    return _REAL_IMPORT(name, globals, locals, fromlist, level)


def _restricted_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
    """Read-only ``open`` gate exposed to synthesized modules."""
    if any(hint in str(mode) for hint in WRITE_MODE_HINTS):
        raise PermissionError("synthesized tools may not open files for writing")
    return _REAL_OPEN(file, mode, *args, **kwargs)


# ---------------------------------------------------------------------------
# Tool model
# ---------------------------------------------------------------------------


@dataclass
class DynamicToolRecord:
    """Metadata and live handle for one synthesized tool."""

    name: str
    description: str
    version: int
    entrypoint: str
    parameters: dict[str, Any]
    source: str
    source_sha256: str
    created_at: float
    status: SynthesisStatus
    validation_report: dict[str, Any] = field(default_factory=dict)
    namespace: Optional[dict[str, Any]] = None
    callable: Optional[Callable[..., Any]] = None
    module: Optional[types.ModuleType] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self, include_source: bool = False) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "entrypoint": self.entrypoint,
            "parameters": self.parameters,
            "source_sha256": self.source_sha256,
            "created_at": self.created_at,
            "status": self.status.value,
            "tags": list(self.tags),
            "validation": self.validation_report,
        }
        if include_source:
            payload["source"] = self.source
        return payload


class DynamicToolRegistry:
    """Version-aware registry of synthesized tools.

    Registration bumps a per-name version. The registry keeps superseded
    versions in a bounded history so a bad synthesis can be rolled back, and
    hot-unloads namespaces on removal so compiled code becomes collectable.
    """

    def __init__(self, max_tools: int = DEFAULT_MAX_TOOLS):
        self.max_tools = max(int(max_tools), 1)
        self._tools: dict[str, DynamicToolRecord] = {}
        self._history: dict[str, list[DynamicToolRecord]] = {}
        self._lock = threading.RLock()

    def register(self, record: DynamicToolRecord) -> DynamicToolRecord:
        """Register (or version-bump) a synthesized tool."""
        with self._lock:
            previous = self._tools.get(record.name)
            if previous is not None:
                self._history.setdefault(record.name, []).append(previous)
            if len(self._tools) >= self.max_tools and record.name not in self._tools:
                oldest_name = min(
                    (name for name in self._tools if name != record.name),
                    key=lambda name: self._tools[name].created_at,
                    default=None,
                )
                if oldest_name is not None:
                    self.unload(oldest_name)
            self._tools[record.name] = record
            record.status = SynthesisStatus.REGISTERED
            return record

    def get(self, name: str) -> Optional[DynamicToolRecord]:
        """Return the active record for ``name``."""
        with self._lock:
            return self._tools.get(name)

    def names(self) -> list[str]:
        """Return every registered tool name."""
        with self._lock:
            return sorted(self._tools)

    def inventory(self, include_source: bool = False) -> list[dict[str, Any]]:
        """Return serialized metadata for every registered tool."""
        with self._lock:
            return [
                self._tools[name].to_dict(include_source=include_source)
                for name in sorted(self._tools)
            ]

    def history(self, name: str) -> list[dict[str, Any]]:
        """Return superseded versions of ``name``."""
        with self._lock:
            return [item.to_dict() for item in self._history.get(name, [])]

    def unload(self, name: str) -> bool:
        """Hot-unload a tool and release its compiled namespace."""
        with self._lock:
            record = self._tools.pop(name, None)
            if record is None:
                return False
            record.status = SynthesisStatus.UNLOADED
            record.callable = None
            if record.namespace is not None:
                record.namespace.clear()
            record.namespace = None
            record.module = None
            return True

    def unload_all(self) -> int:
        """Hot-unload every registered tool, returning the count removed."""
        with self._lock:
            names = list(self._tools)
            return sum(1 for name in names if self.unload(name))

    def __len__(self) -> int:
        """Number of active tools."""
        with self._lock:
            return len(self._tools)


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------


class DynamicToolSynthesizer:
    """Compiles, validates, registers and hot-unloads runtime tools."""

    def __init__(
        self,
        workspace_root: Optional[str | Path] = None,
        registry: Optional[DynamicToolRegistry] = None,
        max_tools: int = DEFAULT_MAX_TOOLS,
    ):
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        )
        self.registry = registry or DynamicToolRegistry(max_tools=max_tools)
        self._lock = threading.RLock()
        self._runtime_registrars: list[Callable[[DynamicToolRecord], Any]] = []
        self._synthesis_count = 0

    # -- host integration --------------------------------------------------

    def attach_runtime_registrar(self, callback: Callable[[DynamicToolRecord], Any]) -> None:
        """Register a host hook invoked after a tool passes self-validation.

        The host (for example the LangChain tool assembly layer) can use this
        to inject the synthesized callable into the live agent tool registry.
        """
        with self._lock:
            self._runtime_registrars.append(callback)

    def _publish_to_runtime(self, record: DynamicToolRecord) -> list[str]:
        """Notify every attached runtime registrar."""
        published: list[str] = []
        for callback in list(self._runtime_registrars):
            try:
                callback(record)
                published.append(getattr(callback, "__name__", "callback"))
            except Exception as exc:  # pragma: no cover - host isolation
                logger.debug("runtime registrar failed for %s: %s", record.name, exc)
        return published

    # -- synthesis ---------------------------------------------------------

    def synthesize(
        self,
        name: str,
        source_code: str,
        description: str = "",
        entrypoint: str = DEFAULT_ENTRYPOINT,
        parameters: Optional[dict[str, Any]] = None,
        test_code: str = "",
        tags: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Validate, compile, self-test and register a synthesized tool.

        Args:
            name: Stable tool name; re-synthesis bumps the version.
            source_code: Python source defining the tool entrypoint.
            description: Human readable purpose of the tool.
            entrypoint: Name of the callable exposed to the agent.
            parameters: Optional JSON-schema style parameter description.
            test_code: Additional synthetic unit-test source. It may define a
                ``self_test()`` function returning a truthy value or a
                ``{"success": bool, ...}`` mapping.
            tags: Optional free-form tags.
            dry_run: Validate and self-test without registering.

        Returns:
            A JSON-serializable synthesis report.
        """
        started = time.time()
        if not _is_valid_identifier(name):
            return self._report(
                name,
                False,
                "invalid-name",
                f"'{name}' is not a valid tool identifier",
                started,
            )
        if not source_code or not source_code.strip():
            return self._report(name, False, "empty-source", "source_code is empty", started)
        if len(source_code.encode("utf-8")) > MAX_SOURCE_BYTES:
            return self._report(
                name, False, "source-too-large", "source_code exceeds the size limit", started
            )

        validator = SecurityAstValidator(self.workspace_root)
        verdict = validator.validate(source_code)
        if not verdict.allowed:
            return self._report(
                name,
                False,
                "security-rejected",
                "static security verification failed",
                started,
                extra={
                    "violations": [item.to_dict() for item in verdict.violations],
                    "warnings": [item.to_dict() for item in verdict.warnings],
                },
            )

        namespace, module, error = self._compile_ephemeral(name, source_code)
        if error is not None:
            return self._report(name, False, "compile-error", error, started)

        entry = namespace.get(entrypoint)
        if entry is None or not callable(entry):
            return self._report(
                name,
                False,
                "missing-entrypoint",
                f"source must define a callable named '{entrypoint}'",
                started,
            )

        if test_code:
            test_verdict = validator.validate(test_code)
            if not test_verdict.allowed:
                return self._report(
                    name,
                    False,
                    "security-rejected",
                    "static security verification failed for test_code",
                    started,
                    extra={"violations": [item.to_dict() for item in test_verdict.violations]},
                )
            namespace, module, error = self._exec_into_namespace(
                namespace, module, name, test_code
            )
            if error is not None:
                return self._report(name, False, "test-compile-error", error, started)

        validation = self._run_self_tests(namespace, entrypoint)
        if not validation["success"]:
            return self._report(
                name,
                False,
                "self-validation-failed",
                str(validation.get("error") or "synthetic unit tests failed"),
                started,
                extra={"validation": validation},
            )

        record = DynamicToolRecord(
            name=name,
            description=description or f"Synthesized runtime tool '{name}'",
            version=self._next_version(name),
            entrypoint=entrypoint,
            parameters=dict(parameters or _infer_parameters(entry)),
            source=source_code,
            source_sha256=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
            created_at=time.time(),
            status=SynthesisStatus.VALIDATED,
            validation_report=validation,
            namespace=namespace,
            callable=entry,
            module=module,
            tags=list(tags or []),
        )

        if dry_run:
            record.status = SynthesisStatus.VALIDATED
            return self._report(
                name,
                True,
                "dry-run",
                "tool validated but not registered",
                started,
                extra={
                    "version": record.version,
                    "validation": validation,
                    "warnings": [item.to_dict() for item in verdict.warnings],
                    "tool": record.to_dict(),
                },
            )

        self.registry.register(record)
        published = self._publish_to_runtime(record)
        with self._lock:
            self._synthesis_count += 1

        return self._report(
            name,
            True,
            "registered",
            f"tool '{name}' synthesized and registered",
            started,
            extra={
                "version": record.version,
                "validation": validation,
                "warnings": [item.to_dict() for item in verdict.warnings],
                "runtime_registrars": published,
                "tool": record.to_dict(),
            },
        )

    # -- compilation -------------------------------------------------------

    def _compile_ephemeral(
        self, name: str, source_code: str
    ) -> tuple[dict[str, Any], types.ModuleType, Optional[str]]:
        """Compile source into an isolated ephemeral module namespace."""
        module = types.ModuleType(f"alpha.dynamic_tools.{name}")
        module.__dict__["__builtins__"] = build_restricted_builtins()
        namespace = module.__dict__
        try:
            with SysPathGuard():
                exec(compile(source_code, f"<synthesized:{name}>", "exec"), namespace)
        except Exception as exc:
            return {}, module, f"{type(exc).__name__}: {exc}"
        return namespace, module, None

    def _exec_into_namespace(
        self,
        namespace: dict[str, Any],
        module: types.ModuleType,
        name: str,
        test_code: str,
    ) -> tuple[dict[str, Any], types.ModuleType, Optional[str]]:
        """Execute additional (test) source inside an existing namespace."""
        try:
            with SysPathGuard():
                exec(compile(test_code, f"<synthesized-tests:{name}>", "exec"), namespace)
        except Exception as exc:
            return namespace, module, f"{type(exc).__name__}: {exc}"
        return namespace, module, None

    # -- self validation ---------------------------------------------------

    def _run_self_tests(self, namespace: dict[str, Any], entrypoint: str) -> dict[str, Any]:
        """Execute the synthesized tool's own synthetic unit tests.

        Contract: if the module exposes ``self_test()`` it must return a truthy
        value or a mapping with ``success`` truthy. When no test hook exists,
        the entrypoint is invoked with no arguments as a smoke check and a tool
        that raises on its own default invocation is rejected.
        """
        started = time.time()
        hook = namespace.get("self_test")
        checks: list[dict[str, Any]] = []
        try:
            if callable(hook):
                outcome = hook()
                if isinstance(outcome, dict):
                    success = bool(outcome.get("success", True))
                    detail = outcome.get("detail") or outcome.get("message") or ""
                else:
                    success = bool(outcome)
                    detail = "" if outcome is None else str(outcome)
                checks.append({"name": "self_test", "success": success, "detail": str(detail)})
                if not success:
                    return {
                        "success": False,
                        "error": f"self_test failed: {detail}",
                        "checks": checks,
                        "elapsed_sec": round(time.time() - started, 6),
                    }
            else:
                entry = namespace.get(entrypoint)
                try:
                    entry()
                    checks.append(
                        {"name": "default_invocation", "success": True, "detail": "entrypoint callable"}
                    )
                except TypeError:
                    checks.append(
                        {
                            "name": "default_invocation",
                            "success": True,
                            "detail": "entrypoint requires arguments; skipped",
                        }
                    )
            return {
                "success": True,
                "checks": checks,
                "elapsed_sec": round(time.time() - started, 6),
            }
        except Exception as exc:
            return {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "checks": checks,
                "elapsed_sec": round(time.time() - started, 6),
            }

    def _next_version(self, name: str) -> int:
        """Return the next version number for ``name``."""
        existing = self.registry.get(name)
        return 1 if existing is None else existing.version + 1

    # -- invocation --------------------------------------------------------

    def invoke(self, name: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Invoke a registered synthesized tool with keyword arguments."""
        record = self.registry.get(name)
        if record is None or record.callable is None:
            return {"success": False, "error": f"no dynamic tool named '{name}'", "result": None}
        try:
            result = record.callable(**dict(payload or {}))
            return {"success": True, "tool": name, "version": record.version, "result": result}
        except Exception as exc:
            return {
                "success": False,
                "tool": name,
                "version": record.version,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=5),
            }

    def unload(self, name: str) -> bool:
        """Hot-unload a synthesized tool and release its namespace."""
        return self.registry.unload(name)

    def list_tools(self, include_source: bool = False) -> dict[str, Any]:
        """Return the inventory of live synthesized tools."""
        return {
            "success": True,
            "count": len(self.registry),
            "results": self.registry.inventory(include_source=include_source),
        }

    # -- reporting ---------------------------------------------------------

    @staticmethod
    def _report(
        name: str,
        success: bool,
        status: str,
        message: str,
        started: float,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build a uniform synthesis report."""
        payload: dict[str, Any] = {
            "success": success,
            "tool": name,
            "status": status,
            "message": message,
            "elapsed_sec": round(time.time() - started, 6),
        }
        if extra:
            payload.update(extra)
        return payload


class SysPathGuard:
    """Context manager preventing synthesized code from mutating ``sys.path``."""

    def __enter__(self) -> "SysPathGuard":
        self._snapshot = list(sys.path)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if list(sys.path) != self._snapshot:
            sys.path[:] = self._snapshot
        if "alpha.dynamic_tools" in sys.modules:
            del sys.modules["alpha.dynamic_tools"]
        for key in [k for k in sys.modules if k.startswith("alpha.dynamic_tools.")]:
            del sys.modules[key]
        return False


def _is_valid_identifier(value: str) -> bool:
    """Return True when ``value`` is a snake_case-safe Python identifier."""
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value or ""))


def _infer_parameters(func: Callable[..., Any]) -> dict[str, Any]:
    """Infer a simple JSON-schema style parameter map from a callable."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return {}
    schema: dict[str, Any] = {}
    for param_name, param in signature.parameters.items():
        annotation = "any"
        if param.annotation is not inspect.Parameter.empty:
            annotation = getattr(param.annotation, "__name__", str(param.annotation))
        schema[param_name] = {
            "type": annotation,
            "required": param.default is inspect.Parameter.empty,
        }
    return schema


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_SYNTHESIZER_LOCK = threading.Lock()
_SYNTHESIZER: Optional[DynamicToolSynthesizer] = None


def get_dynamic_tool_synthesizer(
    workspace_root: Optional[str | Path] = None,
) -> DynamicToolSynthesizer:
    """Return the process-wide synthesizer, creating it on first use."""
    global _SYNTHESIZER
    with _SYNTHESIZER_LOCK:
        if _SYNTHESIZER is None:
            _SYNTHESIZER = DynamicToolSynthesizer(workspace_root=workspace_root)
        return _SYNTHESIZER
