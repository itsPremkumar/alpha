from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckpointStrategy(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    DELTA = "delta"


@dataclass
class WorkflowCheckpoint:
    """A durable, cryptographic checkpoint of workflow state."""
    checkpoint_id: str
    workflow_id: str
    step: int
    state_payload: dict[str, Any]
    parent_id: str | None = None
    strategy: CheckpointStrategy = CheckpointStrategy.FULL
    checksum: str = ""
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.checksum:
            self.checksum = self.compute_checksum()

    def compute_checksum(self) -> str:
        s = json.dumps(self.state_payload, sort_keys=True, default=str)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

    def verify_integrity(self) -> bool:
        return self.checksum == self.compute_checksum()

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "workflow_id": self.workflow_id,
            "step": self.step,
            "state_payload": self.state_payload,
            "parent_id": self.parent_id,
            "strategy": self.strategy.value,
            "checksum": self.checksum,
            "created_at": self.created_at,
        }


class CheckpointEngine:
    """
    Long-Horizon Checkpoint Engine: Supports Full, Incremental, and Delta
    snapshots with SHA256 integrity validation and historical rollback.
    """

    def __init__(self) -> None:
        self.checkpoints: dict[str, WorkflowCheckpoint] = {}
        self.workflow_index: dict[str, list[str]] = {}  # workflow_id -> [checkpoint_ids]

    def create_checkpoint(
        self,
        workflow_id: str,
        step: int,
        state: dict[str, Any],
        strategy: CheckpointStrategy = CheckpointStrategy.FULL,
        parent_id: str | None = None,
    ) -> WorkflowCheckpoint:
        cid = f"chk_{uuid.uuid4().hex[:10]}"
        payload = copy.deepcopy(state)

        if strategy == CheckpointStrategy.DELTA and parent_id and parent_id in self.checkpoints:
            parent_state = self.checkpoints[parent_id].state_payload
            # Store only key-value differences from parent
            delta = {
                k: v for k, v in payload.items()
                if k not in parent_state or parent_state[k] != v
            }
            payload = delta

        cp = WorkflowCheckpoint(
            checkpoint_id=cid,
            workflow_id=workflow_id,
            step=step,
            state_payload=payload,
            parent_id=parent_id,
            strategy=strategy,
        )

        self.checkpoints[cid] = cp
        if workflow_id not in self.workflow_index:
            self.workflow_index[workflow_id] = []
        self.workflow_index[workflow_id].append(cid)
        return cp

    def restore_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        """Restores state. If delta encoded, traverses parent chain to reconstruct."""
        cp = self.checkpoints.get(checkpoint_id)
        if not cp or not cp.verify_integrity():
            return None

        if cp.strategy == CheckpointStrategy.FULL:
            return copy.deepcopy(cp.state_payload)

        # Delta reconstruction: collect path from root to target
        chain = []
        curr: WorkflowCheckpoint | None = cp
        while curr:
            chain.append(curr)
            curr = self.checkpoints.get(curr.parent_id) if curr.parent_id else None

        reconstructed: dict[str, Any] = {}
        for node in reversed(chain):
            reconstructed.update(node.state_payload)

        return reconstructed

    def get_history(self, workflow_id: str) -> list[WorkflowCheckpoint]:
        cids = self.workflow_index.get(workflow_id, [])
        return [self.checkpoints[cid] for cid in cids if cid in self.checkpoints]
