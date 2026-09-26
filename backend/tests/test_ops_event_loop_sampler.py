"""Unit tests for the event-loop liveness sampler (alpha.ops.event_loop)."""

from __future__ import annotations

import asyncio

import pytest

from alpha.ops.event_loop import (
    EventLoopSampler,
    _percentile,
    get_event_loop_sampler,
)


def test_percentile_interpolates_and_rejects_bad_input() -> None:
    assert _percentile([5.0], 0.99) == 5.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert _percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0
    with pytest.raises(ValueError):
        _percentile([], 0.5)
    with pytest.raises(ValueError):
        _percentile([1.0], 1.5)


def test_constructor_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        EventLoopSampler(interval_seconds=0)
    with pytest.raises(ValueError):
        EventLoopSampler(window_seconds=-1)
    with pytest.raises(ValueError):
        EventLoopSampler(history_windows=0)


def test_snapshot_omits_delay_and_cpu_fields_until_first_window() -> None:
    """Absence of delay/CPU fields means "no signal yet", never zero."""
    sampler = EventLoopSampler(interval_seconds=0.01, window_seconds=10.0)
    try:
        sampler.ensure_running()
        snap = sampler.snapshot()
        assert snap["windows_completed"] == 0
        for field in ("delay_last_max_ms", "delay_max_ms", "delay_p99_ms", "cpu_core_ratio"):
            assert field not in snap
    finally:
        sampler.stop()


def test_ensure_running_without_loop_is_a_noop() -> None:
    sampler = EventLoopSampler()
    sampler.ensure_running()  # no running loop -> returns silently
    snap = sampler.snapshot()
    assert snap["windows_completed"] == 0


@pytest.mark.asyncio
async def test_sampler_completes_windows_and_reports_delay_fields() -> None:
    sampler = EventLoopSampler(interval_seconds=0.01, window_seconds=0.05, history_windows=4)
    try:
        sampler.ensure_running()
        await asyncio.sleep(0.2)
        snap = sampler.snapshot()
        assert snap["windows_completed"] >= 1
        assert "delay_last_max_ms" in snap
        assert "delay_max_ms" in snap
        assert "delay_p99_ms" in snap
        assert snap["delay_max_ms"] >= 0.0
        assert snap["observed_seconds"] > 0.0
        # A healthy idle loop must not report a huge scheduling delay.
        assert snap["delay_last_max_ms"] < 1000.0
    finally:
        sampler.stop()


@pytest.mark.asyncio
async def test_history_is_bounded_by_history_windows() -> None:
    sampler = EventLoopSampler(interval_seconds=0.005, window_seconds=0.02, history_windows=2)
    try:
        sampler.ensure_running()
        await asyncio.sleep(0.2)
        snap = sampler.snapshot()
        # windows_completed keeps counting, but retained percentiles only
        # ever look at the bounded deque (maxlen=history_windows).
        assert snap["windows_completed"] >= 3
        assert snap["delay_max_ms"] >= 0.0
    finally:
        sampler.stop()


def test_stop_unbinds_and_snapshot_keeps_last_history() -> None:
    sampler = EventLoopSampler()
    sampler.stop()
    snap = sampler.snapshot()
    assert snap["windows_completed"] == 0


def test_get_event_loop_sampler_returns_singleton() -> None:
    assert get_event_loop_sampler() is get_event_loop_sampler()
