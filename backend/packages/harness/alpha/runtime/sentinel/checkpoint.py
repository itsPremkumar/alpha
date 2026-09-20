"""Checkpoint and restore for the Sentinel FIX stage.

The rule: nothing is fixed without a way back. A checkpoint is a content-addressed
copy of the files a repair is about to touch, written atomically. If verification
fails, the repair is rolled back from the checkpoint.

Also holds the temp-message helpers used by commit.py so commit messages never go
through a shell (which matters on Windows, where messages routinely contain
backslashes and quotes).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_ROOT = ".sentinel/checkpoints"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(dest: Path, data: bytes) -> None:
    """Write via temp file + replace, so a crash cannot leave a partial file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


@dataclass
class Checkpoint:
    """A reversible snapshot of the files a repair is about to touch."""

    root: str
    files: dict[str, str] = field(default_factory=dict)  # relpath -> stored path
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {"root": self.root, "files": dict(self.files), "created_at": self.created_at}


class CheckpointManager:
    """Creates and restores file checkpoints under a repo-local directory."""

    def __init__(self, repo_root: str | Path, *, root: str = _DEFAULT_ROOT) -> None:
        self.repo_root = Path(repo_root)
        self.root = self.repo_root / root

    def create(self, rel_paths: list[str]) -> Checkpoint:
        """Snapshot ``rel_paths``. Missing files are recorded as absent."""
        cp = Checkpoint(root=str(self.root))
        for rel in rel_paths:
            src = self.repo_root / rel
            if not src.is_file():
                cp.files[rel] = ""  # absent — restore deletes it again
                continue
            data = src.read_bytes()
            digest = hashlib.sha256(data).hexdigest()[:16]
            safe = rel.replace("\\", "/").replace("/", "__")
            dest = self.root / f"{safe}.{digest}.bak"
            _atomic_write(dest, data)
            cp.files[rel] = str(dest.relative_to(self.repo_root))
        return cp

    def restore(self, cp: Checkpoint) -> list[str]:
        """Restore every file in the checkpoint. Returns restored rel paths."""
        restored: list[str] = []
        for rel, stored in cp.files.items():
            target = self.repo_root / rel
            if not stored:
                # File did not exist before the fix — remove it again.
                if target.exists():
                    target.unlink()
                    restored.append(rel)
                continue
            data = (self.repo_root / stored).read_bytes()
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, data)
            restored.append(rel)
        return restored


# ── Commit-message temp files ──────────────────────────────────────────────

def write_temp_message(message: str) -> Path:
    """Write a commit message to a temp file, avoiding shell quoting entirely."""
    fd, path = tempfile.mkstemp(prefix="sentinel-msg-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(message)
    return Path(path)


def cleanup_temp_message(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
