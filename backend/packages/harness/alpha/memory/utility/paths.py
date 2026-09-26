"""Filesystem layout for utility state, reusing Alpha's L1 path conventions.

Utility state is kept in its own ``users/<user>/utility`` subtree.  The path
sanitizer, runtime-home fallback, and atomic replacement semantics match the
established L1 helpers while keeping this package from opening or rewriting a
host memory document.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from alpha.agents.memory.l1.paths import agent_bucket, l1_root, safe_segment

__all__ = [
    "atomic_write_text",
    "corrupt_backup_path",
    "document_path",
    "l1_root",
    "preserve_corrupt",
    "safe_segment",
    "scope_dir",
    "utility_root",
]


def utility_root(storage_path: str | Path | None = None) -> Path:
    """Resolve an explicit state root or the shared runtime home.

    An explicit root is used verbatim (after expansion/resolution), which is
    what hermetic callers pass.  The fallback is the same runtime home used by
    L1; the utility subtree is still isolated under each user's directory.
    """

    return l1_root(str(storage_path) if storage_path is not None else None)


def scope_dir(root: Path, user_id: str | None, agent_name: str | None = None) -> Path:
    """Return the per-user/per-agent utility directory."""

    return Path(root) / "users" / safe_segment(user_id or "default") / "utility"


def document_path(root: Path, user_id: str | None, agent_name: str | None = None) -> Path:
    """Return the JSON document for one logical scope."""

    agent = agent_bucket(agent_name)
    return scope_dir(root, user_id, agent_name) / f"{agent}.json"


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace ``path`` with UTF-8 text without a BOM.

    A unique same-directory temporary file makes concurrent store instances
    safer than a fixed ``.tmp`` sibling while retaining the L1 replace
    contract.  The parent is created only by this utility package.
    """

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(text)
            handle.flush()
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary:
            try:
                Path(temporary).unlink()
            except OSError:
                pass


def corrupt_backup_path(path: Path) -> Path:
    """Return a non-existing ``.corrupt-*`` sibling name for ``path``."""

    candidate = Path(path).with_name(f"{Path(path).name}.corrupt-{time.time_ns()}")
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = Path(path).with_name(f"{Path(path).name}.corrupt-{time.time_ns()}-{counter}")
    return candidate


def preserve_corrupt(path: Path) -> Path | None:
    """Move an unreadable document to a forensic sibling and return its path.

    The original bytes are never overwritten.  If the move itself fails, the
    original remains in place and ``None`` is returned so the caller can
    disclose the preservation failure.
    """

    source = Path(path)
    if not source.exists():
        return None
    destination = corrupt_backup_path(source)
    try:
        source.replace(destination)
    except OSError:
        return None
    return destination
