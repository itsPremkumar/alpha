"""Tests for hash-anchored concurrent-safe file edits (O5 hashline)."""

import hashlib
import inspect
import posixpath
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from alpha.sandbox import hashline
from alpha.sandbox.hashline import (
    MAX_HASHLINE_LINES,
    REVISION_TOKEN_CHARS,
    format_hashline,
    format_hashline_ranged,
    line_tag,
    revision_token,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_request(name, args, messages=(), tool_call_id="call-1"):
    runtime = MagicMock()
    runtime.context = {"thread_id": "t-test"}
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": tool_call_id},
        tool=None,
        state={"messages": list(messages)},
        runtime=runtime,
    )


def _middleware(files: dict[str, str]):
    from alpha.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware

    def reader(_runtime, path):
        normalized = posixpath.normpath(path)
        if normalized not in files:
            raise FileNotFoundError(path)
        value = files[normalized]
        if isinstance(value, Exception):
            raise value
        return value

    return ReadBeforeWriteMiddleware(content_reader=reader)


def _ok_result(tool_call_id="call-1", name="write_file"):
    return ToolMessage(content="OK", tool_call_id=tool_call_id, name=name)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_revision_token_is_digest_prefix():
    assert revision_token("hello") == _sha("hello")[:REVISION_TOKEN_CHARS]
    assert len(revision_token("hello")) == REVISION_TOKEN_CHARS


def test_revision_token_changes_with_content():
    assert revision_token("v1") != revision_token("v2")
    assert revision_token("v1") == revision_token("v1")


def test_line_tags_absolute_and_stable():
    assert line_tag(42, "code") == f"42#{_sha('42:code')[:4]}"
    assert line_tag(1, "x") != line_tag(2, "x")


def test_format_hashline_header_and_numbering():
    out = format_hashline("a\nb")
    lines = out.splitlines()
    assert lines[0].startswith("[hashline revision ")
    assert revision_token("a\nb") in lines[0]
    assert lines[1].startswith("1#") and lines[1].endswith("| a")
    assert lines[2].startswith("2#") and lines[2].endswith("| b")


def test_format_hashline_ranged_uses_absolute_numbers_and_full_token():
    full = "l1\nl2\nl3\nl4"
    out = format_hashline_ranged(full, "l3\nl4", start_line=3)
    assert revision_token(full) in out.splitlines()[0]
    assert any(line.startswith("3#") and line.endswith("| l3") for line in out.splitlines())
    assert any(line.startswith("4#") and line.endswith("| l4") for line in out.splitlines())


def test_format_hashline_caps_lines_with_notice():
    import re

    big = "\n".join(f"line {i}" for i in range(MAX_HASHLINE_LINES + 10))
    out = format_hashline(big)
    tagged = [line for line in out.splitlines() if re.match(r"^\d+#[0-9a-f]{4}\|", line)]
    # Exactly MAX lines carry tags, plus header plus overflow notice.
    assert len(tagged) == MAX_HASHLINE_LINES
    assert len(out.splitlines()) == MAX_HASHLINE_LINES + 2
    assert "untagged" in out.splitlines()[-1]
    # Token still covers the whole file.
    assert revision_token(big) in out.splitlines()[0]


# ---------------------------------------------------------------------------
# Gate: anchor verification
# ---------------------------------------------------------------------------

PATH = "/mnt/user-data/outputs/report.md"


def test_valid_anchor_passes_without_any_read_mark():
    # The core hashline guarantee: anchors survive summarization, which
    # deletes the read ToolMessages (and their marks) from context.
    content = "version two"
    mw = _middleware({PATH: content})
    token = revision_token(content)
    request = _make_request(
        "str_replace",
        {"path": PATH, "old_str": "two", "new_str": "2", "anchor_hash": token},
        messages=(),  # no history at all: no marks possible
    )
    handler = MagicMock(return_value=_ok_result(name="str_replace"))
    result = mw.wrap_tool_call(request, handler)
    handler.assert_called_once()
    assert result.status != "error"


@pytest.mark.parametrize("tool_name", ["write_file", "str_replace"])
def test_stale_anchor_rejected_for_both_write_tools(tool_name):
    mw = _middleware({PATH: "current content"})
    request = _make_request(
        tool_name,
        {"path": PATH, "content": "x", "anchor_hash": revision_token("older content")},
    )
    handler = MagicMock(return_value=_ok_result(name=tool_name))
    result = mw.wrap_tool_call(request, handler)
    handler.assert_not_called()
    assert result.status == "error"
    assert "stale" in result.content
    assert "hashline" in result.content
    assert result.additional_kwargs["agent_workspace_write_block"] == {"path": PATH, "tool": tool_name}


@pytest.mark.parametrize("anchor", ["", 12345, ["abc"]])
def test_malformed_anchor_rejected(anchor):
    mw = _middleware({PATH: "current content"})
    request = _make_request("write_file", {"path": PATH, "content": "x", "anchor_hash": anchor})
    handler = MagicMock(return_value=_ok_result())
    result = mw.wrap_tool_call(request, handler)
    handler.assert_not_called()
    assert result.status == "error"
    assert "anchor_hash must be" in result.content


def test_null_anchor_falls_back_to_legacy_path():
    # Explicit JSON null behaves as absent: legacy mark check applies.
    content = "v1"
    mw = _middleware({PATH: content})
    marked = ToolMessage(content="v1", tool_call_id="r1", name="read_file")
    marked.additional_kwargs["agent_workspace_read_mark"] = {"path": PATH, "hash": _sha(content)}
    request = _make_request(
        "write_file",
        {"path": PATH, "content": "v2", "anchor_hash": None},
        messages=[marked],
    )
    handler = MagicMock(return_value=_ok_result())
    result = mw.wrap_tool_call(request, handler)
    handler.assert_called_once()
    assert result.status != "error"


def test_absent_anchor_keeps_legacy_mark_behavior():
    content = "v1"
    mw = _middleware({PATH: content})
    # No mark at all -> still blocked exactly as before.
    blocked = _make_request("write_file", {"path": PATH, "content": "v2"})
    handler = MagicMock(return_value=_ok_result())
    result = mw.wrap_tool_call(blocked, handler)
    handler.assert_not_called()
    assert "have not read its current version" in result.content
    # Matching mark -> still passes.
    marked = ToolMessage(content="v1", tool_call_id="r1", name="read_file")
    marked.additional_kwargs["agent_workspace_read_mark"] = {"path": PATH, "hash": _sha(content)}
    passed = _make_request("write_file", {"path": PATH, "content": "v2"}, messages=[marked])
    handler2 = MagicMock(return_value=_ok_result())
    assert mw.wrap_tool_call(passed, handler2).status != "error"
    handler2.assert_called_once()


def test_anchor_on_missing_file_allows_creation():
    mw = _middleware({})
    request = _make_request("write_file", {"path": PATH, "content": "new", "anchor_hash": "deadbeef" * 2})
    handler = MagicMock(return_value=_ok_result())
    result = mw.wrap_tool_call(request, handler)
    handler.assert_called_once()
    assert result.status != "error"


def test_anchor_with_uninspectable_content_fails_open():
    mw = _middleware({PATH: "Error: remote read failed"})
    request = _make_request("write_file", {"path": PATH, "content": "x", "anchor_hash": "deadbeef" * 2})
    handler = MagicMock(return_value=_ok_result())
    result = mw.wrap_tool_call(request, handler)
    handler.assert_called_once()
    assert result.status != "error"


def test_stale_anchor_beats_valid_mark():
    # The anchor names a strictly newer observation contract: even with a
    # matching legacy mark, a stale anchor rejects (file changed twice).
    old, new = "v1", "v2"
    mw = _middleware({PATH: new})
    marked = ToolMessage(content="v1", tool_call_id="r1", name="read_file")
    marked.additional_kwargs["agent_workspace_read_mark"] = {"path": PATH, "hash": _sha(new)}
    request = _make_request(
        "write_file",
        {"path": PATH, "content": "v3", "anchor_hash": revision_token(old)},
        messages=[marked],
    )
    handler = MagicMock(return_value=_ok_result())
    result = mw.wrap_tool_call(request, handler)
    handler.assert_not_called()
    assert "stale" in result.content


# ---------------------------------------------------------------------------
# Tool schema wiring (no sandbox needed)
# ---------------------------------------------------------------------------


def test_tool_schemas_expose_hashline_params():
    from alpha.sandbox import tools as sandbox_tools

    assert "hashline" in sandbox_tools.read_file_tool.args
    assert "anchor_hash" in sandbox_tools.write_file_tool.args
    assert "anchor_hash" in sandbox_tools.str_replace_tool.args


def test_async_variants_forward_new_params():
    from alpha.sandbox import tools as sandbox_tools

    assert "hashline" in inspect.signature(sandbox_tools._read_file_tool_async).parameters
    assert "anchor_hash" in inspect.signature(sandbox_tools._write_file_tool_async).parameters
    assert "anchor_hash" in inspect.signature(sandbox_tools._str_replace_tool_async).parameters


def test_hashline_module_importable_without_side_effects():
    assert hashline.REVISION_TOKEN_CHARS == 16
    assert hashline.MAX_HASHLINE_LINES == 2000
