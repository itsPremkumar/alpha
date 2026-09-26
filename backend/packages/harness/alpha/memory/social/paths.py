"""Collision-resistant per-scope paths for social memory.

The human-readable label reuses L1's ``safe_segment`` sanitizer, while a digest
of the exact scope prevents distinct identities such as ``a/b`` and ``a_b``
from sharing a state document after sanitization.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from alpha.agents.memory.l1.paths import safe_segment

from .models import normalize_scope


def scope_bucket(owner_scope: str) -> str:
    """Return one bounded, collision-resistant directory segment for a scope."""

    scope = normalize_scope(owner_scope)
    label = safe_segment(scope)[:64]
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    return f"{label}-{digest}"


def social_user_dir(root: Path, owner_scope: str) -> Path:
    """Per-owner social directory under the configured/runtime root."""

    return Path(root) / "users" / scope_bucket(owner_scope) / "social"


def state_path(root: Path, owner_scope: str) -> Path:
    """Path of one owner scope's atomic JSON state document."""

    return social_user_dir(root, owner_scope) / "state.json"


def provenance_path(root: Path, owner_scope: str, day: str) -> Path:
    """Path of one day's append-only social provenance JSONL."""

    return social_user_dir(root, owner_scope) / "provenance" / f"{day}.jsonl"


def iter_state_paths(root: Path):
    """Yield candidate state paths without assuming filenames are reversible."""

    users_dir = Path(root) / "users"
    if not users_dir.exists():
        return
    for path in sorted(users_dir.glob("*/social/state.json")):
        if path.is_file():
            yield path


__all__ = [
    "iter_state_paths",
    "provenance_path",
    "scope_bucket",
    "social_user_dir",
    "state_path",
]
