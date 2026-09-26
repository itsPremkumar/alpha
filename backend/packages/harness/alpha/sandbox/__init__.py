# Reachability seam for the storage-only workspace package (guide sections
# 44/49): :mod:`alpha.deepagent` is re-exported here so it is importable from
# an existing import path (:mod:`alpha.sandbox`) without a new top-level wave.
from alpha.deepagent import (  # noqa: E402
    CompositeWorkspace,
    GrepMatch,
    LocalWorkspace,
    Route,
    VirtualWorkspace,
    WorkspaceConflictError,
    WorkspaceDenied,
    WorkspaceEntry,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspacePathError,
    WorkspaceUnsupported,
)

from .sandbox import Sandbox
from .sandbox_provider import SandboxProvider, get_sandbox_provider

__all__ = [
    "CompositeWorkspace",
    "GrepMatch",
    "LocalWorkspace",
    "Route",
    "Sandbox",
    "SandboxProvider",
    "VirtualWorkspace",
    "WorkspaceConflictError",
    "WorkspaceDenied",
    "WorkspaceEntry",
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspacePathError",
    "WorkspaceUnsupported",
    "get_sandbox_provider",
]
