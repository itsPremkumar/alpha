"""FastAPI Router for Git Shadow Checkpoints and Rollback Operations.

Exposes REST endpoints allowing UI clients (Electron / Next.js) and autonomous operators
to query checkpoints, inspect file diffs, and execute 1-click rollbacks.

**Path confinement**: these routes reach harness functions that read, diff and
**write** host files, and every one of them resolves the caller's ``root_path``
with ``Path(root_path).resolve()`` and no confinement of its own
(``rollback_to_checkpoint`` additionally does ``(root / rel_path).write_text(...)``
after ``target.parent.mkdir(parents=True)``, and ``create_shadow_checkpoint`` runs
``git update-ref`` inside whatever directory it is handed). ``root_path`` is
therefore **server-resolved**: every request is confined to the authenticated
caller's own data bucket, the same "Isolation by Default" rule
(``backend/docs/AUTH_DESIGN.md``) that ``path_utils.resolve_outputs_confined_path``
and ``Paths.user_dir`` already enforce for the other Gateway filesystem routes.
``""`` / ``"."`` means that bucket; any other value must resolve inside it.
The checkpoint registry itself is process-global, so the listing is filtered to the
roots this caller could actually act on.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from alpha.config.paths import get_paths
from alpha.runtime.user_context import get_effective_user_id
from alpha.tools.builtins.code_agentic_core import (
    create_shadow_checkpoint,
    get_all_checkpoints,
    get_checkpoint_diff,
    rollback_to_checkpoint,
)
from alpha.utils.time import coerce_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoints"])

_OUTSIDE_AREA_DETAIL = "Checkpoints are limited to the authenticated user's own data directory"


def _wire_ts(value: object) -> str:
    """A stored epoch float as ISO 8601.

    ``CodeCheckpoint.created_at`` is ``time.time()`` written at creation by
    ``create_shadow_checkpoint`` (code_agentic_core.py:366), so it has no
    "never" state and no epoch-zero sentinel. The registry is process-global
    and in-memory only, so nothing on disk changes either way - the coercion
    exists purely so a client can parse the field as a date, like every other
    Gateway route.
    """
    return coerce_iso(value)


class CheckpointCreateRequest(BaseModel):
    """Payload for creating a checkpoint."""

    label: str = Field(..., description="Descriptive label for the checkpoint")
    root_path: str = Field(".", description="Root directory to checkpoint, relative to (or inside) the caller's own data directory")
    target_files: list[str] | None = Field(None, description="Optional specific file paths to track, relative to root_path")


class CheckpointResponse(BaseModel):
    """The checkpoint shape this plane publishes.

    ``created_at`` is a ``str`` because that is what the routes emit: ISO 8601
    via ``coerce_iso``. It was declared ``float``, which made this the only
    Gateway plane with a *declared* exception to the ISO convention and no
    record of the exception anywhere - the declaration read like an intentional
    contract when it was really the raw ``CodeCheckpoint.created_at`` epoch
    leaking through a schema nobody enforced. ``WorkflowCheckpoint.created_at``
    (runtime/checkpoint/engine.py) has the same float shape but no Gateway
    route reaches it; it is harness API, not this wire.
    """

    checkpoint_id: str
    label: str
    created_at: str
    files_count: int
    test_passed: bool | None = None
    failure_count: int = 0
    git_ref: str | None = None


def _caller_area() -> Path:
    """The only directory tree a checkpoint request may reach."""
    return get_paths().user_dir(get_effective_user_id()).resolve()


def _is_inside_area(candidate: str | Path, area: Path) -> bool:
    try:
        resolved = Path(candidate).resolve()
    except (OSError, ValueError):
        return False
    return resolved == area or resolved.is_relative_to(area)


def _resolve_confined_root(raw: str | None) -> Path:
    """Resolve a client-supplied ``root_path`` inside the caller's own area.

    ``""`` / ``"."`` / ``"./"`` mean the caller's own data directory. A relative
    value is resolved against that directory, so a request can name one of its
    own subdirectories. An absolute value is accepted only when it already points
    inside the area (the UI echoes the ``root_path`` the listing returned).
    Anything else - another user's bucket, a host directory outside
    ``{base_dir}/users/{user_id}``, or a ``..`` escape out of it - is refused.
    """
    area = _caller_area()
    candidate = (raw or "").strip()
    if candidate in {"", ".", "./"}:
        return area
    path = Path(candidate)
    resolved = (path if path.is_absolute() else area / path).resolve()
    if resolved != area and not resolved.is_relative_to(area):
        raise HTTPException(status_code=400, detail=_OUTSIDE_AREA_DETAIL)
    return resolved


def _confined_target_files(root: Path, target_files: list[str] | None) -> list[str] | None:
    """Reject a ``target_files`` entry that would read outside the confined root.

    The harness resolves each entry as ``root / entry``, so an absolute entry or
    one containing ``..`` escapes the confinement the root check just applied.
    """
    if target_files is None:
        return None
    safe: list[str] = []
    for entry in target_files:
        relative = entry.strip()
        if not relative:
            continue
        candidate = Path(relative)
        if candidate.is_absolute() or not (root / candidate).resolve().is_relative_to(root):
            raise HTTPException(status_code=400, detail=_OUTSIDE_AREA_DETAIL)
        safe.append(relative)
    return safe


@router.get("", response_model=list[dict])
def list_checkpoints() -> list[dict[str, Any]]:
    """List the recorded checkpoints this caller can act on, oldest first."""
    # ``_ACTIVE_CHECKPOINTS`` is a process-global registry shared by every
    # authenticated user, so a checkpoint rooted in another area must not be
    # listed here: its label and absolute host root are not this caller's data.
    area = _caller_area()
    return [{**row, "created_at": _wire_ts(row.get("created_at"))} for row in get_all_checkpoints() if _is_inside_area(row.get("root_path") or "", area)]


@router.post("", response_model=dict)
def create_checkpoint(req: CheckpointCreateRequest) -> dict[str, Any]:
    """Create a new lightweight checkpoint capturing the current workspace state."""
    root = _resolve_confined_root(req.root_path)
    target_files = _confined_target_files(root, req.target_files)
    try:
        cp = create_shadow_checkpoint(
            label=req.label,
            root_path=str(root),
            target_files=target_files,
        )
        return {
            "status": "created",
            "checkpoint_id": cp.checkpoint_id,
            "label": cp.label,
            "created_at": _wire_ts(cp.created_at),
            "files_count": len(cp.files_snapshot),
            "git_ref": cp.git_ref,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create checkpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{checkpoint_id}/rollback")
def rollback_checkpoint(checkpoint_id: str, root_path: str = ".") -> dict[str, Any]:
    """Restore the workspace to the specified checkpoint state."""
    root = _resolve_confined_root(root_path)
    res = rollback_to_checkpoint(checkpoint_id=checkpoint_id, root_path=str(root))
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("error", "Rollback failed"))
    return res


@router.get("/{checkpoint_id}/diff")
def preview_checkpoint_diff(checkpoint_id: str, root_path: str = ".") -> dict[str, Any]:
    """Preview file diffs between current workspace files and the checkpoint state."""
    root = _resolve_confined_root(root_path)
    diff = get_checkpoint_diff(checkpoint_id=checkpoint_id, root_path=str(root))
    if "error" in diff:
        raise HTTPException(status_code=404, detail=diff["error"])
    return diff
