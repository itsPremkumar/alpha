"""Hashline Editing Builtin Tool.

Exposes hashline_read and hashline_edit to agents:
- hashline_read: Reads file and tags each line with LINE#HASH| content.
- hashline_edit: Modifies file using verified start_ref and end_ref line hashes.
"""

from __future__ import annotations

from pathlib import Path

from langchain.tools import tool

from agent_workspace.editing.hashline import (
    HashlineMismatchError,
    apply_hashline_edit,
    format_hash_lines,
)
from agent_workspace.safety.comment_guard import check_for_lazy_comments
from agent_workspace.safety.ast_syntax_guard import validate_syntax_precommit


@tool
def hashline_read(file_path: str) -> str:
    """Read a file with content-hashed lines (LINE#HASH| content). Used to get stable line references before editing."""
    p = Path(file_path)
    if not p.exists():
        return f"Error: file '{file_path}' does not exist."
    try:
        content = p.read_text(encoding="utf-8")
        return format_hash_lines(content)
    except Exception as e:
        return f"Error reading file '{file_path}': {e}"


@tool
def hashline_edit(
    file_path: str,
    start_ref: str,
    end_ref: str,
    replacement: str,
) -> str:
    """Edit a file using verified content-hash line references (e.g. start_ref='12#VK', end_ref='15#MB'). Eliminates whitespace and line drift errors."""
    p = Path(file_path)
    if not p.exists():
        return f"Error: file '{file_path}' does not exist."
    try:
        # Pre-check replacement with CommentGuard to prevent lazy omissions
        check_for_lazy_comments(replacement, strict=True)

        content = p.read_text(encoding="utf-8")
        updated = apply_hashline_edit(content, start_ref, end_ref, replacement)
        is_valid, err_msg = validate_syntax_precommit(str(p), updated)
        if not is_valid:
            return err_msg
        p.write_text(updated, encoding="utf-8")
        return f"Successfully updated '{file_path}' from {start_ref} to {end_ref}."
    except HashlineMismatchError as e:
        return f"HashlineMismatchError: {e}"
    except Exception as e:
        return f"Error applying hashline edit: {e}"
