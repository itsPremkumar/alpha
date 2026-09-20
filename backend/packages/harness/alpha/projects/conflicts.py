"""Automatic conflict detection for shared projects.

Detects file/branch overlaps between active agents and records requirement
or decision disagreements. Conflicts escalate to the architect/supervisor
and resolve into ADRs — never by silent overwrite.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from alpha.projects.events import get_event_bus

ConflictKind = Literal["file", "task", "requirement", "architecture", "decision"]
ConflictStatus = Literal["open", "resolving", "resolved"]


@dataclass
class Conflict:
    conflict_id: str
    project_id: str
    kind: ConflictKind
    subject: str
    parties: list[str] = field(default_factory=list)
    details: str = ""
    status: ConflictStatus = "open"
    resolution: str | None = None
    resolved_by: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_lock_conflicts(project_id: str, *, lock_manager=None) -> list[Conflict]:
    """Two live locks from different owners covering the same path = conflict."""
    from alpha.projects.locks import get_lock_manager

    manager = lock_manager or get_lock_manager()
    locks = manager.list_locks(project_id)
    out: list[Conflict] = []
    for i, a in enumerate(locks):
        for b in locks[i + 1 :]:
            if a.owner_bot != b.owner_bot and a.scope == b.scope and a.path == b.path:
                out.append(
                    Conflict(
                        conflict_id=f"cf-{uuid.uuid4().hex[:10]}", project_id=project_id, kind="file", subject=f"{a.scope}:{a.path}", parties=[a.owner_bot, b.owner_bot], details=f"{a.owner_bot} ({a.reason}) vs {b.owner_bot} ({b.reason})"
                    )
                )
    return out


def raise_conflict(project_id: str, kind: ConflictKind, subject: str, parties: list[str], details: str = "", *, actor: str = "supervisor") -> Conflict:
    c = Conflict(conflict_id=f"cf-{uuid.uuid4().hex[:10]}", project_id=project_id, kind=kind, subject=subject, parties=[p.lower() for p in parties], details=details)
    get_event_bus(project_id).emit("conflict_detected", actor, {"conflict_id": c.conflict_id, "kind": kind, "subject": subject, "parties": c.parties, "details": details})
    return c


def resolve_conflict(project_id: str, conflict: Conflict, resolution: str, *, resolved_by: str, record_decision: bool = True) -> Conflict:
    conflict.status = "resolved"
    conflict.resolution = resolution
    conflict.resolved_by = resolved_by
    get_event_bus(project_id).emit("conflict_resolved", resolved_by, {"conflict_id": conflict.conflict_id, "resolution": resolution})
    if record_decision:
        from alpha.projects.decisions import get_decision_log

        get_decision_log(project_id).record(f"Conflict {conflict.conflict_id}: {conflict.subject}", resolution, reason=f"Resolved {conflict.kind} conflict between {', '.join(conflict.parties)}", made_by=resolved_by)
    return conflict
