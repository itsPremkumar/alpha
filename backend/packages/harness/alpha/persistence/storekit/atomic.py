"""Crash-safe replacement of one file by another.

The primitive in this module is intentionally small and boring: it creates a
temporary file in the *same directory* as the target, writes the complete
bytes, flushes and ``fsync``s the file, replaces the target with
:func:`os.replace`, and (on POSIX) ``fsync``s the containing directory.  The
sequence follows the durability rules described by POSIX ``rename(2)`` and
``fsync(2)``: a rename is atomic with respect to other observers, and syncing
the directory makes the new directory entry durable after a crash.  See also
Poul-Henning Kamp's discussion of ``rename(2)`` in the Linux man-pages and the
IEEE Std 1003.1 ``fsync(2)`` specification for the ordering constraint that
motivates syncing the file *before* the rename.  The Windows discussion below
uses the same ordering vocabulary as Microsoft's ``MoveFileEx``/``ReplaceFile``
and ``FlushFileBuffers`` documentation rather than claiming POSIX semantics.

On Windows, :func:`os.replace` uses the operating system's atomic replacement
primitive for a normal local filesystem, so a reader never sees a partially
written file under the real name.  Windows does not provide the POSIX
"open a directory and fsync it" operation through the Python standard
library, however, and the durability of a rename across sudden power loss is
therefore weaker than the POSIX guarantee.  ``fsync_directory`` reports
``False`` on Windows instead of pretending that the directory entry was
synced.  Network filesystems, virtual disks, and container overlay mounts can
add further limits; callers can inspect :class:`AtomicWriteResult` and the
returned directory-sync status rather than inferring a guarantee.

The ``fault`` hook is a test seam, not a production retry mechanism.  It is
called at every named step in :data:`ATOMIC_WRITE_STEPS` and may raise
:class:`InjectedCrash` to model a process dying at that point.  Steps before
``os.replace`` leave the previous target intact; the step after replacement
leaves the complete new content intact.  No partially written temporary file
is ever visible under the target name.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "ATOMIC_WRITE_STEPS",
    "AtomicWriteResult",
    "FsyncPolicy",
    "InjectedCrash",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "directory_fsync_supported",
    "fsync_directory",
    "normalize_fsync_policy",
]


class FsyncPolicy(StrEnum):
    """Durability policy for :func:`atomic_write_bytes`."""

    NONE = "none"
    FILE = "file"
    FILE_AND_DIRECTORY = "file_and_directory"


#: Stable names for the fault-injection boundaries.  Keep this list ordered:
#: it is also the order in which a normal write passes through the steps.
ATOMIC_WRITE_STEPS: tuple[str, ...] = (
    "before_temp_create",
    "after_temp_create",
    "after_temp_write",
    "after_temp_flush",
    "after_temp_fsync",
    "before_replace",
    "after_replace",
    "after_directory_fsync",
)


class InjectedCrash(RuntimeError):
    """Raised by a test fault hook to simulate a process crash."""


FaultHook = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class AtomicWriteResult:
    """Observable outcome of one atomic replacement.

    ``directory_synced`` is intentionally explicit.  It is ``False`` on
    Windows and on filesystems that reject a directory handle, so a caller can
    report the weaker rename-durability guarantee honestly.
    """

    path: Path
    temp_path: Path | None
    bytes_written: int
    replaced: bool
    file_synced: bool
    directory_synced: bool
    steps: tuple[str, ...]

    @property
    def durable_rename(self) -> bool:
        """Whether both the file contents and the rename were synced."""

        return self.replaced and self.file_synced and self.directory_synced


def normalize_fsync_policy(policy: FsyncPolicy | str | bool | None) -> FsyncPolicy:
    """Normalize a caller-friendly fsync setting.

    ``None`` and ``True`` select the strongest policy, ``False`` disables
    syncing, and the string values are the stable serialized names.
    """

    if policy is None or policy is True:
        return FsyncPolicy.FILE_AND_DIRECTORY
    if policy is False:
        return FsyncPolicy.NONE
    if isinstance(policy, FsyncPolicy):
        return policy
    value = str(policy).strip().lower().replace("-", "_")
    try:
        return FsyncPolicy(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in FsyncPolicy)
        raise ValueError(f"fsync policy must be one of {allowed}; got {policy!r}") from exc


def directory_fsync_supported() -> bool:
    """Return whether this platform can fsync a directory handle.

    The answer is intentionally conservative.  Windows returns ``False``
    because the Python standard library has no portable directory fsync
    primitive.  A POSIX build can still fail on a particular filesystem at
    the point of use; :func:`fsync_directory` reports that separately.
    """

    return os.name == "posix"


def fsync_directory(directory: Path | str) -> bool:
    """Best-effort ``fsync`` of a directory entry.

    Returns ``True`` only when the platform actually accepted and completed a
    directory fsync.  It returns ``False`` on Windows and on filesystems that
    do not support syncing directory handles.  A write can still be
    visibility-safe without this step; only crash durability of the rename is
    weaker.
    """

    if os.name != "posix":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(directory), flags)
    except OSError:
        return False
    try:
        os.fsync(descriptor)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def _notify(fault: FaultHook | None, step: str) -> None:
    if fault is not None:
        fault(step)


def atomic_write_bytes(
    path: Path | str,
    data: bytes,
    *,
    fsync: FsyncPolicy | str | bool | None = FsyncPolicy.FILE_AND_DIRECTORY,
    fsync_policy: FsyncPolicy | str | bool | None = None,
    fault: FaultHook | None = None,
    fault_hook: FaultHook | None = None,
    temp_prefix: str = ".",
    temp_suffix: str = ".tmp",
) -> AtomicWriteResult:
    """Atomically replace ``path`` with ``data``.

    The temporary file is created with :func:`tempfile.mkstemp` in the target
    directory, which avoids a cross-device rename and gives each concurrent
    writer a distinct name.  A failed or injected crash before
    ``os.replace`` removes the temporary file in the normal Python path; a
    real power loss can leave that unreferenced file behind, which is harmless
    because it is not the target name.

    Args:
        path: Target file.  Its parent is created if necessary.
        data: Complete bytes to publish.
        fsync: ``none``, ``file``, or ``file_and_directory`` (the default).
        fsync_policy: Explicit alias for ``fsync``.
        fault: Optional callable receiving each step name.  It is intended
            for crash-safety tests and diagnostics.
        fault_hook: Explicit alias for ``fault``.
        temp_prefix: Prefix for the same-directory temporary file.
        temp_suffix: Suffix for the same-directory temporary file.

    Returns:
        A result describing the replacement and which durability steps ran.

    Raises:
        InjectedCrash: When the supplied fault hook raises at a step.
        OSError: For ordinary filesystem failures.  The previous target is
            left untouched when the failure occurs before ``os.replace``.
    """

    target = Path(path)
    policy = normalize_fsync_policy(fsync_policy if fsync_policy is not None else fsync)
    selected_fault = fault_hook if fault_hook is not None else fault
    target.parent.mkdir(parents=True, exist_ok=True)
    _notify(selected_fault, "before_temp_create")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{temp_prefix}{target.name}.",
        suffix=temp_suffix,
        dir=str(target.parent),
    )
    temporary = Path(temporary_name)
    handle = os.fdopen(descriptor, "wb")
    replaced = False
    file_synced = False
    directory_synced = False
    try:
        _notify(selected_fault, "after_temp_create")
        with handle:
            handle.write(data)
            _notify(selected_fault, "after_temp_write")
            handle.flush()
            _notify(selected_fault, "after_temp_flush")
            if policy in {FsyncPolicy.FILE, FsyncPolicy.FILE_AND_DIRECTORY}:
                os.fsync(handle.fileno())
                file_synced = True
            _notify(selected_fault, "after_temp_fsync")
        _notify(selected_fault, "before_replace")
        os.replace(temporary, target)
        replaced = True
        _notify(selected_fault, "after_replace")
        if policy is FsyncPolicy.FILE_AND_DIRECTORY:
            directory_synced = fsync_directory(target.parent)
        _notify(selected_fault, "after_directory_fsync")
    except BaseException:
        # Before replacement, the temporary file is ours to clean up.  After
        # replacement it no longer exists under this name, so there is
        # nothing to remove and, importantly, the target must not be touched.
        if not handle.closed:
            handle.close()
        if not replaced:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return AtomicWriteResult(
        path=target,
        temp_path=temporary,
        bytes_written=len(data),
        replaced=replaced,
        file_synced=file_synced,
        directory_synced=directory_synced,
        steps=ATOMIC_WRITE_STEPS,
    )


def atomic_write_text(
    path: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
    **kwargs: Any,
) -> AtomicWriteResult:
    """Encode ``text`` and delegate to :func:`atomic_write_bytes`."""

    return atomic_write_bytes(path, text.encode(encoding), **kwargs)


def atomic_write_json(
    path: Path | str,
    payload: Mapping[str, Any] | list[Any],
    *,
    indent: int | None = 1,
    sort_keys: bool = False,
    **kwargs: Any,
) -> AtomicWriteResult:
    """Serialize a JSON payload and atomically publish it.

    ``allow_nan=False`` is intentional: JSON ``NaN`` is not portable and a
    store document must remain readable by another process or another release.
    """

    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=indent,
        sort_keys=sort_keys,
        allow_nan=False,
        separators=None if indent is not None else (",", ":"),
    )
    return atomic_write_text(path, text, **kwargs)
