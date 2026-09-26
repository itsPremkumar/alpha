"""Typed failure-reason codes for bot turns and DM relay replies.

A closed vocabulary of machine-readable reason codes carried ALONGSIDE the
free-text error fields (additive — old consumers keep working). Mirrors the
Hermes bot-failure taxonomy: platform-side codes come from the transport
layer, agent-side codes are derived from raw provider error text.

The vocabulary is now SHARED, not bot-private. Swarm tasks, subagent batch
items and subagent lifecycle records all fail against these same codes (see
``alpha.runtime.escalation``), so a caller never has to know which subsystem
produced a failure to decide what to do about it:

* :func:`classify_work_failure` derives a code from any work-unit error text
  (batch item, swarm task, subagent, bot turn).
* :func:`failure_class` collapses the codes onto the four classes that decide
  behaviour — ``transient`` (retry), ``crash`` (resume), ``capability``
  (reassign to a successor) and ``exhausted`` (escalate to a human) — plus
  ``permanent`` (a human must fix the environment) and ``cancelled``.
* :func:`decide_failure` turns (reason, attempt, max_attempts) into a bounded
  :class:`FailureDecision`. The bound is the point: an attempt ceiling always
  ends in an escalation instead of another silent retry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# platform-side (assigned by the transport/relay layer)
RUNTIME_OFFLINE = "runtime_offline"
QUEUED_EXPIRED = "queued_expired"
DELIVERY_TIMEOUT = "delivery_timeout"
AGENT_BLOCKED = "agent_blocked"
CANCELLED = "cancelled"

# agent-side (derived from raw agent/provider error text)
PROVIDER_AUTH_OR_ACCESS = "provider_auth_or_access"
PROVIDER_QUOTA_LIMIT = "provider_quota_limit"
PROVIDER_RATE_LIMIT = "provider_rate_limit"
PROVIDER_SERVER_ERROR = "provider_server_error"
CONTEXT_OVERFLOW = "context_overflow"
MISSING_CONFIG = "missing_config"
MODEL_UNAVAILABLE = "model_unavailable"
UNKNOWN = "unknown"

# work-unit-side (assigned by the owner of a bounded work unit: swarm task,
# subagent batch item, subagent lifecycle record, bot turn). These answer the
# question the provider codes cannot: WHO should carry the work next?
CAPABILITY_MISSING = "capability_missing"
WORKER_CRASH = "worker_crash"
ATTEMPTS_EXHAUSTED = "attempts_exhausted"
DEPENDENCY_FAILED = "dependency_failed"

# Transfer reasons. These are NOT failures — nobody broke, work was moved on
# purpose — but they belong in the same closed vocabulary so the handoff ledger
# can record every transfer with a typed reason instead of free text.
HANDOFF_REQUESTED = "handoff_requested"
SUCCESSION_FALLBACK = "succession_fallback"

ALL_REASONS = frozenset(
    {
        RUNTIME_OFFLINE,
        QUEUED_EXPIRED,
        DELIVERY_TIMEOUT,
        AGENT_BLOCKED,
        CANCELLED,
        PROVIDER_AUTH_OR_ACCESS,
        PROVIDER_QUOTA_LIMIT,
        PROVIDER_RATE_LIMIT,
        PROVIDER_SERVER_ERROR,
        CONTEXT_OVERFLOW,
        MISSING_CONFIG,
        MODEL_UNAVAILABLE,
        CAPABILITY_MISSING,
        WORKER_CRASH,
        ATTEMPTS_EXHAUSTED,
        DEPENDENCY_FAILED,
        HANDOFF_REQUESTED,
        SUCCESSION_FALLBACK,
        UNKNOWN,
    }
)

#: Reasons a supervisor may retry automatically without human intervention.
#: A worker crash is retryable by definition — the work was never finished, and
#: a fresh worker resuming it from its checkpoint is the whole point of crash
#: recovery. It is still BOUNDED: see :func:`decide_failure`.
AUTO_RETRYABLE = frozenset({RUNTIME_OFFLINE, DELIVERY_TIMEOUT, PROVIDER_RATE_LIMIT, PROVIDER_SERVER_ERROR, WORKER_CRASH})


def is_auto_retryable(reason: str) -> bool:
    return reason in AUTO_RETRYABLE


# --- Failure classes -------------------------------------------------------
# Four classes decide behaviour, and they are the same four the recovery
# policies used to encode ad hoc per-subsystem. Keeping them here is what lets
# one call site handle a swarm failure and a batch failure identically.

#: The same attempt is expected to succeed later on its own (throttle, offline
#: runtime, provider 5xx). Retry with backoff while the ceiling allows.
FAILURE_CLASS_TRANSIENT = "transient"
#: The worker died or lost its lease before finishing. The work survives in a
#: checkpoint; resume it (from a fresh worker) rather than failing the unit.
FAILURE_CLASS_CRASH = "crash"
#: The worker cannot do this work at all (missing tool/permission/knowledge).
#: Retrying the same worker is pointless; hand the work to a capable successor.
FAILURE_CLASS_CAPABILITY = "capability"
#: The attempt ceiling was reached. A human owns the decision from here.
FAILURE_CLASS_EXHAUSTED = "exhausted"
#: Retrying cannot help (auth, quota, model, overflow, unrunnable dependency).
FAILURE_CLASS_PERMANENT = "permanent"
#: Deliberately stopped; not a failure and never escalated.
FAILURE_CLASS_CANCELLED = "cancelled"
#: Work was moved on purpose. Nobody failed, so no retry/escalation applies.
FAILURE_CLASS_ROUTED = "routed"

FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        FAILURE_CLASS_TRANSIENT,
        FAILURE_CLASS_CRASH,
        FAILURE_CLASS_CAPABILITY,
        FAILURE_CLASS_EXHAUSTED,
        FAILURE_CLASS_PERMANENT,
        FAILURE_CLASS_CANCELLED,
        FAILURE_CLASS_ROUTED,
    }
)

#: Every reason code -> its class. Exhaustive over :data:`ALL_REASONS` on
#: purpose: an unmapped code would silently fall back to ``permanent``.
REASON_CLASSES: dict[str, str] = {
    RUNTIME_OFFLINE: FAILURE_CLASS_TRANSIENT,
    DELIVERY_TIMEOUT: FAILURE_CLASS_TRANSIENT,
    # The work was never attempted, so the queue is the transient part.
    QUEUED_EXPIRED: FAILURE_CLASS_TRANSIENT,
    PROVIDER_RATE_LIMIT: FAILURE_CLASS_TRANSIENT,
    PROVIDER_SERVER_ERROR: FAILURE_CLASS_TRANSIENT,
    WORKER_CRASH: FAILURE_CLASS_CRASH,
    AGENT_BLOCKED: FAILURE_CLASS_CAPABILITY,
    CAPABILITY_MISSING: FAILURE_CLASS_CAPABILITY,
    MISSING_CONFIG: FAILURE_CLASS_CAPABILITY,
    MODEL_UNAVAILABLE: FAILURE_CLASS_CAPABILITY,
    ATTEMPTS_EXHAUSTED: FAILURE_CLASS_EXHAUSTED,
    PROVIDER_AUTH_OR_ACCESS: FAILURE_CLASS_PERMANENT,
    PROVIDER_QUOTA_LIMIT: FAILURE_CLASS_PERMANENT,
    CONTEXT_OVERFLOW: FAILURE_CLASS_PERMANENT,
    DEPENDENCY_FAILED: FAILURE_CLASS_PERMANENT,
    UNKNOWN: FAILURE_CLASS_PERMANENT,
    CANCELLED: FAILURE_CLASS_CANCELLED,
    HANDOFF_REQUESTED: FAILURE_CLASS_ROUTED,
    SUCCESSION_FALLBACK: FAILURE_CLASS_ROUTED,
}


def failure_class(reason: str) -> str:
    """Classify a reason code. An unknown code is ``permanent``, never ``transient``.

    Defaulting an unmapped code to ``permanent`` is the fail-closed choice: an
    unrecognised failure is escalated to a human instead of being retried
    forever against a ceiling nobody counted.
    """
    return REASON_CLASSES.get(reason, FAILURE_CLASS_PERMANENT)


def requires_human(reason: str) -> bool:
    """True when only a human can move this work forward."""
    return failure_class(reason) in (FAILURE_CLASS_EXHAUSTED, FAILURE_CLASS_PERMANENT)


# --- Bounded attempts ------------------------------------------------------

#: Next action for a failed attempt.
ACTION_RETRY = "retry"
ACTION_RESUME = "resume"
ACTION_REASSIGN = "reassign"
ACTION_ESCALATE = "escalate"
ACTION_STOP = "stop"
#: Not a failure at all (a deliberate transfer): nothing to retry or escalate.
ACTION_NONE = "none"


@dataclass(frozen=True)
class FailureDecision:
    """What to do about one failed attempt of one bounded work unit."""

    action: str
    reason: str
    reason_class: str
    attempt: int
    max_attempts: int
    detail: str = ""

    @property
    def should_escalate(self) -> bool:
        return self.action == ACTION_ESCALATE

    @property
    def should_retry(self) -> bool:
        return self.action in (ACTION_RETRY, ACTION_RESUME)

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "reason": self.reason,
            "reason_class": self.reason_class,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "detail": self.detail,
        }


def decide_failure(reason: str, *, attempt: int, max_attempts: int) -> FailureDecision:
    """Decide the next step for attempt ``attempt`` (1-based) of a bounded unit.

    The attempt ceiling is authoritative: once ``attempt >= max_attempts`` the
    unit is escalated with :data:`ATTEMPTS_EXHAUSTED`, whatever the class says.
    Before the ceiling, a class maps to an action:

    * ``transient``  -> retry (the same worker may get through next time),
    * ``crash``      -> resume from the checkpoint with a fresh worker,
    * ``capability`` -> reassign to a successor that can do the work,
    * ``permanent``  -> escalate now, a retry cannot fix it,
    * ``unknown``    -> retry while the ceiling allows, then escalate,
    * ``cancelled``  -> stop,
    * ``routed``     -> nothing to decide (a deliberate transfer, not a fault).
    """
    resolved = reason if reason in ALL_REASONS else UNKNOWN
    klass = failure_class(resolved)
    ceiling = max(1, int(max_attempts))

    if klass == FAILURE_CLASS_ROUTED:
        return FailureDecision(ACTION_NONE, resolved, klass, attempt, ceiling, "deliberate transfer; nobody failed")
    if resolved == CANCELLED:
        return FailureDecision(ACTION_STOP, resolved, klass, attempt, ceiling, "work was cancelled")
    if attempt >= ceiling:
        return FailureDecision(
            ACTION_ESCALATE,
            ATTEMPTS_EXHAUSTED,
            FAILURE_CLASS_EXHAUSTED,
            attempt,
            ceiling,
            f"attempt {attempt}/{ceiling} failed ({klass}: {resolved})",
        )
    if klass == FAILURE_CLASS_TRANSIENT:
        return FailureDecision(ACTION_RETRY, resolved, klass, attempt, ceiling, f"transient {resolved}; attempts remain")
    if klass == FAILURE_CLASS_CRASH:
        return FailureDecision(ACTION_RESUME, resolved, klass, attempt, ceiling, "worker lost its lease mid-attempt; resume from checkpoint")
    if klass == FAILURE_CLASS_CAPABILITY:
        return FailureDecision(ACTION_REASSIGN, resolved, klass, attempt, ceiling, f"{resolved} is a capability gap, not a bad attempt")
    if resolved == UNKNOWN:
        return FailureDecision(ACTION_RETRY, resolved, klass, attempt, ceiling, f"unclassified failure; attempts remain ({attempt}/{ceiling})")
    return FailureDecision(ACTION_ESCALATE, resolved, klass, attempt, ceiling, f"{klass} failure cannot be retried away")


# Classifier precedence: auth outranks quota by design — real provider 401
# bodies often say "invalid, blocked or out of funds".
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (PROVIDER_AUTH_OR_ACCESS, ("401", "403", "unauthorized", "forbidden", "invalid api key", "invalid_api_key", "access denied", "permission denied")),
    (PROVIDER_QUOTA_LIMIT, ("quota", "out of funds", "insufficient funds", "billing", "credit balance")),
    (PROVIDER_RATE_LIMIT, ("429", "rate limit", "rate_limit", "too many requests", "throttl")),
    (PROVIDER_SERVER_ERROR, ("500", "502", "503", "504", "server error", "overloaded", "try again later")),
    (CONTEXT_OVERFLOW, ("context length", "context_length", "too many tokens", "max tokens", "context overflow")),
    (MODEL_UNAVAILABLE, ("model not found", "model_not_found", "model unavailable", "no such model")),
    (MISSING_CONFIG, ("missing config", "not configured", "no api key", "api key missing", "no credentials")),
)


def classify_agent_error(error_text: str | None) -> str:
    """Derive a machine-readable reason from raw agent/provider error text."""
    text = (error_text or "").lower()
    if not text.strip():
        return UNKNOWN
    for reason, needles in _RULES:
        if any(n in text for n in needles):
            return reason
    return UNKNOWN


# Work-unit rules, consulted AFTER the provider rules so a provider error that
# merely mentions a crash ("500 ... worker died") is still classified as the
# provider fault it is. These cover the failures a batch item, a swarm task or
# a subagent can hit that a bot turn never sees.
_WORK_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # A dependency that can never run makes this unit unrunnable, not failed.
    (DEPENDENCY_FAILED, ("dependency failed", "unrunnable: dependency", "blocked by dependency", "dependency cycle")),
    # The worker is the wrong worker: hand the work to a capable successor.
    (
        CAPABILITY_MISSING,
        (
            "capability_missing",
            "capability missing",
            "unknown capability",
            "not capable",
            "unsupported capability",
            "no such tool",
            "tool not found",
            "unknown tool",
            "lacks the skill",
            "missing permission for tool",
        ),
    ),
    # The worker died or lost its lease before the work finished.
    (
        WORKER_CRASH,
        (
            "lease expired",
            "execution lease expired",
            "lease lost",
            "worker crash",
            "worker died",
            "worker lost",
            "worker exited",
            "orphaned worker",
            "process died",
            "process exited",
            "killed by signal",
            "segfault",
            "connection reset by peer",
            "broken pipe",
            "out of memory",
            "oom",
        ),
    ),
)


def classify_work_failure(error_text: str | None) -> str:
    """Derive a reason code from ANY bounded work unit's error text.

    Same closed vocabulary, same precedence, two extra families: the agent
    rules first (so a provider 429 is never mistaken for something else), then
    the work-unit rules. Callers that already hold a code should pass it
    straight through instead of round-tripping error text.
    """
    text = (error_text or "").lower()
    if not text.strip():
        return UNKNOWN
    for reason, needles in _RULES:
        if any(n in text for n in needles):
            return reason
    for reason, needles in _WORK_RULES:
        if any(n in text for n in needles):
            return reason
    return UNKNOWN


_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def is_valid_agent_name(name: str) -> bool:
    return bool(_AGENT_NAME_RE.match(name or ""))
