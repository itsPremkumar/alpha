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
        # ``APITimeoutError`` was claimed here and by
        # MODEL_PROVIDER_UNAVAILABLE. Both claim it at MRO rank 0, so the
        # higher-severity provider code always won and this entry could never
        # fire -- a claim that cannot happen is worse than no claim, because it
        # reads like coverage. The provider keeps it: a provider that did not
        # answer in time genuinely was not reached.
        exception_types=("TimeoutError", "asyncio.TimeoutError", "ReadTimeout", "ConnectTimeout", "TimeoutException"),
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
    # -- connectivity ------------------------------------------------------
    # Appended, not repurposed: before this existed the registry had no network
    # class at all, so a lost link surfaced as MODEL_PROVIDER_UNAVAILABLE or
    # DEPENDENCY_UNAVAILABLE and the run terminalized as a failure. That is the
    # one outcome the durable-runtime contract forbids ("an internet outage must
    # not become a task failure"), so connectivity gets its own family and its
    # own correlation id.
    #
    # Deliberately ONE code, not two. A degraded link that still let the request
    # through is already exactly what DEGRADED_MODE describes -- "the request
    # succeeded, and you should know why" -- and that code is the registry's
    # single disclosed-degradation 2xx by design. A second 2xx meaning the same
    # thing would split one meaning across two codes, which is the mistake this
    # registry exists to prevent. A degraded link that *did* fail the request is
    # this code, or a provider code, depending on what the provider said.
    #
    # A caller distinguishes this from a provider problem by asking
    # alpha.runtime.network.classify_network_error whether the failure *proves*
    # the link is down, rather than by reading message text: a timeout proves
    # nothing and stays a provider problem.
    _d(
        "NETWORK_UNAVAILABLE",
        ErrorSeverity.WARNING,
        True,
        "The network is unreachable, so work that needs connectivity is paused rather than failed. It resumes automatically when the link returns.",
        "alpha.errors.network",
        recovery=RecoveryAction.RETRY,
        http_status=503,
        exception_types=(
            "ConnectionRefusedError",
            "socket.gaierror",
            "gaierror",
        ),
        message_hints=("network is unreachable", "no route to host", "name or service not known", "temporary failure in name resolution"),
        notes="Warning, not error: the task is alive and parked. Only a confirmed outage uses this code, and recovery is a resume rather than a re-execution.",
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
        exception_types=("NotAuthenticated",),
        notes=(
            "Deliberately does NOT claim the bare name ``AuthenticationError``. That name is an openai/anthropic SDK class and is claimed by "
            "MODEL_PROVIDER_AUTH, which grades CRITICAL and recovery=RESTART. Both claimed it at MRO rank 0, so the provider code always won and this entry was "
            "dead -- and a local sign-in failure would have been published as 'the model provider rejected the configured credentials' with an instruction to "
            "restart the process. Alpha's own auth failures raise the specific names claimed here and by AUTH_INVALID_CREDENTIALS."
        ),
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
        exception_types=("AuthorizationError", "ForbiddenError"),
        notes=(
            "Deliberately does NOT claim the bare names ``PermissionDeniedError`` or ``PermissionDenied``. They are openai/anthropic SDK classes owned by "
            "MODEL_PROVIDER_AUTH (CRITICAL, recovery=RESTART) and SECURITY_POLICY_VIOLATION respectively; at an equal MRO rank the higher severity always won, "
            "so these entries were dead. ``PermissionError`` -- the builtin a real local authorization failure raises -- is no longer claimed by "
            "STORAGE_READ_FAILED either, because that code's retry policy does not fit a permission denial; see that code."
        ),
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
        # Phrases, never the bare token. "sso" is a substring of "assorted" and
        # "asserts", so the bare form classified "assorted results returned" as
        # a sign-on failure with retryable=True -- the same hijack that made
        # "lease" claim every message containing "please". The compound
        # spellings are the only ones a real SSO failure actually uses.
        message_hints=("sso ", "sso:", "sso/", "sso callback", "sso redirect", "sso provider", "oidc", "oauth"),
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
        # ``RateLimitError`` and ``APITimeoutError`` were claimed here *and* by
        # MODEL_PROVIDER_RATE_LIMITED and TIMEOUT. This code grades ERROR and
        # those grade WARNING, and classification prefers the higher severity
        # at an equal MRO rank, so both names always landed here: a 429 was
        # published as 503 "could not be reached" and MODEL_PROVIDER_RATE_LIMITED
        # could never fire for its own exception type. A throttle is not an
        # outage, so the throttle keeps the name. APITimeoutError stays here --
        # a provider that did not answer in time genuinely was not reached -- and
        # the dead duplicate was dropped from TIMEOUT instead.
        exception_types=(
            "APIConnectionError",
            "APITimeoutError",
            "ServiceUnavailableError",
            "InternalServerError",
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
    # 402 only. This code and MODEL_PROVIDER_RATE_LIMITED both claimed 429, and
    # because classification takes the first matching definition and this one is
    # defined first, *every* 429 that carried no recognised exception type was
    # published as "The model provider reports the quota for this account is
    # exhausted" with retryable=False and recovery=INVESTIGATE. That is the
    # deterministic/transient inversion the registry exists to prevent: a
    # per-minute throttle, which clears on its own in seconds, was filed as a
    # billing outage and marked not-retryable, so a supervisor's correct action
    # -- retry shortly -- was published as the wrong one. 402 is unambiguously
    # billing and stays here; 429 is the throttle's.
    #
    # The hints follow the same rule alpha.models.fallback.is_credit_exhausted_
    # error already applies at the model boundary: rate-limit phrasing must not
    # be read as credit exhaustion, because "quota" alone is a *rate* limit at
    # several providers. The bare words "quota" and "exhausted" are therefore
    # gone -- they claimed "Disk quota exceeded on /var" and "connection pool
    # exhausted" as provider billing failures.
    _d(
        "MODEL_PROVIDER_QUOTA",
        ErrorSeverity.ERROR,
        False,
        "The model provider reports the quota for this account is exhausted.",
        "alpha.errors.model_provider",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=429,
        status_codes=frozenset({402}),
        message_hints=(
            "insufficient_quota",
            "insufficient credit",
            "insufficient balance",
            "insufficient_credits",
            "insufficient credit",
            "balance is not enough",
            "balance not enough",
            "exceeded your current quota",
            "quota is exhausted",
            "quota exhausted",
            "billing",
            "billing hard limit",
            "payment required",
            "please add credits",
            "add credits",
            "top up your balance",
        ),
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
        # "requests per" and "per minute" are what a provider's own throttle
        # message looks like ("quota exceeded for quota metric
        # RequestsPerMinute"). Without them that message matches no hint here
        # and, now that MODEL_PROVIDER_QUOTA no longer claims the bare word
        # "quota", it would fall through to INTERNAL_ERROR. The same fragments
        # alpha.models.fallback uses to suppress billing detection.
        message_hints=("rate limit", "too many requests", "slow down", "overloaded_error", "requests per", "per minute", "requestsperminute"),
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
        # Owns the bare ``ConflictError`` name because alpha.runtime.runs.manager
        # raises it most often for exactly this ("Thread X already has an
        # active run"). The same class is also raised there for a lost
        # reservation lease and for an active checkpoint write, which are not
        # admission problems -- see the PERSISTENCE_CONFLICT note. The hints
        # below route the ambiguous cases away from the admission wording
        # where the message makes the real cause visible.
        exception_types=("ActiveRunConflict", "ConflictError", "ActiveScheduledRunConflict"),
        message_hints=("already running", "already in progress", "concurrent run", "active run"),
        notes="A ConflictError raised for a lost reservation lease or an active checkpoint write is a different failure; manager.py should raise a distinct type for those. Until it does, the type-level claim resolves them here.",
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
        # Deliberately phrases, never the bare word "lease". Hints are matched
        # as raw substrings of str(exc), and "lease" is a substring of
        # "please", "release" and "unleased", so the bare form claimed every
        # message containing "please" -- including "please retry" and "please
        # add credits" -- and reported it as a lost run lease with
        # retryable=True. The type claims above are the real path; these hints
        # only have to catch a lease failure that arrives untyped.
        message_hints=("lease lost", "lost the lease", "lease expired", "lease was lost", "ownership", "fenced"),
        notes="Retryable because the *new* lease holder continues the run; this worker must not.",
    ),
    # SPLIT, not reworded. This code used to also claim ``RecursionLimit`` and
    # the ``"recursion limit"`` message hint, so a LangGraph
    # ``GraphRecursionError`` -- the graph being too deep, no budget consumed --
    # was reported here as "A run or token budget for this thread is
    # exhausted", sending the operator to token settings instead of at the
    # agent chain. The two causes have different owners and different
    # remediations, so they are two codes. See ``RUN_RECURSION_LIMIT`` below.
    #
    # No ``exception_types`` claim survives. ``RecursionLimit`` was never a
    # class name in any dependency (LangGraph raises ``GraphRecursionError``),
    # and ``TokenBudgetExceeded`` is not a class anywhere: the token budget is
    # enforced by ``TokenBudgetMiddleware``, which sets a stop reason and
    # injects a final-answer instruction rather than raising. The code stays
    # for the explicit path -- ``report_error("RUN_QUOTA_EXCEEDED")`` and
    # ``CodedError("RUN_QUOTA_EXCEEDED")`` at the budget boundary -- which is
    # the only way a *thread* run/token budget is honestly knowable. Every
    # field below is byte-identical to what it was before the split, so a
    # persisted ``RUN_QUOTA_EXCEEDED`` record still reads the same way.
    _d(
        "RUN_QUOTA_EXCEEDED",
        ErrorSeverity.WARNING,
        False,
        "A run or token budget for this thread is exhausted.",
        "alpha.errors.run",
        recovery=RecoveryAction.NONE,
        http_status=429,
        message_hints=("budget exceeded", "token budget"),
        notes="Thread run/token budget only. A graph that ran out of depth is RUN_RECURSION_LIMIT and a per-cycle resource budget is RSI_BUDGET_EXCEEDED; neither is a token budget and neither can be relieved by buying tokens.",
    ),
    # The graph being too deep. Not a resource condition: no budget was
    # consumed, none is exhausted, and retrying the identical graph against the
    # identical limit hits the identical wall. What the operator does about it
    # is raise ``recursion_limit`` or shorten the agent chain, which is why this
    # is its own code rather than a rewording of the budget message -- a
    # combined "a budget or the depth was exhausted" sentence would still send
    # the reader to token settings.
    #
    # Severity is ERROR, not the WARNING the shared code carried: the run
    # produced no answer at all, which is what ``RUN_EXECUTION_FAILED`` also
    # grades ERROR. The HTTP status is 500, not 429, because nothing about the
    # caller is rate-limited and a 429 invites a client to back off and retry a
    # request that will fail identically.
    _d(
        "RUN_RECURSION_LIMIT",
        ErrorSeverity.ERROR,
        False,
        "The agent graph reached its recursion limit and stopped before it could finish. Raise recursion_limit or shorten the agent chain. This is a depth limit, not a spend limit.",
        "alpha.errors.run",
        recovery=RecoveryAction.INVESTIGATE,
        http_status=500,
        exception_types=("GraphRecursionError",),
        message_hints=("recursion limit", "recursion_limit", "graph recursion"),
        notes="LangGraph super-step ceiling, not a budget. The remediation is recursion_limit (configurable; the Gateway clamps it to max_recursion_limit) or a shorter agent chain, never more tokens.",
    ),
    # The RSI per-cycle hard budget in ``alpha.rsi.budgets``. It was reachable
    # only through ``BudgetExceeded``, which this registry also handed to the
    # thread token-budget code, so aborting an improvement cycle for exceeding
    # a diff-line or workspace-MB limit was reported to the user as "A run or
    # token budget for this thread is exhausted" -- a different system, a
    # different resource, and a different remedy (the operator's per-cycle
    # limits in config), all three of which the shared message denied.
    #
    # Warning and not retryable, matching the refusal it describes: the cycle
    # aborts on purpose and nothing retries it until the next scheduled cycle.
    _d(
        "RSI_BUDGET_EXCEEDED",
        ErrorSeverity.WARNING,
        False,
        "The self-improvement cycle stopped because it reached a hard per-cycle resource limit.",
        "alpha.errors.rsi",
        recovery=RecoveryAction.NONE,
        http_status=500,
        exception_types=("BudgetExceeded",),
        message_hints=("budget_exhausted", "budget exhausted"),
        notes="alpha.rsi.budgets.BudgetExceeded, one of wall_time_s / candidate / changed_file / diff_line / workspace_mb. The true figures are in the detail; the limits are operator config, not a token budget.",
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
        # ``StoreCorruptionError`` was claimed here as well as by
        # PERSISTENCE_CORRUPT. Both grade CRITICAL, so the earlier definition won
        # the tie and the corruption code was unreachable for its own type --
        # which meant ``alpha.goals.store.StoreCorruptionError`` ("Invalid goal
        # store snapshot") was published as 'Conversation state could not be
        # saved' with retryable=True and recovery=restart. That is the worst
        # shape of this defect class: a deterministic, unrecoverable corruption
        # inheriting a transient flag and a restart loop, so a supervisor was
        # told to bounce the process over data that will still be corrupt.
        exception_types=("CheckpointWriteError",),
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
        # ``WriteError``/``DiskFullError`` were claimed here and by
        # STORAGE_WRITE_FAILED. This code grades CRITICAL, so it always won and
        # STORAGE_WRITE_FAILED was unreachable for them. Worse, the real class
        # behind the name is ``httpx.WriteError`` -- a socket write failure,
        # i.e. a transport problem -- and reporting it as "Data could not be
        # written to storage" with recovery=RESTART tells an operator to bounce
        # the process over a network blip. The filesystem-specific names belong
        # to the filesystem code, which is also the one whose message hints
        # already name "disk full" and "no space left".
        exception_types=("PersistenceError",),
    ),
    _d(
        "PERSISTENCE_CONFLICT",
        ErrorSeverity.WARNING,
        True,
        "This record changed while it was being updated. Reload and try again.",
        "alpha.errors.persistence",
        recovery=RecoveryAction.RETRY,
        http_status=409,
        # ``ConflictError`` was claimed here and by RUN_ADMISSION_CONFLICT, and
        # the admission code is defined first, so *every* ConflictError in the
        # system was published as "Another run is already in progress for this
        # thread" -- including the one alpha.runtime.runs.manager raises for a
        # lost reservation lease and the one it raises for an active checkpoint
        # write. Neither is an admission problem, and the remediation text
        # ("try again shortly") is wrong for a lost lease. A generic
        # ConflictError is genuinely ambiguous at the type level, so the
        # claim stays on the code that owns the dominant raise site and the
        # ambiguous cases are routed by hint instead. The fix belongs upstream,
        # in manager.py, which is outside this package: see the report.
        exception_types=("VersionConflictError", "StaleWriteError"),
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
        exception_types=(
            "MCPConnectionError",
            "SseError",
            "stdio_client",
        ),
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
        # ``PermissionError`` and ``IsADirectoryError`` are deterministic:
        # neither the process nor a retry changes the mode bits or turns a
        # directory into a file, so ``retryable=True`` here hands a supervisor a
        # hot loop over a permanent condition. ``ReadError`` (httpx) is
        # genuinely transient and keeps the code's policy, so the honest fix is
        # to stop claiming the two permanent builtins here. They fall to
        # STORAGE_NOT_FOUND / INTERNAL_ERROR rather than to a code that says
        # "try again"; inventing a permission code is left to the storage
        # owner, who knows whether a denied read is a policy or a bug.
        exception_types=("ReadError",),
        message_hints=("read error", "could not read"),
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
