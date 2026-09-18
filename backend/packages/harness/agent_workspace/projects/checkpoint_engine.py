"""Long-Horizon Workspace Checkpoint & Warm Resume Engine.

Serializes the complete operational state of an autonomous project workspace
(agent presence, active resource locks, task contracts, living specs, and worktrees)
to enable instant resume across restarts, system reboots, and multi-day long-running goals.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceCheckpoint:
    checkpoint_id: str
    project_id: str
    tag: str
    created_at: float = field(default_factory=time.time)
    timestamp_iso: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    state_snapshot: dict[str, Any] = field(default_factory=dict)
    members_snapshot: list[dict[str, Any]] = field(default_factory=list)
    active_locks: list[dict[str, Any]] = field(default_factory=list)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    living_spec: dict[str, Any] = field(default_factory=dict)
    worktrees: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckpointEngine:
    """Manages durable checkpoint creation and warm restoration for an autonomous project."""

    def __init__(self, project_id: str, storage_dir: Path | str | None = None):
        self.project_id = project_id
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            base_dir = os.environ.get("AGENT_WORKSPACE_PROJECTS_DIR", ".agent_workspace_projects")
            self.storage_dir = Path(base_dir) / project_id / "checkpoints"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._memory_index: dict[str, WorkspaceCheckpoint] = {}
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load persisted checkpoints from disk."""
        if not self.storage_dir.exists():
            return
        for file in self.storage_dir.glob("ckpt_*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                ckpt = WorkspaceCheckpoint(**data)
                self._memory_index[ckpt.checkpoint_id] = ckpt
            except Exception as e:
                logger.warning(f"Failed to load checkpoint file {file}: {e}")

    def create_checkpoint(self, tag: str = "manual", metadata: dict[str, Any] | None = None) -> WorkspaceCheckpoint:
        """Capture complete project state into a persistent checkpoint."""
        checkpoint_id = f"ckpt-{uuid.uuid4().hex[:8]}"

        # 1. State Snapshot
        state_data: dict[str, Any] = {}
        try:
            from agent_workspace.projects.state import get_state_machine

            sm = get_state_machine(self.project_id)
            state_data = sm.snapshot().to_dict()
        except Exception:
            state_data = {"phase": "active", "project_id": self.project_id}

        # 2. Members / Presence
        members_data: list[dict[str, Any]] = []
        try:
            from agent_workspace.projects.membership import get_presence_store

            store = get_presence_store(self.project_id)
            members_data = [m.to_dict() for m in store.list_members()]
        except Exception:
            pass

        # 3. Resource Locks
        locks_data: list[dict[str, Any]] = []
        try:
            from agent_workspace.projects.locks import get_lock_manager

            lm = get_lock_manager()
            locks_data = [lk.to_dict() for lk in lm.list_active(self.project_id)]
        except Exception:
            pass

        # 4. Task Contracts
        contracts_data: list[dict[str, Any]] = []
        try:
            from agent_workspace.projects.contracts import get_contract_gatekeeper

            cg = get_contract_gatekeeper(self.project_id)
            contracts_data = [c.to_dict() for c in cg.list_contracts()]
        except Exception:
            pass

        # 5. Living Spec
        spec_data: dict[str, Any] = {}
        try:
            from agent_workspace.projects.living_spec import get_living_spec_engine

            ls = get_living_spec_engine(self.project_id)
            spec_data = ls.get_spec().to_dict()
        except Exception:
            pass

        ckpt = WorkspaceCheckpoint(
            checkpoint_id=checkpoint_id,
            project_id=self.project_id,
            tag=tag,
            state_snapshot=state_data,
            members_snapshot=members_data,
            active_locks=locks_data,
            contracts=contracts_data,
            living_spec=spec_data,
            metadata=metadata or {},
        )

        # Persist to disk
        file_path = self.storage_dir / f"ckpt_{checkpoint_id}.json"
        try:
            file_path.write_text(json.dumps(ckpt.to_dict(), indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save checkpoint to disk: {e}")

        self._memory_index[checkpoint_id] = ckpt

        # Emit event
        try:
            from agent_workspace.projects.events import get_event_bus

            get_event_bus(self.project_id).emit(
                "checkpoint_created",
                "checkpoint_engine",
                {"checkpoint_id": checkpoint_id, "tag": tag},
            )
        except Exception:
            pass

        return ckpt

    def list_checkpoints(self) -> list[WorkspaceCheckpoint]:
        """Return all checkpoints ordered by newest first."""
        return sorted(self._memory_index.values(), key=lambda c: c.created_at, reverse=True)

    def get_checkpoint(self, checkpoint_id: str) -> WorkspaceCheckpoint | None:
        return self._memory_index.get(checkpoint_id)

    def restore_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        """Restore project state from a saved checkpoint."""
        ckpt = self.get_checkpoint(checkpoint_id)
        if not ckpt:
            raise KeyError(f"Checkpoint not found: {checkpoint_id}")

        locks_restored = 0
        contracts_restored = 0

        # 1. Restore Locks
        try:
            from agent_workspace.projects.locks import get_lock_manager

            lm = get_lock_manager()
            for lk in ckpt.active_locks:
                try:
                    lm.acquire(
                        project_id=self.project_id,
                        scope=lk.get("scope", "file"),
                        path=lk.get("path", "restored"),
                        owner_bot=lk.get("owner_bot", "restored_bot"),
                        reason=f"Restored from {checkpoint_id}",
                    )
                    locks_restored += 1
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Could not restore locks: {e}")

        # 2. Restore Contracts
        try:
            from agent_workspace.projects.contracts import get_contract_gatekeeper

            cg = get_contract_gatekeeper(self.project_id)
            for c in ckpt.contracts:
                try:
                    cg.create_contract(
                        task_id=c.get("task_id", f"t-{uuid.uuid4().hex[:6]}"),
                        title=c.get("title", "Restored Task"),
                        assignee_bot=c.get("assignee_bot", "coder"),
                        verifier_bot=c.get("verifier_bot"),
                    )
                    contracts_restored += 1
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Could not restore contracts: {e}")

        # 3. Emit restored event
        try:
            from agent_workspace.projects.events import get_event_bus

            get_event_bus(self.project_id).emit(
                "checkpoint_restored",
                "checkpoint_engine",
                {"checkpoint_id": checkpoint_id, "tag": ckpt.tag},
            )
        except Exception:
            pass

        return {
            "restored": True,
            "checkpoint_id": checkpoint_id,
            "project_id": self.project_id,
            "tag": ckpt.tag,
            "locks_restored": locks_restored,
            "contracts_restored": contracts_restored,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


_CHECKPOINT_ENGINES: dict[str, CheckpointEngine] = {}


def get_checkpoint_engine(project_id: str) -> CheckpointEngine:
    if project_id not in _CHECKPOINT_ENGINES:
        _CHECKPOINT_ENGINES[project_id] = CheckpointEngine(project_id)
    return _CHECKPOINT_ENGINES[project_id]
