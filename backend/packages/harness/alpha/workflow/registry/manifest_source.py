"""Shared reader for the generated ``contracts/feature_manifest.json``.

One loader for every registry that projects the manifest, so the engines and
wiring registries cannot disagree about where the file is or what "missing"
means.

Why this is the *only* source for engine/router/middleware/loop counts
-------------------------------------------------------------------------
Those numbers are **generated**, never hand-typed. ``scripts/generate_feature_manifest.py``
produces the artifact and ``scripts/check_generated_drift.py`` fails the build
when the committed copy differs by so much as a byte other than ``generated_at``.
A registry that recomputed any of them by walking the filesystem would create a
second, unreviewed opinion that could silently disagree with the gate — which is
exactly the drift class this repository has already paid for five times (89 → 97
→ 99 engines, 127 → 130 → 134 tools, 55 → 57 → 60 → 61 routers, 8 → 9 loops).

So this module:

* resolves the artifact structurally, via the canonical
  :func:`alpha.capabilities.honesty.repo_root` anchor (an ancestor holding both
  ``backend/packages`` and ``AGENTS.md``) rather than the process cwd, which
  anchors to the wrong tree whenever the Gateway runs from ``backend/``;
* raises :class:`RegistryUnavailable` with the **real** exception text when the
  file is missing or unparsable, so a broken checkout reads as
  ``unavailable`` rather than "zero engines";
* caches the parsed document per resolved path plus its stat fingerprint, so a
  regeneration is picked up without a restart while a hot loop does not re-read
  ~2700 lines of JSON per call.

Nothing here interprets the manifest's meaning: it only reads it. Deciding that
a ``wired: false`` router counts as ``unavailable`` belongs to the registry that
projects it.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from alpha.capabilities.honesty import repo_root
from alpha.workflow.registry.base import RegistryUnavailable

#: Path of the generated artifact relative to the resolved repository root.
MANIFEST_RELPATH = ("contracts", "feature_manifest.json")

#: Source string recorded on every descriptor read through this loader.
SOURCE = "contracts/feature_manifest.json"

#: Sections this loader is willing to hand back. Anything else is a caller bug,
#: and silently returning ``{}`` for it would read as "that class has no
#: entries" — the precise false claim the registry protocol exists to prevent.
_MANIFEST_SECTIONS: frozenset[str] = frozenset(
    {
        "tools",
        "routers",
        "middlewares",
        "loops",
        "engines",
        "intentionally_unwired",
        "dormant_packages",
        "excluded_local_only",
    }
)

_CACHE_LOCK = threading.Lock()
#: resolved path -> (stat fingerprint, parsed document)
_CACHE: dict[Path, tuple[tuple[int, int, int], dict[str, Any]]] = {}


def feature_manifest_path() -> Path:
    """Absolute path of the generated artifact.

    Does not check existence — callers distinguish "absent" from "unreadable"
    by attempting :func:`load_feature_manifest` and handling the raise.
    """
    return repo_root().joinpath(*MANIFEST_RELPATH)


def _stat_fingerprint(path: Path) -> tuple[int, int, int]:
    """``(size, mtime_ns, inode)`` — the invalidation key for the cache.

    ``st_ino`` matters because a regeneration that replaces the file within the
    same filesystem keeps the directory entry's identity stable on some
    platforms; ``mtime_ns`` alone is enough on the others. Reading all three is
    cheaper than being wrong about whether a stale artifact is live.
    """
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ino", 0))


def load_feature_manifest(*, refresh: bool = False) -> dict[str, Any]:
    """Read and parse the generated manifest, cached per stat fingerprint.

    Args:
        refresh: Bypass the cache and re-read. The Gateway calls this on the
            ``GET /api/ops/integration-health`` path so a contributor who just
            ran the generator sees the new counts without a restart.

    Raises:
        RegistryUnavailable: the file is absent, unreadable, is not a JSON
            object, or lacks the sections this loader publishes.
    """
    path = feature_manifest_path()
    if not refresh:
        with _CACHE_LOCK:
            cached = _CACHE.get(path)
        if cached is not None:
            try:
                fingerprint = _stat_fingerprint(path)
            except OSError:
                fingerprint = None
            if fingerprint is not None and fingerprint == cached[0]:
                return cached[1]

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryUnavailable(f"feature manifest unreadable at {path}: {exc}") from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise RegistryUnavailable(f"feature manifest is not valid JSON at {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RegistryUnavailable(f"feature manifest at {path} is a {type(document).__name__}, expected a JSON object")

    missing = sorted(_MANIFEST_SECTIONS - set(document))
    if missing:
        raise RegistryUnavailable(f"feature manifest at {path} is missing section(s): {', '.join(missing)}")

    try:
        fingerprint = _stat_fingerprint(path)
    except OSError:
        fingerprint = None
    if fingerprint is not None:
        with _CACHE_LOCK:
            _CACHE[path] = (fingerprint, document)
    return document


def manifest_section(section: str) -> list[dict[str, Any]]:
    """One manifest section as a list of row mappings.

    Fail-closed on an unknown section name rather than returning an empty list:
    a typo in a section name would otherwise render as a healthy registry that
    happens to contain nothing.
    """
    if section not in _MANIFEST_SECTIONS:
        raise RegistryUnavailable(f"unknown feature-manifest section {section!r}; expected one of {sorted(_MANIFEST_SECTIONS)}")
    rows = load_feature_manifest().get(section)
    if not isinstance(rows, list):
        raise RegistryUnavailable(f"feature manifest section {section!r} is a {type(rows).__name__}, expected a list")
    return [row for row in rows if isinstance(row, dict)]


__all__ = [
    "MANIFEST_RELPATH",
    "SOURCE",
    "feature_manifest_path",
    "load_feature_manifest",
    "manifest_section",
]
