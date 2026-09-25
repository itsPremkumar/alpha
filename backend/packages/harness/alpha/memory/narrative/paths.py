"""Filesystem layout for narrative events, stories, and provenance.

Path escaping and atomic replacement intentionally reuse the established L1
primitives.  Only the narrative subtree is new, so this package does not grow
a second path-safety or write-atomicity policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from alpha.agents.memory.l1.paths import atomic_write_text, l1_root, safe_segment

from .models import SCOPE_TYPES


def _scope_parts(scope: Any, scope_id: str | None = None) -> tuple[str, str]:
    """Normalize a scope argument without accepting path separators as scope."""

    nested_id = scope_id
    value = scope
    if isinstance(value, Mapping):
        nested_id = nested_id or value.get("scope_id") or value.get("id") or value.get("key")
        value = value.get("scope") or value.get("type") or value.get("kind")
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        nested_id = nested_id or value[1]
        value = value[0]
    text = str(value or "user").strip().lower()
    for separator in (":", "/", "#"):
        if separator in text:
            prefix, suffix = text.split(separator, 1)
            if prefix in SCOPE_TYPES:
                nested_id = nested_id or suffix
                text = prefix
                break
    if text not in SCOPE_TYPES:
        raise ValueError(f"scope must be one of {sorted(SCOPE_TYPES)}")
    normalized_id = str(nested_id or "default").strip() or "default"
    return text, normalized_id[:256]


def scope_key(scope: Any, scope_id: str | None = None) -> str:
    """Return the canonical logical key for a scope."""

    kind, normalized_id = _scope_parts(scope, scope_id)
    if normalized_id == "default":
        return kind
    return f"{kind}:{normalized_id}"


def scope_dir(root: Path, scope: Any, scope_id: str | None = None) -> Path:
    """Return the directory for one scope below the narrative root."""

    return root / "narrative" / safe_segment(scope_key(scope, scope_id))


def narrative_root(storage_path: str | None = None) -> Path:
    """Resolve explicit storage or the shared runtime home."""

    return l1_root(storage_path)


def events_path(root: Path, scope: Any, scope_id: str | None = None) -> Path:
    """Return the per-scope event document path."""

    return scope_dir(root, scope, scope_id) / "events.json"


def event_store_path(root: Path, scope: Any, scope_id: str | None = None) -> Path:
    """Compatibility alias for callers that name the document explicitly."""

    return events_path(root, scope, scope_id)


def story_path(root: Path, scope: Any, scope_id: str | None = None) -> Path:
    """Return the per-scope story document path."""

    return scope_dir(root, scope, scope_id) / "story.json"


def story_store_path(root: Path, scope: Any, scope_id: str | None = None) -> Path:
    """Compatibility alias for :func:`story_path`."""

    return story_path(root, scope, scope_id)


def provenance_log_path(root: Path, scope: Any, day: str, scope_id: str | None = None) -> Path:
    """Return one UTC-day append-only provenance log for a scope."""

    clean_day = str(day).strip()
    if not clean_day or any(char in clean_day for char in "/\\"):
        raise ValueError("day must be a YYYY-MM-DD-style path segment")
    return scope_dir(root, scope, scope_id) / "provenance" / f"{clean_day}.jsonl"


__all__ = [
    "atomic_write_text",
    "event_store_path",
    "events_path",
    "l1_root",
    "narrative_root",
    "provenance_log_path",
    "safe_segment",
    "scope_dir",
    "scope_key",
    "story_path",
    "story_store_path",
]
