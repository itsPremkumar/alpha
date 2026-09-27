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

DEFAULT_BOULDER_PATH = Path(".alpha") / "boulder.json"
LEGACY_BOULDER_PATH = Path(".omo") / "boulder.json"


def _replace_atomically(target: Path, payload: str) -> None:
    """Write ``payload`` to ``target`` so no reader ever sees a partial document.

    The caller has already serialized the document in full, so the only
    operations left are creating a sibling staging file and renaming it over the
    target. ``Path.replace`` is atomic on the same filesystem, so on any failure
    the target keeps whatever it held before and only the staging file is
    discarded. Same contract as ``alpha.state.handoff._replace_atomically`` and
    ``alpha.projects.handoffs.HandoffStore._save``.
    """
    staging = target.with_suffix(f"{target.suffix}.tmp")
    try:
        staging.write_text(payload, encoding="utf-8")
        staging.replace(target)
    except BaseException:
        # the target is untouched until replace() runs, so drop the staging file
        # rather than leave half-written debris for the next save to trip over
        staging.unlink(missing_ok=True)
        raise


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
    """Save BoulderState to JSON checkpoint file.

    The document is serialized in full before any file is opened, then written
    through a sibling staging file that atomically replaces the target. Opening
    the target with mode ``"w"`` truncates it immediately while ``json.dump``
    only streams afterwards, so a serialization failure or an interrupted write
    destroyed the checkpoint already on disk -- and because ``load_boulder``
    swallows the decode error and returns ``None``, that made the whole task
    progression unreachable rather than merely degraded.
    """
    target = path or DEFAULT_BOULDER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    payload = json.dumps(state.to_dict(), indent=2)
    _replace_atomically(target, payload)


def load_boulder(path: Path | None = None) -> BoulderState | None:
    """Load current active BoulderState if it exists."""
    target = path or DEFAULT_BOULDER_PATH
    if target == DEFAULT_BOULDER_PATH and not target.exists() and LEGACY_BOULDER_PATH.exists():
        target = LEGACY_BOULDER_PATH
    if target.is_dir():
        for candidate in [target / "boulder.json", target / ".alpha" / "boulder.json", target / ".omo" / "boulder.json", target / ".boulder" / "boulder.json"]:
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
