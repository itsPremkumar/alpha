"""Bounded index projection for the working plane.

The index is the whole point of a working plane. A note the model wrote but
cannot *find* is a note it will re-derive — and re-deriving on a long run is
exactly the cost the plane exists to remove. So every model request carries a
manifest of the plane: address, measured size, and the model's own one-line
purpose.

What it never carries is the body. Two reasons, both measured elsewhere in this
repository: an interface map more than doubles reuse of an agent's own earlier
work while dumping full source achieves nothing *and raises* duplication
(``GroundingMiddleware``), and one request carrying every file's content is a
prompt that grows with the run instead of staying flat.
"""

from __future__ import annotations

from html import escape

from alpha.deepagent.state import WorkingFile

#: Hard ceiling on the rendered index, in characters. The plane is injected on
#: *every* model request, so an unbounded render turns a bounded plane into an
#: unbounded prompt.
MAX_INDEX_CHARS = 2_000

#: Most rows rendered before the remainder is named as an omission. Past this
#: many entries the model cannot act on the list anyway.
MAX_INDEX_ENTRIES = 32

_INDEX_WRAPPER = "deep_agent_workspace"


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def render_working_index(
    files: list[WorkingFile] | None,
    *,
    tool_name: str = "deepagent_workspace",
) -> str:
    """Render the working-plane manifest, or ``""`` when the plane is empty.

    Budget order is the same as every other bounded renderer here: the rows are
    trimmed **before** escaping and wrapping, then escaped, then wrapped — so a
    hostile ``summary`` string cannot spend the budget on markup and squeeze out
    a legitimate row, and cannot inject tags into the projected request either.
    """
    if not files:
        return ""

    rows: list[str] = []
    used = 0
    for file in files[:MAX_INDEX_ENTRIES]:
        path = str(file.get("path", ""))
        content = file.get("content", "")
        size = len(content.encode("utf-8")) if isinstance(content, str) else 0
        summary = " ".join(str(file.get("summary", "")).split())
        row = f"- {path} ({_format_bytes(size)})"
        if summary:
            row += f" — {summary}"
        if used + len(row) + 1 > MAX_INDEX_CHARS:
            break
        used += len(row) + 1
        rows.append(escape(row, quote=False))

    if not rows:
        return ""

    omitted = len(files) - len(rows)
    lines = [
        f"<{_INDEX_WRAPPER}>",
        "Your own scratch files for this thread. They are yours alone, they survive",
        "context compaction, and they are the cheapest place to keep findings, drafts,",
        "constraints and open questions from a long task. Read one before re-deriving it.",
    ]
    lines.extend(rows)
    if omitted > 0:
        lines.append(f"- ... {omitted} more file(s) not shown; list them with {tool_name}(action='ls')")
    lines.append(f"Open one with {tool_name}(action='read'); keep them current with 'write' and 'edit'.")
    lines.append(f"</{_INDEX_WRAPPER}>")
    return "\n".join(lines)
