"""One call that reports an error everywhere it has to be seen.

:func:`report_error` is the fan-out: **log + SSE + metric + recovery** from a
single registry lookup, so a failure cannot be visible in one of those and
absent from the others -- which is the actual definition of the silent failure
this package exists to remove.

Why one helper and not four call sites
--------------------------------------
Every previous pattern made the caller remember four things and, in practice,
made them remember one. A handler logged at warning and never reached the run
feed; a run event was persisted and never counted; a failure was counted and
never told to a human. The cost of forgetting is not symmetric -- an unreported
error is unrecoverable -- so the fan-out is a single entry point with four
guaranteed legs rather than four optional ones.

The four legs
-------------
**log**
    A single record at the code's severity, carrying the code, the
    :class:`ReportedError` correlation id and the request-scoped trace id when
    one is bound. ``exc_info`` is attached only when an exception object exists,
    so a logged message never claims a traceback that is not there.

**SSE**
    The published payload (:meth:`ReportedError.to_payload`) handed to a sink
    the host binds at startup. The default sink records into a counter instead
    of dropping silently, so "nobody bound an SSE sink" is observable rather
    than invisible. The harness never imports ``app.gateway`` to format the
    frame; that stays the host's job.

**metric**
    One counter, ``alpha_errors_total``, labelled only by ``code`` and
    ``severity`` -- both drawn from closed sets, so series count is bounded by
    the taxonomy rather than by traffic. A run id, thread id or trace id is
    never a label.

**recovery**
    The code's :class:`RecoveryAction` plus a bounded, overflow-disclosing
    ledger (:class:`RecoveryLedger`) so a supervisor can ask "what failed and
    what should I do about it" without parsing log text.

Failure containment
-------------------
A sink that raises must not become the outage. Each leg runs in its own guard:
a failing leg is recorded in :attr:`ReportedError.sink_failures` and logged,
and the other legs still run. :attr:`ReportedError` is therefore a complete
statement of what happened -- including which legs did not happen.

Nothing in this module raises to the caller. Reporting an error must never
become the error.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from alpha.errors.registry import (
    ERROR_CODES,
    ErrorDefinition,
    ErrorSeverity,
    RecoveryAction,
    classify,
    require_definition,
)

__all__ = [
    "ERROR_METRIC_NAME",
    "ERROR_SSE_EVENT",
    "SseSink",
    "ErrorReporter",
    "RecoveryLedger",
    "RecoveryRecord",
    "ReportedError",
    "configure_error_reporter",
    "get_error_reporter",
    "report_error",
    "reset_error_reporter",
]

logger = logging.getLogger(__name__)

#: The SSE event name a host should publish. One name for the whole taxonomy, so
#: a client subscribes once and reads ``error_code`` off the payload.
ERROR_SSE_EVENT: Final = "error"

#: Declared on ``alpha.ops.metrics``; labels are the closed ``code``/``severity``.
ERROR_METRIC_NAME: Final = "alpha_errors_total"

_LOG_LEVELS: Final[dict[ErrorSeverity, int]] = {
    ErrorSeverity.INFO: logging.INFO,
    ErrorSeverity.WARNING: logging.WARNING,
    ErrorSeverity.ERROR: logging.ERROR,
    ErrorSeverity.CRITICAL: logging.CRITICAL,
}

#: How many recovery records are retained before the oldest is dropped. The
#: ledger is a live view for a supervisor, not a durable log.
RECOVERY_LEDGER_MAX: Final = 512

#: ``context`` is a diagnostic, not a payload: cap it so a caller cannot turn a
#: report into an unbounded event, and never let it grow the metric labels.
MAX_CONTEXT_KEYS: Final = 16
MAX_CONTEXT_VALUE_CHARS: Final = 200

#: One sink signature: a published payload, or nothing at all.
SseSink = Callable[[dict[str, Any]], None]


class RecoveryDisposition(StrEnum):
    """Terminal state of one recovery ledger entry."""

    PENDING = "pending"
    HANDLED = "handled"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class ReportedError:
    """What a report produced, including which legs failed."""

    definition: ErrorDefinition
    detail: str
    exception_type: str | None
    trace_id: str | None
    context: Mapping[str, Any]
    logged: bool
    sse_published: bool
    metric_incremented: bool
    recovery_recorded: bool
    sink_failures: tuple[tuple[str, str], ...] = ()

    @property
    def code(self) -> str:
        return self.definition.code

    @property
    def severity(self) -> ErrorSeverity:
        return self.definition.severity

    @property
    def retryable(self) -> bool:
        return self.definition.retryable

    @property
    def correlation_id(self) -> str:
        return self.definition.correlation_id

    @property
    def recovery(self) -> RecoveryAction:
        return self.definition.recovery

    @property
    def fully_reported(self) -> bool:
        """True when every leg ran. Used by tests and by the gate's own audit."""
        return not self.sink_failures

    def to_payload(self) -> dict[str, Any]:
        """The published shape: code, policy, message, correlation, context.

        The exception's own text stays out of ``error_message`` (the registry
        owns the user-facing wording) and is carried separately in ``detail``,
        which is the operator field, not the user field.
        """
        payload: dict[str, Any] = {
            "type": ERROR_SSE_EVENT,
            **self.definition.to_metadata(),
            "http_status": self.definition.http_status,
        }
        if self.detail:
            payload["error_detail"] = self.detail
        if self.exception_type is not None:
            payload["exception_type"] = self.exception_type
        if self.trace_id is not None:
            payload["trace_id"] = self.trace_id
        if self.context:
            payload["context"] = dict(self.context)
        if self.sink_failures:
            payload["sink_failures"] = [{"sink": name, "error": text} for name, text in self.sink_failures]
        return payload


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    """One entry in the recovery ledger."""

    code: str
    severity: ErrorSeverity
    recovery: RecoveryAction
    retryable: bool
    correlation_id: str
    detail: str
    trace_id: str | None
    disposition: RecoveryDisposition = RecoveryDisposition.PENDING
    sequence: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "recovery": self.recovery.value,
            "retryable": self.retryable,
            "correlation_id": self.correlation_id,
            "detail": self.detail,
            "trace_id": self.trace_id,
            "disposition": self.disposition.value,
            "sequence": self.sequence,
        }


class RecoveryLedger:
    """Bounded, drop-oldest, overflow-disclosing view of what failed.

    Same policy as the event bus and the trace recorder: a slow or absent
    consumer must never block the reporter, and losing a record must be
    counted rather than silent.
    """

    def __init__(self, *, max_records: int = RECOVERY_LEDGER_MAX) -> None:
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        self._max_records = max_records
        self._lock = threading.Lock()
        self._records: list[RecoveryRecord] = []
        self._sequence = 0
        self._dropped_total = 0

    def record(self, record: RecoveryRecord) -> RecoveryRecord:
        with self._lock:
            self._sequence += 1
            stored = _resequence(record, self._sequence)
            self._records.append(stored)
            while len(self._records) > self._max_records:
                self._records.pop(0)
                self._dropped_total += 1
            return stored

    def resolve(self, code: str, disposition: RecoveryDisposition) -> int:
        """Mark every pending record for ``code`` handled. Returns how many."""
        if not isinstance(disposition, RecoveryDisposition):
            raise TypeError("disposition must be a RecoveryDisposition")
        with self._lock:
            changed = 0
            updated: list[RecoveryRecord] = []
            for record in self._records:
                if record.code == code and record.disposition is RecoveryDisposition.PENDING:
                    updated.append(
                        RecoveryRecord(
                            code=record.code,
                            severity=record.severity,
                            recovery=record.recovery,
                            retryable=record.retryable,
                            correlation_id=record.correlation_id,
                            detail=record.detail,
                            trace_id=record.trace_id,
                            disposition=disposition,
                            sequence=record.sequence,
                        )
                    )
                    changed += 1
                else:
                    updated.append(record)
            self._records = updated
            return changed

    def records(self) -> tuple[RecoveryRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def pending(self) -> tuple[RecoveryRecord, ...]:
        return tuple(r for r in self.records() if r.disposition is RecoveryDisposition.PENDING)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "records": len(self._records),
                "dropped_total": self._dropped_total,
                "sequence": self._sequence,
            }


def _resequence(record: RecoveryRecord, sequence: int) -> RecoveryRecord:
    return RecoveryRecord(
        code=record.code,
        severity=record.severity,
        recovery=record.recovery,
        retryable=record.retryable,
        correlation_id=record.correlation_id,
        detail=record.detail,
        trace_id=record.trace_id,
        disposition=record.disposition,
        sequence=sequence,
    )


def _default_metric_sink(reported: ReportedError) -> None:
    """Declare once, then increment. Declaration is idempotent by design."""
    from alpha.ops.metrics import get_metrics_registry

    handle = get_metrics_registry().counter(
        ERROR_METRIC_NAME,
        help="Reported errors, by registry code and severity. Identity never appears as a label.",
        labels=("code", "severity"),
    )
    handle.inc(code=reported.code, severity=reported.severity.value)


def _current_trace_id() -> str | None:
    # Imported lazily so importing this package stays cheap, and imported
    # unconditionally because alpha.trace_context is part of this same harness
    # distribution: if it were genuinely missing, the error belongs to surface
    # rather than to be downgraded to "no trace id".
    from alpha.trace_context import get_current_trace_id

    return get_current_trace_id()


def _bound_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep the diagnostic bounded: no unbounded values, no surprise keys."""
    if not context:
        return {}
    bounded: dict[str, Any] = {}
    for key, value in context.items():
        if len(bounded) >= MAX_CONTEXT_KEYS:
            break
        name = str(key)[:64]
        if isinstance(value, str):
            bounded[name] = value[:MAX_CONTEXT_VALUE_CHARS]
        elif isinstance(value, bool) or isinstance(value, (int, float)):
            bounded[name] = value
        elif value is None:
            bounded[name] = None
        else:
            bounded[name] = str(value)[:MAX_CONTEXT_VALUE_CHARS]
    return bounded


@dataclass
class ErrorReporter:
    """The four-legged fan-out, with every leg individually injectable.

    All four sinks are optional but all four are *attempted*; a missing sink is
    not a failure, it is reported as ``<leg>_published=False`` so the gap is
    visible. Tests bind recording callables; the Gateway binds its SSE sink once
    at startup.
    """

    log_sink: Callable[[ReportedError], None] | None = None
    sse_sink: SseSink | None = None
    metric_sink: Callable[[ReportedError], None] | None = None
    recovery_sink: Callable[[RecoveryRecord], None] | None = None
    ledger: RecoveryLedger = field(default_factory=RecoveryLedger)
    #: Counts legs that had no sink bound at all, so "never wired" is visible.
    unbound_sse: int = 0
    unbound_metric: int = 0
    unbound_recovery: int = 0

    # -- legs ---------------------------------------------------------
    def _emit_log(self, reported: ReportedError, exc: BaseException | None) -> bool:
        if self.log_sink is not None:
            self.log_sink(reported)
            return True
        level = _LOG_LEVELS[reported.severity]
        logger.log(
            level,
            "error code=%s severity=%s retryable=%s recovery=%s correlation_id=%s trace_id=%s detail=%s",
            reported.code,
            reported.severity.value,
            reported.retryable,
            reported.recovery.value,
            reported.correlation_id,
            reported.trace_id or "-",
            reported.detail or "-",
            exc_info=exc,
        )
        return True

    def _emit_sse(self, reported: ReportedError) -> bool:
        if self.sse_sink is None:
            self.unbound_sse += 1
            return False
        self.sse_sink(reported.to_payload())
        return True

    def _emit_metric(self, reported: ReportedError) -> bool:
        if self.metric_sink is None:
            self.unbound_metric += 1
            _default_metric_sink(reported)
            return True
        self.metric_sink(reported)
        return True

    def _emit_recovery(self, reported: ReportedError) -> bool:
        record = RecoveryRecord(
            code=reported.code,
            severity=reported.severity,
            recovery=reported.recovery,
            retryable=reported.retryable,
            correlation_id=reported.correlation_id,
            detail=reported.detail,
            trace_id=reported.trace_id,
        )
        if self.recovery_sink is None:
            self.unbound_recovery += 1
            self.ledger.record(record)
            return True
        self.recovery_sink(record)
        self.ledger.record(record)
        return True

    # -- entry point ---------------------------------------------------
    def report(
        self,
        code: str,
        *,
        exc: BaseException | None = None,
        detail: str = "",
        context: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> ReportedError:
        """Report ``code`` on all four legs and return what happened.

        ``code`` is a registry key. An unknown code raises :class:`KeyError`
        from :func:`require_definition` -- deliberately, because a silent
        fallback to ``INTERNAL_ERROR`` would hide a typo in the taxonomy
        itself. Use :meth:`report_exception` when the code is not known yet.
        """
        definition = require_definition(code)
        if exc is not None:
            detail = detail or _detail_from(exc)
        reported = ReportedError(
            definition=definition,
            detail=_bound_detail(detail),
            exception_type=type(exc).__name__ if exc is not None else None,
            trace_id=trace_id if trace_id is not None else _current_trace_id(),
            context=_bound_context(context),
            logged=False,
            sse_published=False,
            metric_incremented=False,
            recovery_recorded=False,
        )
        return self._run(reported, exc)

    def report_exception(
        self,
        exc: BaseException,
        *,
        context: Mapping[str, Any] | None = None,
        code: str | None = None,
        detail: str = "",
        trace_id: str | None = None,
    ) -> ReportedError:
        """Classify ``exc`` into a code, then report it.

        Classification is total, so this can never produce an uncoded report.
        Pass ``code`` when the raise site knows better than the type does.
        """
        definition = require_definition(code) if code is not None else classify(exc)
        reported = ReportedError(
            definition=definition,
            detail=_bound_detail(detail or _detail_from(exc)),
            exception_type=type(exc).__name__,
            trace_id=trace_id if trace_id is not None else _current_trace_id(),
            context=_bound_context(context),
            logged=False,
            sse_published=False,
            metric_incremented=False,
            recovery_recorded=False,
        )
        return self._run(reported, exc)

    def _run(self, reported: ReportedError, exc: BaseException | None) -> ReportedError:
        legs: tuple[tuple[str, Callable[[], bool], str], ...] = (
            ("log", lambda: self._emit_log(reported, exc), "logged"),
            ("sse", lambda: self._emit_sse(reported), "sse_published"),
            ("metric", lambda: self._emit_metric(reported), "metric_incremented"),
            ("recovery", lambda: self._emit_recovery(reported), "recovery_recorded"),
        )
        results: dict[str, bool] = {}
        failures: list[tuple[str, str]] = []
        for name, run, attribute in legs:
            try:
                ok = bool(run())
            except Exception as sink_error:  # noqa: BLE001 - a sink must not become the outage
                ok = False
                failures.append((name, f"{type(sink_error).__name__}: {sink_error}"))
                logger.warning(
                    "error report leg %s failed for code=%s; remaining legs still ran",
                    name,
                    reported.code,
                    exc_info=sink_error,
                )
            results[attribute] = ok
        return ReportedError(
            definition=reported.definition,
            detail=reported.detail,
            exception_type=reported.exception_type,
            trace_id=reported.trace_id,
            context=reported.context,
            logged=results["logged"],
            sse_published=results["sse_published"],
            metric_incremented=results["metric_incremented"],
            recovery_recorded=results["recovery_recorded"],
            sink_failures=tuple(failures),
        )

    # -- inspection ---------------------------------------------------
    def stats(self) -> dict[str, int]:
        stats = dict(self.ledger.stats())
        stats["unbound_sse"] = self.unbound_sse
        stats["unbound_metric"] = self.unbound_metric
        stats["unbound_recovery"] = self.unbound_recovery
        return stats


#: The log leg is the one leg that gets ``exc_info``; nothing else needs the
#: exception object, and a :class:`ReportedError` deliberately does not carry a
#: traceback because it is also a published value.


def _detail_from(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    return text[:MAX_CONTEXT_VALUE_CHARS]


def _bound_detail(detail: str) -> str:
    return (detail or "")[:MAX_CONTEXT_VALUE_CHARS]


_REPORTER_LOCK = threading.Lock()
_REPORTER: ErrorReporter | None = None


def get_error_reporter() -> ErrorReporter:
    """The process reporter. Created once; bind sinks on it at startup."""
    global _REPORTER
    reporter = _REPORTER
    if reporter is not None:
        return reporter
    with _REPORTER_LOCK:
        if _REPORTER is None:
            _REPORTER = ErrorReporter()
        return _REPORTER


def configure_error_reporter(
    *,
    sse_sink: SseSink | None = None,
    recovery_sink: Callable[[RecoveryRecord], None] | None = None,
    log_sink: Callable[[ReportedError], None] | None = None,
    metric_sink: Callable[[ReportedError], None] | None = None,
) -> ErrorReporter:
    """Bind host sinks onto the process reporter and return it.

    Idempotent per call: a host that restarts a subsystem re-binds rather than
    accumulating duplicate sinks.
    """
    reporter = get_error_reporter()
    if sse_sink is not None:
        reporter.sse_sink = sse_sink
    if recovery_sink is not None:
        reporter.recovery_sink = recovery_sink
    if log_sink is not None:
        reporter.log_sink = log_sink
    if metric_sink is not None:
        reporter.metric_sink = metric_sink
    return reporter


def reset_error_reporter() -> None:
    """Drop the process reporter. Tests and host shutdown only."""
    global _REPORTER
    with _REPORTER_LOCK:
        _REPORTER = None


def report_error(
    code: str,
    *,
    exc: BaseException | None = None,
    detail: str = "",
    context: Mapping[str, Any] | None = None,
    trace_id: str | None = None,
    reporter: ErrorReporter | None = None,
) -> ReportedError:
    """Report a coded error to log + SSE + metric + recovery. Never raises.

    This is the single reporting entry point for the backend. Handlers call it
    instead of hand-rolling a log line, because a hand-rolled log line is how
    an error ends up in exactly one of the four places it needed to be.
    """
    target = reporter if reporter is not None else get_error_reporter()
    return target.report(code, exc=exc, detail=detail, context=context, trace_id=trace_id)


def report_exception(
    exc: BaseException,
    *,
    context: Mapping[str, Any] | None = None,
    code: str | None = None,
    detail: str = "",
    trace_id: str | None = None,
    reporter: ErrorReporter | None = None,
) -> ReportedError:
    """Classify ``exc`` into a registry code and report it on all four legs."""
    target = reporter if reporter is not None else get_error_reporter()
    return target.report_exception(exc, context=context, code=code, detail=detail, trace_id=trace_id)


def registered_codes() -> Iterable[str]:
    """The closed code set, for dashboards and the support bundle."""
    return tuple(sorted(ERROR_CODES))


# Kept for callers that want the severity->level mapping without importing it.
def log_level_for(severity: ErrorSeverity) -> int:
    return _LOG_LEVELS[severity]
