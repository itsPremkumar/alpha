
from alpha.runtime.checkpoint import (
    CheckpointEngine,
    CheckpointStrategy,
)


def test_full_checkpoint_and_integrity_verification():
    engine = CheckpointEngine()
    state = {"stage": "init", "files_modified": ["app.py"], "tokens": 1500}

    cp = engine.create_checkpoint(
        workflow_id="wf_101",
        step=1,
        state=state,
        strategy=CheckpointStrategy.FULL,
    )

    assert cp.verify_integrity() is True
    assert cp.strategy == CheckpointStrategy.FULL

    restored = engine.restore_checkpoint(cp.checkpoint_id)
    assert restored == state


def test_delta_checkpoint_chain_reconstruction():
    engine = CheckpointEngine()

    # Step 1: Root snapshot
    root_state = {"a": 1, "b": 2, "c": 3}
    cp1 = engine.create_checkpoint(
        workflow_id="wf_delta",
        step=1,
        state=root_state,
        strategy=CheckpointStrategy.FULL,
    )

    # Step 2: Delta snapshot (only "b" changed to 20, "d" added)
    step2_state = {"a": 1, "b": 20, "c": 3, "d": 4}
    cp2 = engine.create_checkpoint(
        workflow_id="wf_delta",
        step=2,
        state=step2_state,
        strategy=CheckpointStrategy.DELTA,
        parent_id=cp1.checkpoint_id,
    )

    # In delta strategy, only modified/added keys are stored in payload
    assert "b" in cp2.state_payload
    assert "d" in cp2.state_payload
    assert "a" not in cp2.state_payload  # unmodified

    # Restoring cp2 should traverse the parent chain and fully reconstruct step2_state!
    restored2 = engine.restore_checkpoint(cp2.checkpoint_id)
    assert restored2 == step2_state
    assert restored2["b"] == 20
    assert restored2["d"] == 4
    assert restored2["a"] == 1
