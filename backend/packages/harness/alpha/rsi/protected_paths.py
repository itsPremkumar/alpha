"""Machine-readable protected-path policy for RSI candidates (plan WP-B3, feature #14).

Purpose: a deny-list covering spec section 15 surfaces, enforced against
*candidate workspaces and candidate-issued commands*, defined **in code**
so a candidate can never rewrite its own policy (plan section 5.3:
``alpha/rsi/protected_paths.py`` is a code constant — no code path lets a
candidate edit it and then approve itself).

Honest-failure contract (plan section 3 WP-B3 / section 5):

- ``PROTECTED_RULES`` is a module-level tuple literal.  Rules are **never**
  loaded from JSON/YAML/any file; the module contains no file-reading call
  that could feed the policy (a candidate that can write files still cannot
  change these rules).
- The policy cannot be disabled by any candidate input: there is no flag,
  environment variable, or argument that clears ``PROTECTED_RULES``.
- ``classify()`` never silent-allows: an unmatched/unknown path resolves to
  ``("review_required", "")`` — the cautious default.
- ``assert_candidate_path_allowed()`` is fail-closed: ``deny`` raises
  ``PermissionError`` unconditionally (even with ``reviewed=True``);
  ``review_required`` raises ``PermissionError`` unless the caller passes
  ``reviewed=True`` explicitly.  ``reviewed`` must come from the CALLER's
  human-approval channel (a human/HITL resolution) — review can never be
  self-granted inside the candidate loop, and nothing in this module can
  grant it on the candidate's behalf.
- ``redacted_paths()`` returns **patterns only** — never file contents
  (secret hygiene: no ``.env`` bodies, no tokens; plan section 5.7).

Matching semantics (documented, deterministic):

- Paths are normalized (``\\`` -> ``/``, leading ``./`` and ``~`` stripped,
  trailing ``/`` dropped) and matched against every trailing segment suffix
  of the normalized path, so absolute workspace paths resolve to their
  repo-relative tail.
- Within a pattern: ``**/`` matches zero or more leading segments, ``**``
  matches any characters (including ``/``), ``*`` matches zero or more
  characters *within* one segment, ``?`` matches one non-segment character.
- The first matching rule in ``PROTECTED_RULES`` order wins, so the
  returned ``matched_pattern`` is deterministic.
- Over-matching errs toward protection: e.g. an exact tail segment of
  ``main``/``master`` (a branch name) is denied, and any path whose tail
  sits under a protected directory is covered.

Integration (per plan WP-B3): called from ``alpha/rsi/workspace.py``
(``RsiWorkspaceManager.run_checks`` — edit-target paths) and from the
WP-C2 promotion scope check; complementary to
``alpha/evolution/engine.py::FORBIDDEN_SURFACES`` (surface level) and
``alpha/safety/self_repo_guard.py`` (command level).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

__all__ = [
    "PROTECTED_RULES",
    "ProtectedRule",
    "assert_candidate_path_allowed",
    "classify",
    "redacted_paths",
]

MODE_DENY = "deny"
MODE_REVIEW = "review_required"


@dataclass(frozen=True)
class ProtectedRule:
    """One protected-path rule; ``mode`` is exactly ``deny`` or ``review_required``."""

    pattern: str
    mode: Literal["deny", "review_required"]


def _rule(pattern: str, mode: str) -> ProtectedRule:
    """Validate at import time so a malformed constant can never weaken the policy."""
    if mode not in (MODE_DENY, MODE_REVIEW):
        raise ValueError(f"protected rule mode must be 'deny' or 'review_required', got {mode!r}")
    if not pattern or "\\" in pattern:
        raise ValueError(f"protected rule pattern must be non-empty with '/' separators, got {pattern!r}")
    return ProtectedRule(pattern=pattern, mode=mode)  # type: ignore[arg-type]


# Code constants (plan section 5.3) — candidates may propose changes to these
# only as ordinary reviewable PRs through the normal human path; nothing in
# this module loads rules from a file or reads candidate-supplied config.
PROTECTED_RULES: tuple[ProtectedRule, ...] = (
    _rule(".env*", MODE_DENY),
    _rule("**/.env*", MODE_DENY),
    _rule("**/.jwt_secret", MODE_DENY),
    _rule("**/credentials*", MODE_DENY),
    _rule(".github/workflows/**", MODE_REVIEW),
    _rule("**/contracts/feature_manifest.json", MODE_DENY),
    _rule("backend/tests/**", MODE_DENY),
    _rule("backend/packages/harness/alpha/benchmarks/**", MODE_DENY),
    _rule("alpha/reproduction/gates.py", MODE_DENY),
    _rule("alpha/policy/**", MODE_DENY),
    _rule("backend/app/gateway/auth/**", MODE_DENY),
    _rule("references/**", MODE_DENY),
    # Branch names; branch rules are enforced by WP-B1 (never touch main).
    _rule("main", MODE_DENY),
    _rule("master", MODE_DENY),
)


def _normalize(path: str | Path) -> str:
    """Normalize to a '/'-separated, relative-looking string without I/O.

    Never touches the filesystem: classification is a pure string check, so
    no file contents can influence or leak through the returned values.
    """
    text = str(path).strip().replace("\\", "/")
    if text.startswith("~"):
        text = text[1:]
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _suffixes(normalized: str) -> list[str]:
    """Full path plus every trailing segment suffix (first = longest)."""
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return []
    return ["/".join(parts[index:]) for index in range(len(parts))]


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    """Translate a rule pattern into an anchored regex (segment-aware globs)."""
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**/", index):
                out.append(r"(?:.*/)?")
                index += 3
                continue
            if pattern.startswith("**", index):
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return re.compile(f"^{''.join(out)}$")


def _matches(pattern: str, normalized: str) -> bool:
    regex = _compile(pattern)
    return any(regex.match(suffix) for suffix in _suffixes(normalized))


def classify(path: str | Path) -> tuple[str, str]:
    """Classify ``path`` as ``(mode, matched_pattern)``.

    - matched: the first rule in ``PROTECTED_RULES`` order wins, so the
      pattern string returned is always one of the code-constant patterns;
    - unmatched/unknown: ``("review_required", "")`` — cautious default,
      never silent-allow (plan section 3 WP-B3).

    Both returned strings are drawn only from ``{deny, review_required}``
    and ``PROTECTED_RULES`` patterns — never file contents (honesty/hygiene
    pin in tests).
    """
    normalized = _normalize(path)
    if not normalized:
        return (MODE_REVIEW, "")
    for rule in PROTECTED_RULES:
        if _matches(rule.pattern, normalized):
            return (rule.mode, rule.pattern)
    return (MODE_REVIEW, "")


def assert_candidate_path_allowed(path: str | Path, *, reviewed: bool = False) -> None:
    """Fail-closed gate for a candidate path; returns ``None`` only when allowed.

    - ``deny``: raises ``PermissionError`` unconditionally — even
      ``reviewed=True`` cannot override a denied surface.
    - ``review_required`` (including every unknown path): raises
      ``PermissionError`` unless ``reviewed=True`` is passed.

    ``reviewed`` MUST come from the caller's human-approval channel (a
    human/HITL resolution recorded outside the candidate loop).  Review can
    never be self-granted inside the candidate loop: this function has no
    side channel, and nothing here derives ``reviewed`` from candidate
    input.  Callers must not pass ``reviewed=True`` based on candidate
    output alone.
    """
    mode, pattern = classify(path)
    if mode == MODE_DENY:
        raise PermissionError(f"candidate path denied by protected-path policy: {path} (rule: {pattern})")
    if not reviewed:
        raise PermissionError(
            f"candidate path requires human review before it is allowed: {path} "
            "(reviewed=True must be granted by a human/HITL approval channel, never by the candidate loop)"
        )


def redacted_paths(paths: list[str | Path]) -> list[str]:
    """Return the matched rule PATTERN for each path — for disclosure.

    One pattern string per input (``""`` when unmatched).  File contents are
    never read and never echoed (secret hygiene: a denied ``.env`` path is
    disclosed as ``.env*``, not as its body).  Length is preserved so a
    caller can zip paths with redactions.
    """
    return [classify(path)[1] for path in paths]
