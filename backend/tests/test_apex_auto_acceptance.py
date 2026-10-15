from __future__ import annotations

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
