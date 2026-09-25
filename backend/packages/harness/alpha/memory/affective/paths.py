"""On-disk layout for per-user affective events and provenance.

Path safety, runtime-home fallback, and atomic text replacement intentionally
reuse the L1 primitives rather than maintaining a second escaping/write
contract. Only the affective subtree layout is new.
"""

from __future__ import annotations

from pathlib import Path

from alpha.agents.memory.l1.paths import atomic_write_text, l1_root, safe_segment


def affective_root(storage_path: str | None) -> Path:
    """Resolve an explicit root or the shared Alpha runtime home."""
    return l1_root(storage_path)


def user_dir(root: Path, user_id: str | None) -> Path:
    """Per-user affective directory under ``root``."""
    return root / "users" / safe_segment(user_id or "default") / "affective"


def events_path(root: Path, user_id: str | None) -> Path:
    """Return the event document for one user."""
    return user_dir(root, user_id) / "events.json"


def provenance_log_path(root: Path, user_id: str | None, day: str) -> Path:
    """Return one UTC-day provenance JSONL path for a user."""
    return user_dir(root, user_id) / "provenance" / f"{day}.jsonl"


__all__ = [
    "affective_root",
    "atomic_write_text",
    "events_path",
    "provenance_log_path",
    "safe_segment",
    "user_dir",
]
