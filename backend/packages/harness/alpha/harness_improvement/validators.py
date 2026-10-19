"""Deterministic deliverable validators (guide §11.1 layer 1).

A validator answers exactly one question and answers it in three states:
``PASSED``, ``FAILED`` or ``NOT_RUN``. The third is not a pass. When a file
cannot be read, a check that never ran and a check that ran and succeeded must
not collapse into the same value — that collapse is how "we did not look" turns
into "we verified nothing is wrong".

Two safety properties are enforced here rather than trusted from callers:

* **Paths are confined by refusal, not repair.** A check path that is absolute,
  drive-qualified, contains traversal, or resolves through a symlink outside the
  workspace is a ``FAILED`` check with ``path_escape`` as its detail, never a
  silently clamped path.
* **A missing workspace is ``NOT_RUN`` for every check**, with the reason
  recorded once, so a probe that never materialized cannot read as "all checks
  passed on an empty directory".
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from alpha.harness_improvement.models import DeliverableCheck, ValidatorResult, ValidatorStatus

#: Check kinds this module implements. Anything else is NOT_RUN, because an
#: unknown check kind that silently passes is worse than a missing check.
CHECK_KINDS: frozenset[str] = frozenset({"exists", "parses_json", "min_bytes", "required_sections", "text_contains"})


def confined_path(workspace: Path, raw: str) -> tuple[Path | None, str | None]:
    """Resolve *raw* under *workspace*, or return the refusal reason.

    Returns ``(path, None)`` when the path is inside the workspace and
    ``(None, reason)`` otherwise. The check is done on the *resolved* path with
    ``strict=False`` so a symlink that points outside the workspace is caught
    before the file is opened.
    """
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.drive or raw.startswith("~") or raw.startswith("\\\\"):
        return None, f"path {raw!r} is absolute or drive-qualified; only workspace-relative paths are accepted"
    if ".." in candidate.parts:
        return None, f"path {raw!r} contains traversal segments"
    if "\\" in raw:
        return None, f"path {raw!r} uses backslashes; use forward slashes for cross-platform checks"
    try:
        workspace_resolved = workspace.resolve()
        resolved = (workspace_resolved / candidate).resolve()
    except OSError as exc:
        return None, f"path {raw!r} could not be resolved: {exc}"
    if workspace_resolved != resolved and workspace_resolved not in resolved.parents:
        return None, f"path {raw!r} resolves outside the probe workspace"
    return resolved, None


def _run_one(check: DeliverableCheck, workspace: Path) -> ValidatorResult:
    if check.kind not in CHECK_KINDS:
        return ValidatorResult(name=check.name, status=ValidatorStatus.NOT_RUN, detail=f"unknown check kind {check.kind!r}", kind=check.kind)
    path, refusal = confined_path(workspace, check.path)
    if path is None:
        return ValidatorResult(name=check.name, status=ValidatorStatus.FAILED, detail=f"path_escape: {refusal}", kind=check.kind)

    if check.kind == "exists":
        if path.is_file():
            return ValidatorResult(name=check.name, status=ValidatorStatus.PASSED, detail=f"{check.path} exists", kind=check.kind)
        return ValidatorResult(name=check.name, status=ValidatorStatus.FAILED, detail=f"{check.path} does not exist", kind=check.kind)

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ValidatorResult(name=check.name, status=ValidatorStatus.FAILED, detail=f"{check.path} does not exist", kind=check.kind)
    except OSError as exc:
        return ValidatorResult(name=check.name, status=ValidatorStatus.NOT_RUN, detail=f"{check.path} could not be read: {exc}", kind=check.kind)

    if check.kind == "min_bytes":
        if len(raw) >= max(1, check.min_bytes):
            return ValidatorResult(name=check.name, status=ValidatorStatus.PASSED, detail=f"{len(raw)} bytes >= {check.min_bytes}", kind=check.kind)
        return ValidatorResult(name=check.name, status=ValidatorStatus.FAILED, detail=f"{len(raw)} bytes < required {check.min_bytes}", kind=check.kind)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ValidatorResult(name=check.name, status=ValidatorStatus.FAILED, detail=f"{check.path} is not valid UTF-8: {exc}", kind=check.kind)

    if check.kind == "parses_json":
        try:
            json.loads(text)
        except ValueError as exc:
            return ValidatorResult(name=check.name, status=ValidatorStatus.FAILED, detail=f"{check.path} is not valid JSON: {exc}", kind=check.kind)
        return ValidatorResult(name=check.name, status=ValidatorStatus.PASSED, detail=f"{check.path} parses as JSON", kind=check.kind)

    if check.kind == "required_sections":
        missing = [section for section in check.sections if not _has_section(text, section)]
        if missing:
            return ValidatorResult(name=check.name, status=ValidatorStatus.FAILED, detail=f"{check.path} is missing sections: {', '.join(missing)}", kind=check.kind)
        return ValidatorResult(name=check.name, status=ValidatorStatus.PASSED, detail=f"{check.path} contains every required section", kind=check.kind)

    # text_contains: `detail` carries the literal needle.
    if check.detail and check.detail in text:
        return ValidatorResult(name=check.name, status=ValidatorStatus.PASSED, detail=f"{check.path} contains the required text", kind=check.kind)
    return ValidatorResult(name=check.name, status=ValidatorStatus.FAILED, detail=f"{check.path} does not contain the required text", kind=check.kind)


def _has_section(text: str, section: str) -> bool:
    """True when a Markdown ATX heading contains *section* (case-insensitive).

    Uses the same outline discipline as the repo's upload extractor: 3–6
    hashes, a space/tab separator, ≤3 leading spaces, fenced code skipped.
    """
    needle = section.strip().lower()
    if not needle:
        return False
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        candidate = line.lstrip(" \t")
        if len(line) - len(candidate) > 3:
            continue
        hashes = 0
        while hashes < len(candidate) and candidate[hashes] == "#":
            hashes += 1
        if not 3 <= hashes <= 6:
            continue
        rest = candidate[hashes:]
        if not rest or rest[0] not in " \t":
            continue
        heading = rest.strip().strip("#").strip().lower()
        if needle in heading:
            return True
    return False


def run_deliverable_checks(checks: Iterable[DeliverableCheck], workspace: Path | None) -> tuple[ValidatorResult, ...]:
    """Run every check against *workspace*.

    ``workspace`` being ``None`` (or not existing) means the probe never
    materialized a tree: every check is ``NOT_RUN`` with the reason attached,
    never a silent pass.
    """
    check_list = tuple(checks)
    if workspace is None:
        return tuple(ValidatorResult(name=c.name, status=ValidatorStatus.NOT_RUN, detail="no probe workspace was provided to the validator run", kind=c.kind) for c in check_list)
    if not workspace.exists() or not workspace.is_dir():
        return tuple(ValidatorResult(name=c.name, status=ValidatorStatus.NOT_RUN, detail=f"probe workspace {workspace} does not exist", kind=c.kind) for c in check_list)
    return tuple(_run_one(check, workspace) for check in check_list)


def validator_summary(results: Iterable[ValidatorResult]) -> dict[str, int]:
    """Counts by status. ``not_run`` is reported separately, never as passed."""
    summary = {"passed": 0, "failed": 0, "not_run": 0}
    for result in results:
        summary[str(result.status)] += 1
    return summary
