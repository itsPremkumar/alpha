"""Built-in delta_checkpoint tool inspired by hermes-asi-master."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.runtime.checkpoint import CheckpointEngine, CheckpointStrategy

_GLOBAL_CHECKPOINT_ENGINE = CheckpointEngine()


@tool("create_workflow_checkpoint", parse_docstring=True)
def create_workflow_checkpoint(
    workflow_id: str,
    step: int,
    state_json: str,
    strategy: str = "full",
    parent_checkpoint_id: str = "",
) -> str:
    """Create a durable, cryptographic SHA256-verified workflow checkpoint.

    Supports 'full', 'incremental', and 'delta' snapshots with state rollback and historical verification.

    Args:
        workflow_id: Identifier of the running workflow or mission.
        step: Current execution step index.
        state_json: JSON dictionary representing the current active state.
        strategy: 'full', 'incremental', or 'delta'.
        parent_checkpoint_id: Optional parent checkpoint ID for delta compression.
    """
    try:
        state = json.loads(state_json) if state_json else {}
    except Exception:
        state = {}

    strat_map = {
        "full": CheckpointStrategy.FULL,
        "incremental": CheckpointStrategy.INCREMENTAL,
        "delta": CheckpointStrategy.DELTA,
    }
    strat = strat_map.get(strategy.lower().strip(), CheckpointStrategy.FULL)

    cp = _GLOBAL_CHECKPOINT_ENGINE.create_checkpoint(
        workflow_id=workflow_id,
        step=step,
        state=state,
        strategy=strat,
        parent_id=parent_checkpoint_id.strip() or None,
    )

    return json.dumps({
        "status": "created",
        "checkpoint_id": cp.checkpoint_id,
        "workflow_id": cp.workflow_id,
        "step": cp.step,
        "strategy": cp.strategy.value,
        "checksum": cp.checksum,
        "integrity_verified": cp.verify_integrity(),
    }, indent=2)
