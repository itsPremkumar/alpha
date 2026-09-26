"""One error-code taxonomy, one fan-out, no silent failures.

This package is the only place a stable error code is defined
(:mod:`alpha.errors.registry`) and the only sanctioned way to report one
(:func:`alpha.errors.report_error`). Together they make the four places a
failure has to be seen -- log, SSE, metric, recovery -- one call instead of four
remembered ones.

    from alpha.errors import CodedError, report_error

    raise CodedError("SANDBOX_UNAVAILABLE", detail=f"provider={name}")

    # in a handler that cannot re-raise:
    report_error("EVENT_STREAM_WRITE_FAILED", exc=exc, context={"run_id": run_id})

The CI companion is ``scripts/check_no_silent_failures.py``: it fails the build
when a caught exception is neither re-raised nor reported. A handler that
reports through this package satisfies it by construction.
"""

from __future__ import annotations

from alpha.errors.registry import (
    ERROR_CODES,
    CodedError,
    ErrorDefinition,
    ErrorSeverity,
    RecoveryAction,
    classify,
    codes_by_severity,
    get_definition,
    is_registered,
    require_definition,
)
from alpha.errors.report import (
    ERROR_METRIC_NAME,
    ERROR_SSE_EVENT,
    ErrorReporter,
    RecoveryDisposition,
    RecoveryLedger,
    RecoveryRecord,
    ReportedError,
    configure_error_reporter,
    get_error_reporter,
    log_level_for,
    registered_codes,
    report_error,
    report_exception,
    reset_error_reporter,
)

__all__ = [
    "ERROR_CODES",
    "ERROR_METRIC_NAME",
    "ERROR_SSE_EVENT",
    "CodedError",
    "ErrorDefinition",
    "ErrorReporter",
    "ErrorSeverity",
    "RecoveryAction",
    "RecoveryDisposition",
    "RecoveryLedger",
    "RecoveryRecord",
    "ReportedError",
    "classify",
    "codes_by_severity",
    "configure_error_reporter",
    "get_definition",
    "get_error_reporter",
    "is_registered",
    "log_level_for",
    "registered_codes",
    "report_error",
    "report_exception",
    "require_definition",
    "reset_error_reporter",
]
