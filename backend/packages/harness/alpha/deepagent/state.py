"""Thread-scoped working plane: deepagents' ``StateBackend``, Alpha-owned.

LangChain's ``deepagents`` ships a ``StateBackend`` — a virtual filesystem that
lives in the graph state rather than on a host disk. That is the piece Alpha's
own storage-only seam (:mod:`alpha.deepagent.workspace` /
:mod:`alpha.deepagent.composite`) never had: both of those route to
:class:`LocalWorkspace`, which needs a real directory. What a long, multi-step
run needs instead is a **bounded scratch space of its own**, one that

* needs no sandbox acquire, no host path and no disk write,
* survives context compaction (it is a state channel, not ``messages``),
* survives a checkpoint/resume and a Gateway restart,
* is projected into every model request as an **index** — path, size and the
  model's own one-line purpose — never as a body.

This module is that backend plus the LangGraph channel that carries it.

Three decisions a future edit must not undo:

1. **The state value is ordered by write, and a write moves its file to the
   tail.** Recency therefore *is* list order, so eviction drops from the front
   and needs no clock. A wall-clock timestamp would make every checkpoint
   non-deterministic and would make two identical runs produce two different
   state blobs.
2. **A delete is an operation, not a tombstone.** The reducer accepts
   :class:`WorkingFileOp` entries whose ``content`` is ``None`` and drops the
   path outright, so a deleted file leaves no residue and cannot be resurrected
   by an older concurrent write folding in afterwards.
3. **Every bound is refused with the bound named, never clamped.** A write
   that would exceed ``MAX_WORKING_FILES`` or ``MAX_WORKING_TOTAL_BYTES``
   raises so the model learns the actual ceiling; silently truncating the
   content would leave a file whose bytes nobody wrote.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from alpha.deepagent.workspace import (
    DEFAULT_READ_LIMIT,
    GREP_LINE_PREVIEW_CHARS,
    GrepMatch,
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspacePathError,
    WorkspaceUnsupported,
    join_workspace_path,
    normalize_workspace_parts,
)

# ---------------------------------------------------------------------------
# Bounds. Every one of these is a refusal, never a clamp.
# ---------------------------------------------------------------------------

#: Most files the working plane holds. 64 x 32 KiB is already far past what a
#: single turn can usefully consult, and the whole plane is projected into
#: every model request, so an unbounded count is an unbounded prompt.
MAX_WORKING_FILES = 64

#: Ceiling on the summed ``content`` size of every working file.
MAX_WORKING_TOTAL_BYTES = 262_144

#: Ceiling on one file's content. Sized to hold a findings note or a draft
#: section; anything larger belongs on disk via ``write_file`` or in an
#: externalized tool result.
MAX_WORKING_FILE_BYTES = 32_768

#: Ceiling on the model's own per-file one-line purpose, so the projected index
#: stays a directory and cannot become a second copy of the body.
MAX_WORKING_SUMMARY_CHARS = 160

__all__ = [
    "MAX_WORKING_FILE_BYTES",
    "MAX_WORKING_FILES",
    "MAX_WORKING_SUMMARY_CHARS",
    "MAX_WORKING_TOTAL_BYTES",
    "StateWorkspace",
    "WorkingFile",
    "WorkingFileOp",
    "merge_working_files",
    "working_files_bytes",
    "working_plane_limits",
]


class WorkingFile(TypedDict):
    """One stored working file.

    ``summary`` is the model's own one-line purpose for the file. It is what
    makes the projected index useful: a list of paths is a directory, and a
    directory is not a memory. It is capped at
    :data:`MAX_WORKING_SUMMARY_CHARS` and is never the body.
    """

    path: str
    content: str
    summary: NotRequired[str]


class WorkingFileOp(TypedDict):
    """One write to the working plane.

    ``content=None`` is a **delete**. It is an operation rather than a stored
    tombstone so a removed path cannot reappear when an older concurrent write
    folds into the channel afterwards.
    """

    path: str
    content: str | None
    summary: NotRequired[str]


def _normalize_summary(summary: object) -> str:
    if not isinstance(summary, str):
        return ""
    collapsed = " ".join(summary.split())
    return collapsed[:MAX_WORKING_SUMMARY_CHARS]


def _content_bytes(content: str) -> int:
    """Measured UTF-8 size in bytes — never ``len(content)``, which undercounts
    every non-ASCII path the model writes and so under-enforces the ceiling."""
    return len(content.encode("utf-8"))


def working_files_bytes(files: list[WorkingFile] | None) -> int:
    """Summed byte size of every stored file. Unreadable is not possible here:
    the content is in the state, so this is a measurement, not an estimate."""
    return sum(_content_bytes(file.get("content", "")) for file in files or [])


def working_plane_limits() -> dict[str, int]:
    """The module-level hard ceiling, so a refusal can name the number.

    This is the *channel* bound, not the operator's narrowed one — the reducer
    enforces this regardless of configuration, so it is the number a refusal
    must name. The tool reports its live configured limits separately in every
    reply.
    """
    return {
        "max_files": MAX_WORKING_FILES,
        "max_file_bytes": MAX_WORKING_FILE_BYTES,
        "max_total_bytes": MAX_WORKING_TOTAL_BYTES,
        "max_summary_chars": MAX_WORKING_SUMMARY_CHARS,
    }


def _normalize_op(op: WorkingFileOp) -> tuple[str, str | None]:
    """Validate one op, returning ``(path, content)`` where ``None`` deletes."""
    raw_path = op.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise WorkspacePathError("a working-file op must carry a non-empty 'path'")
    path = join_workspace_path(normalize_workspace_parts(raw_path))
    content = op.get("content")
    if content is None:
        return path, None
    if not isinstance(content, str):
        raise WorkspaceError(f"working-file content must be a string for {path!r}")
    return path, content


def merge_working_files(
    existing: list[WorkingFile] | None,
    new: list[WorkingFileOp] | None,
) -> list[WorkingFile]:
    """Reducer for the ``working_files`` channel.

    Semantics, in order:

    * ``new is None`` — the node did not touch the plane, so ``existing`` is
      preserved verbatim (same rule as ``merge_todos``/``merge_goal``).
    * a ``None`` content drops that path outright (a delete).
    * a path written twice inside one update keeps the **last** value, so a
      superseded draft cannot win by appearing first.
    * a written path moves to the **tail**: order is recency, which is what
      eviction reads and what keeps a checkpoint diff minimal.
    * over-capacity drops from the **front** (oldest write first) and the
      eviction is visible in the resulting list — the model can see a file it
      wrote is gone, which is the only honest outcome.
    """
    if new is None:
        return list(existing or [])

    merged: dict[str, WorkingFile] = {file["path"]: file for file in existing or []}
    for op in new:
        path, content = _normalize_op(op)
        if content is None:
            merged.pop(path, None)
            continue
        entry: WorkingFile = {"path": path, "content": content}
        summary = _normalize_summary(op.get("summary"))
        if summary:
            entry["summary"] = summary
        elif path in merged and merged[path].get("summary"):
            # A content rewrite keeps the previously declared purpose: dropping
            # it would silently demote the file to a bare path in the index.
            entry["summary"] = merged[path]["summary"]
        merged.pop(path, None)
        merged[path] = entry

    return _enforce_bounds(list(merged.values()))


def _enforce_bounds(files: list[WorkingFile]) -> list[WorkingFile]:
    """Drop oldest-written files until both count and byte ceilings hold."""
    kept = files
    if len(kept) > MAX_WORKING_FILES:
        kept = kept[-MAX_WORKING_FILES:]
    total = working_files_bytes(kept)
    while kept and total > MAX_WORKING_TOTAL_BYTES:
        dropped = kept.pop(0)
        total -= _content_bytes(dropped.get("content", ""))
    return kept


class StateWorkspace:
    """A bounded virtual workspace over the ``working_files`` graph channel.

    Satisfies the :class:`~alpha.deepagent.workspace.VirtualWorkspace` shape so
    a :class:`~alpha.deepagent.composite.CompositeWorkspace` route could point
    at a working plane, with one deliberate difference in the return contract:
    every mutator returns ``True`` when it changed the plane rather than
    ``None``, because the caller must know whether to emit a channel write at
    all. A ``None`` return would force every caller to diff the file list to
    find out.

    The constructor bounds default to the module constants, which are the
    **hard** ceiling the channel reducer also enforces — the state channel must
    stay bounded whatever the operator configured. An operator may narrow them
    per deployment; a wider limit than the module constant is refused, because
    a config that could widen the channel would let a long run grow the state
    blob without bound.
    """

    __slots__ = ("_files", "_max_file_bytes", "_max_files", "_max_total_bytes")

    def __init__(
        self,
        files: list[WorkingFile] | None = None,
        *,
        max_files: int = MAX_WORKING_FILES,
        max_file_bytes: int = MAX_WORKING_FILE_BYTES,
        max_total_bytes: int = MAX_WORKING_TOTAL_BYTES,
    ) -> None:
        if max_files > MAX_WORKING_FILES:
            raise WorkspaceError(f"max_files cannot exceed the channel ceiling of {MAX_WORKING_FILES}")
        if max_file_bytes > MAX_WORKING_FILE_BYTES:
            raise WorkspaceError(f"max_file_bytes cannot exceed the channel ceiling of {MAX_WORKING_FILE_BYTES}")
        if max_total_bytes > MAX_WORKING_TOTAL_BYTES:
            raise WorkspaceError(f"max_total_bytes cannot exceed the channel ceiling of {MAX_WORKING_TOTAL_BYTES}")
        self._max_files = max(1, max_files)
        self._max_file_bytes = max(256, max_file_bytes)
        self._max_total_bytes = max(1024, max_total_bytes)
        # dict preserves insertion order, so order == write order == recency.
        self._files: dict[str, WorkingFile] = {file["path"]: file for file in files or []}

    # -- read side ---------------------------------------------------------

    @property
    def files(self) -> list[WorkingFile]:
        """The plane as a state value, in write order."""
        return list(self._files.values())

    @property
    def total_bytes(self) -> int:
        return working_files_bytes(self.files)

    def ls(self, path: str = "/") -> list[tuple[str, str, int]]:
        """List ``(path, kind, size_bytes)`` rows under ``path``.

        ``size_bytes`` is measured for files and ``0`` for the directory row,
        which is what a directory's own entry means — there is no inode here to
        stat.
        """
        prefix_parts = normalize_workspace_parts(path, allow_root=True)
        if not prefix_parts:
            rows: list[tuple[str, str, int]] = []
            seen_dirs: set[str] = set()
            for file in self._files.values():
                parts = file["path"].lstrip("/").split("/")
                for depth in range(1, len(parts)):
                    ancestor = "/" + "/".join(parts[:depth])
                    if ancestor not in seen_dirs:
                        seen_dirs.add(ancestor)
                        rows.append((ancestor, "dir", 0))
                rows.append((file["path"], "file", _content_bytes(file["content"])))
            return rows
        prefix = join_workspace_path(prefix_parts) + "/"
        rows = []
        for file in self._files.values():
            if file["path"].startswith(prefix):
                rows.append((file["path"], "file", _content_bytes(file["content"])))
        if not rows and join_workspace_path(prefix_parts) not in self._files:
            raise WorkspaceNotFoundError(f"no such path in the working plane: {path!r}")
        return rows

    def read(self, path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> str:
        if offset < 0:
            raise WorkspaceError(f"read offset must be >= 0, got {offset}")
        if limit < 1:
            raise WorkspaceError(f"read limit must be >= 1, got {limit}")
        key = join_workspace_path(normalize_workspace_parts(path))
        file = self._files.get(key)
        if file is None:
            raise WorkspaceNotFoundError(f"no such file in the working plane: {path!r}")
        lines = file["content"].splitlines(keepends=True)
        return "".join(lines[offset : offset + limit])

    def glob(self, pattern: str) -> list[str]:
        """Match paths against a ``/``-separated glob.

        Implemented over :func:`fnmatch.fnmatchcase` on each stored path
        rather than a filesystem walk: the plane is a flat map, so a pattern
        like ``/notes/*.md`` matches exactly the stored keys and there is no
        directory tree to traverse.
        """
        import fnmatch

        raw = str(pattern).replace("\\", "/")
        if not raw.strip():
            raise WorkspaceError(f"empty glob pattern: {pattern!r}")
        for part in raw.split("/"):
            if part == "..":
                raise WorkspacePathError(f"'..' is not allowed in glob patterns: {pattern!r}")
        matcher = raw if raw.startswith("/") else "*/" + raw
        return sorted(path for path in self._files if fnmatch.fnmatchcase(path, matcher))

    def grep(self, pattern: str, path: str = "/", max_results: int = 200) -> list[GrepMatch]:
        import re

        if max_results < 1:
            raise WorkspaceError(f"max_results must be >= 1, got {max_results}")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise WorkspaceError(f"invalid regular expression {pattern!r}: {exc}") from exc
        base_parts = normalize_workspace_parts(path, allow_root=True)
        results: list[GrepMatch] = []
        for file in self._files.values():
            if len(results) >= max_results:
                break
            if base_parts and not file["path"].startswith(join_workspace_path(base_parts) + "/"):
                continue
            for line_no, line in enumerate(file["content"].splitlines(), start=1):
                if not regex.search(line):
                    continue
                if len(line) > GREP_LINE_PREVIEW_CHARS:
                    line = line[:GREP_LINE_PREVIEW_CHARS] + " [truncated]"
                results.append(GrepMatch(path=file["path"], line=line_no, text=line))
                if len(results) >= max_results:
                    break
        return results

    def index(self) -> list[dict[str, object]]:
        """The progressive-disclosure projection: address and purpose, no body.

        This is what the model sees without opening anything, and the reason a
        working plane beats a longer message tail — the model can tell *which*
        of its own notes to open.
        """
        return [
            {
                "path": file["path"],
                "bytes": _content_bytes(file["content"]),
                "summary": file.get("summary", ""),
            }
            for file in self._files.values()
        ]

    async def execute(self, command: str, cwd: str | None = None) -> str:
        raise WorkspaceUnsupported("StateWorkspace does not run commands (not implemented by design); shell execution lives in alpha.sandbox (the 'bash' tool / Sandbox.execute_command)")

    # -- write side --------------------------------------------------------

    def write(self, path: str, content: str, summary: str = "") -> bool:
        """Create or replace a file. Returns ``True`` when the plane changed."""
        key = join_workspace_path(normalize_workspace_parts(path))
        size = _content_bytes(content)
        if size > self._max_file_bytes:
            raise WorkspaceError(f"{key!r} is {size} bytes, over the {self._max_file_bytes}-byte working-file ceiling; split it or write it to disk with 'write_file'")
        projected = self.total_bytes - _content_bytes(self._files[key]["content"] if key in self._files else "") + size
        if projected > self._max_total_bytes:
            raise WorkspaceError(f"writing {key!r} would take the working plane to {projected} bytes, over the {self._max_total_bytes}-byte ceiling; delete or shorten an existing file")
        if key not in self._files and len(self._files) >= self._max_files:
            raise WorkspaceError(f"the working plane already holds {self._max_files} files; delete one with the 'delete' action before writing another")
        entry: WorkingFile = {"path": key, "content": content}
        normalized_summary = _normalize_summary(summary)
        if normalized_summary:
            entry["summary"] = normalized_summary
        elif key in self._files and self._files[key].get("summary"):
            entry["summary"] = self._files[key]["summary"]
        self._files.pop(key, None)  # re-insert at the tail: order is recency
        self._files[key] = entry
        return True

    def edit(self, path: str, old: str, new: str, replace_all: bool = False) -> bool:
        """Surgically replace ``old`` with ``new`` inside an existing file.

        The same fail-closed contract as ``LocalWorkspace.edit``: an empty
        ``old``, a missing occurrence and an ambiguous multi-occurrence replace
        are all refusals, because a half-applied edit is worse than a refused
        one — the model would keep reasoning over text nobody wrote.
        """
        if old == "":
            # Same error class as LocalWorkspace.edit, so a caller catching the
            # workspace vocabulary behaves identically on either backend.
            raise WorkspaceConflictError("edit requires a non-empty 'old' string")
        key = join_workspace_path(normalize_workspace_parts(path))
        file = self._files.get(key)
        if file is None:
            raise WorkspaceNotFoundError(f"no such file in the working plane: {path!r}")
        text = file["content"]
        count = text.count(old)
        if count == 0:
            raise WorkspaceConflictError(f"'old' string not found in {key!r}")
        if count > 1 and not replace_all:
            raise WorkspaceConflictError(f"'old' string occurs {count} times in {key!r}; pass replace_all=True to replace every occurrence")
        replacement = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        size = _content_bytes(replacement)
        if size > self._max_file_bytes:
            raise WorkspaceError(f"editing {key!r} would make it {size} bytes, over the {self._max_file_bytes}-byte working-file ceiling")
        file["content"] = replacement
        self._files.pop(key, None)
        self._files[key] = file
        return True

    def delete(self, path: str) -> bool:
        """Remove a file. Returns ``True`` when there was one to remove."""
        key = join_workspace_path(normalize_workspace_parts(path))
        return self._files.pop(key, None) is not None

    # -- protocol-shaped async surface -------------------------------------

    async def als_write(self, path: str, content: str, summary: str = "") -> bool:
        """Async alias. There is no IO here — the plane is in memory — so this
        is a direct call rather than an ``asyncio.to_thread`` hop, and the
        Blockbuster gate sees no blocking syscall on the event loop."""
        return self.write(path, content, summary)

    async def als_read(self, path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> str:
        return self.read(path, offset, limit)
