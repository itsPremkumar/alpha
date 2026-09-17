"""Boulder State Machine & Multi-Session Checkpointing.

Persists Sisyphus task progression across session interruptions, rate-limits,
and system restarts:
- Tracks top-level goal, step checklist, session lineage, and timestamps.
- Checkpoints progress to .omo/boulder.json or custom path.
- Enables seamless task resumption without human intervention.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BOULDER_PATH = Path(".deerflow") / "boulder.json"
LEGACY_BOULDER_PATH = Path(".omo") / "boulder.json"


@dataclass
class ChecklistItem:
    item: str
    completed: bool = False
    evidence: str = ""


@dataclass
class BoulderState:
    work_id: str
    top_level_task: str
    checklist: list[ChecklistItem] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    status: str = "in_progress"  # "in_progress", "completed", "failed"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BoulderState:
        checklist = [
            ChecklistItem(
                item=c["item"],
                completed=c.get("completed", False),
                evidence=c.get("evidence", ""),
            )
            for c in data.get("checklist", [])
        ]
        return cls(
            work_id=data["work_id"],
            top_level_task=data["top_level_task"],
            checklist=checklist,
            session_ids=data.get("session_ids", []),
            status=data.get("status", "in_progress"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


def create_boulder(
    task: str,
    checklist: list[str],
    path: Path | None = None,
    session_id: str | None = None,
) -> BoulderState:
    """Create and persist a new Boulder state."""
    state = BoulderState(
        work_id=f"work_{uuid.uuid4().hex[:8]}",
        top_level_task=task,
        checklist=[ChecklistItem(item=item) for item in checklist],
        session_ids=[session_id] if session_id else [],
    )
    save_boulder(state, path)
    return state


def save_boulder(state: BoulderState, path: Path | None = None) -> None:
    """Save BoulderState to JSON checkpoint file."""
    target = path or DEFAULT_BOULDER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    with open(target, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2)


def load_boulder(path: Path | None = None) -> BoulderState | None:
    """Load current active BoulderState if it exists."""
    target = path or DEFAULT_BOULDER_PATH
    if target == DEFAULT_BOULDER_PATH and not target.exists() and LEGACY_BOULDER_PATH.exists():
        target = LEGACY_BOULDER_PATH
    if target.is_dir():
        for candidate in [target / "boulder.json", target / ".deerflow" / "boulder.json", target / ".omo" / "boulder.json", target / ".boulder" / "boulder.json"]:
            if candidate.exists():
                target = candidate
                break
    if not target.exists():
        return None
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        return BoulderState.from_dict(data)
    except Exception:
        return None


def update_checklist_item(
    item_index: int,
    completed: bool,
    evidence: str = "",
    path: Path | None = None,
) -> BoulderState:
    """Mark a checklist item completed with verification evidence."""
    state = load_boulder(path)
    if not state:
        raise RuntimeError("No active Boulder state found to update.")
    if item_index < 0 or item_index >= len(state.checklist):
        raise IndexError(f"Checklist index {item_index} out of range")

    state.checklist[item_index].completed = completed
    if evidence:
        state.checklist[item_index].evidence = evidence

    # If all items are completed, mark boulder completed
    if all(item.completed for item in state.checklist):
        state.status = "completed"

    save_boulder(state, path)
    return state


def append_session_id(session_id: str, path: Path | None = None) -> BoulderState:
    """Record a new session ID into the multi-session chain."""
    state = load_boulder(path)
    if not state:
        raise RuntimeError("No active Boulder state found.")
    if session_id not in state.session_ids:
        state.session_ids.append(session_id)
    save_boulder(state, path)
    return state


def complete_boulder(path: Path | None = None) -> BoulderState:
    """Mark the entire boulder task as successfully completed."""
    state = load_boulder(path)
    if not state:
        raise RuntimeError("No active Boulder state found.")
    state.status = "completed"
    save_boulder(state, path)
    return state


def clear_boulder(path: Path | None = None) -> None:
    """Remove active boulder file upon cleanup."""
    target = path or DEFAULT_BOULDER_PATH
    if target.exists():
        target.unlink()
