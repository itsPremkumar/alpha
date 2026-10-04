"""Regression: a log line that *mentions* a word is not a log line that *reports* a fault.

The bug this pins
-----------------
``alpha.runtime.sentinel.sources.logs`` prefiltered on a case-insensitive
alternation of ``failed|error|warning|timeout|traceback`` and classified severity
and kind by searching the line for the same bare words. Measured against this
repository's own ``logs/`` that produced **103 signals of which 0 were real**:

* 89 were pytest *parametrization ids* reported as high-severity
  ``test_failure`` -- ``tests/test_goal_contracts.py::test_attempt_transition_matrix[pending-failed]``
  matched ``\\bfailed\\b`` because of ``re.I``;
* 3 more were "critical", one of them a test id embedding the fixture string
  ``"Error (ModuleNotFoundError): No module named 'bash'-"``;
* the rest were test *names* containing ``failing``, ``traceback`` or ``warning``.

``test_failure`` is the kind that routes to the test-repair strategy, so the
sentinel was manufacturing 89 phantom high-severity incidents aimed at the
auto-repair path.

Every line in ``_REAL_FAULTS`` and ``_PHANTOMS`` below is copied from this
repository, so the test fails against the real data rather than a caricature of
it.
"""

from __future__ import annotations

import pytest

from alpha.runtime.sentinel.sources.logs import (
    classify_kind,
    classify_severity,
    exception_class_in,
    is_fault_report,
    scan_text,
)

# (line, expected kind, expected severity) for genuine fault reports.
_REAL_FAULTS: list[tuple[str, str, str]] = [
    ("Traceback (most recent call last):", "unknown", "critical"),
    ("ModuleNotFoundError: No module named 'bash'", "import_error", "high"),
    ("SyntaxError: invalid syntax", "syntax_error", "high"),
    ("IndentationError: unexpected indent", "syntax_error", "high"),
    ("AssertionError: assert 1 == 2", "test_failure", "high"),
    ("ConnectionRefusedError: [Errno 111] Connection refused", "connectivity", "high"),
    ("TimeoutError: model timed out", "connectivity", "high"),
    ("FileNotFoundError: [Errno 2] No such file or directory: 'a'", "missing_file", "high"),
    ("PermissionError: [Errno 13] Permission denied", "permission_error", "high"),
    ("SystemExit: 1", "unknown", "critical"),
    ("NameError: name 'x' is not defined", "unknown", "high"),
    ("2026-10-04 15:35:59 - alpha.config - ERROR - failed to load config", "unknown", "high"),
    ("2026-10-04 15:35:59 - alpha.config - WARNING - slow start", "warning", "low"),
    ("2026-10-04 15:35:59 - alpha.config - CRITICAL - disk gone", "unknown", "critical"),
    ("FAILED tests/test_a.py::test_b - AssertionError: assert 0", "test_failure", "high"),
    ("FAILED tests/test_a.py::test_b - ValueError: bad", "test_failure", "high"),
    ("ERROR tests/test_a.py::test_b", "test_failure", "high"),
    ("E       assert 1 == 2", "test_failure", "high"),
    ("E       ValueError: bad", "unknown", "high"),
    ("=================================== FAILURES ===================================", "test_failure", "high"),
    ("  1 failed, 300 passed in 12.4s", "test_failure", "high"),
    ("DeprecationWarning: datetime.utcnow() is deprecated", "warning", "low"),
    ("PytestUnknownMarkWarning: Unknown pytest.mark.timeout", "warning", "low"),
]

#: Lines that exist in this repository's logs and are NOT fault reports.
_PHANTOMS: list[str] = [
    # The 89 phantom test_failures, verbatim.
    "tests/test_goal_contracts.py::test_attempt_transition_matrix[pending-failed]",
    "tests/test_goal_contracts.py::test_attempt_transition_matrix[running-failed]",
    "tests/test_goal_contracts.py::test_attempt_transition_matrix[failed-succeeded]",
    "tests/test_extension_task_lifecycle.py::test_budget_exhaustion_mid_hook_logs_a_warning_not_a_traceback",
    "tests/test_intent_goal_engine.py::test_resolver_triggers_without_typed_slash[The test suite is failing with a traceback after the last change.-/self-heal-True]",
    "tests/test_logging_chokepoint.py::test_traceback_text_is_scrubbed",
    "tests/test_logging_level_from_config.py::test_logging_level_from_config_known_and_defaults[warning-30]",
    "tests/test_swarm_stigmergy_telemetry.py::test_budget_pressure_escalates_with_measured_usage[750-warning]",
    "tests/test_deferred_tool_crosscontext.py::test_first_answer_wins_without_a_traceback",
    "tests/test_durable_runtime_realtime.py::test_x[asyncio]",
    # The three phantom "critical"s: an id embedding an exception-shaped fixture.
    "tests/test_tool_failure_is_never_a_success.py::test_both_conventions_classify[Error (ModuleNotFoundError): No module named 'bash'-",
    "tests/test_tool_failure_is_never_a_success.py::test_both_conventions_classify[Error (SyntaxError): invalid syntax-error]",
    # A parametrization label that is itself exception-shaped.
    "tests/test_workflow_failures.py::TestClassification::test_every_spec_class_is_reachable[TimeoutError: model ti]",
    "tests/test_workflow_failures.py::TestNonRetryableClasses::test_futile_retry_classes_are_non_retryable[parser_f]",
    # A bare node id with no runner report marker.
    "tests/test_a.py::test_b",
    # Prose that merely names a state.
    "the -failed state transition is well covered",
    "add error handling to the parser",
    "retries=2 so a failed attempt is expected",
    "test no errors when the list is empty",
    "ERROR_COUNT = 0",
    "warning: this is informational",
]


@pytest.mark.parametrize(("line", "kind", "severity"), _REAL_FAULTS)
def test_real_fault_reports_are_still_detected(line: str, kind: str, severity: str) -> None:
    assert is_fault_report(line), f"a real fault report was missed: {line!r}"
    assert classify_kind(line) == kind, f"{line!r} -> kind {classify_kind(line)!r}, expected {kind!r}"
    assert classify_severity(line) == severity, f"{line!r} -> severity {classify_severity(line)!r}, expected {severity!r}"


@pytest.mark.parametrize("line", _PHANTOMS)
def test_phantom_lines_produce_no_signal(line: str) -> None:
    assert not is_fault_report(line), f"a mere mention of a word became a signal: {line!r}"


def test_classifiers_are_total_over_arbitrary_input() -> None:
    """`classify_*` stay callable on anything: they return a rank, never raise."""
    for line in ("", "   ", "no faults here at all", "\x00binary-ish", "a" * 5000):
        assert isinstance(classify_kind(line), str)
        assert classify_severity(line) in {"critical", "high", "medium", "low"}


def test_exception_class_is_carried_in_the_signal_context() -> None:
    """An operator sees *what* was raised, not just the message."""
    signals = scan_text("ValueError: bad input", origin="t.log")
    assert len(signals) == 1
    assert signals[0].context["exception"] == "ValueError"
    assert exception_class_in("ValueError: bad input") == "ValueError"


def test_scan_text_end_to_end_yields_only_real_signals() -> None:
    """The whole-document path applies the same gate, not just the helper."""
    text = "\n".join(
        [
            "tests/test_goal_contracts.py::test_attempt_transition_matrix[pending-failed]",
            "add error handling to the parser",
            "2026-10-04 15:35:59 - alpha.config - ERROR - disk gone",
            "tests/test_logging_chokepoint.py::test_traceback_text_is_scrubbed",
            "ModuleNotFoundError: No module named 'bash'",
        ]
    )
    kinds = [s.kind for s in scan_text(text, origin="t.log")]
    assert kinds == ["unknown", "import_error"], kinds


def test_a_traceback_block_is_captured_with_its_exception_line() -> None:
    text = "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "run.py", line 9, in boom',
            "    raise RuntimeError('detonated')",
            "RuntimeError: detonated",
            "tests/test_x.py::test_y",
        ]
    )
    signals = scan_text(text, origin="t.log")
    assert len(signals) == 1
    signal = signals[0]
    assert signal.severity == "critical"
    assert signal.message == "RuntimeError: detonated"
    assert "run.py" in signal.context["excerpt"]
    # The trailing node id after the traceback is not a second signal.
    assert all("::" not in s.message for s in signals)
