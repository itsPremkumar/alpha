"""Deep Agents storage workspace (guide sections 5.2/5.3, 44, 48, 49.1-49.2).

A storage-only filesystem interface plus composite path routing, distinct from
the execution-centric :mod:`alpha.sandbox` surface. The public names are
re-exported from :mod:`alpha.sandbox` so the package is reachable from an
existing import path (landed-wave pattern).
"""

from __future__ import annotations

from .composite import CompositeWorkspace, Route
from .index import MAX_INDEX_CHARS, MAX_INDEX_ENTRIES, render_working_index
from .state import (
    MAX_WORKING_FILE_BYTES,
    MAX_WORKING_FILES,
    MAX_WORKING_SUMMARY_CHARS,
    MAX_WORKING_TOTAL_BYTES,
    StateWorkspace,
    WorkingFile,
    WorkingFileOp,
    merge_working_files,
    working_files_bytes,
    working_plane_limits,
)
from .workspace import (
    DEFAULT_READ_LIMIT,
    GREP_LINE_PREVIEW_CHARS,
    GrepMatch,
    LocalWorkspace,
    VirtualWorkspace,
    WorkspaceConflictError,
    WorkspaceDenied,
    WorkspaceEntry,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspacePathError,
    WorkspaceUnsupported,
)

__all__ = [
    "DEFAULT_READ_LIMIT",
    "GREP_LINE_PREVIEW_CHARS",
    "MAX_INDEX_CHARS",
    "MAX_INDEX_ENTRIES",
    "MAX_WORKING_FILE_BYTES",
    "MAX_WORKING_FILES",
    "MAX_WORKING_SUMMARY_CHARS",
    "MAX_WORKING_TOTAL_BYTES",
    "CompositeWorkspace",
    "GrepMatch",
    "LocalWorkspace",
    "Route",
    "StateWorkspace",
    "VirtualWorkspace",
    "WorkingFile",
    "WorkingFileOp",
    "WorkspaceConflictError",
    "WorkspaceDenied",
    "WorkspaceEntry",
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspacePathError",
    "WorkspaceUnsupported",
    "merge_working_files",
    "render_working_index",
    "working_files_bytes",
    "working_plane_limits",
]
