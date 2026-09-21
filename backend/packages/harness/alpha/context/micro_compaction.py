"""Micro-Compaction and Rolling Semantic Receipt Generator inspired by Hermes Agent."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


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

    lines = [line.strip() for line in text.splitlines() if line.strip()]
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


def _protect_indices(messages: list[dict[str, Any]], split_idx: int) -> dict[int, str]:
    """Map position -> text for every tool message eligible for compaction."""
    candidates: dict[int, str] = {}
    for idx in range(min(split_idx, len(messages))):
        msg = messages[idx]
        if msg.get("role") not in ("tool", "function"):
            continue
        content = str(msg.get("content", ""))
        if len(content) > 300:
            candidates[idx] = content
    return candidates


async def aapply_micro_compaction_smart(
    messages: list[dict[str, Any]],
    protected_tail_count: int = 4,
    *,
    task: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Micro-compaction that protects chunks the task still needs.

    Same contract and same output shape as :func:`apply_micro_compaction`, plus
    one System One pass that scores the compaction candidates and leaves the
    load-bearing ones intact. When System One is off, unreachable, or unsure
    this is byte-identical to :func:`apply_micro_compaction`.
    """
    if len(messages) <= protected_tail_count:
        return [dict(m) for m in messages], 0

    keep: set[int] = set()
    split_idx = len(messages) - protected_tail_count
    candidates = _protect_indices(messages, split_idx)
    if candidates and task.strip():
        try:
            from alpha.context.retention import MAX_CHUNKS, RetentionScores, retention_scores

            # Longest first: the chunks worth arguing about are the big ones.
            ordered = sorted(candidates, key=lambda i: -len(candidates[i]))[:MAX_CHUNKS]
            scores: RetentionScores | None = await retention_scores(task, [candidates[i] for i in ordered])
        except Exception:
            logger.debug("System One retention scoring unavailable; compacting as usual.", exc_info=True)
            scores = None
        if scores is not None:
            for position, index in enumerate(ordered):
                if scores.should_keep(position) is True:
                    keep.add(index)

    compacted: list[dict[str, Any]] = []
    chars_saved = 0
    for idx, msg in enumerate(messages):
        m_copy = dict(msg)
        if idx < split_idx and m_copy.get("role") in ("tool", "function") and idx not in keep:
            content = str(m_copy.get("content", ""))
            if len(content) > 300:
                condensed = compact_tool_output(str(m_copy.get("name", "tool")), {}, content)
                chars_saved += len(content) - len(condensed)
                m_copy["content"] = condensed
                m_copy["micro_compacted"] = True
        compacted.append(m_copy)

    return compacted, max(0, chars_saved // 4)


def apply_micro_compaction_smart(
    messages: list[dict[str, Any]],
    protected_tail_count: int = 4,
    *,
    task: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Sync wrapper; falls back to plain compaction inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(aapply_micro_compaction_smart(messages, protected_tail_count, task=task))
    logger.debug("apply_micro_compaction_smart inside a running loop; compacting as usual.")
    return apply_micro_compaction(messages, protected_tail_count)
