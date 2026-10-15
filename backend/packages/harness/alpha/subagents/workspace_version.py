"""Workspace version protocol — optimistic concurrency control for subagents.

Multiple subagents can be delegated against the *same* workspace directory
(parent fan-out, sibling specialists, retries). Nothing in the tool layer
serializes them: each child runs its own graph on its own thread and its
``write_file`` / ``str_replace`` calls land on disk whenever they land. The
result is the classic lost-update — subagent B reads ``shared.py`` at version
1, subagent A rewrites it, B commits on top of A's content and silently
discards it.

This module is the counter that makes the race *observable*. Every workspace
keeps a monotonic integer in a JSON sidecar next to the files it guards
(``.workspace_version.json``); a subagent snapshots the version when it starts
touching files and re-checks it when it commits. A mismatch means somebody else
wrote in between, which is reported as :class:`ConflictError` rather than
silently accepted.

Design invariants:

- **The counter is the only shared state.** Read/write *sets* are per-subagent
  runtime bookkeeping and stay in memory; only the integer is persisted, so the
  sidecar stays small, diffable, and safe to rewrite on every commit.
- **Commits are the only writers of the counter.** A successful commit bumps it
  by exactly one, so "version advanced" always means "some committed write
  happened", never "somebody merely looked".
- **No write paths means no commit.** A read-only subagent advances nothing:
  bumping the version for a pure reader would manufacture conflicts for every
  concurrent writer.
- **Fail closed on mismatch, fail open on I/O.** A version mismatch raises; an
  unreadable/corrupt sidecar degrades to "version 0" with a warning, because a
  broken counter file must not take a subagent's real work down with it.
- **Serialized per workspace root.** Increments are guarded by a lock keyed on
  the resolved root, so two store instances in one process (the normal shape —
  one per delegated execution) cannot interleave a read-modify-write. Writes go
  through a temp file + ``os.replace`` so a reader never observes a torn file.
  Cross-*process* writers are not serialized by this lock; see
  :meth:`WorkspaceVersionStore.increment`.

The protocol is advisory by design: it detects and reports the race, it does not
merge or roll back. The files are already on disk by the time a conflict is
raised — unwriting them would be a different (and much more dangerous) feature.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Name of the per-workspace JSON sidecar holding the version counter.
VERSION_FILE_NAME = ".workspace_version.json"

#: Tool names whose calls write files into the workspace.
WORKSPACE_WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "str_replace", "hashline_edit"})
#: Tool names whose calls read files from the workspace.
WORKSPACE_READ_TOOLS: frozenset[str] = frozenset({"read_file"})
#: Argument names that carry a workspace-relative path in a file tool call.
#: Mirrors ``alpha.agents.middlewares.edit_detection._PATH_ARGUMENTS``; kept
#: local on purpose — that set exists to classify *code edits*, this one to
#: classify *workspace access*, and coupling them would make a change to one
#: silently redefine the other.
WORKSPACE_PATH_ARGUMENTS: frozenset[str] = frozenset({"file", "file_path", "filename", "path", "target_path", "target_file"})


class ConflictError(RuntimeError):
    """A commit was rejected because the workspace moved under the writer.

    Attributes:
        read_version: The workspace version the writer based its work on.
        current_version: The version on disk when the commit was attempted.
        conflicting_paths: The paths the writer tried to commit — i.e. exactly
            the writes that were NOT applied under the version protocol. The
            bytes may still be on disk (nothing rolls them back), so callers
            must treat this as "these writes raced somebody", not "nothing was
            written".
    """

    def __init__(self, read_version: int, current_version: int, conflicting_paths: Sequence[str]) -> None:
        self.read_version = int(read_version)
        self.current_version = int(current_version)
        self.conflicting_paths = list(conflicting_paths)
        shown = ", ".join(self.conflicting_paths[:_CONFLICT_PATH_PREVIEW]) or "(none)"
        if len(self.conflicting_paths) > _CONFLICT_PATH_PREVIEW:
            shown += f", … (+{len(self.conflicting_paths) - _CONFLICT_PATH_PREVIEW} more)"
        super().__init__(f"Workspace version conflict: read at v{self.read_version} but the workspace is at v{self.current_version}; {len(self.conflicting_paths)} written path(s) were not committed against the current version: {shown}")


#: How many conflicting paths the exception message names before eliding.
_CONFLICT_PATH_PREVIEW = 5


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """An immutable view of one subagent's position in the version protocol.

    Attributes:
        subagent_id: The tracked subagent this snapshot belongs to.
        workspace_root: The workspace the snapshot was taken against.
        version: The workspace version this subagent is working from — the
            value to pass as ``read_version`` at commit time.
        read_paths: Paths read so far (informational; not used for conflict
            detection, which is version-based, not path-based).
        write_paths: Paths written so far.
        acquired_at: When the snapshot was taken.
    """

    subagent_id: str
    workspace_root: str
    version: int
    read_paths: frozenset[str]
    write_paths: frozenset[str]
    acquired_at: datetime


# One lock per workspace root, so two store instances pointed at the same
# directory still serialize their read-modify-write cycles. The registry itself
# is guarded, and entries are never removed: a workspace root is a small,
# bounded set in practice, and dropping a lock while another thread is inside
# its critical section would reintroduce the race the registry exists to close.
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(workspace_root: Path) -> threading.RLock:
    key = str(workspace_root)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def normalize_workspace_path(path: str | os.PathLike[str]) -> str:
    """Canonicalize a path for use as a tracking key.

    Separator differences (``a/b.py`` vs ``a\\b.py``) are the same file on the
    same workspace, so they must land in one set entry or a subagent that read
    a file through one spelling would look like it never touched it. Only the
    spelling is normalized — the path is NOT resolved against the filesystem,
    because these keys are workspace-relative and resolution would drag in the
    host's real cwd.
    """
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/") or text


def _dedupe_preserving_order(paths: Iterable[Any]) -> list[str]:
    """Stringify paths, drop empties, and keep first-seen order without dupes."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in paths:
        if raw is None:
            continue
        text = str(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkspaceVersionStore:
    """Version counter + per-subagent access tracking for one workspace.

    One instance per workspace root is the intended shape, but instances are
    cheap and interchangeable: the persisted counter is the source of truth, so
    a second instance over the same root observes the same version (see
    :meth:`get_version`) and contends on the same lock.

    Args:
        workspace_root: Directory the counter guards. Not required to exist at
            construction time — the sidecar is created on the first write, so a
            store can be built before the workspace is provisioned.
    """

    def __init__(self, workspace_root: str | os.PathLike[str]) -> None:
        self.workspace_root = Path(workspace_root)
        self.version_file = self.workspace_root / VERSION_FILE_NAME
        # RLock: commit_writes calls the read/write helpers below while already
        # holding it, and a plain Lock would self-deadlock.
        self._lock = _lock_for(self.workspace_root)
        # Runtime-only tracking. Per-subagent so sibling delegations never see
        # each other's access, and deliberately not persisted: it describes
        # live executions, and a stale entry from a crashed run would otherwise
        # outlive the run it belonged to.
        self._reads: dict[str, set[str]] = {}
        self._writes: dict[str, set[str]] = {}
        self._base_versions: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Version counter
    # ------------------------------------------------------------------

    def get_version(self) -> int:
        """Return the current workspace version (``0`` when never committed)."""
        with self._lock:
            return self._read_version_locked()

    #: Alias kept for call sites that read better as "the workspace version"
    #: than as "the store's version".
    get_workspace_version = get_version

    def increment(self) -> int:
        """Advance the counter by one and return the new version.

        Serialized against every other instance sharing this workspace root.
        The bump and the persist happen under one lock, so concurrent
        increments cannot lose each other's update.
        """
        with self._lock:
            new_version = self._read_version_locked() + 1
            self._write_version_locked(new_version)
            return new_version

    def commit_writes(self, read_version: int, written_paths: Sequence[str] | None = None) -> bool:
        """Validate ``read_version`` against the counter and commit the writes.

        Returns ``True`` when the commit is accepted (and the counter was
        advanced). Raises :class:`ConflictError` when the workspace moved.

        An empty ``written_paths`` is always accepted without advancing the
        counter: a subagent that wrote nothing has nothing to lose to a
        concurrent writer, and bumping the version on its behalf would
        manufacture a conflict for everybody else.
        """
        paths = _dedupe_preserving_order(written_paths or [])
        with self._lock:
            if not paths:
                return True
            current = self._read_version_locked()
            if current != int(read_version):
                raise ConflictError(int(read_version), current, paths)
            self._write_version_locked(current + 1)
            return True

    def _read_version_locked(self) -> int:
        """Read the counter, degrading to ``0`` on any unusable sidecar.

        Called with ``self._lock`` held. A missing file is the normal
        "never committed" state. A malformed one is reported and treated as
        ``0``: the alternative — propagating the parse error — would turn a
        damaged bookkeeping file into a failed subagent run.
        """
        if not self.version_file.exists():
            return 0

        try:
            raw = self.version_file.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Could not read workspace version file %s; treating version as 0", self.version_file, exc_info=True)
            return 0

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Workspace version file %s is not valid JSON; treating version as 0", self.version_file)
            return 0

        version = data.get("version") if isinstance(data, Mapping) else None
        # ``bool`` is an ``int`` subclass; a stray ``true`` must not read as 1.
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            logger.warning("Workspace version file %s has an unusable version %r; treating version as 0", self.version_file, version)
            return 0
        return version

    def _write_version_locked(self, version: int) -> None:
        """Persist ``version`` atomically. Called with ``self._lock`` held.

        Temp-file-then-``os.replace`` so a concurrent reader (another store
        instance, another process) sees either the old file or the new one —
        never a half-written JSON document.
        """
        directory = self.version_file.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": version}, separators=(",", ":"), sort_keys=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=directory,
                prefix=f"{VERSION_FILE_NAME}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.version_file)
        except OSError:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    logger.debug("Could not remove temp workspace version file %s", tmp_path, exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Per-subagent access tracking
    # ------------------------------------------------------------------

    def acquire_read_snapshot(self, subagent_id: str) -> WorkspaceSnapshot:
        """Start (or re-read) tracking for ``subagent_id`` and snapshot it.

        This is the entry point a delegated execution calls before its first
        tool call: it fixes the subagent's base version at "now", so anything
        another writer commits afterwards shows up as a conflict at commit
        time rather than as a silent overwrite. Idempotent — calling it again
        for an already-tracked subagent returns the same base version with the
        access sets accumulated so far.
        """
        with self._lock:
            self._ensure_tracked_locked(subagent_id)
            return WorkspaceSnapshot(
                subagent_id=subagent_id,
                workspace_root=str(self.workspace_root),
                version=self._base_versions[subagent_id],
                read_paths=frozenset(self._reads[subagent_id]),
                write_paths=frozenset(self._writes[subagent_id]),
                acquired_at=_utcnow(),
            )

    def record_read(self, subagent_id: str, path: str) -> None:
        """Record that ``subagent_id`` read ``path`` (starting tracking if new)."""
        key = normalize_workspace_path(path)
        if not key:
            return
        with self._lock:
            self._ensure_tracked_locked(subagent_id)
            self._reads[subagent_id].add(key)

    def record_write(self, subagent_id: str, path: str) -> None:
        """Record that ``subagent_id`` wrote ``path`` (starting tracking if new)."""
        key = normalize_workspace_path(path)
        if not key:
            return
        with self._lock:
            self._ensure_tracked_locked(subagent_id)
            self._writes[subagent_id].add(key)

    def get_read_set(self, subagent_id: str) -> set[str]:
        """Return a copy of the paths ``subagent_id`` has read."""
        with self._lock:
            return set(self._reads.get(subagent_id, ()))

    def get_write_set(self, subagent_id: str) -> set[str]:
        """Return a copy of the paths ``subagent_id`` has written."""
        with self._lock:
            return set(self._writes.get(subagent_id, ()))

    def get_base_version(self, subagent_id: str) -> int:
        """Return the version ``subagent_id`` started working from.

        For a subagent that has not started tracking yet this is the current
        version — "tracking starts now" — returned read-only so a query can
        never begin tracking as a side effect.
        """
        with self._lock:
            tracked = self._base_versions.get(subagent_id)
            if tracked is not None:
                return tracked
            return self._read_version_locked()

    def clear_subagent(self, subagent_id: str) -> None:
        """Drop all tracking for ``subagent_id``, leaving every other subagent intact.

        Called when a delegated execution reaches a terminal state so a
        long-lived process does not accumulate one entry per historical run.
        """
        with self._lock:
            self._reads.pop(subagent_id, None)
            self._writes.pop(subagent_id, None)
            self._base_versions.pop(subagent_id, None)

    def _ensure_tracked_locked(self, subagent_id: str) -> None:
        """Begin tracking ``subagent_id`` at the current version, if not started.

        Called with ``self._lock`` held. First access wins: the base version is
        fixed once and later reads/writes cannot move it, which is what makes
        "read at v1, wrote at v3" detectable.
        """
        if subagent_id in self._base_versions:
            return
        self._base_versions[subagent_id] = self._read_version_locked()
        self._reads.setdefault(subagent_id, set())
        self._writes.setdefault(subagent_id, set())


def validate_and_commit_writes(
    store: WorkspaceVersionStore,
    read_version: int,
    written_paths: Sequence[str] | None = None,
) -> bool:
    """Validate ``read_version`` against ``store`` and commit ``written_paths``.

    The functional entry point for callers that hold a store rather than a
    subclass: it delegates to :meth:`WorkspaceVersionStore.commit_writes`, so
    the counter is read, checked, and bumped under that store's lock — a
    check-then-act split here would let two writers both pass between the read
    and the bump.

    Returns:
        ``True`` when the commit was accepted.

    Raises:
        ConflictError: When the workspace version no longer matches
            ``read_version`` and there were paths to commit.
    """
    return store.commit_writes(read_version, written_paths)


def _iter_path_arguments(value: Any) -> Iterable[str]:
    """Yield every path-looking string inside a tool call's argument tree.

    Tool arguments are nested and provider-dependent (a path can sit at the
    top level, inside a ``files`` list, or inside a per-file dict), so this
    walks the structure instead of naming one argument key.
    """
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = str(key).lower()
            if normalized_key in WORKSPACE_PATH_ARGUMENTS and isinstance(child, str):
                yield child
            else:
                yield from _iter_path_arguments(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_path_arguments(child)


def harvest_workspace_access(messages: Iterable[Any]) -> tuple[set[str], set[str]]:
    """Derive ``(read_paths, write_paths)`` from a subagent's message history.

    Reads the tool *calls* out of the assistant turns — the same source the
    read-before-write gate and the edit classifier use — so a subagent that
    only narrated a file without calling a tool on it is not counted as having
    accessed it. Accepts both message objects and ``model_dump()`` dicts.

    Only tools that operate directly on a named workspace file are classified.
    ``bash`` writes are deliberately ignored: the command text is not a
    reliable statement of which files it touched, and guessing would put
    phantom entries in the write set.
    """
    reads: set[str] = set()
    writes: set[str] = set()
    for message in messages or ():
        if isinstance(message, Mapping):
            tool_calls = message.get("tool_calls") or []
        else:
            tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            if isinstance(call, Mapping):
                name = str(call.get("name") or "")
                args = call.get("args")
            else:
                name = str(getattr(call, "name", "") or "")
                args = getattr(call, "args", None)
            if name in WORKSPACE_WRITE_TOOLS:
                target = writes
            elif name in WORKSPACE_READ_TOOLS:
                target = reads
            else:
                continue
            for raw_path in _iter_path_arguments(args):
                key = normalize_workspace_path(raw_path)
                if key:
                    target.add(key)
    return reads, writes
