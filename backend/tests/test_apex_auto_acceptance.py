from __future__ import annotations

import json
import logging

from alpha.apex.auto_acceptance import evaluate_run_acceptance_criteria, evaluate_tool_readback_criteria


def _events(*, written: str = "expected marker", read: str = "expected marker", read_path: str = "/workspace/result.txt") -> list[dict]:
    return [
        {
            "seq": 1,
            "event_type": "llm.ai.response",
            "content": {
                "type": "ai",
                "tool_calls": [{"id": "write-1", "name": "write_file", "args": {"path": "/workspace/result.txt", "content": written}}],
            },
        },
        {
            "seq": 2,
            "event_type": "llm.tool.result",
            "content": {"name": "write_file", "tool_call_id": "write-1", "status": "success"},
        },
        {
            "seq": 3,
            "event_type": "llm.ai.response",
            "content": {
                "type": "ai",
                "tool_calls": [{"id": "read-1", "name": "read_file", "args": {"path": read_path}}],
            },
        },
        {
            "seq": 4,
            "event_type": "llm.tool.result",
            "content": {"name": "read_file", "tool_call_id": "read-1", "status": "success", "content": read},
        },
    ]


def _auto_test_events(*, status: str = "passed", exit_code: int = 0, failure_count: int = 0, test_command: str = "") -> list[dict]:
    report = {
        "status": status,
        "execution": "argv",
        "command": ["python", "-m", "pytest"],
        "exit_code": exit_code,
        "failure_count": failure_count,
    }
    return [
        {
            "seq": 1,
            "event_type": "llm.ai.response",
            "content": {
                "type": "ai",
                "tool_calls": [{"id": "tests-1", "name": "auto_test_and_repair", "args": {"test_command": test_command}}],
            },
        },
        {
            "seq": 2,
            "event_type": "llm.tool.result",
            "content": {
                "name": "auto_test_and_repair",
                "tool_call_id": "tests-1",
                "status": "success",
                "content": json.dumps(report),
            },
        },
    ]


def test_explicit_exact_file_readback_is_measured_from_tool_events() -> None:
    criterion = 'Read the file back and verify it contains exactly "expected marker".'

    report = evaluate_tool_readback_criteria([criterion], _events())

    assert report is not None
    assert report.evaluator == "run_tool_readback"
    assert report.passed is True
    assert "readback matched the declared exact text" in report.criteria[0].evidence
    assert report.criteria[0].evaluated_at is not None


def test_readback_mismatch_is_measured_not_passed() -> None:
    criterion = 'Read the file back and verify it contains exactly "expected marker".'

    report = evaluate_tool_readback_criteria([criterion], _events(read="different"))

    assert report is not None
    assert report.passed is False
    assert report.criteria[0].verdict.value == "not_met"


def test_unrecognized_criterion_stays_with_manual_verification() -> None:
    assert evaluate_tool_readback_criteria(["The implementation is production ready"], _events()) is None


def test_readback_requires_successful_ordered_same_path_tool_calls() -> None:
    criterion = 'Read the file back and verify it contains exactly "expected marker".'
    wrong_path = _events(read_path="/workspace/other.txt")
    assert evaluate_tool_readback_criteria([criterion], wrong_path) is None

    failed_write = _events()
    failed_write[1]["content"]["status"] = "error"
    assert evaluate_tool_readback_criteria([criterion], failed_write) is None

    out_of_order = _events()
    out_of_order[0]["seq"] = 3
    out_of_order[1]["seq"] = 4
    out_of_order[2]["seq"] = 1
    out_of_order[3]["seq"] = 2
    assert evaluate_tool_readback_criteria([criterion], out_of_order) is None


def test_readback_cannot_pass_on_an_earlier_write_after_a_later_overwrite() -> None:
    criterion = 'Read the file back and verify it contains exactly "expected marker".'
    events = _events()
    events.extend(
        [
            {
                "seq": 5,
                "event_type": "llm.ai.response",
                "content": {
                    "type": "ai",
                    "tool_calls": [{"id": "write-2", "name": "write_file", "args": {"path": "/workspace/result.txt", "content": "overwritten"}}],
                },
            },
            {"seq": 6, "event_type": "llm.tool.result", "content": {"name": "write_file", "tool_call_id": "write-2", "status": "success"}},
        ]
    )

    assert evaluate_tool_readback_criteria([criterion], events) is None


def test_auto_evaluation_refuses_mixed_supported_and_unsupported_criteria() -> None:
    criteria = ['Read the file back and verify it contains exactly "expected marker".', "All tests pass"]
    assert evaluate_tool_readback_criteria(criteria, _events()) is None


def test_explicit_artifact_check_measures_non_empty_readback_with_digest() -> None:
    criterion = "File artifact at `/workspace/result.txt` exists and is non-empty."

    report = evaluate_run_acceptance_criteria([criterion], _events())

    assert report is not None
    assert report.evaluator == "run_artifact_readback"
    assert report.passed is True
    assert "bytes=15" in report.criteria[0].evidence
    assert "sha256=" in report.criteria[0].evidence


def test_explicit_artifact_check_reports_empty_readback_as_not_met() -> None:
    criterion = "File artifact at `/workspace/result.txt` exists and is non-empty."

    report = evaluate_run_acceptance_criteria([criterion], _events(written="", read=""))

    assert report is not None
    assert report.passed is False
    assert report.criteria[0].verdict.value == "not_met"
    assert "bytes=0" in report.criteria[0].evidence


def test_existing_artifact_can_be_measured_without_a_write_in_this_run() -> None:
    criterion = 'File artifact at "/workspace/result.txt" exists and is non-empty.'
    events = _events()[2:]
    events[0]["seq"] = 1
    events[1]["seq"] = 2

    report = evaluate_run_acceptance_criteria([criterion], events)

    assert report is not None
    assert report.passed is True
    assert "sha256=" in report.criteria[0].evidence


def test_artifact_check_requires_read_after_latest_successful_write() -> None:
    criterion = "File artifact at `/workspace/result.txt` exists and is non-empty."
    events = _events()
    events.extend(
        [
            {
                "seq": 5,
                "event_type": "llm.ai.response",
                "content": {"type": "ai", "tool_calls": [{"id": "write-2", "name": "write_file", "args": {"path": "/workspace/result.txt", "content": "changed"}}]},
            },
            {"seq": 6, "event_type": "llm.tool.result", "content": {"name": "write_file", "tool_call_id": "write-2", "status": "success"}},
        ]
    )

    assert evaluate_run_acceptance_criteria([criterion], events) is None


def test_artifact_and_exact_text_criteria_can_share_one_measured_report() -> None:
    criteria = [
        'Read the file back and verify it contains exactly "expected marker".',
        "File artifact at `/workspace/result.txt` exists and is non-empty.",
    ]

    report = evaluate_run_acceptance_criteria(criteria, _events())

    assert report is not None
    assert report.passed is True
    assert report.evaluator == "run_artifact_readback+run_tool_readback"


def test_auto_detected_project_tests_are_measured_from_correlated_tool_result() -> None:
    report = evaluate_run_acceptance_criteria(["Auto-detected project test suite passes."], _auto_test_events())

    assert report is not None
    assert report.evaluator == "run_auto_test"
    assert report.passed is True
    assert "auto-detected argv test receipt" in report.criteria[0].evidence
    assert "exit_code=0" in report.criteria[0].evidence


def test_declared_evidence_kind_must_match_the_collector() -> None:
    criterion = 'Read the file back and verify it contains exactly "expected marker".'

    assert (
        evaluate_run_acceptance_criteria(
            [criterion],
            _events(),
            required_evidence_kinds=[["exact_text_readback"]],
        )
        is not None
    )
    assert (
        evaluate_run_acceptance_criteria(
            [criterion],
            _events(),
            required_evidence_kinds=[["test_result"]],
        )
        is None
    )
    assert (
        evaluate_run_acceptance_criteria(
            [criterion],
            _events(),
            required_evidence_kinds=[["exact_text_readback", "exact_text_readback"]],
        )
        is None
    )


def test_auto_detected_test_failure_is_not_reported_as_a_pass() -> None:
    report = evaluate_run_acceptance_criteria(
        ["Auto-detected project test suite passes."],
        _auto_test_events(status="failed", exit_code=1, failure_count=2),
    )

    assert report is not None
    assert report.passed is False
    assert report.criteria[0].verdict.value == "not_met"
    assert "exit_code=1" in report.criteria[0].evidence


def test_latest_auto_test_attempt_controls_the_verdict() -> None:
    events = _auto_test_events()
    later = _auto_test_events(status="failed", exit_code=1, failure_count=3)
    later[0]["seq"] = 3
    later[0]["content"]["tool_calls"][0]["id"] = "tests-2"
    later[1]["seq"] = 4
    later[1]["content"]["tool_call_id"] = "tests-2"
    events.extend(later)

    report = evaluate_run_acceptance_criteria(["Auto-detected project test suite passes."], events)

    assert report is not None
    assert report.passed is False
    assert "exit_code=1" in report.criteria[0].evidence


def test_parallel_latest_test_calls_are_ambiguous_and_stay_unverified() -> None:
    events = _auto_test_events()
    events[0]["content"]["tool_calls"].append({"id": "tests-2", "name": "auto_test_and_repair", "args": {}})
    events.append(
        {
            "seq": 3,
            "event_type": "llm.tool.result",
            "content": {
                "name": "auto_test_and_repair",
                "tool_call_id": "tests-2",
                "status": "success",
                "content": json.dumps({"status": "passed", "execution": "argv", "command": ["pytest"], "exit_code": 0, "failure_count": 0}),
            },
        }
    )

    assert evaluate_run_acceptance_criteria(["Auto-detected project test suite passes."], events) is None


def test_auto_test_collector_refuses_model_supplied_commands_and_bad_receipts() -> None:
    criterion = "Auto-detected project test suite passes."
    assert evaluate_run_acceptance_criteria([criterion], _auto_test_events(test_command="echo tests pass")) is None

    malformed_args = _auto_test_events()
    malformed_args[0]["content"]["tool_calls"][0]["args"]["test_command"] = []
    assert evaluate_run_acceptance_criteria([criterion], malformed_args) is None

    wrong_exit = _auto_test_events()
    wrong_exit[1]["content"]["content"] = json.dumps({"status": "passed", "execution": "argv", "command": ["pytest"], "exit_code": 1, "failure_count": 0})
    assert evaluate_run_acceptance_criteria([criterion], wrong_exit) is None

    missing_result = _auto_test_events()[:1]
    assert evaluate_run_acceptance_criteria([criterion], missing_result) is None

    duplicate_id = _auto_test_events()
    duplicate_id[0]["content"]["tool_calls"].append({"id": "tests-1", "name": "auto_test_and_repair", "args": {}})
    assert evaluate_run_acceptance_criteria([criterion], duplicate_id) is None


def test_malformed_auto_test_receipt_stays_unverified_and_is_logged(caplog) -> None:
    events = _auto_test_events()
    events[1]["content"]["content"] = "not-json"

    with caplog.at_level(logging.DEBUG, logger="alpha.apex.auto_acceptance"):
        assert evaluate_run_acceptance_criteria(["Auto-detected project test suite passes."], events) is None

    assert "leaving it unverified" in caplog.text


def test_unencodable_artifact_readback_stays_unverified_and_is_logged(caplog) -> None:
    events = _events(read=chr(0xD800))

    with caplog.at_level(logging.DEBUG, logger="alpha.apex.auto_acceptance"):
        assert evaluate_run_acceptance_criteria(["File artifact at `/workspace/result.txt` exists and is non-empty."], events) is None

    assert "not UTF-8 encodable" in caplog.text
