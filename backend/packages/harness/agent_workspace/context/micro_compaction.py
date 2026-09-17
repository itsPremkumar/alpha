"""Micro-Compaction and Rolling Semantic Receipt Generator inspired by Hermes Agent."""

from __future__ import annotations

import re
from typing import Any


def compact_tool_output(
    tool_name: str,
    tool_input: dict[str, Any] | str,
    raw_output: str,
    max_chars: int = 250,
) -> str:
    """Condense verbose tool execution output into a high-density semantic receipt."""
    text = str(raw_output).strip()
    if len(text) <= max_chars:
        return text

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    first_line = re.sub(r"[=\-]{3,}", "", lines[0]).strip() if lines else ""
    last_line = re.sub(r"[=\-]{3,}", "", lines[-1]).strip() if len(lines) > 1 else ""

    # Look for errors or test passes
    status = "OK"
    for line in lines:
        clean_line = re.sub(r"[=\-]{3,}", "", line).strip()
        if re.search(r"\b(error|failed|exception|traceback|fatal)\b", clean_line, re.IGNORECASE):
            status = f"ERROR ({clean_line[:40]})"
            break
        elif re.search(r"\b(passed|succeeded|verified)\b", clean_line, re.IGNORECASE):
            status = f"SUCCESS ({clean_line[:40]})"

    args_repr = str(tool_input)[:40]
    receipt = f"[TOOL RECEIPT: {tool_name}({args_repr}) -> {status} | {len(lines)} lines | '{first_line[:30]}...' -> '{last_line[:30]}']"
    if len(receipt) > max_chars:
        receipt = receipt[:max_chars - 4] + "...]"
    return receipt


def apply_micro_compaction(
    messages: list[dict[str, Any]],
    protected_tail_count: int = 4,
) -> tuple[list[dict[str, Any]], int]:
    """Perform rolling micro-compaction on older tool turns prior to the active conversation window."""
    if len(messages) <= protected_tail_count:
        return [dict(m) for m in messages], 0

    compacted: list[dict[str, Any]] = []
    split_idx = len(messages) - protected_tail_count
    chars_saved = 0

    for idx, msg in enumerate(messages):
        m_copy = dict(msg)
        role = m_copy.get("role")

        # Only compact tool outputs in the older history
        if idx < split_idx and role in ("tool", "function"):
            content = str(m_copy.get("content", ""))
            tool_name = m_copy.get("name", "tool")
            if len(content) > 300:
                condensed = compact_tool_output(tool_name, {}, content)
                chars_saved += len(content) - len(condensed)
                m_copy["content"] = condensed
                m_copy["micro_compacted"] = True

        compacted.append(m_copy)

    rough_tokens_saved = max(0, chars_saved // 4)
    return compacted, rough_tokens_saved
