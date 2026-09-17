from agent_workspace.orchestration.durable_replay import (
    DurableReplayEngine,
)


def test_durable_replay_idempotency():
    engine = DurableReplayEngine()

    # Append event with idempotency key
    ev1 = engine.append_event(
        task_id="task_abc",
        event_type="TOOL_EXECUTED",
        payload={"result": "created_table"},
        idempotency_key="tx_123",
    )
    assert engine.is_action_completed("tx_123") is True

    # Duplicate call should return the existing event without re-executing
    ev2 = engine.append_event(
        task_id="task_abc",
        event_type="TOOL_EXECUTED",
        payload={"result": "duplicate_attempt"},
        idempotency_key="tx_123",
    )
    assert ev1.event_id == ev2.event_id


def test_durable_replay_crash_and_recovery():
    engine = DurableReplayEngine()
    t_id = "task_long_run"

    # Step 1: Initial event & checkpoint
    engine.append_event(t_id, "TASK_STARTED", {"objective": "process batch"})
    engine.save_checkpoint(
        task_id=t_id,
        step_index=1,
        state="running",
        variables={"processed_count": 50},
    )

    # Step 2: More events after checkpoint
    engine.append_event(
        t_id,
        "TOOL_EXECUTED",
        {"variable_name": "processed_count", "output": 100},
        idempotency_key="batch_part_2",
    )
    engine.append_event(t_id, "STATE_TRANSITION", {"new_state": "verifying"})

    # Simulate crash and recover
    recovery = engine.recover_task(t_id)
    assert recovery["task_id"] == t_id
    assert recovery["recovered_from_checkpoint"] is not None
    assert recovery["active_state"] == "verifying"
    assert recovery["restored_variables"]["processed_count"] == 100
    assert recovery["replayed_events_count"] == 2
