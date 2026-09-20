"""FastAPI Router for Git Shadow Checkpoints and Rollback Operations.

Exposes REST endpoints allowing UI clients (Electron / Next.js) and autonomous operators
to query checkpoints, inspect file diffs, and execute 1-click rollbacks.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from alpha.tools.builtins.code_agentic_core import (
    create_shadow_checkpoint,
    get_all_checkpoints,
    rollback_to_checkpoint,
    get_checkpoint_diff,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoints"])


class CheckpointCreateRequest(BaseModel):
    label: str = Field(..., description="Descriptive label for the checkpoint")
    root_path: str = Field(".", description="Root directory to checkpoint")
    target_files: Optional[List[str]] = Field(None, description="Optional specific file paths to track")


class CheckpointResponse(BaseModel):
    checkpoint_id: str
    label: str
    created_at: float
    files_count: int
    test_passed: Optional[bool] = None
    failure_count: int = 0
    git_ref: Optional[str] = None


@router.get("", response_model=List[dict])
def list_checkpoints():
    """List all recorded checkpoints in chronological order."""
    return get_all_checkpoints()


@router.post("", response_model=dict)
def create_checkpoint(req: CheckpointCreateRequest):
    """Create a new lightweight checkpoint capturing the current workspace state."""
    try:
        cp = create_shadow_checkpoint(
            label=req.label,
            root_path=req.root_path,
            target_files=req.target_files,
        )
        return {
            "status": "created",
            "checkpoint_id": cp.checkpoint_id,
            "label": cp.label,
            "created_at": cp.created_at,
            "files_count": len(cp.files_snapshot),
            "git_ref": cp.git_ref,
        }
    except Exception as e:
        logger.error(f"Failed to create checkpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{checkpoint_id}/rollback")
def rollback_checkpoint(checkpoint_id: str, root_path: str = "."):
    """Restore the workspace to the specified checkpoint state."""
    res = rollback_to_checkpoint(checkpoint_id=checkpoint_id, root_path=root_path)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("error", "Rollback failed"))
    return res


@router.get("/{checkpoint_id}/diff")
def preview_checkpoint_diff(checkpoint_id: str, root_path: str = "."):
    """Preview file diffs between current workspace and the checkpoint state."""
    diff = get_checkpoint_diff(checkpoint_id=checkpoint_id, root_path=root_path)
    if "error" in diff:
        raise HTTPException(status_code=404, detail=diff["error"])
    return diff
