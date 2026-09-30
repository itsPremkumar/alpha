"""Memory-extraction health: make a dead memory subsystem render as dead.

DeerMem's updater is deliberately best-effort -- ``update_memory`` returns
``False`` on any failure and never raises, because a failed extraction must not
take down the run that produced the conversation. That is correct for the
*run*, and it used to be the end of the story: the exception was logged and
dropped, ``GET /api/memory/status`` kept answering ``200`` with
``facts: []``, and the run reported ``status: success``.

Those three facts are indistinguishable from "a brand-new agent that simply has
no memories yet". An operator reading that response cannot tell a working
memory from an amnesiac one, which is exactly the failure this repo's rules
forbid: a degraded state must render as degraded, with its reason.

This module is the missing surface. The updater records what actually happened
on its single choke point (the ``return False`` path) and the Gateway reports
it. It lives inside ``deermem/`` rather than in the host harness because
``backends/deermem/`` may be vendored into another project with no ``alpha.*``
package at all: ``tests/test_deermem_self_contained.py::
test_portability_only_abc_contract_imports_alpha`` pins that directory to
exactly one host import line, so everything the updater needs at runtime has to
be a relative import. The host reads the record through
``MemoryManager.memory_health()`` (a Tier-3 optional hook returning ``None`` by
default), which keeps the dependency direction one-way.

The record is process-local and in-memory on purpose:

* it is an observability record, not durable state -- a restart legitimately
  forgets it, and the status surface says ``"unknown"`` rather than inventing
  a healthy answer before the first attempt;
* nothing here is a second lifecycle owner. Memory remains a subsystem a run
  *observes*; this never writes, mutates, or influences a run status.

The error string is bounded and masked. Provider error bodies are the most
likely place for a leaked key to appear (a rejected ``Authorization`` header is
echoed by several gateways), so :func:`_safe_error_text` truncates and masks
before the text can reach an operator-facing JSON field. That is
defence-in-depth for a field that is already behind Gateway auth, not a
security boundary.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Health must never be able to grow without bound, and must never be able to
# print an unbounded provider body into an API response.
MAX_ERROR_CHARS = 500
MAX_MODEL_CHARS = 200
MAX_SCOPE_CHARS = 200

# Credential-shaped runs are masked rather than returned. Ordered longest-first
# so a longer prefix is not partially masked before a shorter one matches.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._\-~+/=]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|apikey|token|secret|password)\b\s*[:=]\s*\S+"),
)

# ``status = "degraded"`` is the whole point of this module, so the vocabulary
# is fixed and small. Unknown (no attempt recorded yet) is NOT "ok".
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_DISABLED = "disabled"
STATUS_UNKNOWN = "unknown"


class MemoryUpdateDisabled(RuntimeError):
    """No chat model could be resolved for memory extraction.

    Subclasses ``RuntimeError`` so the pre-existing "an update raises" contract
    (and any caller catching ``RuntimeError``) is unchanged; the distinct type
    only lets the status surface name *why* extraction is dark.
    """


class MemoryUpdateRejected(RuntimeError):
    """The conversation could not be turned into an update prompt.

    Raised only to carry the reason into :func:`record_failure`; extraction is
    best-effort and never propagates this to the caller.
    """


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _mask(text: str) -> str:
    masked = text
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub("[redacted]", masked)
    return masked


def _safe_error_text(exc: BaseException) -> str:
    """Render an exception as a bounded, credential-masked single line."""
    text = _mask(str(exc)).replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1].rstrip() + "\u2026"
    return text


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "\u2026"
    return text


def describe_model(llm: Any) -> str | None:
    """Best-effort human label for the chat model backing memory extraction.

    DeerMem's LLM is injected (``host_llm``) or built from
    ``backend_config.model``; neither is a fixed class, so this reads the
    attributes the real factories actually set rather than importing a class
    and isinstance-checking it. Returns ``None`` when extraction has no model
    at all -- which is itself the condition an operator needs to see.
    """
    if llm is None:
        return None
    for attr in ("model", "model_name", "model_id", "deployment_name"):
        value = getattr(llm, attr, None)
        if isinstance(value, str) and value.strip():
            return _clip(value, MAX_MODEL_CHARS)
    return _clip(type(llm).__name__, MAX_MODEL_CHARS)


@dataclass
class MemoryUpdateHealth:
    """Mutable record of what the memory-update LLM has actually done."""

    # ``None`` means "not determined yet" (no backend has published a model),
    # which is deliberately distinct from ``False`` ("determined: no model").
    # Reporting a fresh process as "disabled" would be its own small lie: the
    # backend simply has not been built yet, so nobody knows.
    extraction_enabled: bool | None = None
    model: str | None = None
    model_source: str | None = None
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error_type: str | None = None
    last_error: str | None = None
    last_error_scope: str | None = None
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    _reason: str = field(default="", repr=False)

    @property
    def status(self) -> str:
        """The single most urgent true statement about extraction.

        Ordering is deliberate. An observed failure outranks the static
        configuration verdict, because a recorded failure is a live fact while
        "no model configured" is a fact about the config: an operator who only
        sees the top line must be sent to the thing that is actually happening.
        Both facts stay readable -- ``extraction_enabled`` and ``reason`` are
        not hidden by the choice.
        """
        if self.last_error is not None or self.consecutive_failures:
            return STATUS_DEGRADED
        if self.extraction_enabled is None:
            return STATUS_UNKNOWN
        if not self.extraction_enabled:
            return STATUS_DISABLED
        if self.last_success_at is None:
            return STATUS_UNKNOWN
        return STATUS_OK

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the Gateway status surface."""
        return {
            "status": self.status,
            "reason": self._reason or None,
            "extraction_enabled": self.extraction_enabled,
            "model": self.model,
            "model_source": self.model_source,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error_type": self.last_error_type,
            "last_error": self.last_error,
            "last_error_scope": self.last_error_scope,
            "consecutive_failures": self.consecutive_failures,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
        }


_lock = threading.Lock()
_health = MemoryUpdateHealth()


def get_memory_update_health() -> MemoryUpdateHealth:
    return _health


def reset_memory_update_health() -> None:
    """Clear the record (tests, and an explicit config/backend reload).

    Resets the existing object in place rather than rebinding the module
    global: every holder of :func:`get_memory_update_health` -- the backend's
    ``memory_health()`` included -- keeps observing the live record, so a
    reload cannot leave a caller reading a detached snapshot.
    """
    with _lock:
        _health.extraction_enabled = None
        _health.model = None
        _health.model_source = None
        _health.last_attempt_at = None
        _health.last_success_at = None
        _health.last_failure_at = None
        _health.last_error_type = None
        _health.last_error = None
        _health.last_error_scope = None
        _health.consecutive_failures = 0
        _health.total_successes = 0
        _health.total_failures = 0
        _health._reason = ""


def set_extraction_backend(*, model: str | None, source: str) -> str:
    """Record which model extraction resolved to, and whether it is usable.

    ``source`` names *why* that model was chosen (``"host_llm"`` for the
    inherited app default, ``"backend_config.model"`` for an explicit one,
    ``"none"`` when no model could be built). It is the piece that turns
    ``model: null`` in config.yaml from an ambiguous blank into a stated fact:
    "empty here means the app default, which is *this* model".
    """
    with _lock:
        _health.extraction_enabled = model is not None
        _health.model = model
        _health.model_source = source
        _health._reason = "" if model is not None else f"memory extraction is disabled: no chat model resolved ({source}); updates are dropped and nothing is learned"
    return _health.status


def record_attempt(*, scope: str | None = None) -> None:
    with _lock:
        _health.last_attempt_at = _now()
        if _health.extraction_enabled is None:
            # An attempt happened before (or without) a backend publishing a
            # model. Do not claim either way -- just say it is undetermined.
            _health._reason = _health._reason or "memory extraction attempted before its model was determined"


def record_success() -> None:
    with _lock:
        _health.last_success_at = _now()
        _health.consecutive_failures = 0
        _health.total_successes += 1
        _health.last_error = None
        _health.last_error_type = None
        _health.last_error_scope = None
        _health._reason = ""


def record_failure(exc: BaseException, *, scope: str | None = None, reason: str | None = None) -> None:
    """Record a dropped update, keeping the server's own reason for it."""
    text = _safe_error_text(exc)
    with _lock:
        _health.last_failure_at = _now()
        _health.last_error_type = type(exc).__name__
        _health.last_error = text or type(exc).__name__
        _health.last_error_scope = _clip(scope, MAX_SCOPE_CHARS)
        _health.consecutive_failures += 1
        _health.total_failures += 1
        _health._reason = reason or f"the last memory update failed: {_health.last_error_type}: {_health.last_error}"
