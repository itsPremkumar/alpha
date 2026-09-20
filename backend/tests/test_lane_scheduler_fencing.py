"""Unit tests for the Multi-Lane Execution Scheduler and Writer Lease Fence."""

from __future__ import annotations

import pytest

from alpha.runtime.lane_scheduler import (
    ExecutionLane,
    FenceContentionError,
    LaneScheduler,
    WriterFence,
)


def test_writer_fence_exclusive_lease():
    fence = WriterFence(default_lease_seconds=5.0)
    thread_id = "thread-fence-1"

    token1 = fence.acquire(thread_id, owner="runner-1")
    assert token1.is_valid is True
    assert fence.is_locked(thread_id) is True

    # Immediate second acquire should raise FenceContentionError
    with pytest.raises(FenceContentionError):
        fence.acquire(thread_id, owner="runner-2", timeout=0.0)

    # Release token1
    fence.release(token1)
    assert token1.is_valid is False
    assert fence.is_locked(thread_id) is False

    # Now runner-2 can acquire
    token2 = fence.acquire(thread_id, owner="runner-2")
    assert token2.owner == "runner-2"
    fence.release(token2)


def test_lane_scheduler_admission_and_queue():
    scheduler = LaneScheduler(
        max_background_concurrency=2,
        max_interactive_concurrency=5,
        max_background_queue=10,
    )

    # First two background tasks admitted immediately
    admitted1, t1 = scheduler.admit_or_queue("t-1", ExecutionLane.BACKGROUND_AUTONOMOUS)
    admitted2, t2 = scheduler.admit_or_queue("t-2", ExecutionLane.BACKGROUND_AUTONOMOUS)
    assert admitted1 is True
    assert admitted2 is True

    # Third background task queued
    admitted3, t3 = scheduler.admit_or_queue("t-3", ExecutionLane.BACKGROUND_AUTONOMOUS, payload="cron_job")
    assert admitted3 is False
    stats = scheduler.get_lane_stats()
    assert stats["active"]["background_autonomous"] == 2
    assert stats["queued_background"] == 1

    # Release one task -> popped next task
    next_task = scheduler.release_task(ExecutionLane.BACKGROUND_AUTONOMOUS)
    assert next_task is not None
    assert next_task.task_id == t3
    assert next_task.payload == "cron_job"
    stats2 = scheduler.get_lane_stats()
    assert stats2["queued_background"] == 0


def test_lane_scheduler_queue_overflow():
    scheduler = LaneScheduler(
        max_background_concurrency=1,
        max_background_queue=2,
    )

    # 1 active
    admitted, _ = scheduler.admit_or_queue("t-1", ExecutionLane.BACKGROUND_AUTONOMOUS)
    assert admitted is True

    # 2 queued (reaches capacity)
    scheduler.admit_or_queue("t-2", ExecutionLane.BACKGROUND_AUTONOMOUS)
    scheduler.admit_or_queue("t-3", ExecutionLane.BACKGROUND_AUTONOMOUS)

    # Next one must raise OverflowError
    with pytest.raises(OverflowError, match="queue is at capacity"):
        scheduler.admit_or_queue("t-overflow", ExecutionLane.BACKGROUND_AUTONOMOUS)


def test_writer_fence_none_token_release():
    fence = WriterFence()
    # Releasing None should be a safe no-op without raising
    fence.release(None)
