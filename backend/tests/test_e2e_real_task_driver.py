"""Unit tests for the live end-to-end task driver's pure helpers.

``scripts/e2e_real_task.py`` assigns a real task to a running Gateway and
monitors the SSE stream; its SSE framing parser and its verdict logic must be
correct offline, because they are what decides whether an observed run counts
as a success. These tests run with no Gateway available.

Contract pinned here:

- ``parse_sse`` follows ``text/event-stream`` framing: named events,
  newline-joined ``data:`` lines, blank-line dispatch, ``:`` comments
  ignored, unknown fields (``id:``/``retry:``) ignored, an unnamed frame
  dispatches as ``message``, and a trailing frame with no terminating blank
  line is still dispatched when the stream ends (a truncated connection must
  surface its last frame, not swallow it).
- ``evaluate`` returns an empty problem list only for a genuinely completed
  run: a terminal ``end`` frame, a durable ``success`` status, and zero SSE
  ``error`` frames. Every failure class (error frame, missing end, missing
  record, non-success status) is reported — never collapsed to a generic
  "stream interrupted".
"""

from scripts.e2e_real_task import _first_ai_text, evaluate, extract_artifact_paths, parse_sse


def test_parse_sse_named_frames_and_data_join():
    lines = [
        "event: metadata",
        'data: {"run_id": "r-1"}',
        "",
        "event: messages",
        'data: {"type": "ai",',
        'data: "content": "hello"}',
        "",
    ]
    assert list(parse_sse(lines)) == [
        ("metadata", '{"run_id": "r-1"}'),
        ("messages", '{"type": "ai",\n"content": "hello"}'),
    ]


def test_parse_sse_ignores_comments_and_unknown_fields():
    lines = [
        ": keep-alive",
        "id: 42",
        "retry: 1000",
        "event: end",
        "data: {}",
        "",
    ]
    assert list(parse_sse(lines)) == [("end", "{}")]


def test_parse_sse_unnamed_frame_dispatches_as_message():
    lines = ["data: plain", ""]
    assert list(parse_sse(lines)) == [("message", "plain")]


def test_parse_sse_trailing_frame_without_blank_line_is_dispatched():
    """A cut connection must still surface its last frame (e.g. `error`)."""
    lines = ["event: error", 'data: {"code": "unhandled_run_exception"}']
    assert list(parse_sse(lines)) == [("error", '{"code": "unhandled_run_exception"}')]


def test_parse_sse_blank_line_without_frame_is_skipped():
    lines = ["", "", "event: end", "data: {}", "", ""]
    assert list(parse_sse(lines)) == [("end", "{}")]


def test_evaluate_clean_success_has_no_problems():
    events = [("metadata", "{}"), ("messages", "{}"), ("end", "{}")]
    assert evaluate(events, "success", None) == []


def test_evaluate_reports_error_frame_even_with_end_and_success():
    events = [("error", '{"code": "boom"}'), ("end", "{}")]
    problems = evaluate(events, "success", None)
    assert len(problems) == 1
    assert "SSE error frame" in problems[0]
    assert "boom" in problems[0]


def test_evaluate_reports_missing_terminal_end():
    problems = evaluate([("metadata", "{}")], "success", None)
    assert any("terminal 'end'" in p for p in problems)


def test_evaluate_reports_durable_failure_with_status_and_error():
    problems = evaluate([("end", "{}")], "error", "Agent directory not found: agents/coder")
    assert len(problems) == 1
    assert "status='error'" in problems[0]
    assert "Agent directory not found" in problems[0]


def test_evaluate_reports_missing_run_record():
    problems = evaluate([("end", "{}")], None, None)
    assert any("run record not found" in p for p in problems)


def test_first_ai_text_picks_last_assistant_message():
    payload = {
        "data": [
            {"type": "human", "content": "task"},
            {"type": "ai", "content": "first answer"},
            {"type": "tool", "content": "tool output"},
            {"type": "ai", "content": "final answer"},
        ]
    }
    assert _first_ai_text(payload) == "final answer"


def test_first_ai_text_handles_list_content_blocks():
    payload = [{"type": "ai", "content": [{"type": "text", "text": "block text"}]}]
    assert _first_ai_text(payload) == "block text"


def test_first_ai_text_handles_run_event_envelopes():
    """``GET /runs/{id}/messages`` returns run-event envelopes, not bare
    messages: the LangChain message dict lives under ``content``, beside
    ``event_type``. The driver must read the nested shape — this is the
    shape the live Gateway actually serves."""
    payload = {
        "data": [
            {
                "event_type": "llm.human.input",
                "category": "message",
                "content": {"type": "human", "content": "create the file"},
            },
            {
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"type": "ai", "content": "Switching to that task."},
            },
            {
                "event_type": "llm.tool.result",
                "category": "message",
                "content": {"type": "tool", "content": "OK"},
            },
            {
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"type": "ai", "content": "Done. The file was created."},
            },
        ]
    }
    assert _first_ai_text(payload) == "Done. The file was created."


def test_extract_artifact_paths_reads_workspace_changes_files():
    """The live workspace-changes payload is ``{summary, files: [{path, ...}]}``;
    older drivers looked for ``produced_paths``/``paths`` and saw nothing."""
    payload = {
        "version": 1,
        "summary": {"created": 1, "modified": 0, "deleted": 0},
        "files": [
            {"path": "/mnt/user-data/outputs/e2e-task-1.md", "root": "outputs", "status": "created"},
            {"path": "/mnt/user-data/outputs/other.txt", "root": "outputs", "status": "modified"},
        ],
        "available": True,
    }
    assert extract_artifact_paths(payload) == [
        "/mnt/user-data/outputs/e2e-task-1.md",
        "/mnt/user-data/outputs/other.txt",
    ]


def test_extract_artifact_paths_falls_back_to_legacy_keys_and_handles_absence():
    assert extract_artifact_paths({"produced_paths": ["a.md"]}) == ["a.md"]
    assert extract_artifact_paths({}) == []
    assert extract_artifact_paths({"files": []}) == []
