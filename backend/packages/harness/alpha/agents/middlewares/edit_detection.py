"""Shared classification for source-code edit tool results."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

_CODE_EDIT_TOOLS = frozenset({"write_file", "str_replace", "hashline_edit"})
_CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".mjs",
        ".php",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)
_PATH_ARGUMENTS = frozenset({"file", "file_path", "filename", "path", "target_path", "target_file"})


def _tool_call_args(messages: list[Any], tool_call_id: str | None, tool_name: str) -> Mapping[str, Any] | None:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call.get("name") != tool_name:
                continue
            if tool_call_id and call.get("id") != tool_call_id:
                continue
            args = call.get("args")
            if isinstance(args, Mapping):
                return args
    return None


def _paths(value: Any, key: str = "") -> Iterator[str]:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            normalized_key = str(child_key).lower()
            if normalized_key in _PATH_ARGUMENTS and isinstance(child, str):
                yield child
            else:
                yield from _paths(child, normalized_key)
    elif isinstance(value, list):
        for child in value:
            yield from _paths(child, key)


def is_source_code_edit_result(messages: list[Any], result: ToolMessage) -> bool:
    """Return true only when a known file edit targets a source-code suffix.

    Tool names alone are insufficient: ``write_file`` also creates ordinary
    notes, reports and other non-code artifacts. We correlate the tool result
    with its assistant call and inspect the declared target path.
    """
    tool_name = str(getattr(result, "name", "") or "").lower()
    if tool_name not in _CODE_EDIT_TOOLS:
        return False
    args = _tool_call_args(messages, getattr(result, "tool_call_id", None), tool_name)
    if args is None:
        return False
    for value in _paths(args):
        suffix = PurePath(value).suffix.lower()
        if not suffix:
            suffix = PurePosixPath(value).suffix.lower() or PureWindowsPath(value).suffix.lower()
        if suffix in _CODE_SUFFIXES:
            return True
    return False
