"""Hashline Editing Builtin Tool.

Exposes hashline_read and hashline_edit to agents:
- hashline_read: Reads file and tags each line with LINE#HASH| content.
- hashline_edit: Modifies file using verified start_ref and end_ref line hashes.

Both tools resolve ``file_path`` through the SAME sandbox path mapping as
``ls``/``read_file``/``write_file``/``str_replace``
(:func:`alpha.sandbox.tools.resolve_sandbox_tool_path`) and read/write through
the sandbox itself. They previously called ``Path(file_path).exists()``
directly, which bypassed that mapping entirely. The two visible consequences
were both lies about real state:

1. A sandbox-mapped file that ``write_file`` had just created and ``read_file``
   and ``ls`` had just confirmed was reported as "does not exist", so an agent
   was told its own write failed.
2. A host path outside the mapping was read and edited even though every other
   tool refused it with "Permission denied".

One more rule follows from routing through the sandbox: existence is decided by
the sandbox (and therefore by the mapping), and a path that is refused is
refused -- never reported as missing.
"""

from __future__ import annotations

from langchain.tools import tool

from alpha.editing.hashline import (
    HashlineMismatchError,
    apply_hashline_edit,
    format_hash_lines,
)
from alpha.safety.ast_syntax_guard import validate_syntax_precommit
from alpha.safety.comment_guard import check_for_lazy_comments
from alpha.tools.types import Runtime


def _read_sandbox_file(runtime: Runtime, path: str) -> str:
    """Read *path* through the sandbox using read_file's own resolution rules.

    The sandbox tool layer is imported lazily: ``alpha.tools.builtins`` is a
    gated cold-start target and this module must not pull it in at import time.

    Raises:
        FileNotFoundError: The mapped path does not exist.
        PermissionError: The path is outside the sandbox mapping.
    """
    from alpha.sandbox.tools import read_current_file_content

    return read_current_file_content(runtime, path)


def _edit_sandbox_file(runtime: Runtime, path: str, new_content: str) -> None:
    """Write *new_content* to *path* through the sandbox, under its file lock."""
    from alpha.sandbox.tools import (
        ensure_sandbox_initialized,
        ensure_thread_directories_exist,
        get_file_operation_lock,
        resolve_sandbox_tool_path,
    )

    sandbox = ensure_sandbox_initialized(runtime)
    ensure_thread_directories_exist(runtime)
    resolved = resolve_sandbox_tool_path(runtime, path, read_only=False)
    with get_file_operation_lock(sandbox, resolved):
        sandbox.write_file(resolved, new_content)


@tool
def hashline_read(runtime: Runtime, file_path: str) -> str:
    """Read a file with content-hashed lines (LINE#HASH| content). Used to get stable line references before editing.

    Args:
        file_path: The **absolute sandbox path** to the file, e.g. `/mnt/user-data/workspace/app.py`.
            The path is resolved through the same sandbox mapping as `ls`, `read_file`
            and `write_file`, so a path those tools accept is a path this tool accepts.
    """
    try:
        content = _read_sandbox_file(runtime, file_path)
    except FileNotFoundError:
        # Verified through the sandbox mapping: this path really is absent.
        return f"Error: file '{file_path}' does not exist."
    except IsADirectoryError:
        return f"Error: '{file_path}' is a directory, not a file."
    except PermissionError:
        return f"Error: Permission denied reading file: {file_path}"
    except Exception as e:
        return f"Error reading file '{file_path}': {e}"
    return format_hash_lines(content)


@tool
def hashline_edit(
    runtime: Runtime,
    file_path: str,
    start_ref: str,
    end_ref: str,
    replacement: str,
) -> str:
    """Edit a file using verified content-hash line references (e.g. start_ref='12#VK', end_ref='15#MB'). Eliminates whitespace and line drift errors.

    Args:
        file_path: The **absolute sandbox path** to the file, e.g. `/mnt/user-data/workspace/app.py`.
            Resolved through the same sandbox mapping as `ls`, `read_file` and `write_file`.
        start_ref: First line reference, as returned by hashline_read (`<line>#<2-char-hash>`).
        end_ref: Last line reference (inclusive), in the same format.
        replacement: Text to put in place of the referenced line range.
    """
    try:
        # Pre-check replacement with CommentGuard to prevent lazy omissions
        check_for_lazy_comments(replacement, strict=True)

        content = _read_sandbox_file(runtime, file_path)
        updated = apply_hashline_edit(content, start_ref, end_ref, replacement)
        is_valid, err_msg = validate_syntax_precommit(file_path, updated)
        if not is_valid:
            return err_msg
        _edit_sandbox_file(runtime, file_path, updated)
        return f"Successfully updated '{file_path}' from {start_ref} to {end_ref}."
    except FileNotFoundError:
        # Verified through the sandbox mapping: this path really is absent.
        return f"Error: file '{file_path}' does not exist."
    except IsADirectoryError:
        return f"Error: '{file_path}' is a directory, not a file."
    except PermissionError:
        return f"Error: Permission denied accessing file: {file_path}"
    except HashlineMismatchError as e:
        return f"HashlineMismatchError: {e}"
    except Exception as e:
        return f"Error applying hashline edit: {e}"
