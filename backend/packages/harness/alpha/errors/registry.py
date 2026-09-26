"""The one registry of stable error codes for the whole backend.

Why this module exists
----------------------
Before it, four disjoint taxonomies described the same failures four different
ways: run event types (``alpha.runtime.events.catalog``), the trace event
taxonomy (``alpha.observability.events``), the Gateway auth enum
(``app.gateway.auth.errors.AuthErrorCode``) and the config self-tuning
validation enum (``ValidationErrorCode``). Nothing forced them to agree, so a
failure could be raised with one code, logged with another, and rendered to the
user as bare prose (``app.gateway.services`` collapses exceptions to strings).

This module is the **only** place a stable error code is defined. Everything
else -- the SSE payload, the log record, the metric labels, the recovery hint
and the HTTP status a caller maps -- is derived from one :class:`ErrorDefinition`
here, so a code can never mean two severities or two retry policies.

The contract
------------
* **One code, one meaning.** A code is added here, not invented at a raise site.
* **The user-facing message lives with the code**, not at the call site, so the
  same failure reads the same way in SSE, in a channel reply and in a support
  bundle. Messages never interpolate exception text: the caller already has it.
* **Correlation is a property of the code.** Every occurrence of a code reports
  under the same ``correlation_id``, so one grep or one dashboard panel covers
  the whole family. Per-occurrence identity (request/run/thread) rides the
  existing ``alpha.trace_context`` id and is carried separately in
  :attr:`ReportedError.context` -- it is deliberately *not* a code attribute,
  because a per-occurrence id is not stable and stability is the point.
* **Classification is total.** Any exception maps to exactly one code: by
  :class:`CodedError` first, then by exception type, then by provider status,
  then :data:`INTERNAL_ERROR`. A run can never end with an uncoded failure.
* **Adding a code is a contract change.** Codes are part of the public payload
  surface, so they are append-only: never repurpose or renumber an existing
  code, and never widen a code's severity in place. Add a new one.

Metrics hygiene: labels are drawn only from :class:`ErrorSeverity` and from
this closed code set. A run id, thread id, user id or trace id is never a label
-- see ``alpha/ops/metrics.py`` for why label cardinality is a hard cap.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

__all__ = [
    "ERROR_CODES",
    "CodedError",
    "ErrorDefinition",
    "ErrorSeverity",
    "RecoveryAction",
    "classify",
    "codes_by_severity",
    "get_definition",
    "is_registered",
    "require_definition",
]


class ErrorSeverity(StrEnum):
    """How loud a failure is. Closed set: it is a metric label and an SSE value."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: Final[dict[ErrorSeverity, int]] = {
    ErrorSeverity.INFO: 0,
    ErrorSeverity.WARNING: 1,
    ErrorSeverity.ERROR: 2,
    ErrorSeverity.CRITICAL: 3,
}


class RecoveryAction(StrEnum):
    """What a supervisor is expected to do about a code. Closed set, not prose.

    ``NONE`` means "the failure is already fully reported and nothing should
    retry it". It is the honest default for deterministic failures: silently
    offering a retry is how a permanent misconfiguration becomes a hot loop.
    """

    NONE = "none"
    RETRY = "retry"
    FALLBACK = "fallback"
    RESTART = "restart"
    INVESTIGATE = "investigate"


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """One code's whole published contract.

    ``correlation_id`` is the stable family key (``alpha.errors.model_provider``
    style), *not* a per-occurrence id: it is what a log query, a metric group
    and a support-bundle section all match on.
    """

    code: str
    severity: ErrorSeverity
    retryable: bool
    message: str
    correlation_id: str
    recovery: RecoveryAction = RecoveryAction.NONE
    http_status: int = 500
    #: Exception class names (MRO-agnostic, ``str(exc.__class__.__name__)``) this
    #: code claims. Classification is first-match over the MRO, so a subclass of
    #: a listed name is claimed too.
    exception_types: tuple[str, ...] = ()
    #: Lowercase substrings matched against ``str(exc)`` when no type matched.
    #: Deliberately coarse: a message heuristic must never outrank a real type.
    message_hints: tuple[str, ...] = ()
    #: Numeric provider status codes claimed by this code (429, 503, ...).
    status_codes: frozenset[int] = frozenset()
    #: Substrings of an HTTP status reason/body used by the message heuristic.
    notes: str = ""

    def to_metadata(self) -> dict[str, object]:
        """The published shape shared by SSE, log records and ``run.error``."""
        return {
            "error_code": self.code,
            "severity": self.severity.value,
            "retryable": self.retryable,
            "error_message": self.message,
            "error_correlation_id": self.correlation_id,
            "recovery": self.recovery.value,
        }


def _d(
    code: str,
    severity: ErrorSeverity,
    retryable: bool,
    message: str,
    correlation_id: str,
    *,
    recovery: RecoveryAction = RecoveryAction.NONE,
    http_status: int = 500,
    exception_types: tuple[str, ...] = (),
    message_hints: tuple[str, ...] = (),
    status_codes: frozenset[int] = frozenset(),
    notes: str = "",
) -> ErrorDefinition:
    return ErrorDefinition(
        code=code,
        severity=severity,
        retryable=retryable,
        message=message,
        correlation_id=correlation_id,
        recovery=recovery,
        http_status=http_status,
        exception_types=exception_types,
        message_hints=message_hints,
        status_codes=status_codes,
        notes=notes,
    )


# --------------------------------------------------------------------------
# The registry. Append-only: never renumber, repurpose or re-grade a code.
# --------------------------------------------------------------------------
_DEFINITIONS: Final[tuple[ErrorDefinition, ...]] = (
    # -- generic fallbacks -------------------------------------------------
    _d(
        "INTERNAL_ERROR",
        ErrorSeverity.ERROR,
        False,
        "Something went wrong while running this request.",
        "alpha.errors.internal",
        recovery=RecoveryAction.INVESTIGATE,
        notes="Total fallback so a run can never end with an uncoded failure.",
    ),
    _d(
        "NOT_IMPLEMENTED",
        ErrorSeverity.ERROR,
        False,
        "This capability is not implemented in this deployment.",
        "alpha.errors.not_implemented",
        recovery=RecoveryAction.NONE,
        http_status=501,
        exception_types=("NotImplementedError",),
    ),
    _d(
        "DEPENDENCY_UNAVAILABLE",
        ErrorSeverity.WARNING,
        True,
        "A required dependency is not available right now.",
        "alpha.errors.dependency",
        recovery=RecoveryAction.RETRY,
        http_status=503,
        message_hints=("not available", "no module named", "unavailable"),
    ),
    _d(
        "TIMEOUT",
        ErrorSeverity.WARNING,
        True,
        "This operation took too long and was stopped.",
        "alpha.errors.timeout",
        recovery=RecoveryAction.RETRY,
        http_status=504,
        exception_types=("TimeoutError", "asyncio.TimeoutError", "ReadTimeout", "ConnectTimeout", "TimeoutException", "APITimeoutError"),
        message_hints=("timed out", "timeout", "deadline exceeded"),
    ),
    _d(
        "CANCELLED",
        ErrorSeverity.INFO,
        False,
        "This operation was cancelled before it finished.",
        "alpha.errors.cancelled",
        recovery=RecoveryAction.NONE,
        http_status=499,
        exception_types=("asyncio.CancelledError", "CancelledError"),
    ),
    _d(
        "DEGRADED_MODE",
        ErrorSeverity.WARNING,
        False,
        "Continuing with reduced functionality because part of the system is unavailable.",
        "alpha.errors.degraded",
        recovery=RecoveryAction.FALLBACK,
        http_status=200,
        notes="A deliberate best-effort boundary: the caller told the truth instead of failing.",
    ),
    # -- configuration -----------------------------------------------------
    _d(
        "CONFIG_INVALID",
        ErrorSeverity.CRITICAL,
        False,
        "The configuration is invalid and this request cannot be served.",
        "alpha.errors.config",
        recovery=RecoveryAction.RESTART,
        http_status=500,
        exception_types=(
            "ConfigError",
            "ConfigValidationError",
            "AppConfigError",
        ),
        message_hints=("invalid config", "config.yaml", "misconfigured", "validation failed"),
    ),
    _d(
        "CONFIG_UNAVAILABLE",
        ErrorSeverity.CRITICAL,
        False,
        "The configuration could not be read.",
        "alpha.errors.config",
        recovery=RecoveryAction.RESTART,
        http_status=500,
        exception_types=("ConfigUnavailableError",),
    ),
    # -- authentication / authorization ------------------------------------
    _d(
        "AUTH_REQUIRED",
        ErrorSeverity.WARNING,
        False,
        "Sign in to continue.",
        "alpha.errors.auth",
        recovery=RecoveryAction.NONE,
        http_status=401,
        exception_types=("NotAuthenticated", "AuthenticationError"),
    ),
    _d(
        "AUTH_INVALID_CREDENTIALS",
        ErrorSeverity.WARNING,
        False,
        "That sign-in attempt could not be verified.",
        "alpha.errors.auth",
        recovery=RecoveryAction.NONE,
        http_status=401,
        exception_types=("InvalidCredentialsError", "TokenInvalidError", "TokenExpiredError"),
    ),
    _d(
        "AUTH_FORBIDDEN",
        ErrorSeverity.WARNING,
        False,
        "You do not have access to this resource.",
        "alpha.errors.authz",
        recovery=RecoveryAction.NONE,
        http_status=403,
        exception_types=("PermissionDeniedError", "AuthorizationError", "ForbiddenError"),
    ),
    _d(
        "AUTH_QUOTA_EXCEEDED",
        ErrorSeverity.WARNING,
        False,
        "This account has reached its usage limit.",
        "alpha.errors.authz",
        recovery=RecoveryAction.NONE,
        http_status=403,
        notes="Not retryable: a retry cannot refill a quota, only wait for the reset window.",
    ),
    _d(
        "AUTH_SSO_FAILED",
        ErrorSeverity.WARNING,
        True,
        "Single sign-on could not be completed.",
        "alpha.errors.auth",
        recovery=RecoveryAction.RETRY,
        http_status=502,
        message_hints=("sso", "oidc", "oauth"),
    ),
    # -- model providers ---------------------------------------------------
    _d(
        "MODEL_PROVIDER_UNAVAILABLE",
        ErrorSeverity.ERROR,
        True,
        "The model provider could not be reached. This is usually temporary.",
        "alpha.errors.model_provider",
        recovery=RecoveryAction.RETRY,
        http_status=503,
        exception_types=(
            "APIConnectionError",
            "APITimeoutError",
            "ServiceUnavailableError",
            "InternalServerError",
            "RateLimitError",
        ),
        status_codes=frozenset({500, 502, 503, 504}),
        message_hints=("overloaded", "service unavailable", "bad gateway", "upstream", "connection reset", "server disconnected"),
    ),
    _d(
        "MODEL_PROVIDER_AUTH",
        ErrorSeverity.CRITICAL,
        False,
        "The model provider rejected the configured credentials.",
        "alpha.errors.model_provider",
        recovery=RecoveryAction.RESTART,
        http_status=502,
        exception_types=("AuthenticationError", "PermissionDeniedError"),
        status_codes=frozenset({401, 403}),
        message_hints=("invalid api key", "unauthorized", "invalid_api_key", "permission denied"),
    ),
    _d(
        "MODEL_PROVIDER_QUOTA",
        ErrorSeverity.ERROR,
        False,
        "The model provider reports the quota for this account is exhausted.",
        "alpha.errors.model_provider",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=429,
        status_codes=frozenset({402, 429}),
        message_hints=("quota", "insufficient_quota", "billing", "exhausted", "payment required"),
    ),
    _d(
        "MODEL_PROVIDER_RATE_LIMITED",
        ErrorSeverity.WARNING,
        True,
        "The model provider is rate limiting requests. Retrying shortly.",
        "alpha.errors.model_provider",
        recovery=RecoveryAction.RETRY,
        http_status=429,
        exception_types=("RateLimitError",),
        status_codes=frozenset({429}),
        message_hints=("rate limit", "too many requests", "slow down", "overloaded_error"),
    ),
    _d(
        "MODEL_RESPONSE_INVALID",
        ErrorSeverity.ERROR,
        True,
        "The model returned a response that could not be used.",
        "alpha.errors.model_provider",
        recovery=RecoveryAction.RETRY,
        http_status=502,
        exception_types=("OutputParserException", "JSONDecodeError", "UnicodeDecodeError"),
        message_hints=("could not parse", "invalid json", "malformed output"),
    ),
    # -- runs --------------------------------------------------------------
    _d(
        "RUN_EXECUTION_FAILED",
        ErrorSeverity.ERROR,
        False,
        "This run stopped because of an error.",
        "alpha.errors.run",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=500,
        notes="The generic terminal run failure: the graph raised and the run could not continue.",
    ),
    _d(
        "RUN_CANCELLED",
        ErrorSeverity.INFO,
        False,
        "This run was stopped.",
        "alpha.errors.run",
        recovery=RecoveryAction.NONE,
        http_status=409,
        exception_types=("RunCancelled", "RunInterrupted"),
    ),
    _d(
        "RUN_NOT_FOUND",
        ErrorSeverity.WARNING,
        False,
        "That run no longer exists.",
        "alpha.errors.run",
        recovery=RecoveryAction.NONE,
        http_status=404,
        exception_types=("RunNotFound", "RecordNotFoundError"),
    ),
    _d(
        "RUN_ADMISSION_CONFLICT",
        ErrorSeverity.WARNING,
        True,
        "Another run is already in progress for this thread. Try again shortly.",
        "alpha.errors.run",
        recovery=RecoveryAction.RETRY,
        http_status=409,
        exception_types=("ActiveRunConflict", "ConflictError", "ActiveScheduledRunConflict"),
        message_hints=("already running", "already in progress", "concurrent run"),
    ),
    _d(
        "RUN_OWNERSHIP_LOST",
        ErrorSeverity.WARNING,
        True,
        "This run is no longer owned by this worker.",
        "alpha.errors.run",
        recovery=RecoveryAction.RETRY,
        http_status=409,
        exception_types=("RunOwnershipLost", "LeaseLost"),
        message_hints=("lease", "ownership", "fenced"),
        notes="Retryable because the *new* lease holder continues the run; this worker must not.",
    ),
    _d(
        "RUN_QUOTA_EXCEEDED",
        ErrorSeverity.WARNING,
        False,
        "A run or token budget for this thread is exhausted.",
        "alpha.errors.run",
        recovery=RecoveryAction.NONE,
        http_status=429,
        exception_types=("BudgetExceeded", "TokenBudgetExceeded", "RecursionLimit"),
        message_hints=("budget exceeded", "recursion limit", "token budget"),
    ),
    _d(
        "RUN_DELIVERY_UNVERIFIED",
        ErrorSeverity.ERROR,
        False,
        "This run produced files but could not prove it presented them.",
        "alpha.errors.run",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=500,
    ),
    # -- checkpoints and state ---------------------------------------------
    _d(
        "CHECKPOINT_INCOMPATIBLE",
        ErrorSeverity.ERROR,
        False,
        "This thread was written by an incompatible version and cannot be resumed.",
        "alpha.errors.checkpoint",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=409,
        exception_types=("CheckpointModeMismatchError", "CheckpointChannelModeError", "CheckpointLineageIntegrityError"),
    ),
    _d(
        "CHECKPOINT_WRITE_FAILED",
        ErrorSeverity.CRITICAL,
        True,
        "Conversation state could not be saved.",
        "alpha.errors.checkpoint",
        recovery=RecoveryAction.RESTART,
        http_status=500,
        exception_types=("CheckpointWriteError", "StoreCorruptionError"),
    ),
    _d(
        "STATE_ACCESS_FAILED",
        ErrorSeverity.ERROR,
        True,
        "Conversation state could not be read.",
        "alpha.errors.checkpoint",
        recovery=RecoveryAction.RETRY,
        http_status=500,
    ),
    # -- tools and sandbox -------------------------------------------------
    _d(
        "TOOL_EXECUTION_FAILED",
        ErrorSeverity.ERROR,
        True,
        "A tool call failed while running.",
        "alpha.errors.tool",
        recovery=RecoveryAction.RETRY,
        http_status=500,
        exception_types=("ToolExecutionError", "ToolError", "ToolTimeoutError"),
    ),
    _d(
        "TOOL_NOT_FOUND",
        ErrorSeverity.WARNING,
        False,
        "The requested tool is not available.",
        "alpha.errors.tool",
        recovery=RecoveryAction.NONE,
        http_status=404,
        exception_types=("ToolNotFound", "PackageNotFoundError"),
    ),
    _d(
        "TOOL_SCHEMA_INVALID",
        ErrorSeverity.ERROR,
        False,
        "The tool's arguments did not match its schema.",
        "alpha.errors.tool",
        recovery=RecoveryAction.NONE,
        http_status=400,
        message_hints=("invalid_tool_calls", "tool schema", "validation error for tool"),
    ),
    _d(
        "SANDBOX_UNAVAILABLE",
        ErrorSeverity.ERROR,
        True,
        "The execution sandbox is not available right now.",
        "alpha.errors.sandbox",
        recovery=RecoveryAction.FALLBACK,
        http_status=503,
        exception_types=("SandboxUnavailableError", "SandboxBeingDestroyedError", "SandboxIdentityCollisionError"),
        message_hints=("sandbox",),
    ),
    _d(
        "SANDBOX_TIMEOUT",
        ErrorSeverity.WARNING,
        True,
        "A sandbox command took too long and was stopped.",
        "alpha.errors.sandbox",
        recovery=RecoveryAction.RETRY,
        http_status=504,
        exception_types=("SandboxTimeoutError", "CommandTimeout"),
    ),
    _d(
        "SANDBOX_POLICY_DENIED",
        ErrorSeverity.WARNING,
        False,
        "The sandbox refused this operation under its network or filesystem policy.",
        "alpha.errors.sandbox",
        recovery=RecoveryAction.NONE,
        http_status=403,
        exception_types=("SandboxPolicyDenied", "NetworkPolicyDenied", "EgressDenied"),
    ),
    # -- persistence -------------------------------------------------------
    _d(
        "PERSISTENCE_WRITE_FAILED",
        ErrorSeverity.CRITICAL,
        True,
        "Data could not be written to storage.",
        "alpha.errors.persistence",
        recovery=RecoveryAction.RESTART,
        http_status=500,
        exception_types=("WriteError", "PersistenceError", "DiskFullError"),
    ),
    _d(
        "PERSISTENCE_CONFLICT",
        ErrorSeverity.WARNING,
        True,
        "This record changed while it was being updated. Reload and try again.",
        "alpha.errors.persistence",
        recovery=RecoveryAction.RETRY,
        http_status=409,
        exception_types=("ConflictError", "VersionConflictError", "StaleWriteError"),
    ),
    _d(
        "PERSISTENCE_UNAVAILABLE",
        ErrorSeverity.CRITICAL,
        True,
        "The database is not available right now.",
        "alpha.errors.persistence",
        recovery=RecoveryAction.RESTART,
        http_status=503,
        exception_types=("OperationalError", "InterfaceError", "DBAPIError", "StoreUnavailableError", "CapacityBackendError", "OwnershipBackendError"),
        message_hints=("database is locked", "no such table", "connection refused", "server closed the connection"),
    ),
    _d(
        "PERSISTENCE_CORRUPT",
        ErrorSeverity.CRITICAL,
        False,
        "Stored data is corrupt and could not be read.",
        "alpha.errors.persistence",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=500,
        exception_types=("CorruptionError", "StoreCorruptionError", "MemoryCorruptionError"),
    ),
    # -- event stream ------------------------------------------------------
    _d(
        "EVENT_STREAM_WRITE_FAILED",
        ErrorSeverity.ERROR,
        True,
        "A run event could not be written to the event store.",
        "alpha.errors.event_stream",
        recovery=RecoveryAction.RETRY,
        http_status=500,
    ),
    _d(
        "EVENT_STREAM_UNAVAILABLE",
        ErrorSeverity.WARNING,
        True,
        "The live event stream is temporarily unavailable.",
        "alpha.errors.event_stream",
        recovery=RecoveryAction.RETRY,
        http_status=503,
        exception_types=("StreamBridgeError", "BusClosedError"),
    ),
    # -- MCP ---------------------------------------------------------------
    _d(
        "MCP_CONNECTION_FAILED",
        ErrorSeverity.ERROR,
        True,
        "Could not connect to the MCP server.",
        "alpha.errors.mcp",
        recovery=RecoveryAction.RETRY,
        http_status=503,
        exception_types=("MCPConnectionError", "SseError", "stdio_client",),
        message_hints=("mcp", "model context protocol"),
    ),
    _d(
        "MCP_TOOL_FAILED",
        ErrorSeverity.ERROR,
        True,
        "An MCP tool call failed.",
        "alpha.errors.mcp",
        recovery=RecoveryAction.RETRY,
        http_status=502,
        exception_types=("McpTaskProtocolError", "MCPError", "McpError"),
    ),
    _d(
        "MCP_PROTOCOL_ERROR",
        ErrorSeverity.ERROR,
        False,
        "The MCP server sent a response that does not follow the protocol.",
        "alpha.errors.mcp",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=502,
        exception_types=("McpTaskConfigurationError", "McpProtocolError"),
    ),
    # -- filesystem / storage ----------------------------------------------
    _d(
        "STORAGE_READ_FAILED",
        ErrorSeverity.ERROR,
        True,
        "A file could not be read.",
        "alpha.errors.storage",
        recovery=RecoveryAction.RETRY,
        http_status=500,
        exception_types=(
            "ReadError",
            "IsADirectoryError",
            "PermissionError",
        ),
        message_hints=("read error", "could not read", "is a directory"),
    ),
    _d(
        "STORAGE_WRITE_FAILED",
        ErrorSeverity.ERROR,
        True,
        "A file could not be written.",
        "alpha.errors.storage",
        recovery=RecoveryAction.RETRY,
        http_status=500,
        exception_types=("WriteError", "DiskFullError"),
        message_hints=("read-only file system", "no space left", "disk full", "could not write"),
    ),
    _d(
        "STORAGE_NOT_FOUND",
        ErrorSeverity.WARNING,
        False,
        "That file or directory does not exist.",
        "alpha.errors.storage",
        recovery=RecoveryAction.NONE,
        http_status=404,
        exception_types=("FileNotFoundError", "NotADirectoryError"),
    ),
    # -- extensions and upstream integrations ------------------------------
    _d(
        "EXTENSION_LOAD_FAILED",
        ErrorSeverity.ERROR,
        False,
        "A configured extension failed to load.",
        "alpha.errors.extension",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=500,
        exception_types=("ExtensionLoadError", "ModuleNotFoundError", "ImportError"),
    ),
    _d(
        "UPSTREAM_UNAVAILABLE",
        ErrorSeverity.WARNING,
        True,
        "An external service this feature depends on is unavailable.",
        "alpha.errors.upstream",
        recovery=RecoveryAction.FALLBACK,
        http_status=502,
        exception_types=("LightRAGAPIError", "RAGFlowAPIError", "Mem0APIError", "HonchoRequestError", "AgentEyeUnavailableError"),
    ),
    # -- security ----------------------------------------------------------
    _d(
        "SECURITY_POLICY_VIOLATION",
        ErrorSeverity.CRITICAL,
        False,
        "This request was blocked by a security policy.",
        "alpha.errors.security",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=403,
        exception_types=("SecurityViolation", "PolicyDenied", "PermissionDenied"),
        message_hints=("prompt injection", "not allowed by policy", "path traversal"),
    ),
    _d(
        "INVALID_INPUT",
        ErrorSeverity.WARNING,
        False,
        "That input is not valid.",
        "alpha.errors.input",
        recovery=RecoveryAction.NONE,
        http_status=400,
        exception_types=("InvalidUpdateError", "PathNotAllowed", "WorkspacePathError", "ValueError", "TypeError"),
    ),
)


def _index() -> dict[str, ErrorDefinition]:
    table: dict[str, ErrorDefinition] = {}
    for definition in _DEFINITIONS:
        if definition.code in table:
            raise ValueError(f"duplicate error code in registry: {definition.code!r}")
        table[definition.code] = definition
    return table


#: The single source of truth. Read it; do not shadow it with a local dict.
ERROR_CODES: Final[Mapping[str, ErrorDefinition]] = MappingProxyType(_index())


def get_definition(code: str) -> ErrorDefinition | None:
    """Return the definition for ``code``, or ``None`` when it is unknown."""
    return ERROR_CODES.get(code)


def require_definition(code: str) -> ErrorDefinition:
    """Return the definition for ``code`` or raise.

    Failing loudly on an unknown code is deliberate: a typo'd code that silently
    degraded to ``INTERNAL_ERROR`` would erase the distinction between "we have
    no taxonomy entry" and "we have one and it is wrong".
    """
    definition = ERROR_CODES.get(code)
    if definition is None:
        raise KeyError(f"unknown error code: {code!r}")
    return definition


def is_registered(code: str) -> bool:
    return code in ERROR_CODES


def codes_by_severity(severity: ErrorSeverity) -> tuple[str, ...]:
    """Closed-set view used by metrics assertions and the support bundle."""
    return tuple(sorted(code for code, definition in ERROR_CODES.items() if definition.severity is severity))


class CodedError(RuntimeError):
    """An exception that carries a registry code, so classification is exact.

    Raise this instead of a bare ``RuntimeError`` when the failure is a known
    condition: the code, severity, retry policy and user message then come from
    the registry instead of being re-decided at every raise site.
    """

    def __init__(self, code: str, detail: str = "", *, context: Mapping[str, Any] | None = None) -> None:
        definition = require_definition(code)
        super().__init__(detail or definition.message)
        self.definition = definition
        self.detail = detail
        self.context: dict[str, Any] = dict(context or {})

    @property
    def code(self) -> str:
        return self.definition.code

    def __reduce__(self) -> tuple[Any, ...]:
        # ``context`` is keyword-only, so it travels as unpickling state rather
        # than as a third positional argument. Returning it positionally would
        # make a CodedError unpicklable in exactly the multi-process case that
        # most needs it to survive.
        return (self.__class__, (self.code, self.detail), {"context": self.context})


def _exception_mro_names(exc: BaseException) -> list[str]:
    return [klass.__name__ for klass in type(exc).__mro__]


def _status_of(exc: BaseException) -> int | None:
    for attribute in ("status_code", "http_status", "status", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return status
    return None


def _mro_rank(definition: ErrorDefinition, names: list[str]) -> int | None:
    if not definition.exception_types:
        return None
    for index, name in enumerate(names):
        if name in definition.exception_types:
            return index
    return None


def classify(exc: BaseException | None) -> ErrorDefinition:
    """Map an exception to exactly one registered code. Total, never ``None``.

    Resolution order, strongest evidence first:

    1. :class:`CodedError` -- an explicit code always wins.
    2. Exception type, matched against each definition's ``exception_types``
       over the MRO so a subclass is claimed by its closest registered base.
    3. A numeric provider status code, which distinguishes auth/quota/rate
       limit/5xx without trusting prose.
    4. A message substring hint, lowercase, only for codes that registered one.
    5. :data:`INTERNAL_ERROR`.

    Steps 3 and 4 are heuristics and say so in ``notes``; step 5 guarantees the
    taxonomy is closed so no failure can reach a caller uncoded.
    """
    if isinstance(exc, CodedError):
        return exc.definition
    if exc is None:
        return require_definition("INTERNAL_ERROR")

    names = _exception_mro_names(exc)
    ranked: list[tuple[int, ErrorDefinition]] = []
    for definition in ERROR_CODES.values():
        rank = _mro_rank(definition, names)
        if rank is not None:
            ranked.append((rank, definition))
    if ranked:
        ranked.sort(key=lambda item: (item[0], -item[1].severity.rank))
        return ranked[0][1]

    status = _status_of(exc)
    if status is not None:
        for definition in ERROR_CODES.values():
            if status in definition.status_codes:
                return definition

    text = str(exc).lower()
    if text:
        for definition in ERROR_CODES.values():
            if definition.message_hints and any(hint in text for hint in definition.message_hints):
                return definition

    return require_definition("INTERNAL_ERROR")
