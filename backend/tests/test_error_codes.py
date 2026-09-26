"""Contract tests for the single error-code registry and its fan-out helper.

These pin the two properties the whole taxonomy rests on:

1. **One owner.** Every code is defined in ``alpha.errors.registry``, codes are
   unique, and every definition carries a complete policy (severity, retryable,
   user-facing message, correlation id, recovery, HTTP status). A code that
   cannot be resolved, classified or reported is the failure mode that
   reintroduces four taxonomies, so it is tested directly.
2. **One fan-out, four legs, no silent loss.** ``report_error`` attempts log,
   SSE, metric and recovery. A sink that raises must not take down the other
   legs, must not raise to the caller, and must be *disclosed* on the result --
   because "the report worked" is only true if all four legs ran.
"""

from __future__ import annotations

import logging
import pickle
import sqlite3

import pytest

from alpha.errors import (
    ERROR_CODES,
    ERROR_METRIC_NAME,
    ERROR_SSE_EVENT,
    CodedError,
    ErrorDefinition,
    ErrorReporter,
    ErrorSeverity,
    RecoveryAction,
    RecoveryDisposition,
    RecoveryLedger,
    ReportedError,
    classify,
    codes_by_severity,
    get_definition,
    is_registered,
    log_level_for,
    report_error,
    report_exception,
    require_definition,
)
from alpha.ops.metrics import get_metrics_registry


class TestRegistryIsTheSingleOwner:
    def test_every_definition_is_complete(self):
        for code, definition in ERROR_CODES.items():
            assert definition.code == code, f"{code} is filed under a mismatched key"
            assert isinstance(definition.severity, ErrorSeverity)
            assert isinstance(definition.retryable, bool)
            assert isinstance(definition.recovery, RecoveryAction)
            assert isinstance(definition, ErrorDefinition)
            assert definition.message.strip(), f"{code} has no user-facing message"
            assert definition.correlation_id.strip(), f"{code} has no correlation id"
            assert definition.correlation_id.startswith("alpha.errors."), f"{code} correlation id is not namespaced"
            assert 100 <= definition.http_status < 600, f"{code} has a non-HTTP status {definition.http_status}"

    def test_only_a_disclosed_degradation_answers_with_2xx(self):
        """A 2xx here means "the request succeeded, and you should know why".

        Exactly one code may say that. Any second one would let a real failure
        answer 200 because it was convenient.
        """
        two_xx = {code for code, definition in ERROR_CODES.items() if 200 <= definition.http_status < 300}
        assert two_xx == {"DEGRADED_MODE"}

    def test_correlation_id_is_a_closed_family_key_not_per_occurrence(self):
        """A correlation id is a stable grouping key, so it must be shared."""
        families: dict[str, set[str]] = {}
        for code, definition in ERROR_CODES.items():
            families.setdefault(definition.correlation_id, set()).add(code)
        internal = families["alpha.errors.internal"]
        assert internal == {"INTERNAL_ERROR"}, "the generic fallback must be the sole member of its family"

    def test_codes_are_unique_and_sorted_iteration_is_stable(self):
        codes = list(ERROR_CODES)
        assert len(codes) == len(set(codes))
        assert all(codes), "an empty code would be unusable as a registry key"

    def test_severity_groups_partition_the_registry(self):
        total = sum(len(codes_by_severity(severity)) for severity in ErrorSeverity)
        assert total == len(ERROR_CODES), "a code is missing from every severity view"

    def test_unknown_code_raises_instead_of_degrading(self):
        """A typo must be loud; silently falling back would hide the typo."""
        assert get_definition("NO_SUCH_CODE") is None
        assert is_registered("NO_SUCH_CODE") is False
        with pytest.raises(KeyError):
            require_definition("NO_SUCH_CODE")

    def test_reporting_an_unknown_code_raises(self):
        with pytest.raises(KeyError):
            report_error("NO_SUCH_CODE")

    def test_every_code_is_a_valid_metric_label_value(self):
        """Labels come from the closed code set, so no code may need sanitising."""
        for code in ERROR_CODES:
            assert len(code) <= 64
            assert all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in code)

    def test_retryable_codes_offer_a_recovery_action(self):
        """A retryable failure that tells nobody what to do is a hot loop."""
        for code, definition in ERROR_CODES.items():
            if definition.retryable:
                assert definition.recovery is not RecoveryAction.NONE, f"{code} is retryable but offers no recovery"


class TestClassification:
    def test_classification_is_total(self):
        for exc in (ValueError("x"), RuntimeError(), MemoryError(), Exception(), KeyError("k")):
            assert is_registered(classify(exc).code), "classification must never yield an unregistered code"

    def test_classify_none_is_the_generic_fallback(self):
        assert classify(None).code == "INTERNAL_ERROR"

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (TimeoutError("slow"), "TIMEOUT"),
            (NotImplementedError(), "NOT_IMPLEMENTED"),
            (FileNotFoundError("nope"), "STORAGE_NOT_FOUND"),
            (sqlite3.OperationalError("database is locked"), "PERSISTENCE_UNAVAILABLE"),
            (ValueError("bad value"), "INVALID_INPUT"),
        ],
    )
    def test_known_types_map_to_their_code(self, exc, expected):
        assert classify(exc).code == expected

    def test_bare_value_error_is_not_treated_as_a_config_failure(self):
        """Regression: ValueError is the most generic exception in Python."""
        assert classify(ValueError("x")).code == "INVALID_INPUT"

    def test_provider_status_disambiguates_without_prose(self):
        class StatusError(Exception):
            status_code = 429

        assert classify(StatusError()).code in {"MODEL_PROVIDER_QUOTA", "MODEL_PROVIDER_RATE_LIMITED"}

    def test_message_hints_only_apply_to_codes_that_declared_them(self):
        assert classify(Exception("insufficient_quota")).code == "MODEL_PROVIDER_QUOTA"
        assert classify(Exception("completely unrelated text")).code == "INTERNAL_ERROR"

    def test_coded_error_always_wins(self):
        exc = CodedError("SANDBOX_TIMEOUT", detail="provider=x")
        assert classify(exc).code == "SANDBOX_TIMEOUT"
        assert classify(RuntimeError("x"), ) is not None
        assert classify(CodedError("NOT_IMPLEMENTED")).code == "NOT_IMPLEMENTED"

    def test_coded_error_survives_a_pickle_round_trip(self):
        exc = CodedError("TIMEOUT", detail="d", context={"k": "v"})
        rebuilt = pickle.loads(pickle.dumps(exc))
        assert rebuilt.code == "TIMEOUT"
        assert rebuilt.detail == "d"
        assert rebuilt.context == {"k": "v"}


class _Recorder:
    """Bound sink that records what it was handed."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, value):
        self.calls.append(value)


def _reporter(**kwargs) -> ErrorReporter:
    return ErrorReporter(log_sink=lambda _r: None, **kwargs)


class TestFanOut:
    def test_all_four_legs_run(self):
        sse, metric, recovery = _Recorder(), _Recorder(), _Recorder()
        reporter = _reporter(sse_sink=sse, metric_sink=metric, recovery_sink=recovery)
        result = reporter.report("SANDBOX_UNAVAILABLE", detail="provider=x")

        assert isinstance(result, ReportedError)
        assert result.logged and result.sse_published and result.metric_incremented and result.recovery_recorded
        assert result.fully_reported
        assert not result.sink_failures
        assert len(sse.calls) == 1 and len(metric.calls) == 1 and len(recovery.calls) == 1

    def test_one_sink_failing_does_not_stop_the_others(self):
        metric, recovery = _Recorder(), _Recorder()

        def boom(_payload):
            raise RuntimeError("sse transport is down")

        reporter = ErrorReporter(log_sink=lambda _r: None, sse_sink=boom, metric_sink=metric, recovery_sink=recovery)
        result = reporter.report("EVENT_STREAM_WRITE_FAILED")

        assert result.sse_published is False
        assert result.metric_incremented and result.recovery_recorded, "a dead SSE leg must not silence the others"
        assert [name for name, _ in result.sink_failures] == ["sse"]
        assert "sse transport is down" in dict(result.sink_failures)["sse"]
        assert not result.fully_reported, "a failed leg must be disclosed, not hidden"

    def test_reporting_never_raises_to_the_caller(self):
        def explode(_reported):
            raise RuntimeError("log sink is broken")

        reporter = ErrorReporter(
            log_sink=explode,
            sse_sink=_Recorder(),
            metric_sink=lambda _r: (_ for _ in ()).throw(RuntimeError("metric boom")),
            recovery_sink=lambda _r: (_ for _ in ()).throw(RuntimeError("recovery boom")),
        )
        result = reporter.report("INTERNAL_ERROR")
        assert {name for name, _ in result.sink_failures} == {"log", "metric", "recovery"}
        assert result.sse_published, "the one healthy leg must still have run"

    def test_unbound_sse_sink_is_counted_not_silent(self):
        """'Nobody bound an SSE sink' must be observable, not invisible."""
        reporter = _reporter()
        result = reporter.report("INTERNAL_ERROR")
        assert result.sse_published is False
        assert reporter.stats()["unbound_sse"] == 1

    def test_recovery_ledger_records_and_resolves(self):
        reporter = _reporter()
        reporter.report("MODEL_PROVIDER_UNAVAILABLE", detail="a")
        reporter.report("MODEL_PROVIDER_UNAVAILABLE", detail="b")
        assert len(reporter.ledger.pending()) == 2
        assert reporter.ledger.resolve("MODEL_PROVIDER_UNAVAILABLE", RecoveryDisposition.HANDLED) == 2
        assert reporter.ledger.pending() == ()

    def test_ledger_discloses_overflow(self):
        ledger = RecoveryLedger(max_records=2)
        for index in range(5):
            reporter = ErrorReporter(log_sink=lambda _r: None, ledger=ledger)
            reporter.report("INTERNAL_ERROR", detail=str(index))
        stats = ledger.stats()
        assert stats["records"] == 2
        assert stats["dropped_total"] == 3, "dropping a recovery record must be counted"

    def test_ledger_rejects_a_bad_disposition(self):
        ledger = RecoveryLedger()
        reporter = ErrorReporter(log_sink=lambda _r: None, ledger=ledger)
        reporter.report("INTERNAL_ERROR")
        with pytest.raises(TypeError):
            ledger.resolve("INTERNAL_ERROR", "handled")

    def test_module_helper_uses_the_process_reporter(self):
        result = report_error("TIMEOUT", detail="d")
        assert result.code == "TIMEOUT"
        assert result.metric_incremented, "the default metric leg must work with no wiring"


class TestPayloadAndPolicy:
    def test_payload_carries_the_whole_policy(self):
        result = ErrorReporter(log_sink=lambda _r: None).report("PERSISTENCE_UNAVAILABLE", detail="db down")
        payload = result.to_payload()
        assert payload["type"] == ERROR_SSE_EVENT
        assert payload["error_code"] == "PERSISTENCE_UNAVAILABLE"
        assert payload["severity"] == "critical"
        assert payload["retryable"] is True
        assert payload["error_message"] == require_definition("PERSISTENCE_UNAVAILABLE").message
        assert payload["error_correlation_id"] == "alpha.errors.persistence"
        assert payload["recovery"] == "restart"
        assert payload["http_status"] == 503
        assert payload["error_detail"] == "db down"

    def test_user_message_is_registry_owned_not_interpolated(self):
        """The registry owns user wording; exception text is a separate field."""
        result = ErrorReporter(log_sink=lambda _r: None).report("TIMEOUT", detail="token=secret-ish text")
        payload = result.to_payload()
        assert "secret-ish" not in payload["error_message"]
        assert payload["error_detail"] == "token=secret-ish text"

    def test_context_is_bounded(self):
        context = {f"k{i}": "v" * 500 for i in range(40)}
        result = ErrorReporter(log_sink=lambda _r: None).report("INTERNAL_ERROR", context=context)
        assert len(result.context) <= 16
        assert all(len(v) <= 200 for v in result.context.values() if isinstance(v, str))

    def test_exception_is_attached_for_the_log_leg_only(self):
        reported = report_exception(sqlite3.OperationalError("database is locked"))
        assert reported.exception_type == "OperationalError"
        assert "database is locked" in reported.detail

    def test_log_level_tracks_severity(self):
        assert log_level_for(ErrorSeverity.INFO) == logging.INFO
        assert log_level_for(ErrorSeverity.CRITICAL) == logging.CRITICAL
        assert log_level_for(ErrorSeverity.ERROR) > log_level_for(ErrorSeverity.WARNING)

    def test_default_metric_sink_declares_a_bounded_counter(self):
        registry = get_metrics_registry()
        ErrorReporter(log_sink=lambda _r: None).report("INVALID_INPUT")
        text = registry.render_prometheus()
        assert ERROR_METRIC_NAME in text, "the default metric leg must declare and increment its counter"
        assert 'code="INVALID_INPUT"' in text
        assert 'severity="warning"' in text

    def test_metric_labels_never_carry_identity(self):
        """A run id in a scrape target is a cardinality bomb with a handle on it."""
        registry = get_metrics_registry()
        ErrorReporter(log_sink=lambda _r: None).report("INVALID_INPUT", context={"run_id": "run-secret-handle"})
        series = [line for line in registry.render_prometheus().splitlines() if line.startswith(ERROR_METRIC_NAME)]
        assert series, "expected at least one alpha_errors_total series"
        for line in series:
            assert "run_id" not in line
            assert "run-secret-handle" not in line
            assert "trace_id" not in line

    def test_unknown_code_never_reaches_a_sink(self):
        sse = _Recorder()
        reporter = _reporter(sse_sink=sse)
        with pytest.raises(KeyError):
            reporter.report("TOTALLY_UNKNOWN")
        assert sse.calls == []
