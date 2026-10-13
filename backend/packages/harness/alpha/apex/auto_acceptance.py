"""Small, evidence-bound automatic acceptance checks for completed APEX runs.

This module recognizes only explicit exact-text file readback criteria. It does
not interpret general natural language, infer a goal from model prose, or run a
command. Unsupported criteria stay with the existing owner/operator evidence
path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from alpha.mission.acceptance import AcceptanceReport, CriterionResult, CriterionVerdict

_EXACT_TEXT = re.compile(r"(?:exactly|exact(?:\s+text)?\s+of)\s+([\"'`])(.+?)\1", re.IGNORECASE | re.DOTALL)
_MAX_EXPECTED_TEXT = 16_384


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
            )
        )
    return AcceptanceReport(criteria=results, evaluator="run_tool_readback")
