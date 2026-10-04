"""Windows PowerShell script source.

Detects the bug class that made ``start.ps1`` completely unparseable: a ``.ps1``
file saved as UTF-8 **without** a byte-order mark while containing non-ASCII
characters. Windows PowerShell 5.1 decodes a BOM-less ``.ps1`` as ANSI, so every
non-ASCII character is misdecoded and any string literal containing one is
corrupted -- producing parse errors that prevent the script running at all.

This is a good Sentinel target because it is silent, deterministic, mechanically
detectable and mechanically fixable, and its failure mode looks nothing like its
cause (the app simply "does not start").
"""

from __future__ import annotations

import logging
from pathlib import Path

from alpha.runtime.sentinel.signals import Signal

logger = logging.getLogger(__name__)

SOURCE = "scripts"

#: UTF-8 byte-order mark.
BOM = b"\xef\xbb\xbf"

#: Directories never worth scanning. Pruned *during* traversal, not after --
#: see :func:`scan_directory` for why the distinction is the whole point.
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        ".next",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        # Git worktrees are full checkouts of this same repository. Without this the
        # scan reads the identical `start.ps1` once per worktree and reports a
        # separate `missing_bom` signal for each copy of one defect.
        ".worktrees",
        # Runtime/state trees that are never source.
        ".alpha",
        "site-packages",
    }
)


def has_bom(data: bytes) -> bool:
    return data.startswith(BOM)


def non_ascii_chars(text: str) -> list[str]:
    return sorted({c for c in text if ord(c) > 127})


def needs_bom(path: str | Path) -> bool:
    """True when a .ps1 has non-ASCII content but no BOM."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        data = p.read_bytes()
    except OSError:
        return False
    if has_bom(data):
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # Not UTF-8 at all - a different problem, not the BOM one.
        return False
    return bool(non_ascii_chars(text))


def scan_file(path: str | Path) -> list[Signal]:
    """Return a Signal when ``path`` is a .ps1 that needs a BOM."""
    p = Path(path)
    if p.suffix.lower() != ".ps1":
        return []
    if not needs_bom(p):
        return []

    data = p.read_bytes()
    chars = non_ascii_chars(data.decode("utf-8", errors="replace"))
    shown = "".join(chars)[:40]
    return [
        Signal(
            source=SOURCE,
            kind="missing_bom",
            message=f"PowerShell script has non-ASCII content but no UTF-8 BOM: {p.name} ({shown!r})",
            severity="critical",
            context={
                "path": str(p),
                "non_ascii_count": sum(1 for c in data.decode("utf-8", errors="replace") if ord(c) > 127),
                "non_ascii_chars": shown,
            },
        )
    ]


def iter_candidate_files(root: str | Path, *, pattern: str = "*.ps1") -> list[Path]:
    """Every matching file under ``root`` with :data:`SKIP_DIR_NAMES` pruned.

    ``os.walk`` with ``dirnames`` reassigned **in place** is the only walk that
    stops descending. ``Path.rglob`` has no such hook, so filtering its output
    pays the full traversal cost and then throws the result away.

    That is not hypothetical. This function previously did exactly
    ``root.rglob(pattern)`` followed by a ``SKIP_DIR_NAMES`` check on
    ``p.parts``, and measured on this repository:

    * ``rglob`` walked **87,073 files** in **24.1s** because ``.venv`` is inside
      the scanned root;
    * the only three ``*.ps1`` it found were all inside ``.venv``, so the filter
      discarded every one of them and the scan returned an empty list;
    * the pruned walk answers the same question in **2.4s** (10.1x faster).

    ``alpha.knowledge.code_index`` and ``alpha.knowledge.self_documentation``
    already document this exact rule for the same reason.
    """
    import fnmatch
    import os

    base = Path(root)
    if not base.is_dir():
        return []

    found: list[Path] = []
    for current, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        # topdown=True makes this assignment the prune point. Mutating the list
        # in place is what tells os.walk not to descend.
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES)
        for filename in sorted(filenames):
            if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                found.append(Path(current) / filename)
    return found


def scan_directory(directory: str | Path, *, pattern: str = "*.ps1") -> list[Signal]:
    """Scan a directory tree for .ps1 files needing a BOM."""
    signals: list[Signal] = []
    for p in iter_candidate_files(directory, pattern=pattern):
        signals.extend(scan_file(p))
    return signals


def apply_bom_fix(path: str | Path) -> bool:
    """Add a UTF-8 BOM to ``path``. Returns True when a BOM was added.

    Idempotent: a file that already has a BOM is left untouched.
    """
    p = Path(path)
    data = p.read_bytes()
    if has_bom(data):
        return False
    p.write_bytes(BOM + data)
    return True


def verify_bom_fix(path: str | Path) -> bool:
    """True when a .ps1 file is verified to have a BOM or has no non-ASCII characters requiring one."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        data = p.read_bytes()
        return has_bom(data) or not bool(non_ascii_chars(data.decode("utf-8")))
    except Exception:
        return False
