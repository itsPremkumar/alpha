"""Sub-Agent Resilience Engine: Checkpointing, Hot-Replacement & Orphan Adoption.

Guarantees operational resilience for autonomous subagents:
- Step-level checkpointing preserving intermediate artifacts, evidence, and next steps.
- Hot-replacement engine that spawns a successor worker resuming directly from the checkpoint.
- Parent-failure recovery: when a parent crashes, active children enter the Orphan Adoption
  Queue and are reattached to the Organization Supervisor or replacement parent without lost work.
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

from alpha.subagents.lifecycle import (
    SubagentLifecycleManager,
    SubagentRecord,
    SubagentStatusEnum,
    get_subagent_lifecycle_manager,
)

logger = logging.getLogger(__name__)

_GLOBAL_RESILIENCE_ENGINE: SubagentResilienceEngine | None = None


@dataclass
class SubagentCheckpoint:
    checkpoint_id: str
    subagent_id: str
    step_index: int
    intermediate_artifacts: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    next_action: str = ""
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubagentCheckpoint:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def get_subagent_resilience_engine(
    lifecycle_manager: SubagentLifecycleManager | None = None,
    storage_dir: Path | str | None = None,
) -> SubagentResilienceEngine:
    """Returns singleton instance of SubagentResilienceEngine."""
    global _GLOBAL_RESILIENCE_ENGINE
    if _GLOBAL_RESILIENCE_ENGINE is None:
        mgr = lifecycle_manager or get_subagent_lifecycle_manager()
        _GLOBAL_RESILIENCE_ENGINE = SubagentResilienceEngine(lifecycle_manager=mgr, storage_dir=storage_dir)
    return _GLOBAL_RESILIENCE_ENGINE


class SubagentResilienceEngine:
    """Manages checkpoints, hot-replacement, and orphan adoption."""

    def __init__(
        self,
        lifecycle_manager: SubagentLifecycleManager,
        storage_dir: Path | str | None = None,
    ):
        self.lifecycle = lifecycle_manager
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            base = os.environ.get("AGENT_WORKSPACE_HOME", "~/.agent-workspace")
            self.storage_dir = Path(os.path.expanduser(base)) / "subagents" / "checkpoints"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoints: dict[str, list[SubagentCheckpoint]] = {}
        self._load_checkpoints()

    def _load_checkpoints(self) -> None:
        if not self.storage_dir.exists():
            return
        for f in self.storage_dir.glob("*.json"):
            try:
                sid = f.stem
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                    if isinstance(data, list):
                        self._checkpoints[sid] = [SubagentCheckpoint.from_dict(item) for item in data]
            except Exception as exc:
                logger.warning(f"Failed to load checkpoint file {f}: {exc}")

    def save_checkpoint(
        self,
        subagent_id: str,
        step_index: int,
        intermediate_artifacts: list[str] | None = None,
        decisions: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        next_action: str = "",
    ) -> SubagentCheckpoint:
        """Persists a step checkpoint for a subagent."""
        cp = SubagentCheckpoint(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:6]}",
            subagent_id=subagent_id,
            step_index=step_index,
            intermediate_artifacts=intermediate_artifacts or [],
            decisions=decisions or [],
            evidence=evidence or [],
            next_action=next_action,
        )

        if subagent_id not in self._checkpoints:
            self._checkpoints[subagent_id] = []
        self._checkpoints[subagent_id].append(cp)

        # Persist to disk
        target = self.storage_dir / f"{subagent_id}.json"
        try:
            with open(target, "w", encoding="utf-8") as fp:
                json.dump([c.to_dict() for c in self._checkpoints[subagent_id]], fp, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to save checkpoint for {subagent_id}: {exc}")

        logger.info(f"Checkpoint saved for subagent '{subagent_id}' at step {step_index}")
        return cp

    def get_latest_checkpoint(self, subagent_id: str) -> SubagentCheckpoint | None:
        cps = self._checkpoints.get(subagent_id, [])
        return cps[-1] if cps else None

    def hot_replace_subagent(self, failed_subagent_id: str, reason: str = "") -> SubagentRecord:
        """Spawns a replacement subagent restoring the latest checkpoint from the failed worker."""
        old_rec = self.lifecycle.get_subagent(failed_subagent_id)
        if not old_rec:
            raise ValueError(f"Cannot replace unknown subagent '{failed_subagent_id}'.")

        # 1. Fetch latest checkpoint
        latest_cp = self.get_latest_checkpoint(failed_subagent_id)

        # 2. Mark old worker as failed/replaced
        self.lifecycle.fail_subagent(failed_subagent_id, f"Hot-replaced: {reason or 'Stalled/Failed'}")

        # 3. Create modified contract with restored state
        new_contract = old_rec.contract
        if latest_cp:
            new_contract.injected_context["restored_checkpoint"] = {
                "checkpoint_id": latest_cp.checkpoint_id,
                "step_index": latest_cp.step_index,
                "intermediate_artifacts": latest_cp.intermediate_artifacts,
                "evidence": latest_cp.evidence,
                "next_action": latest_cp.next_action,
            }

        # 4. Spawn replacement subagent under same parent
        replacement = self.lifecycle.spawn_subagent(
            parent_agent_id=old_rec.parent_agent_id,
            contract=new_contract,
            parent_task_id=old_rec.parent_task_id,
            depth=old_rec.depth,
        )

        logger.info(f"Hot-replacement successful: spawned '{replacement.subagent_id}' to replace '{failed_subagent_id}' (resuming from step {latest_cp.step_index if latest_cp else 0}).")
        return replacement

    def handle_parent_failure(self, dead_parent_id: str) -> dict[str, Any]:
        """Scans active children of a dead parent; adopts or preserves them per survival policy."""
        children = self.lifecycle.list_subagents(parent_id=dead_parent_id)
        orphaned: list[str] = []
        cancelled: list[str] = []

        for ch in children:
            if ch.status in (SubagentStatusEnum.RUNNING, SubagentStatusEnum.READY, SubagentStatusEnum.WAITING):
                policy = ch.contract.survival_policy
                if policy == "cancel_on_parent_failure":
                    self.lifecycle.cancel_subagent(ch.subagent_id, reason=f"Parent '{dead_parent_id}' died")
                    cancelled.append(ch.subagent_id)
                else:
                    # Mark orphaned for adoption
                    ch.is_orphaned = True
                    self.lifecycle.checkpoint_to_disk(ch.subagent_id)
                    orphaned.append(ch.subagent_id)

        logger.info(f"Parent failure reconciliation for '{dead_parent_id}': {len(orphaned)} orphaned, {len(cancelled)} cancelled.")
        return {
            "parent_agent_id": dead_parent_id,
            "orphaned_subagents": orphaned,
            "cancelled_subagents": cancelled,
        }

    def adopt_orphaned_subagents(
        self,
        new_parent_id: str,
        specific_subagent_ids: list[str] | None = None,
    ) -> list[str]:
        """Transfers orphaned subagents to a new parent agent (e.g. Org Supervisor)."""
        adopted: list[str] = []
        all_recs = self.lifecycle.list_subagents()

        for rec in all_recs:
            if rec.is_orphaned and rec.status in (SubagentStatusEnum.RUNNING, SubagentStatusEnum.READY, SubagentStatusEnum.WAITING):
                if specific_subagent_ids is None or rec.subagent_id in specific_subagent_ids:
                    rec.parent_agent_id = new_parent_id
                    rec.is_orphaned = False
                    self.lifecycle.renew_lease(rec.subagent_id)
                    self.lifecycle.checkpoint_to_disk(rec.subagent_id)
                    adopted.append(rec.subagent_id)

        logger.info(f"New parent '{new_parent_id}' successfully adopted {len(adopted)} orphaned subagents.")
        return adopted

    def list_orphans(self) -> list[SubagentRecord]:
        """Returns all currently orphaned active subagents awaiting adoption."""
        return [r for r in self.lifecycle.list_subagents() if r.is_orphaned and r.status in (SubagentStatusEnum.RUNNING, SubagentStatusEnum.READY, SubagentStatusEnum.WAITING)]
