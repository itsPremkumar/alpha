"""Declared write scopes, conflict detection and expiring path leases.

Ownership is **coordination, not isolation** — the plan is explicit that "a
path lease alone is not isolation", so this module exists to refuse overlapping
launches *before* they start rather than to make concurrent writers safe.
Two agents that must not share a working tree are separated by a worktree or
by serialization, never by a claim.

Three rules:

* **Refusal, not repair.** A declared path is normalised and then checked
  against the workspace root; anything escaping is rejected by name
  (:class:`~alpha.completion.errors.PathEscape`) rather than being rewritten
  into a path that happens to fit.
* **Conservative matching.** Two claims conflict on an exact match *or* when
  either is an ancestor directory of the other, because `src/app.py` and
  `src/` are the same file to a writer.
* **Leases expire.** A crashed worker's claim must die on its own, so every
  claim carries ``expires_at`` and an expired claim is swept rather than
  blocking the scope forever. Releasing is an audited transition
  (``ownership.released``), never a silent drop.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from .errors import NotFound, OwnershipConflict, PathEscape
from .models import CompletionScope, OwnershipClaim, TaskNode
from .policy import CompletionSettings


@dataclass(frozen=True)
class OwnershipConflictDetail:
    """One pair of claims that cannot both hold."""

    task_id: str
    owner: str
    path: str
    conflicts_with_task: str
    conflicts_with_path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "owner": self.owner,
            "path": self.path,
            "conflicts_with_task": self.conflicts_with_task,
            "conflicts_with_path": self.conflicts_with_path,
            "reason": self.reason,
        }


def normalize_paths(paths: list[str], settings: CompletionSettings) -> list[str]:
    """Normalise declared paths to workspace-relative POSIX and refuse escapes.

    ``foo/../bar`` becomes ``bar``; ``/etc/passwd`` and ``../../x`` are
    refused. Empty and ``.`` declarations are dropped: a claim on the whole
    workspace is a claim on nothing in particular and would otherwise be read
    as "owns everything".
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        text = str(raw).strip().replace("\\", "/")
        if not text:
            continue
        candidate = text[:-1] if text.endswith("/") and len(text) > 1 else text
        resolved = settings.resolve(candidate)  # raises PathEscape on escape
        rel = settings.relpath(resolved)
        if rel in {".", ""}:
            continue
        cleaned = posixpath.normpath(candidate.replace("\\", "/"))
        if cleaned.startswith(".."):
            raise PathEscape(f"path {raw!r} normalises outside the workspace root", subject=str(raw))
        if rel not in seen:
            seen.add(rel)
            normalized.append(rel)
    return normalized


def _is_ancestor(a: str, b: str) -> bool:
    """True when ``a`` is ``b`` or a directory ancestor of ``b``."""
    if a == b:
        return True
    return b.startswith(a.rstrip("/") + "/")


def conflicts_between(a: str, b: str) -> bool:
    """Whether two normalised paths are the same write surface."""
    return _is_ancestor(a, b) or _is_ancestor(b, a)


def detect_conflicts(
    paths: list[str],
    claims: list[OwnershipClaim],
    *,
    scope_id: str = "",
    exclude_task: str = "",
    now: float = 0.0,
) -> list[OwnershipConflictDetail]:
    """Every live claim ``paths`` would collide with.

    Expired claims are ignored (they are swept separately): a lease nobody
    holds anymore must not refuse new work.
    """
    found: list[OwnershipConflictDetail] = []
    for claim in claims:
        if claim.task_id == exclude_task:
            continue
        if claim.expired(now):
            continue
        for mine in paths:
            for theirs in claim.paths:
                if conflicts_between(mine, theirs):
                    found.append(
                        OwnershipConflictDetail(
                            task_id=exclude_task,
                            owner="",
                            path=mine,
                            conflicts_with_task=claim.task_id,
                            conflicts_with_path=theirs,
                            reason=(f"live claim on {theirs!r} (task {claim.task_id!r}, owner {claim.owner!r}) overlaps {mine!r}; serialise, isolate in a worktree, or release it first"),
                        )
                    )
    return found


def claim_paths(
    scope: CompletionScope,
    task: TaskNode,
    paths: list[str],
    settings: CompletionSettings,
    *,
    now: float,
) -> OwnershipClaim:
    """Claim ``paths`` for ``task``, refusing any live overlap.

    Raises :class:`~alpha.completion.errors.OwnershipConflict` carrying every
    conflicting pair, so a caller learns the whole set at once instead of
    fixing one and rediscovering the next.
    """
    normalized = normalize_paths(paths, settings)
    if not normalized:
        raise PathEscape(f"task {task.task_id!r} declared no usable write scope", subject=task.task_id)
    details = detect_conflicts(normalized, scope.ownership, scope_id=scope.scope_id, exclude_task=task.task_id, now=now)
    if details:
        raise OwnershipConflict(
            f"task {task.task_id!r} cannot claim {normalized}: {details[0].reason}",
            conflicts=[detail.to_dict() for detail in details],
            subject=task.task_id,
        )
    claim = OwnershipClaim(
        task_id=task.task_id,
        owner=task.owner,
        paths=normalized,
        claimed_at=now,
        expires_at=now + max(1.0, settings.ownership_lease_seconds),
    )
    scope.ownership = [existing for existing in scope.ownership if existing.task_id != task.task_id] + [claim]
    task.owns = normalized
    return claim


def renew_claim(scope: CompletionScope, task_id: str, settings: CompletionSettings, *, now: float) -> OwnershipClaim:
    """Extend a live claim's lease; an expired one must be re-acquired."""
    for claim in scope.ownership:
        if claim.task_id == task_id:
            if claim.expired(now):
                raise OwnershipConflict(
                    f"claim for task {task_id!r} expired at {claim.expires_at:g}; re-acquire it rather than silently renewing",
                    subject=task_id,
                )
            claim.expires_at = now + max(1.0, settings.ownership_lease_seconds)
            return claim
    raise NotFound(f"task {task_id!r} holds no claim in scope {scope.scope_id!r}", subject=task_id)


def release_claim(scope: CompletionScope, task_id: str) -> bool:
    """Release a claim (auditable transition). Returns whether one existed."""
    before = len(scope.ownership)
    scope.ownership = [claim for claim in scope.ownership if claim.task_id != task_id]
    task = scope.tasks.get(task_id)
    if task is not None:
        task.owns = []
    return len(scope.ownership) != before


def sweep_expired(scope: CompletionScope, *, now: float) -> list[OwnershipClaim]:
    """Drop every expired claim and return them for the event journal."""
    live = [claim for claim in scope.ownership if not claim.expired(now)]
    expired = [claim for claim in scope.ownership if claim.expired(now)]
    if expired:
        scope.ownership = live
        for claim in expired:
            task = scope.tasks.get(claim.task_id)
            if task is not None:
                task.owns = []
    return expired


__all__ = [
    "OwnershipConflictDetail",
    "claim_paths",
    "conflicts_between",
    "detect_conflicts",
    "normalize_paths",
    "renew_claim",
    "release_claim",
    "sweep_expired",
]
