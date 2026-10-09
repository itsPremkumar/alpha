"""Workspace boundary for tools that take a model-supplied `root_path`.

Several code tools accept a `root_path` string and then either read a tree,
write files, or `subprocess.run(..., cwd=root)`. Before this module existed each
one did `Path(root_path).resolve()` and used the result, so a model-supplied
`root_path` of `C:\\` or `/` was accepted silently.

Two distinct escapes matter and they are different bugs:

* **The root itself can point anywhere.** `auto_test_and_repair` ran
  `subprocess.run(cmd, shell=True, cwd=root)`, so the root chose the working
  directory of an arbitrary host command.
* **A relative path can escape the root.** `manage_code_checkpoint` snapshotted
  `root / target_files[i]` and rollback wrote it back, with `mkdir(parents=True)`
  on the parent. A `target_files` entry of ``../../../../.ssh/authorized_keys``
  therefore both *read* and *wrote* outside the workspace.

## Which roots are allowed

``project_root()`` (``ALPHA_PROJECT_ROOT``, else the process working directory)
is always allowed and needs no declaration. A deployment that keeps agent
workspaces *outside* the project - a git worktree tree, an evaluation sandbox, a
per-run scratch directory - declares them, mirroring the ``workspace_roots``
contract in ``alpha.bots.survey``, which states the same rule as "the home
directory is only ever scanned when the operator names it explicitly, never by
default". Two sources, in order:

* ``sandbox.workspace_roots`` in ``config.yaml`` - the operator's durable
  declaration.
* ``ALPHA_WORKSPACE_ROOTS`` - an ``os.pathsep``-separated list, for a process
  with no readable config, and for tests, which should not need a config file to
  exercise a tool.

An entry that does not resolve to an existing directory is skipped rather than
raised: a stale declaration must not make every workspace tool fail closed at
startup.

## What this is not

This is a **workspace containment** check, not a privilege sandbox. It bounds
*where a tool looks*; it does not grant or revoke the ability to execute code.
Command execution is gated separately, by
:func:`alpha.sandbox.security.is_host_bash_allowed`.
"""

from __future__ import annotations

import os
from pathlib import Path

from alpha.config.runtime_paths import project_root

#: Directories that are never a valid read/write target for a workspace tool.
#: `.git` holds the repository's own object store and refs; a tool that writes
#: there can rewrite history or install a hook, and a tool that reads it will
#: happily surface every object in the repository.
_FORBIDDEN_SUBPATHS: tuple[str, ...] = (".git",)


class WorkspaceBoundaryError(PermissionError):
    """A supplied path escapes the workspace boundary.

    Subclasses ``PermissionError`` deliberately: the local-bash path validator
    already raises ``PermissionError`` for the same class of mistake, so callers
    that handle one handle both.
    """

    def __init__(self, message: str, *, requested: str, boundary: Path) -> None:
        super().__init__(message)
        self.requested = requested
        self.boundary = boundary


def _is_within(candidate: Path, boundary: Path) -> bool:
    """True when *candidate* is *boundary* or lives beneath it.

    Uses ``os.path.commonpath`` on already-resolved absolute paths rather than
    ``Path.is_relative_to`` so a Windows drive-relative or UNC path cannot raise
    or compare oddly, and so a case-differing path on a case-insensitive volume
    still matches.
    """
    try:
        return os.path.commonpath([str(candidate), str(boundary)]) == str(boundary)
    except ValueError:
        # Different drives on Windows: definitively outside.
        return False


def _extra_workspace_roots() -> list[Path]:
    """Resolve the operator-declared roots that supplement ``project_root()``."""
    roots: list[Path] = []

    try:
        from alpha.config.app_config import get_app_config

        cfg = get_app_config()
        declared = list(getattr(getattr(cfg, "sandbox", None), "workspace_roots", None) or [])
    except Exception:
        # An unreadable config must not silently widen *or* narrow the boundary;
        # the env source below still applies, and the default project root always does.
        declared = []

    for entry in [*declared, *os.getenv("ALPHA_WORKSPACE_ROOTS", "").split(os.pathsep)]:
        entry = str(entry or "").strip()
        if not entry:
            continue
        try:
            candidate = Path(entry).expanduser().resolve()
        except OSError:
            continue
        if candidate.is_dir():
            roots.append(candidate)

    return roots


def allowed_workspace_roots() -> list[Path]:
    """Every root a workspace tool may operate in, de-duplicated."""
    roots: list[Path] = []
    for root in (project_root(), *_extra_workspace_roots()):
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def resolve_workspace_root(root_path: str, *, allow_outside_root: bool = False) -> Path:
    """Resolve and validate a tool-supplied workspace root.

    Args:
        root_path: The model- or caller-supplied root. Relative paths resolve
            against the process working directory, as ``Path.resolve()`` does.
        allow_outside_root: Skip the containment check entirely. Only for a call
            site with its own, narrower authority to name a directory (for
            example one that already validated the path against a sandbox
            provider's mapping table).

    Returns:
        The resolved, existing directory.

    Raises:
        WorkspaceBoundaryError: The path is missing, is not a directory, or is
            outside every allowed workspace root.
    """
    raw = str(root_path or ".").strip() or "."
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:  # e.g. a path that is too long, or a symlink loop
        raise WorkspaceBoundaryError(
            f"Path {raw!r} could not be resolved: {exc}",
            requested=raw,
            boundary=project_root(),
        ) from exc

    if not resolved.exists():
        raise WorkspaceBoundaryError(
            f"Path {raw!r} does not exist.",
            requested=raw,
            boundary=project_root(),
        )
    if not resolved.is_dir():
        raise WorkspaceBoundaryError(
            f"Path {raw!r} is not a directory.",
            requested=raw,
            boundary=project_root(),
        )

    if allow_outside_root:
        return resolved

    roots = allowed_workspace_roots()
    if any(_is_within(resolved, root) for root in roots):
        return resolved

    listed = ", ".join(str(r) for r in roots)
    raise WorkspaceBoundaryError(
        f"Path {raw!r} resolves to {resolved}, which is outside every allowed workspace root. "
        f"Allowed roots: {listed}. An operator can add one with `sandbox.workspace_roots` in "
        "config.yaml or the ALPHA_WORKSPACE_ROOTS environment variable.",
        requested=raw,
        boundary=roots[0] if roots else project_root(),
    )


def _windows_reading_escapes(resolved_root: Path, raw: str) -> bool:
    """True when *raw* would leave *resolved_root* read with Windows separators.

    ``\\`` is a path separator on Windows and an ordinary filename byte on POSIX,
    so ``..\\outside.py`` resolves *inside* the root on Linux while escaping it on
    Windows. The absolute-path leg of :func:`resolve_workspace_file` already
    refuses a leading backslash on every platform for exactly this reason; this
    is the traversal half of the same decision, so one path string gets one
    verdict whichever host validates it. Without it a workspace shared between
    hosts is certified safe here and escapes there.
    """
    if "\\" not in raw:
        return False
    try:
        windows_reading = (resolved_root / Path(raw.replace("\\", "/"))).resolve()
    except OSError:
        return True  # unresolvable is refused, never assumed contained
    return not _is_within(windows_reading, resolved_root)


def resolve_workspace_file(root: Path, relative: str) -> Path:
    """Resolve *relative* against *root*, refusing anything that escapes it.

    This is the check a bare ``root / rel`` cannot provide: ``Path`` happily
    produces ``root/../../outside``, and a caller that then creates parent
    directories turns a read into a write outside the workspace.

    Args:
        root: An already-validated workspace root (see
            :func:`resolve_workspace_root`).
        relative: The relative path the tool was asked to touch.

    Returns:
        The resolved absolute path, guaranteed to be *root* or beneath it.

    Raises:
        WorkspaceBoundaryError: The path is absolute, escapes *root*, or targets
            a reserved ``.git`` subpath.
    """
    raw = str(relative or "").strip()
    if not raw:
        raise WorkspaceBoundaryError(
            "An empty file path was supplied.",
            requested=relative,
            boundary=root,
        )

    candidate = Path(raw)
    if candidate.is_absolute() or candidate.drive or raw.startswith(("/", "\\")):
        raise WorkspaceBoundaryError(
            f"File path {raw!r} must be relative to the workspace root {root}, not absolute.",
            requested=raw,
            boundary=root,
        )

    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()

    if not _is_within(resolved, resolved_root):
        raise WorkspaceBoundaryError(
            f"File path {raw!r} resolves to {resolved}, which is outside the workspace root {resolved_root}. Relative paths may not traverse upwards.",
            requested=raw,
            boundary=resolved_root,
        )

    if _windows_reading_escapes(resolved_root, raw):
        raise WorkspaceBoundaryError(
            f"File path {raw!r} resolves outside the workspace root {resolved_root} once backslash is read as a path separator, which is what it is on Windows. "
            "The boundary refuses that reading rather than certify a string that escapes on a Windows host.",
            requested=raw,
            boundary=resolved_root,
        )

    parts = {p.lower() for p in resolved.relative_to(resolved_root).parts}
    for forbidden in _FORBIDDEN_SUBPATHS:
        if forbidden in parts:
            raise WorkspaceBoundaryError(
                f"File path {raw!r} targets the reserved {forbidden!r} directory. Repository internals are not a valid read or write target.",
                requested=raw,
                boundary=resolved_root,
            )
    return resolved
