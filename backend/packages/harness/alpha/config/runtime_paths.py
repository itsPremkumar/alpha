"""Runtime path resolution for standalone harness usage."""

from __future__ import annotations

import os
from pathlib import Path

#: Files that only exist at the repository root, not in a subproject. Used to
#: locate the repository structurally instead of trusting the working directory.
_REPO_ROOT_MARKERS: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")


def project_root() -> Path:
    """Return the caller project root for runtime-owned files.

    This is "where Alpha writes its own state", **not** "where the repository
    is". It resolves ``ALPHA_PROJECT_ROOT`` or the process working directory,
    and the documented launch command is ``cd backend && uvicorn
    app.gateway.app:app``, so under the shipped launcher it answers ``backend/``.
    Correct for :func:`runtime_home` and :func:`resolve_path`; wrong for any
    caller that needs to read a repository file such as ``start.ps1``. Use
    :func:`repository_root` for that question.
    """
    if env_root := os.getenv("ALPHA_PROJECT_ROOT"):
        root = Path(env_root).resolve()
        if not root.exists():
            raise ValueError(f"ALPHA_PROJECT_ROOT is set to '{env_root}', but the resolved path '{root}' does not exist.")
        if not root.is_dir():
            raise ValueError(f"ALPHA_PROJECT_ROOT is set to '{env_root}', but the resolved path '{root}' is not a directory.")
        return root
    return Path.cwd().resolve()


def repository_root() -> Path:
    """Return the repository root, located structurally rather than by cwd.

    An explicit ``ALPHA_REPOSITORY_ROOT`` wins when it is set and valid. Otherwise
    the outermost ancestor of this module containing both ``backend/packages`` and
    a repository marker is the root, which is correct regardless of the working
    directory the process was launched from.

    Never fall back to :func:`project_root`: a caller that asked for the
    repository and silently receives the current working directory is how
    Alpha's own PowerShell sentinel ended up watching ``backend/`` -- a tree
    with zero ``.ps1`` files -- and reporting no signals from a source that
    exists specifically to watch them.
    """
    override = os.getenv("ALPHA_REPOSITORY_ROOT")
    if override:
        root = Path(override).resolve()
        if root.is_dir():
            return root
        raise ValueError(f"ALPHA_REPOSITORY_ROOT is set to '{override}', but '{root}' is not a directory.")

    here = Path(__file__).resolve()
    match: Path | None = None
    for candidate in here.parents:
        if (candidate / "backend" / "packages").is_dir() and any((candidate / m).is_file() for m in _REPO_ROOT_MARKERS):
            # Keep walking outward rather than returning the first hit.
            # `_REPO_ROOT_MARKERS` are AGENTS.md/CLAUDE.md, which this repo
            # deliberately places at every depth, so the *innermost* directory
            # satisfying the test is not necessarily the repository. A stray
            # `backend/packages/harness/alpha/backend/packages` directory (a
            # misdirected mkdir, untracked) made the walk stop inside the
            # harness package, and every reader then resolved
            # `contracts/feature_manifest.json` to a path under the package --
            # the generated manifest lives at the repository root, so
            # `load_feature_manifest` raised RegistryUnavailable and nine
            # self-inventory tests failed. `backend/packages` only exists for
            # real at the repository root, so the outermost match wins.
            match = candidate
    if match is not None:
        return match
    # A relocated/embedded checkout with no marker: the harness package's known
    # depth still lands on the directory that contains `backend/`.
    return here.parents[4]


def runtime_home() -> Path:
    """Return the writable Alpha state directory."""
    if env_home := os.getenv("ALPHA_HOME"):
        return Path(env_home).resolve()
    return project_root() / ".alpha"


def resolve_path(value: str | os.PathLike[str], *, base: Path | None = None) -> Path:
    """Resolve absolute paths as-is and relative paths against the project root."""
    path = Path(value)
    if not path.is_absolute():
        path = (base or project_root()) / path
    return path.resolve()


def existing_project_file(names: tuple[str, ...]) -> Path | None:
    """Return the first existing named file under the project root."""
    root = project_root()
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None
