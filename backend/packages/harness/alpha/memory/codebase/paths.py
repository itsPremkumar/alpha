"""Runtime paths and atomic text writes for codebase structure memory.

The layout follows the L1 memory convention of a caller-selected root with
safe per-scope directory names::

    {root}/repos/{safe_repo_id}/snapshots.json

When no root is supplied, the Alpha runtime home is used and a ``codebase``
subdirectory keeps this derived state separate from other memory families.
Path sanitization is lexical and never follows or imports the analyzed project.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

DEFAULT_AGENT_BUCKET = "__default__"
_UNSAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_write_counter = threading.Lock()
_write_sequence = 0


def safe_segment(value: str | None) -> str:
    """Return a safe, bounded directory segment using L1 conventions.

    Empty values use the reserved ``__default__`` bucket.  Characters outside
    ``[A-Za-z0-9._-]`` fold to ``_`` and leading/trailing dots are removed so a
    caller cannot escape the state root.
    """

    if not value:
        return DEFAULT_AGENT_BUCKET
    cleaned = _UNSAFE_SEGMENT_RE.sub("_", str(value)).strip("._") or "x"
    return cleaned[:100]


def codebase_root(storage_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the codebase memory root.

    An explicit path is treated as the root and resolved without requiring it
    to exist yet.  With no explicit path, ``runtime_home()`` is resolved lazily
    so importing this module does not touch global configuration or the real
    repository.
    """

    if storage_path:
        return Path(storage_path).expanduser().resolve()
    from alpha.config.runtime_paths import runtime_home

    return (Path(runtime_home()) / "codebase").resolve()


def repo_dir(root: str | os.PathLike[str], repo_id: str | None) -> Path:
    """Return the per-repository directory below ``root``."""

    return Path(root).expanduser().resolve() / "repos" / safe_segment(repo_id)


def snapshot_path(root: str | os.PathLike[str], repo_id: str | None) -> Path:
    """Return the JSON document path for one repository's snapshot history."""

    return repo_dir(root, repo_id) / "snapshots.json"


# L1-compatible spelling for callers that want to make the root convention
# explicit.  It is an alias, not a second state format.
l1_root = codebase_root


def atomic_write_text(path: str | os.PathLike[str], text: str) -> None:
    """Atomically write UTF-8 text using a same-directory temporary file.

    ``os.replace`` is atomic on the supported local filesystems.  A unique temp
    name prevents two independent store instances from replacing one another's
    partial file; the store's per-path lock additionally serializes its
    read/modify/write transactions.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    global _write_sequence
    with _write_counter:
        _write_sequence += 1
        sequence = _write_sequence
    temp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{threading.get_ident()}-{sequence}")
    encoded = text.encode("utf-8")
    try:
        with temp.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        try:
            directory_fd = os.open(str(target.parent), os.O_RDONLY)
        except (OSError, AttributeError):
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "DEFAULT_AGENT_BUCKET",
    "atomic_write_text",
    "codebase_root",
    "l1_root",
    "repo_dir",
    "safe_segment",
    "snapshot_path",
]
