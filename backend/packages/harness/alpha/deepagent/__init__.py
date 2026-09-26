"""Deep Agents storage workspace (guide sections 5.2/5.3, 44, 48, 49.1-49.2).

A storage-only filesystem interface plus composite path routing, distinct from
the execution-centric :mod:`alpha.sandbox` surface. The public names are
re-exported from :mod:`alpha.sandbox` so the package is reachable from an
existing import path (landed-wave pattern).
"""

from __future__ import annotations

from .composite import CompositeWorkspace, Route
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
    "CompositeWorkspace",
    "GrepMatch",
    "LocalWorkspace",
    "Route",
    "VirtualWorkspace",
    "WorkspaceConflictError",
    "WorkspaceDenied",
    "WorkspaceEntry",
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspacePathError",
    "WorkspaceUnsupported",
]
