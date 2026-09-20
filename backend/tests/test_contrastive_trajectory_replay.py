"""Unit tests for Contrastive Trajectory Replay and Negative-Path Memory."""

import pytest

from alpha.memory.contrastive_trajectory_replay import (
    ContrastiveTrajectoryReplay,
    TrajectoryRecord,
    query_contrastive_memory,
    record_trajectory_outcome,
)


def test_trajectory_record_and_persistence(tmp_path):
    storage = tmp_path / "memory.json"
    mem = ContrastiveTrajectoryReplay(persistence_path=storage)

    rec = mem.record_outcome(
        task_id="task_001",
        failure_signature="ZeroDivisionError: division by zero",
        erroneous_hypothesis="Multiply numerator by 10 to prevent zero division",
        failed_patch="return (n * 10) / d",
        winning_resolution="if d == 0: return 0.0; return n / d",
    )

    assert rec.record_id in mem.records
    assert rec.occurrence_count == 1

    # Reload from disk
    mem2 = ContrastiveTrajectoryReplay(persistence_path=storage)
    assert rec.record_id in mem2.records
    assert mem2.records[rec.record_id].winning_resolution == rec.winning_resolution


def test_negative_constraint_injection(tmp_path):
    mem = ContrastiveTrajectoryReplay(persistence_path=tmp_path / "memory.json")

    mem.record_outcome(
        task_id="task_002",
        failure_signature="IndexError: list index out of range",
        erroneous_hypothesis="Hardcode index access to items[0]",
        failed_patch="return items[0]",
        winning_resolution="return items[0] if items else None",
    )

    # Query with relevant keywords
    constraints = mem.query_negative_constraints(
        query="Need to access items from the list safely",
        failure_signature="IndexError: list index out of range",
    )

    assert len(constraints) >= 1
    c = constraints[0]
    assert "NEGATIVE CONSTRAINT" in c["negative_constraint"]
    assert "Hardcode index access" in c["negative_constraint"]
    assert "RECOMMENDED RESOLUTION" in c.get("proven_winning_resolution", "")


def test_cyclic_trap_detector(tmp_path):
    mem = ContrastiveTrajectoryReplay(persistence_path=tmp_path / "memory.json")

    mem.record_outcome(
        task_id="task_003",
        failure_signature="TypeError: unsupported operand type",
        erroneous_hypothesis="Cast string directly to integer",
        failed_patch="x = int(val)",
    )

    # Agent is about to repeat the exact same failed hypothesis
    trap_check = mem.detect_cyclic_trap(
        proposed_hypothesis="Cast string directly to integer",
        proposed_patch="x = int(val)",
    )

    assert trap_check["is_cyclic_trap"] is True
    assert "CYCLIC TRAP DETECTED" in trap_check["warning"]

    # Unrelated hypothesis is not flagged as a trap
    safe_check = mem.detect_cyclic_trap(
        proposed_hypothesis="Use regex validator before parsing integer",
    )
    assert safe_check["is_cyclic_trap"] is False


def test_contrastive_memory_tools():
    # Test record tool
    res_rec = record_trajectory_outcome.invoke({
        "task_id": "session_tool_test",
        "failure_signature": "KeyError: 'auth_token'",
        "erroneous_hypothesis": "Assume auth_token always present in headers dict",
        "failed_patch": "token = headers['auth_token']",
        "winning_resolution": "token = headers.get('auth_token', '')",
    })
    assert res_rec["success"] is True
    assert "record_id" in res_rec["data"]

    # Test query tool
    res_query = query_contrastive_memory.invoke({
        "query": "Missing auth_token header access in request",
        "failure_signature": "KeyError: 'auth_token'",
        "top_k": 3,
    })
    assert res_query["success"] is True
    assert res_query["data"]["matched_constraints_count"] >= 1
    assert len(res_query["data"]["negative_constraints"]) >= 1
