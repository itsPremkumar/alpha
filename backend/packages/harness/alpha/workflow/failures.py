"""Workflow-node failure classification, error signatures and stagnation detection.

Why this exists
---------------
The workflow engine used to decide retries purely by substring-matching a node's
``retry_on_errors`` markers against the raw failure text.  That answers "may I
try again?" but never "WHAT failed?", so the journal could not say why a node
was retried, a repeated identical error looked like four independent transient
faults, and a class of failures that a retry cannot possibly fix (auth,
permission, security) was still retried whenever a marker matched.

Three vocabularies already existed in this repository and this module does not
replace any of them:

* ``alpha.recovery.policies`` — the run-level retry/terminal strategy, which
  remains the single authority for *what to do* after a failure.  A classified
  node failure is bridged onto it with
  :meth:`ClassifiedFailure.recovery_class`, so the workflow plane does not grow
  a second retry policy table.
* ``alpha.bots.failure_reasons`` — the shared work-unit reason codes used by
  swarm tasks, batch items, subagents and bot turns.
  :meth:`ClassifiedFailure.reason_code` maps a node failure onto those codes so
  a node reads as the same work unit everywhere else.
* the six other partial vocabularies found by audit — none of them covers
  node-level failures inside a workflow run, which is the granularity this
  module classifies at.

Scope boundary
--------------
This is a **retry-decision classification**, not a stable error-code taxonomy.
`alpha.observability.taxonomy` declares `alpha.errors.registry` the only place
a stable, customer-facing error *code* may be defined, and this module
deliberately does not compete with it: `NodeFailureClass` answers "may this node
sensibly be attempted again, and is it the same fault as last time?", which is a
different question from "which stable code does this error carry?" Callers that
need a stable code should take one from `alpha.errors.registry`.

Honesty rules
-------------
* Classification is **deterministic substring/keyword matching over the measured
  failure text**, never a model judgement.  Every verdict carries the
  ``matched_rule`` that produced it so an operator can see which rule fired;
  an unmatched failure is ``UNKNOWN_FAILURE``, never an optimistic guess.
* ``retryable`` is a statement about the CLASS, not a promise that a retry will
  work.  ``UNKNOWN_FAILURE`` is retryable within the node's own bounded
  ``RetryPolicy`` because we do not know enough to rule it out — that ceiling is
  disclosed rather than hidden.
* A signature is a *normalized* rendering of the text (identifiers, numbers,
  paths and hex digests collapse away).  Two failures sharing a signature are
  the same fault wearing different digits; that is what makes stagnation
  measurable instead of argued about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ClassifiedFailure",
    "NodeFailureClass",
    "StagnationDetector",
    "StagnationVerdict",
    "classify_node_failure",
    "error_signature",
    "is_non_retryable",
]


class NodeFailureClass(StrEnum):
    """Why a workflow node failed, at node granularity (spec failure taxonomy)."""

    TOOL_FAILURE = "tool_failure"
    NETWORK_FAILURE = "network_failure"
    AUTH_FAILURE = "auth_failure"
    RATE_LIMIT = "rate_limit"
    MODEL_FAILURE = "model_failure"
    MODEL_TIMEOUT = "model_timeout"
    PARSER_FAILURE = "parser_failure"
    VALIDATION_FAILURE = "validation_failure"
    TEST_FAILURE = "test_failure"
    BUILD_FAILURE = "build_failure"
    DEPENDENCY_FAILURE = "dependency_failure"
    ENVIRONMENT_DRIFT = "environment_drift"
    PERMISSION_DENIED = "permission_denied"
    SECURITY_BLOCK = "security_block"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    AGENT_CRASH = "agent_crash"
    WORKER_LOST = "worker_lost"
    CONTEXT_CORRUPTION = "context_corruption"
    UNKNOWN_FAILURE = "unknown_failure"


#: Classes a retry provably cannot fix.  Retrying these only burns budget and
#: repeats the side effect attempt, so they stop the retry loop even when a
#: node's ``retry_on_errors`` marker (for example ``"*"``) would otherwise
#: allow it.  Ordered most-specific-first by the caller; this is the set.
_NON_RETRYABLE: frozenset[NodeFailureClass] = frozenset(
    {
        NodeFailureClass.AUTH_FAILURE,
        NodeFailureClass.PERMISSION_DENIED,
        NodeFailureClass.SECURITY_BLOCK,
        NodeFailureClass.PARSER_FAILURE,
        NodeFailureClass.VALIDATION_FAILURE,
        NodeFailureClass.CONTEXT_CORRUPTION,
    }
)

# Ordered most specific first: a broad rule placed above a narrow one would
# shadow it (``tool`` would swallow ``tool timeout``).  Each entry is
# (class, keywords); a keyword matches case-insensitively as a substring.
_RULES: tuple[tuple[NodeFailureClass, tuple[str, ...]], ...] = (
    # Security / auth / permission first: these outrank any tool or model word
    # that may also appear in the same message.
    (
        NodeFailureClass.SECURITY_BLOCK,
        (
            "security block",
            "blocked by policy",
            "policy violation",
            "prompt injection",
            "guardrail",
            "refused by safety",
            "sandbox violation",
        ),
    ),
    (
        NodeFailureClass.PERMISSION_DENIED,
        (
            "permission denied",
            "access denied",
            "not permitted",
            "forbidden",
            "403",
        ),
    ),
    (
        NodeFailureClass.AUTH_FAILURE,
        (
            "unauthorized",
            "authentication failed",
            "invalid api key",
            "invalid api_key",
            "api key not",
            "token expired",
            "invalid token",
            "401",
            "login failed",
        ),
    ),
    (
        NodeFailureClass.RATE_LIMIT,
        (
            "rate limit",
            "rate_limit",
            "too many requests",
            "429",
            "throttl",
            "quota exceeded",
            "resource has been exhausted",
        ),
    ),
    (
        NodeFailureClass.ENVIRONMENT_DRIFT,
        (
            "environment drift",
            "version mismatch",
            "unexpected node version",
            "unsupported version",
            "command not found",
            "executable not found",
            "no such file or directory",
        ),
    ),
    (
        NodeFailureClass.DEPENDENCY_FAILURE,
        (
            "no module named",
            "module not found",
            "importerror",
            "modulenotfounderror",
            "dependency",
            "cannot find package",
            "version conflict",
            "pep 517",
        ),
    ),
    (
        NodeFailureClass.RESOURCE_EXHAUSTED,
        (
            "out of memory",
            "memoryerror",
            "no space left",
            "disk full",
            "too many open files",
            "enospc",
            "emfile",
            "resource exhausted",
            "enomem",
        ),
    ),
    (
        NodeFailureClass.WORKER_LOST,
        (
            "lease expired",
            "worker lost",
            "worker died",
            "heartbeat missed",
            "heartbeat missing",
            "process gone",
            "stale worker",
        ),
    ),
    (
        NodeFailureClass.AGENT_CRASH,
        (
            "segmentation fault",
            "segfault",
            "core dumped",
            "aborted",
            "killed",
            "traceback (most recent call last)",
        ),
    ),
    (
        NodeFailureClass.TEST_FAILURE,
        (
            "test failed",
            "tests failed",
            "failing test",
            "assertionerror",
            "assert failed",
            "assertion failed",
            "pytest",
            "failed assertions",
        ),
    ),
    (
        NodeFailureClass.BUILD_FAILURE,
        (
            "build failed",
            "compile error",
            "compilation failed",
            "typecheck",
            "type-check",
            "type error",
            "lint failed",
            "lint error",
            "syntaxerror",
            "bundle failed",
            "cannot find module",
        ),
    ),
    (
        NodeFailureClass.PARSER_FAILURE,
        (
            "expecting value",
            "unexpected token",
            "json decode",
            "jsondecodeerror",
            "failed to parse",
            "parse error",
            "malformed response",
            "invalid json",
        ),
    ),
    (
        NodeFailureClass.VALIDATION_FAILURE,
        (
            "validation error",
            "validation failed",
            "schema validation",
            "does not match the expected",
            "invalid input",
            "invalid argument",
            "pydantic",
            "missing required field",
        ),
    ),
    (
        NodeFailureClass.MODEL_TIMEOUT,
        (
            "model timeout",
            "llm timeout",
            "provider timeout",
            "generation timed out",
            "inference timed out",
        ),
    ),
    (
        NodeFailureClass.CONTEXT_CORRUPTION,
        (
            "context corrupted",
            "corrupt context",
            "checksum mismatch",
            "truncated context",
            "context is corrupt",
        ),
    ),
    (
        NodeFailureClass.NETWORK_FAILURE,
        (
            "connection refused",
            "connection reset",
            "connection aborted",
            "connection error",
            "network is unreachable",
            "host unreachable",
            "name resolution",
            "getaddrinfo",
            "temporary failure in name resolution",
            "dns",
            "socket closed",
            "broken pipe",
            "offline",
            "eai_again",
            "unreachable",
            "ssl",
            "tls",
        ),
    ),
    (
        NodeFailureClass.MODEL_FAILURE,
        (
            "model",
            "provider",
            "upstream",
            "overloaded",
            "service unavailable",
            "internal server error",
            "bad gateway",
            "502",
            "503",
            "504",
            "llm",
            "openai",
            "anthropic",
            "ollama",
        ),
    ),
    (
        NodeFailureClass.TOOL_FAILURE,
        (
            "tool",
            "command failed",
            "exit code",
            "execution failed",
            "script failed",
            "bash",
        ),
    ),
)

# Number of consecutive identical signatures that constitutes stagnation.
DEFAULT_STAGNATION_LIMIT = 3

# Normalisation patterns for a signature, applied in order.  The goal is that
# "connection refused to host 10.0.0.5" and "connection refused to host 10.0.0.9"
# produce ONE signature, because they are one fault, not two.
_SIG_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-f]{7,64}\b"), "<hex>"),  # hashes / digests / short ids
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"[A-Za-z]:\\[^\s'\"]+"), "<path>"),  # windows paths
    (re.compile(r"(/[\w.\-]+)+/"), "<path>"),  # posix paths
    (re.compile(r"\b\d+(?:\.\d+)*\b"), "<n>"),  # version numbers and counts
    (re.compile(r"0x[0-9a-f]+"), "<hex>"),
)


def error_signature(text: str) -> str:
    """Normalize a failure message so two tellings of ONE fault compare equal.

    Identifiers, uuids, paths, hex digests and numbers collapse to placeholders
    and the remainder is whitespace-folded.  The empty string normalizes to
    ``""``: an empty failure message has no signature, and callers must not
    treat two empty messages as evidence of a shared cause.
    """
    lowered = (text or "").lower()
    for pattern, replacement in _SIG_SUBS:
        lowered = pattern.sub(replacement, lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def is_non_retryable(failure_class: NodeFailureClass) -> bool:
    """True when retrying this class cannot possibly change the outcome."""
    return failure_class in _NON_RETRYABLE


@dataclass(frozen=True)
class ClassifiedFailure:
    """One classification verdict, carrying its own provenance."""

    failure_class: NodeFailureClass
    #: The rule that matched, or ``"default:no rule matched"``.  This is what
    #: makes the verdict auditable — never leave it empty.
    matched_rule: str
    #: Normalized signature for stagnation comparison (``""`` if no text).
    signature: str
    #: May the retry loop try again *on class grounds*?  A node's own
    #: ``RetryPolicy`` markers still gate the actual decision.
    retryable: bool

    @property
    def recovery_class(self) -> str:
        """Bridge onto ``alpha.recovery.policies.FailureClass``.

        The run-level vocabulary has seven classes; a node failure that has no
        natural home there maps to ``"unknown"`` rather than being forced into
        a neighbouring class that would mis-route the terminal strategy.
        """
        return _RECOVERY_BRIDGE.get(self.failure_class, "unknown")

    @property
    def reason_code(self) -> str | None:
        """Bridge onto ``alpha.bots.failure_reasons`` codes, or ``None``.

        ``None`` means this class has no counterpart in the shared work-unit
        taxonomy.  Returning the wrong code would make a node look like a
        different kind of work unit, so absence is reported instead.
        """
        return _REASON_BRIDGE.get(self.failure_class)


# node failure class -> alpha.recovery.policies.FailureClass (7-class vocabulary)
_RECOVERY_BRIDGE: dict[NodeFailureClass, str] = {
    NodeFailureClass.RATE_LIMIT: "model_rate_limit",
    NodeFailureClass.MODEL_TIMEOUT: "model_timeout",
    NodeFailureClass.TOOL_FAILURE: "tool_failure",
    NodeFailureClass.CONTEXT_CORRUPTION: "context_overflow",
    NodeFailureClass.AGENT_CRASH: "container_crash",
    NodeFailureClass.WORKER_LOST: "container_crash",
}

# node failure class -> alpha.bots.failure_reasons reason code (shared vocabulary)
_REASON_BRIDGE: dict[NodeFailureClass, str] = {
    NodeFailureClass.AUTH_FAILURE: "provider_auth_or_access",
    NodeFailureClass.PERMISSION_DENIED: "provider_auth_or_access",
    NodeFailureClass.RATE_LIMIT: "provider_rate_limit",
    NodeFailureClass.MODEL_FAILURE: "provider_server_error",
    NodeFailureClass.MODEL_TIMEOUT: "delivery_timeout",
    NodeFailureClass.TOOL_FAILURE: "provider_server_error",
    NodeFailureClass.NETWORK_FAILURE: "runtime_offline",
    NodeFailureClass.CONTEXT_CORRUPTION: "context_overflow",
    NodeFailureClass.AGENT_CRASH: "worker_crash",
    NodeFailureClass.WORKER_LOST: "worker_crash",
    NodeFailureClass.DEPENDENCY_FAILURE: "dependency_failed",
    NodeFailureClass.ENVIRONMENT_DRIFT: "missing_config",
}


def classify_node_failure(text: str, *, exception_type: str | None = None) -> ClassifiedFailure:
    """Classify a measured node failure.

    ``exception_type`` (for example ``"TimeoutError"``) is consulted when the
    message itself matches no rule, so a bare exception type still yields a
    real class instead of ``UNKNOWN_FAILURE``.  The message wins over the type
    when both match: a message carries more information than its type name.
    """
    raw = text or ""
    haystack = raw.lower()

    for failure_class, keywords in _RULES:
        for keyword in keywords:
            if keyword in haystack:
                return ClassifiedFailure(
                    failure_class=failure_class,
                    matched_rule=f"keyword:{keyword}",
                    signature=error_signature(raw),
                    retryable=not is_non_retryable(failure_class),
                )

    if exception_type:
        type_name = exception_type.strip().lower()
        for failure_class, type_rule in _TYPE_RULES:
            if type_rule in type_name:
                return ClassifiedFailure(
                    failure_class=failure_class,
                    matched_rule=f"exception_type:{exception_type}",
                    signature=error_signature(raw),
                    retryable=not is_non_retryable(failure_class),
                )

    return ClassifiedFailure(
        failure_class=NodeFailureClass.UNKNOWN_FAILURE,
        matched_rule="default:no rule matched",
        signature=error_signature(raw),
        retryable=True,
    )


# Exception-type fallback, consulted only when no keyword matched.  A timeout
# is mapped to MODEL_TIMEOUT only when it is explicitly a model/LLM timeout;
# the generic timeout already matched no keyword, so it stays UNKNOWN rather
# than being silently promoted to a class the message did not support.
_TYPE_RULES: tuple[tuple[NodeFailureClass, str], ...] = (
    (NodeFailureClass.PERMISSION_DENIED, "permission"),
    (NodeFailureClass.AUTH_FAILURE, "auth"),
    (NodeFailureClass.RESOURCE_EXHAUSTED, "memory"),
    (NodeFailureClass.PARSER_FAILURE, "decode"),
    (NodeFailureClass.VALIDATION_FAILURE, "validation"),
    (NodeFailureClass.AGENT_CRASH, "crash"),
)


@dataclass(frozen=True)
class StagnationVerdict:
    """Result of observing one failure signature."""

    stagnated: bool
    identical_streak: int
    limit: int
    reason: str


class StagnationDetector:
    """Detect "the same failure N times in a row" and force a strategy change.

    Spec §47/§48: a loop that reproduces one identical error is not making
    progress, and repeating it is not a retry — it is a stall.  The detector is
    deliberately per-node and bounded: it keeps only the current streak plus a
    count of observations, so a long-running run cannot grow an unbounded list
    of historical errors.

    An empty signature never contributes to a streak.  Two failures with no
    text at all are not evidence that the SAME thing went wrong, and counting
    them would fire stagnation on a node that emits no output.
    """

    def __init__(self, *, limit: int = DEFAULT_STAGNATION_LIMIT) -> None:
        if limit < 1:
            raise ValueError(f"stagnation limit must be >= 1, got {limit}")
        self.limit = int(limit)
        self._streak = 0
        self._last_signature = ""
        self.observations = 0

    def observe(self, signature: str) -> StagnationVerdict:
        """Record one failure and report whether the streak has stalled."""
        self.observations += 1
        if not signature:
            # No measurable signature: the streak is neither continued nor
            # cleared, because we learned nothing about sameness either way.
            return StagnationVerdict(
                stagnated=False,
                identical_streak=self._streak,
                limit=self.limit,
                reason="failure carried no measurable signature; sameness is unknown",
            )

        if signature == self._last_signature:
            self._streak += 1
        else:
            self._last_signature = signature
            self._streak = 1

        if self._streak >= self.limit:
            return StagnationVerdict(
                stagnated=True,
                identical_streak=self._streak,
                limit=self.limit,
                reason=(f"STAGNATION_DETECTED: {self._streak} consecutive attempt(s) produced the identical error signature; repeating the unchanged strategy cannot differentiate a transient fault from a deterministic one"),
            )
        return StagnationVerdict(
            stagnated=False,
            identical_streak=self._streak,
            limit=self.limit,
            reason=f"identical error signature seen {self._streak}/{self.limit} time(s)",
        )

    @property
    def stagnant(self) -> bool:
        """True once the streak has reached the limit at least once."""
        return self._streak >= self.limit
