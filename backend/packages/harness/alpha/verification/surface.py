"""The test surface: what the tests checked, captured before and after a repair.

This is the answer to the known failure mode of every self-repairing agent.
Deleting an assertion, loosening a matcher to match the broken output, or
marking a test ``skip`` all produce a green run, and a loop that only asks "did
the tests pass?" will happily finish on a repair that removed the thing being
tested. A loop that cannot be satisfied that way is worth more than no loop,
because the alternative manufactures a green result.

So the surface is captured **before** a repair is dispatched and **after** it
returns, and the two are compared. A repair that reduced the surface is not
"an attempt that failed" — it is a refused attempt, and the loop settles
UNVERIFIED with reason ``weakened_test``.

## What counts as the surface

A per-file record of three independent facts, each of which a weakening edit
touches:

``assertions``
    The multiset of canonical assertion expressions, from the existing
    ``TestAssertionImmutabilityGuard`` — the harness's single owner of that rule.
    A deleted assertion, a rewritten matcher, and a loosened comparison are all
    "a canonical expression that was there and is not now".

``tests``
    The set of test-function names. A whole test deleted while its assertions
    went with it is a coverage loss the assertion count alone would show only as
    a number, not as a *name*; keeping the names makes the report say which test
    disappeared.

``exclusions``
    Count of skip/xfail/expected-failure markers, and count of
    ``pytest.skip``/``xfail`` calls inside test bodies. A test that is still
    present and still asserts — but is now skipped — has not lost an assertion
    and must still be caught.

## Where it reads from

``capture_surface`` takes file *paths* and reads them through an injected
reader. The controller passes the thread's workspace-scoped reader, so the
comparison sees the same bytes a test run would. A file that cannot be read is
recorded as unreadable, and a surface with an unreadable file is **indeterminate**,
not clean — indeterminate refuses the repair, which is the fail-closed direction.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.selfrepair.assertion_guard import TestAssertionImmutabilityGuard

__all__ = [
    "FileSurface",
    "SurfaceComparison",
    "TestSurface",
    "capture_surface",
    "compare_surfaces",
]

_GUARD = TestAssertionImmutabilityGuard()

#: In-body skip markers. A test whose only change is "now it skips" has lost its
#: coverage while keeping every assertion, so the marker count is tracked
#: separately from the assertions.
_IN_BODY_SKIP_RE = re.compile(r"\bpytest\.(?:skip|xfail)\s*\(|\bunittest\.SkipTest\s*\(|\braise\s+SkipTest\b")

#: Decorator/class-level skip markers.
_DECORATED_SKIP_RE = re.compile(
    r"@(?:unittest\.)?skip(?:If|Unless)?\b|@(?:pytest\.mark|mark)\.(?:skip|skipif|xfail)\b|@(?:unittest\.)?expectedFailure\b|\bxit\s*\(|\bxdescribe\s*\(",
    re.IGNORECASE,
)

#: Bound on files per surface, so a misconfigured root cannot turn a comparison
#: into a full-tree read. Exceeding it is recorded as a truncation, and a
#: truncated surface is treated as indeterminate by the comparison.
_MAX_SURFACE_FILES = 500


@dataclass(frozen=True)
class FileSurface:
    """One test file's surface at one moment in time."""

    path: str
    assertions: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    excluded_count: int = 0
    readable: bool = True

    @property
    def assertion_count(self) -> int:
        return len(self.assertions)


@dataclass(frozen=True)
class TestSurface:
    """The whole captured surface, plus whether it was captured completely."""

    files: Mapping[str, FileSurface] = field(default_factory=dict)
    truncated: bool = False

    @property
    def complete(self) -> bool:
        return not self.truncated and all(entry.readable for entry in self.files.values())

    @property
    def assertion_total(self) -> int:
        return sum(entry.assertion_count for entry in self.files.values())

    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self.files))


@dataclass(frozen=True)
class SurfaceComparison:
    """The verdict on one repair attempt, with the reason spelled out.

    ``indeterminate`` is separate from ``reduced`` on purpose. A comparison that
    could not be made is not a clean bill of health, and the controller settles
    UNVERIFIED either way — but the reason differs, so the report says which
    happened instead of guessing.
    """

    reduced: bool
    indeterminate: bool
    reasons: tuple[str, ...] = ()
    before_assertions: int = 0
    after_assertions: int = 0

    @property
    def blocking(self) -> bool:
        """Whether this comparison must stop the repair from counting.

        Both a reduction and an indeterminate comparison block. A comparison that
        cannot be made is not permission to proceed.
        """
        return self.reduced or self.indeterminate

    def render(self) -> str:
        if self.indeterminate and not self.reduced:
            return f"test surface could not be compared: {'; '.join(self.reasons) or 'unknown'}"
        if self.reduced:
            return f"repair reduced the test surface: {'; '.join(self.reasons)}"
        return "test surface unchanged or strengthened"


def capture_surface(
    paths: Iterable[str],
    reader: Callable[[str], str | None] | None = None,
) -> TestSurface:
    """Capture the surface of *paths* as they are right now.

    *reader* returns the file's text, or ``None`` when the file does not exist.
    The default reader reads from the host filesystem; the controller injects the
    thread's workspace reader instead, so a sandboxed run compares the same bytes
    its tests ran against.
    """
    read = reader or _read_host
    files: dict[str, FileSurface] = {}
    truncated = False
    seen: list[str] = []
    for raw in paths:
        path = _normalize(raw)
        if path in files:
            continue
        if len(seen) >= _MAX_SURFACE_FILES:
            truncated = True
            break
        seen.append(path)
        try:
            text = read(path)
        except Exception:
            text = None
        if text is None:
            files[path] = FileSurface(path=path, readable=False)
            continue
        files[path] = _surface_for(path, text)
    return TestSurface(files=files, truncated=truncated)


def compare_surfaces(before: TestSurface, after: TestSurface) -> SurfaceComparison:
    """Compare two captures and say whether the repair reduced coverage.

    A reduction is any of: a watched test file that no longer exists, a
    canonical assertion that was present and is not, a test function that was
    present and is not, an assertion count that fell without a name explaining
    it, or an exclusion marker that was added. A file that appeared is not a
    reduction (new tests are the point). A file whose assertion set *grew* is
    never a reduction either, even if another file lost one — the per-file
    comparison is what decides, not the totals.
    """
    reasons: list[str] = []
    indeterminate = False

    if before.truncated or after.truncated:
        indeterminate = True
        reasons.append("the surface capture was truncated, so it is not a complete picture")

    for path, entry in sorted(before.files.items()):
        if not entry.readable:
            indeterminate = True
            reasons.append(f"{path} could not be read before the repair, so its coverage cannot be compared")
            continue
        current = after.files.get(path)
        if current is None:
            # Deleted, not merely unreadable: the file that carried the
            # assertions is gone, so the coverage it carried is gone with it.
            reasons.append(f"{path} was deleted")
            continue
        if not current.readable:
            # Unreadable *after* a repair is not "we cannot tell", it is "this
            # file asserts nothing we can execute any more" — a deleted file and
            # an unreadable one cost the same coverage. Treated as a reduction
            # so the report names the weakening rather than the indeterminacy.
            reasons.append(f"{path} could not be read after the repair, so the assertions it carried are not running")
            continue

        before_counts = _counts(entry.assertions)
        after_counts = _counts(current.assertions)
        lost = [expression for expression, count in before_counts.items() if after_counts.get(expression, 0) < count]
        for expression in lost:
            reasons.append(f"{path}: assertion removed or weakened ({expression})")
        if entry.assertion_count < current.assertion_count and not lost:
            # Gained more than it lost, and lost nothing: strengthening.
            pass
        elif entry.assertion_count > current.assertion_count and not lost:
            # Fewer assertions with none of the originals missing: the file lost
            # shape the canonical form cannot express (a helper call, a loop).
            # Counted as a reduction on the count alone.
            reasons.append(f"{path}: assertion count fell from {entry.assertion_count} to {current.assertion_count}")

        lost_tests = [name for name in entry.tests if name not in current.tests]
        for name in lost_tests:
            reasons.append(f"{path}: test removed ({name})")

        if current.excluded_count > entry.excluded_count:
            reasons.append(f"{path}: {current.excluded_count - entry.excluded_count} new skip/xfail marker(s)")

    return SurfaceComparison(
        reduced=bool(reasons) and not indeterminate,
        indeterminate=indeterminate,
        reasons=tuple(reasons),
        before_assertions=before.assertion_total,
        after_assertions=after.assertion_total,
    )


def _surface_for(path: str, text: str) -> FileSurface:
    """Extract the three surface facts from one file's text."""
    if not _GUARD.is_test_file(path):
        # Not a test file by the harness's own definition; record it with no
        # assertions so a change to it is compared as "unchanged" rather than
        # silently ignored. A production file is not the test surface.
        return FileSurface(path=path, assertions=(), tests=(), excluded_count=0)
    assertions = tuple(_GUARD.extract_python_assertions(text))
    tests = tuple(sorted(_test_names(text)))
    excluded = len(_DECORATED_SKIP_RE.findall(text)) + len(_IN_BODY_SKIP_RE.findall(text))
    return FileSurface(path=path, assertions=assertions, tests=tests, excluded_count=excluded)


def _test_names(text: str) -> set[str]:
    """Names of the test functions in *text*, by AST where it parses.

    A file that does not parse as Python yields no names and the file is reported
    unreadable-ish by the caller, because a test file that cannot be parsed is
    not a surface anybody can reason about.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_test_name(node.name):
            names.add(node.name)
        if isinstance(node, ast.ClassDef) and _is_test_name(node.name):
            names.add(node.name)
    return names


def _is_test_name(name: str) -> bool:
    return name.startswith("test") or name.endswith("Test") or name.endswith("Tests") or name.startswith("Test") or name.endswith("Spec")


def _counts(assertions: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for assertion in assertions:
        counts[assertion] = counts.get(assertion, 0) + 1
    return counts


def _normalize(path: str) -> str:
    return str(path).replace("\\", "/").strip()


def _read_host(path: str) -> str | None:
    candidate = Path(path)
    if not candidate.is_file():
        return None
    return candidate.read_text(encoding="utf-8", errors="replace")


def surface_from_changes(changed_paths: Iterable[Any]) -> tuple[str, ...]:
    """The test files among a turn's changed paths — the surface a repair could touch.

    Takes whatever identifies a change (a path string, a mapping, a ``Path``) and
    returns the normalised paths the guard recognises as tests. A repair is only
    judged against files the run was actually editing; a test file the run never
    touched cannot have been weakened by it, and including it would make an
    unrelated edit look like a repair failure.
    """
    paths: list[str] = []
    for changed in changed_paths:
        candidate: Any = changed
        if isinstance(changed, Mapping):
            candidate = changed.get("path") or changed.get("file_path") or changed.get("file") or ""
        if isinstance(candidate, Path):
            candidate = str(candidate)
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        normalized = _normalize(candidate)
        if _GUARD.is_test_file(normalized):
            paths.append(normalized)
    return tuple(dict.fromkeys(paths))
