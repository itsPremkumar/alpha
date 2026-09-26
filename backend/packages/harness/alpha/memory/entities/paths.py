"""Entity-memory paths, reusing the L1 safety and runtime-home contracts."""

from __future__ import annotations

from pathlib import Path

from alpha.agents.memory.l1.paths import atomic_write_text, l1_root, safe_segment


def entity_root(storage_path: str | None) -> Path:
    """Resolve an explicit entity root or the shared Alpha runtime home."""
    return l1_root(storage_path)


def user_dir(root: Path, user_id: str) -> Path:
    """Return the per-user entity directory under ``root``.

    A real user id is mandatory: unlike legacy global-memory buckets, an
    unresolved owner must never silently share a default entity namespace.
    """
    cleaned = str(user_id).strip()
    if not cleaned:
        raise ValueError("user_id is required for entity memory")
    return root / "users" / safe_segment(cleaned) / "entities"


def store_path(root: Path, user_id: str) -> Path:
    """Return one user's atomic entity document path."""
    return user_dir(root, user_id) / "store.json"


def provenance_log_path(root: Path, user_id: str, day: str) -> Path:
    """Return one UTC-day append-only entity provenance path."""
    return user_dir(root, user_id) / "provenance" / f"{day}.jsonl"


__all__ = [
    "atomic_write_text",
    "entity_root",
    "provenance_log_path",
    "safe_segment",
    "store_path",
    "user_dir",
]
