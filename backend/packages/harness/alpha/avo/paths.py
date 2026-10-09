"""Path confinement: the checks that decide whether a candidate may touch a file.

The failure modes this module exists to catch are all *shapes*, not content:
``../`` out of a worktree, an absolute path ignoring the root, a symlink that
resolves outside, a Windows 8.3 short name, and a protected-prefix match that
passes because the comparison was done on a raw string rather than a normalised
one.

Two rules make the rest work:

1. **Every comparison happens on one normalised form.** A path is resolved
   relative to the workspace root and compared forward-slashed. A matcher that
   compares ``C:\\x\\..\\y`` and ``C:/y`` to a pattern is a matcher that can be
   walked through.
2. **A symlink that escapes is a refusal, not a warning.** The candidate may not
   create a link that points outside its own worktree, and the check runs on the
   *resolved* target, because the link's own text is not where the bytes go.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

__all__ = [
    "PathRefused",
    "normalize_relative",
    "matches_any",
    "is_protected",
    "assert_within_root",
]


class PathRefused(ValueError):
    """A path the candidate is not permitted to name."""

    def __init__(self, path: str, reason: str, *, matched: str | None = None) -> None:
        self.path = path
        self.reason = reason
        self.matched = matched
        detail = f"{reason}: {path!r}"
        if matched:
            detail += f" (matched {matched!r})"
        super().__init__(detail)


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def normalize_relative(path: str) -> str:
    """Normalise ``path`` to a forward-slashed, root-relative, traversal-free form.

    Raises :class:`PathRefused` for anything that is not a plain relative path.
    """
    if not isinstance(path, str) or not path.strip():
        raise PathRefused(str(path), "path is empty")
    text = path.strip()
    if text.startswith("\\\\") or _WINDOWS_DRIVE.match(text) or text.startswith("/") or text.startswith("~"):
        raise PathRefused(path, "path must be relative to the workspace root")
    if _URL_SCHEME.match(text):
        raise PathRefused(path, "path must not be a URL")
    if "\x00" in text:
        raise PathRefused(path, "path contains a NUL byte")
    unified = text.replace("\\", "/")
    parts: list[str] = []
    for part in unified.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise PathRefused(path, "path traverses above the workspace root")
        parts.append(part)
    if not parts:
        raise PathRefused(path, "path resolves to the workspace root itself")
    return "/".join(parts)


def _pattern_matches(normalised: str, pattern: str) -> bool:
    """Prefix-aware glob match on already-normalised, root-relative paths.

    ``**`` matches any number of segments, ``*`` matches within one segment,
    and a directory pattern covers its whole subtree: ``candidates/**`` and
    ``candidates`` both match ``candidates/c1/x.py``, so a profile written
    either way grants the same thing. Matching the bare directory and nothing
    under it would be the opposite of the grant.
    """
    pat = pattern.replace("\\", "/").strip()
    if not pat:
        return False
    if pat == "**":
        return True
    pat = pat[2:] if pat.startswith("./") else pat
    subtree = pat.endswith("/**") or not any(ch in pat for ch in "*?[")
    if pat.endswith("/**"):
        pat = pat[:-3]
    pat = pat.rstrip("/")
    if not pat:
        return False
    if _compile(pat).fullmatch(normalised) is not None:
        return True
    if subtree and (normalised.startswith(pat + "/") or _compile(pat + "/**").fullmatch(normalised) is not None):
        return True
    return False


_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _compile(pattern: str) -> re.Pattern[str]:
    cached = _PATTERN_CACHE.get(pattern)
    if cached is not None:
        return cached
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if index + 1 < length and pattern[index + 1] == "*":
                index += 2
                if index < length and pattern[index] == "/":
                    index += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    compiled = re.compile("".join(out))
    _PATTERN_CACHE[pattern] = compiled
    return compiled


def matches_any(path: str, patterns: Iterable[str]) -> str | None:
    """The first pattern in ``patterns`` that covers ``path``, else ``None``."""
    normalised = normalize_relative(path)
    for pattern in patterns:
        if _pattern_matches(normalised, pattern):
            return pattern
    return None


def is_protected(path: str, protected: Iterable[str]) -> str | None:
    """The protected entry covering ``path``, or ``None`` when it is not covered."""
    return matches_any(path, protected)


def assert_within_root(path: str, root: Path) -> Path:
    """Resolve ``path`` under ``root`` and refuse anything that escapes.

    The escape check is on the *resolved* path, so a symlink pointing outside
    the root is refused even though the candidate only wrote a link. The root
    itself is resolved once for the same reason: comparing a relative path to a
    symlinked root is a comparison that can be won.
    """
    normalised = normalize_relative(path)
    resolved_root = root.resolve()
    candidate = (resolved_root / normalised).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise PathRefused(path, "resolved path escapes the workspace root") from exc
    if candidate == resolved_root:
        raise PathRefused(path, "path resolves to the workspace root itself")
    return candidate


def is_windows() -> bool:  # pragma: no cover - platform guard, exercised on Windows
    return os.name == "nt"


def posix(path: str) -> PurePosixPath:
    """A ``PurePosixPath`` for a relative string, for callers that want one."""
    return PurePosixPath(normalize_relative(path))
