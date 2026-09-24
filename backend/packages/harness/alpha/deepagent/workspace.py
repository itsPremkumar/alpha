"""Storage-backend protocol for the virtual workspace (guide sections 5.1/5.2, 44, 48, 49.1-49.2).

Deep Agents exposes one filesystem interface over several storage backends.
Alpha already owns the *execution*-centric filesystem surface: the ``Sandbox``
ABC (:mod:`alpha.sandbox.sandbox`) with its virtual ``/mnt/user-data`` path
contract, local/Docker/E2B providers, and the ``ls``/``read_file``/
``write_file``/``str_replace``/``glob``/``grep`` tools built on top. What the
guide's ``BackendProtocol`` mapping (section 44) still lacks in Alpha is the
storage-only half: a small, dependency-free interface over durable storage
that composite path routing (:mod:`alpha.deepagent.composite`) can point at
without a sandbox acquire, authorization gate, or shell.

Scope decisions (honest, fail-closed):

* ``execute`` is part of the guide's protocol, but a storage backend must not
  grow a shell: ``LocalWorkspace.execute`` raises ``WorkspaceUnsupported`` and
  points callers at ``alpha.sandbox``'s ``bash`` tool. Not implemented beats a
  pretend sandbox.
* Paths are workspace-absolute (``/notes/a.txt``) and resolve under one root.
  ``..`` components, colon-bearing components (drive letters / alternate data
  streams), and symlink escapes raise :class:`WorkspacePathError` before any
  IO — the model is never the security boundary (guide sections 13/40).
* ``edit`` refuses an empty ``old`` string, a missing occurrence, and an
  ambiguous multi-occurrence replace unless ``replace_all=True`` — the same
  fail-closed contract the harness edit tool uses.
* Every public coroutine does its filesystem work in ``asyncio.to_thread`` so
  the event loop stays non-blocking. Text IO is ``encoding="utf-8"``.
* ``size_bytes`` is measured or ``None`` (stat failure / directory) — never
  guessed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

#: Default number of lines returned by :meth:`VirtualWorkspace.read`.
DEFAULT_READ_LIMIT = 1000

#: Longest grep line preview retained in a :class:`GrepMatch`; longer lines are
#: truncated with an explicit marker instead of being carried whole.
GREP_LINE_PREVIEW_CHARS = 1000


class WorkspaceError(RuntimeError):
    """Base class for virtual-workspace failures."""


class WorkspaceNotFoundError(WorkspaceError):
    """The addressed path does not exist in the workspace."""


class WorkspacePathError(WorkspaceError):
    """The path is malformed or would escape the workspace root (fail-closed)."""


class WorkspaceDenied(WorkspaceError):
    """A policy route denied the path before any storage backend saw it."""


class WorkspaceUnsupported(WorkspaceError):
    """The operation is honestly not implemented by this backend."""


class WorkspaceConflictError(WorkspaceError):
    """A mutating operation conflicted with the current file content."""


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """One directory listing row.

    ``size_bytes`` is the measured on-disk size for files, or ``None`` when
    the entry is a directory or ``stat`` failed — never an estimate.
    """

    path: str
    kind: Literal["file", "dir"]
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class GrepMatch:
    """One grep hit; ``line`` is 1-based, ``text`` is a bounded preview."""

    path: str
    line: int
    text: str


@runtime_checkable
class VirtualWorkspace(Protocol):
    """Guide section 5.2's ``VirtualWorkspace`` interface, Alpha-owned.

    Implementations are storage backends: they read and write bytes, they do
    not execute commands (see :class:`WorkspaceUnsupported`).
    """

    async def ls(self, path: str = "/") -> list[WorkspaceEntry]: ...

    async def read(self, path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> str: ...

    async def write(self, path: str, content: str) -> None: ...

    async def edit(self, path: str, old: str, new: str, replace_all: bool = False) -> None: ...

    async def delete(self, path: str) -> None: ...

    async def glob(self, pattern: str) -> list[str]: ...

    async def grep(self, pattern: str, path: str = "/", max_results: int = 200) -> list[GrepMatch]: ...

    async def execute(self, command: str, cwd: str | None = None) -> str: ...


class LocalWorkspace:
    """Root-confined, disk-backed :class:`VirtualWorkspace`.

    This is the guide's ``LocalWorkspaceBackend``: plain durable files under
    one host directory, with no sandbox lifecycle. It is what composite
    routing points durable prefixes (``/workspace``, ``/artifacts``, …) at.
    """

    def __init__(self, root: str | os.PathLike[str], *, create: bool = False) -> None:
        root_path = Path(root)
        if create:
            root_path.mkdir(parents=True, exist_ok=True)
        if not root_path.is_dir():
            raise WorkspaceError(f"workspace root is not a directory: {root_path}")
        self._root = root_path.resolve()

    @property
    def root(self) -> Path:
        """The resolved host directory backing this workspace."""
        return self._root

    # ------------------------------------------------------------------
    # Path handling (fail-closed, before any IO)
    # ------------------------------------------------------------------

    @staticmethod
    def _relative_parts(path: str) -> list[str]:
        raw = str(path).replace("\\", "/")
        parts: list[str] = []
        for part in raw.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise WorkspacePathError(f"'..' is not allowed in workspace paths: {path!r}")
            if ":" in part:
                raise WorkspacePathError(f"drive/stream components are not allowed in workspace paths: {path!r}")
            parts.append(part)
        return parts

    def _resolve(self, path: str) -> Path:
        parts = self._relative_parts(path)
        candidate = self._root.joinpath(*parts) if parts else self._root
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._root):
            raise WorkspacePathError(f"path escapes the workspace root: {path!r}")
        return resolved

    @staticmethod
    def _display(target: Path, root: Path) -> str:
        return "/" + target.relative_to(root).as_posix()

    def _require_file(self, path: str) -> Path:
        target = self._resolve(path)
        if not target.exists():
            raise WorkspaceNotFoundError(f"no such file: {path!r}")
        if not target.is_file():
            raise WorkspaceError(f"not a file: {path!r}")
        return target

    @staticmethod
    def _read_text(target: Path, display: str) -> str:
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(f"file is not valid UTF-8: {display!r}") from exc
        except OSError as exc:
            raise WorkspaceError(f"cannot read {display!r}: {exc}") from exc

    def _write_sync(self, path: str, content: str) -> None:
        target = self._resolve(path)
        if target.is_dir():
            raise WorkspaceError(f"cannot write over a directory: {path!r}")
        parent = target.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"cannot create parent directory for {path!r}: {exc}") from exc
        if not parent.is_dir() or not parent.resolve().is_relative_to(self._root):
            raise WorkspacePathError(f"parent directory escapes the workspace root: {path!r}")
        fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=target.name + ".", suffix=".alpha-tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # Sync cores (public coroutines wrap these in asyncio.to_thread)
    # ------------------------------------------------------------------

    def _ls_sync(self, path: str) -> list[WorkspaceEntry]:
        target = self._resolve(path)
        if not target.exists():
            raise WorkspaceNotFoundError(f"no such path: {path!r}")
        if target.is_file():
            resolved = target
            try:
                size = resolved.stat().st_size
            except OSError:
                size = None
            return [WorkspaceEntry(path=self._display(resolved, self._root), kind="file", size_bytes=size)]
        if not target.is_dir():
            raise WorkspaceError(f"not a directory: {path!r}")
        entries: list[WorkspaceEntry] = []
        for child in sorted(target.iterdir(), key=lambda item: item.name):
            try:
                resolved_child = child.resolve()
            except OSError:
                continue
            # A link resolving outside the root is not workspace content:
            # listings exclude it, while direct access raises (fail-closed).
            if not resolved_child.is_relative_to(self._root):
                continue
            kind: Literal["file", "dir"] = "dir" if resolved_child.is_dir() else "file"
            size: int | None = None
            if kind == "file":
                try:
                    size = resolved_child.stat().st_size
                except OSError:
                    size = None
            entries.append(WorkspaceEntry(path=self._display(resolved_child, self._root), kind=kind, size_bytes=size))
        return entries

    def _read_sync(self, path: str, offset: int, limit: int) -> str:
        if offset < 0:
            raise WorkspaceError(f"read offset must be >= 0, got {offset}")
        if limit < 1:
            raise WorkspaceError(f"read limit must be >= 1, got {limit}")
        target = self._require_file(path)
        text = self._read_text(target, self._display(target, self._root))
        lines = text.splitlines(keepends=True)
        return "".join(lines[offset : offset + limit])

    def _edit_sync(self, path: str, old: str, new: str, replace_all: bool) -> None:
        if old == "":
            raise WorkspaceConflictError("edit requires a non-empty 'old' string")
        target = self._require_file(path)
        display = self._display(target, self._root)
        text = self._read_text(target, display)
        count = text.count(old)
        if count == 0:
            raise WorkspaceConflictError(f"'old' string not found in {display!r}")
        if count > 1 and not replace_all:
            raise WorkspaceConflictError(
                f"'old' string occurs {count} times in {display!r}; pass replace_all=True to replace every occurrence"
            )
        self._write_sync(path, text.replace(old, new))

    def _delete_sync(self, path: str) -> None:
        parts = self._relative_parts(path)
        if not parts:
            raise WorkspacePathError("refusing to delete the workspace root itself")
        candidate = self._root.joinpath(*parts)
        if candidate.is_symlink():
            # Unlinking the link itself never touches its target, inside or out.
            try:
                candidate.unlink()
            except OSError as exc:
                raise WorkspaceError(f"cannot delete {path!r}: {exc}") from exc
            return
        target = self._resolve(path)
        if target == self._root:
            raise WorkspacePathError("refusing to delete the workspace root itself")
        if not target.exists():
            raise WorkspaceNotFoundError(f"no such path: {path!r}")
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            raise WorkspaceError(f"cannot delete {path!r}: {exc}") from exc

    @staticmethod
    def _pattern_rel(pattern: str) -> str:
        raw = str(pattern).replace("\\", "/")
        parts = [part for part in raw.split("/") if part not in ("", ".")]
        if not parts:
            raise WorkspaceError(f"empty glob pattern: {pattern!r}")
        for part in parts:
            if part == "..":
                raise WorkspacePathError(f"'..' is not allowed in glob patterns: {pattern!r}")
            if ":" in part:
                raise WorkspacePathError(f"drive/stream components are not allowed in glob patterns: {pattern!r}")
        return "/".join(parts)

    def _glob_sync(self, pattern: str) -> list[str]:
        rel_pattern = self._pattern_rel(pattern)
        try:
            matches = sorted(self._root.glob(rel_pattern))
        except ValueError as exc:
            raise WorkspaceError(f"invalid glob pattern {pattern!r}: {exc}") from exc
        out: list[str] = []
        for match in matches:
            try:
                resolved = match.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(self._root):
                continue
            out.append(self._display(resolved, self._root))
        return out

    def _grep_sync(self, pattern: str, path: str, max_results: int) -> list[GrepMatch]:
        if max_results < 1:
            raise WorkspaceError(f"max_results must be >= 1, got {max_results}")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise WorkspaceError(f"invalid regular expression {pattern!r}: {exc}") from exc
        base = self._resolve(path)
        if not base.exists():
            raise WorkspaceNotFoundError(f"no such path: {path!r}")
        if base.is_file():
            candidates = [base]
        else:
            candidates = sorted(item for item in base.rglob("*") if item.is_file())
        results: list[GrepMatch] = []
        for candidate in candidates:
            if len(results) >= max_results:
                break
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(self._root):
                continue
            try:
                text = resolved.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # Binary or unreadable files are skipped, never guessed at.
                continue
            display = self._display(resolved, self._root)
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    if len(line) > GREP_LINE_PREVIEW_CHARS:
                        line = line[:GREP_LINE_PREVIEW_CHARS] + " [truncated]"
                    results.append(GrepMatch(path=display, line=line_no, text=line))
                    if len(results) >= max_results:
                        break
        return results

    # ------------------------------------------------------------------
    # Public coroutine surface
    # ------------------------------------------------------------------

    async def ls(self, path: str = "/") -> list[WorkspaceEntry]:
        return await asyncio.to_thread(self._ls_sync, path)

    async def read(self, path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> str:
        return await asyncio.to_thread(self._read_sync, path, offset, limit)

    async def write(self, path: str, content: str) -> None:
        await asyncio.to_thread(self._write_sync, path, content)

    async def edit(self, path: str, old: str, new: str, replace_all: bool = False) -> None:
        await asyncio.to_thread(self._edit_sync, path, old, new, replace_all)

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._delete_sync, path)

    async def glob(self, pattern: str) -> list[str]:
        return await asyncio.to_thread(self._glob_sync, pattern)

    async def grep(self, pattern: str, path: str = "/", max_results: int = 200) -> list[GrepMatch]:
        return await asyncio.to_thread(self._grep_sync, pattern, path, max_results)

    async def execute(self, command: str, cwd: str | None = None) -> str:
        raise WorkspaceUnsupported(
            "LocalWorkspace does not run commands (not implemented by design); "
            "shell execution lives in alpha.sandbox (the 'bash' tool / Sandbox.execute_command)"
        )
