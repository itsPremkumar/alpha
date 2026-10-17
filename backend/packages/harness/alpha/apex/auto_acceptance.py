"""Small, evidence-bound automatic acceptance checks for completed APEX runs.

This module recognizes only explicit exact-text file readback criteria. It does
not interpret general natural language, infer a goal from model prose, or run a
command. Unsupported criteria stay with the existing owner/operator evidence
path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from alpha.mission.acceptance import AcceptanceReport, CriterionResult, CriterionVerdict

logger = logging.getLogger(__name__)

_EXACT_TEXT = re.compile(r"(?:exactly|exact(?:\s+text)?\s+of)\s+([\"'`])(.+?)\1", re.IGNORECASE | re.DOTALL)
_MAX_EXPECTED_TEXT = 16_384
_MAX_ARTIFACT_PATH = 1_024
_MAX_ARTIFACT_BYTES = 1 << 20
_ARTIFACT_CRITERION = re.compile(r"\bfile\s+artifact\s+(?:at|path\s+is)\s+[`\"']([^`\"']+)[`\"']\s+(?:exists|is\s+present)\s+and\s+(?:is\s+)?non-empty\b", re.IGNORECASE)
_AUTO_TEST_CRITERION = re.compile(r"^\s*auto-detected project test suite passes\.?\s*$", re.IGNORECASE)


def _criterion_expected_text(criterion: str) -> str | None:
    lowered = criterion.casefold()
    if "file" not in lowered or "read" not in lowered or "exact" not in lowered:
        return None
    match = _EXACT_TEXT.search(criterion)
    if match is None:
        return None
    expected = match.group(2)
    if not expected or len(expected) > _MAX_EXPECTED_TEXT:
        return None
    return expected


def _criterion_artifact_path(criterion: str) -> str | None:
    """Recognize only the explicit file-artifact check this module measures."""
    match = _ARTIFACT_CRITERION.search(criterion)
    if match is None:
        return None
    path = match.group(1).strip()
    if not path or len(path) > _MAX_ARTIFACT_PATH:
        return None
    return path


def _content(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = event.get("content")
    return value if isinstance(value, Mapping) else None


def _readback_result(
    expected: str,
    events: Sequence[Mapping[str, Any]],
) -> tuple[bool, str] | None:
    calls: dict[str, tuple[int, str, str, str]] = {}
    for event in events:
        if event.get("event_type") != "llm.ai.response":
            continue
        content = _content(event)
        if content is None or content.get("type") != "ai":
            continue
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            continue
        tool_calls = content.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, Mapping):
                continue
            name, call_id, args = call.get("name"), call.get("id"), call.get("args")
            if name not in {"write_file", "read_file"} or not isinstance(call_id, str) or not call_id or not isinstance(args, Mapping):
                continue
            path = args.get("path")
            if not isinstance(path, str) or not path.strip():
                continue
            if name == "write_file":
                value = args.get("content")
                if not isinstance(value, str):
                    continue
            else:
                value = ""
            if call_id in calls:
                # Provider IDs are correlation keys. Reuse would make a tool
                # result ambiguous, so never choose whichever call was last.
                return None
            calls[call_id] = (seq, name, path, value)

    writes: list[tuple[int, str, str]] = []
    reads: list[tuple[int, str, str]] = []
    for event in events:
        if event.get("event_type") != "llm.tool.result":
            continue
        result = _content(event)
        if result is None:
            continue
        call_id = result.get("tool_call_id")
        call = calls.get(call_id) if isinstance(call_id, str) else None
        seq = event.get("seq")
        if call is None or not isinstance(seq, int) or isinstance(seq, bool):
            continue
        call_seq, name, path, written = call
        if seq <= call_seq or result.get("name") != name or result.get("status") != "success":
            continue
        if name == "write_file":
            writes.append((seq, path, written))
        else:
            read_content = result.get("content")
            if isinstance(read_content, str):
                reads.append((seq, path, read_content))

    latest_write_by_path: dict[str, tuple[int, str]] = {}
    for write_seq, path, written in writes:
        previous = latest_write_by_path.get(path)
        if previous is None or write_seq > previous[0]:
            latest_write_by_path[path] = (write_seq, written)

    measured_paths: list[tuple[int, str, str, str]] = []
    for path, (write_seq, written) in latest_write_by_path.items():
        later_reads = [(seq, text) for seq, read_path, text in reads if read_path == path and seq > write_seq]
        if later_reads:
            read_seq, read_text = later_reads[-1]
            measured_paths.append((write_seq, read_seq, path, read_text if written == expected else ""))
            if written != expected:
                measured_paths[-1] = (write_seq, read_seq, path, "<last write differed from the declared exact text>")
    if len(measured_paths) == 1:
        write_seq, read_seq, path, read_text = measured_paths[0]
        matched = read_text == expected
        return matched, (f"write_file and later read_file succeeded for {path}; readback {'matched' if matched else 'did not match'} the declared exact text (events {write_seq}, {read_seq}).")
    return None


def _artifact_readback_result(path: str, events: Sequence[Mapping[str, Any]]) -> tuple[bool, str] | None:
    """Measure one exact artifact path from a successful, correlated read_file result.

    The latest successful write must be followed by a successful read of that
    same path. This prevents an earlier read from proving an artifact after it
    was overwritten. The event stream is run-scoped and fully scanned by the
    Gateway; output larger than the evidence bound remains unverified.
    """
    calls: dict[str, tuple[int, str, str]] = {}
    for event in events:
        if event.get("event_type") != "llm.ai.response":
            continue
        content = _content(event)
        seq = event.get("seq")
        if content is None or content.get("type") != "ai" or not isinstance(seq, int) or isinstance(seq, bool):
            continue
        tool_calls = content.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, Mapping):
                continue
            name, call_id, args = call.get("name"), call.get("id"), call.get("args")
            if name not in {"write_file", "read_file"} or not isinstance(call_id, str) or not call_id or not isinstance(args, Mapping):
                continue
            call_path = args.get("path")
            if call_path != path:
                continue
            if call_id in calls:
                return None
            calls[call_id] = (seq, name, path)

    successful_writes: list[int] = []
    successful_reads: list[tuple[int, str]] = []
    for event in events:
        if event.get("event_type") != "llm.tool.result":
            continue
        result = _content(event)
        seq = event.get("seq")
        call_id = result.get("tool_call_id") if result is not None else None
        call = calls.get(call_id) if isinstance(call_id, str) else None
        if call is None or not isinstance(seq, int) or isinstance(seq, bool):
            continue
        call_seq, name, call_path = call
        if seq <= call_seq or result.get("name") != name or result.get("status") != "success":
            continue
        if name == "write_file":
            successful_writes.append(seq)
        else:
            body = result.get("content")
            if isinstance(body, str):
                successful_reads.append((seq, body))

    latest_write = max(successful_writes, default=-1)
    later_reads = [(seq, body) for seq, body in successful_reads if seq > latest_write]
    if not later_reads:
        return None
    read_seq, body = max(later_reads, key=lambda row: row[0])
    if len(body) > _MAX_ARTIFACT_BYTES:
        return None
    try:
        encoded = body.encode("utf-8")
    except UnicodeEncodeError as exc:
        logger.debug("APEX artifact readback is not UTF-8 encodable; leaving it unverified (%s)", type(exc).__name__)
        return None
    if len(encoded) > _MAX_ARTIFACT_BYTES:
        return None
    digest = hashlib.sha256(encoded).hexdigest()
    detail = f"successful read_file for {path}; bytes={len(encoded)} sha256={digest} (event {read_seq})"
    return len(encoded) > 0, detail


def _auto_test_result(events: Sequence[Mapping[str, Any]]) -> tuple[bool, str] | None:
    """Measure the latest auto-detected test run from its correlated receipt.

    A caller-supplied command can be an arbitrary program, so it cannot prove
    that the project's tests ran. Only calls with an omitted/empty
    ``test_command`` are eligible; Alpha's detector chooses the project harness
    and executes it as argv. The result must correlate by tool-call id and be
    ordered after its request.
    """
    calls: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for event in events:
        if event.get("event_type") != "llm.ai.response":
            continue
        content = _content(event)
        seq = event.get("seq")
        tool_calls = content.get("tool_calls") if content is not None and content.get("type") == "ai" else None
        if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, Mapping) or call.get("name") != "auto_test_and_repair":
                continue
            call_id, args = call.get("id"), call.get("args")
            if not isinstance(call_id, str) or not call_id or not isinstance(args, Mapping) or call_id in calls:
                return None
            calls[call_id] = (seq, args)
    if not calls:
        return None

    latest_seq = max(call_seq for call_seq, _args in calls.values())
    latest_calls = [(call_id, args) for call_id, (call_seq, args) in calls.items() if call_seq == latest_seq]
    if len(latest_calls) != 1:
        return None
    latest_call_id, args = latest_calls[0]
    call_seq = latest_seq
    supplied_command = args.get("test_command")
    if supplied_command is not None and (not isinstance(supplied_command, str) or supplied_command != ""):
        return None

    results: list[tuple[int, Mapping[str, Any]]] = []
    for event in events:
        if event.get("event_type") != "llm.tool.result":
            continue
        result = _content(event)
        seq = event.get("seq")
        if result is None or result.get("tool_call_id") != latest_call_id:
            continue
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= call_seq:
            continue
        results.append((seq, result))
    if len(results) != 1:
        return None

    result_seq, tool_result = results[0]
    if tool_result.get("name") != "auto_test_and_repair" or tool_result.get("status") != "success":
        return None
    raw_report = tool_result.get("content")
    if not isinstance(raw_report, str):
        return None
    try:
        report = json.loads(raw_report)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.debug("APEX auto-test receipt is not valid JSON; leaving it unverified (%s)", type(exc).__name__)
        return None
    if not isinstance(report, dict) or report.get("execution") != "argv":
        return None
    command = report.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        return None
    status = report.get("status")
    exit_code = report.get("exit_code")
    failure_count = report.get("failure_count")
    if status == "passed":
        if type(exit_code) is not int or exit_code != 0 or type(failure_count) is not int or failure_count != 0:
            return None
        passed = True
        outcome = "passed; exit_code=0; failure_count=0"
    elif status == "failed" and type(exit_code) is int and exit_code != 0 and type(failure_count) is int and failure_count >= 0:
        passed = False
        outcome = f"failed; exit_code={exit_code}; failure_count={failure_count}"
    else:
        # Refusal, skip, timeout, malformed and unknown outcomes are not a
        # completed measurement and must not be converted into a pass/failure.
        return None
    # Do not persist the project-controlled command text in the evidence report:
    # test scripts can include secrets or sensitive paths in their arguments.
    return passed, f"auto-detected argv test receipt; {outcome} (event {result_seq})"


def evaluate_tool_readback_criteria(
    criteria: Sequence[str],
    events: Sequence[Mapping[str, Any]],
) -> AcceptanceReport | None:
    """Evaluate all criteria only when each has an exact supported readback form.

    ``None`` means the event stream or criterion set is not fully covered by
    this checker, so a broader verifier/operator must decide. A returned report
    is based on tool-call/result pairs from the same run and includes measured
    failures as ``NOT_MET`` rather than silently retrying or passing them.
    """
    if not criteria or not events:
        return None
    parsed: list[tuple[str, str]] = []
    for criterion in criteria:
        text = str(criterion)
        expected = _criterion_expected_text(text)
        if expected is None:
            return None
        parsed.append((text, expected))

    results: list[CriterionResult] = []
    for criterion, expected in parsed:
        measured = _readback_result(expected, events)
        if measured is None:
            return None
        met, evidence = measured
        results.append(
            CriterionResult(
                criterion=criterion,
                verdict=CriterionVerdict.MET if met else CriterionVerdict.NOT_MET,
                evidence=evidence,
                evaluated_at=time.time(),
            )
        )
    return AcceptanceReport(criteria=results, evaluator="run_tool_readback")


def evaluate_run_acceptance_criteria(
    criteria: Sequence[str],
    events: Sequence[Mapping[str, Any]],
    *,
    required_evidence_kinds: Sequence[Sequence[str]] | None = None,
) -> AcceptanceReport | None:
    """Dispatch supported run evidence checks without interpreting free-form prose.

    Supported checks are exact-text file readback, explicit non-empty artifact
    readback, and the exact criterion ``Auto-detected project test suite passes.``
    The latter only accepts a correlated, successful `auto_test_and_repair`
    call with no caller-supplied command. Unknown or ambiguous criteria leave
    the complete report unverified.
    """
    if not criteria or not events:
        return None
    if required_evidence_kinds is not None and (
        len(required_evidence_kinds) != len(criteria) or any(not isinstance(kinds, Sequence) or isinstance(kinds, (str, bytes)) or not all(isinstance(kind, str) and kind for kind in kinds) for kinds in required_evidence_kinds)
    ):
        return None
    if required_evidence_kinds is not None and any(len(kinds) > 8 or len(set(kinds)) != len(kinds) for kinds in required_evidence_kinds):
        return None
    parsed: list[tuple[str, str, str]] = []
    for index, criterion in enumerate(criteria):
        text = str(criterion)
        if _AUTO_TEST_CRITERION.fullmatch(text):
            kind = "auto_test"
            parsed.append((text, kind, ""))
            supported_evidence_kind = "test_result"
            if required_evidence_kinds is not None and required_evidence_kinds[index] and set(required_evidence_kinds[index]) != {supported_evidence_kind}:
                return None
            continue
        expected = _criterion_expected_text(text)
        if expected is not None:
            parsed.append((text, "exact_text", expected))
            supported_evidence_kind = "exact_text_readback"
            if required_evidence_kinds is not None and required_evidence_kinds[index] and set(required_evidence_kinds[index]) != {supported_evidence_kind}:
                return None
            continue
        path = _criterion_artifact_path(text)
        if path is None:
            return None
        parsed.append((text, "artifact", path))
        supported_evidence_kind = "artifact_digest"
        if required_evidence_kinds is not None and required_evidence_kinds[index] and set(required_evidence_kinds[index]) != {supported_evidence_kind}:
            return None

    results: list[CriterionResult] = []
    evaluators: set[str] = set()
    for criterion, kind, expected in parsed:
        if kind == "exact_text":
            measured = _readback_result(expected, events)
            if measured is None:
                return None
            met, evidence = measured
            evaluators.add("run_tool_readback")
        elif kind == "artifact":
            measured = _artifact_readback_result(expected, events)
            if measured is None:
                return None
            met, evidence = measured
            evaluators.add("run_artifact_readback")
        else:
            measured = _auto_test_result(events)
            if measured is None:
                return None
            met, evidence = measured
            evaluators.add("run_auto_test")
        results.append(CriterionResult(criterion=criterion, verdict=CriterionVerdict.MET if met else CriterionVerdict.NOT_MET, evidence=evidence, evaluated_at=time.time()))
    return AcceptanceReport(criteria=results, evaluator="+".join(sorted(evaluators)))
