"""``wave_dispatched`` must report a measured ``peak_in_flight``, not a constant.

``DynamicWorkflowEngine._record_wave`` declared ``peak_in_flight: int = 0`` and
NEITHER call site passed it, so every wave in every run journaled 0 - including
waves that genuinely ran wide. Nothing measured it either: the engine sizes a
wave's pool from ``ConcurrencyGovernor.limit`` but never *acquires* the
governor, so the slots that track real overlap were never taken.

Observed live before this fix (4 independent nodes, disjoint write scopes,
``policies.max_concurrency: 4``, all four succeeded in one wave)::

    "wave_metrics": [{"wave_index": 1, "nodes": ["p1","p2","p3","p4"],
                      "node_count": 4, "concurrency": 4,
                      "elapsed_seconds": 0.406, "peak_in_flight": 0}]

A field named ``peak_in_flight`` reading 0 for a 4-wide wave is a false
measurement, not an absent one. ``observability`` already projects
``payload.get("peak_in_flight")`` verbatim, so the lie was entirely in the
producer.

The fix measures real overlap around each per-node call (``WaveOverlap``). It is
measurement only - it never blocks and never admits, because the documented
contract of the governor here is that it bounds CONCURRENCY and not wave
membership; gating admission on an in-flight count would admit exactly
``limit`` nodes and silently serialise a three-node wave into three steps.
"""

from __future__ import annotations

import threading
import time

import pytest

from alpha.workflow.runtime import WaveOverlap


def test_probe_counts_overlap_on_a_real_thread_pool() -> None:
    """The probe itself: 4 items on a 4-slot pool must observe real overlap.

    Tested directly because a broken probe makes every engine-level assertion
    below vacuous - it would report 0 for a genuinely concurrent wave for the
    same reason the old hardcoded default did.
    """

    probe = WaveOverlap()
    overlap_seen = 0
    barrier = threading.Barrier(4, timeout=10.0)

    def _work(_item: int) -> None:
        # Every thread must reach the barrier before any may leave, so all four
        # are provably in flight at the same instant. Without this the test could
        # pass on a serial implementation by accident.
        nonlocal overlap_seen
        with probe.track():
            barrier.wait()
            overlap_seen = max(overlap_seen, probe.peak)

    threads = [threading.Thread(target=_work, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15.0)

    assert all(not thread.is_alive() for thread in threads)
    assert overlap_seen == 4
    assert probe.peak == 4
    # Fully drained: the counter must not leak, or a later wave would over-report.
    assert probe._current == 0


def test_probe_reports_one_for_serial_work() -> None:
    """Serial work is a real measurement of 1, not a fabricated 0."""

    probe = WaveOverlap()
    for _ in range(3):
        with probe.track():
            pass

    assert probe.peak == 1


def test_probe_releases_the_slot_when_the_body_raises() -> None:
    """A failing node must not strand its slot and inflate the next wave."""

    probe = WaveOverlap()
    with pytest.raises(ValueError):
        with probe.track():
            msg = "node failed"
            raise ValueError(msg)

    assert probe._current == 0
    with probe.track():
        pass
    assert probe.peak == 1


def test_observe_passes_arguments_and_return_values_through() -> None:
    """`observe` must be transparent: same args in, same result out."""

    probe = WaveOverlap()
    wrapped = probe.observe(lambda value: value * 2)

    assert [wrapped(n) for n in (1, 2, 3)] == [2, 4, 6]
    assert probe.peak >= 1


def test_record_wave_journals_the_measured_peak_not_the_default() -> None:
    """Structural pin: both `_record_wave` call sites must pass a measurement.

    This is a source assertion and therefore WEAKER than the behavioural tests
    above - it proves the keyword is supplied, not that the number is right. It
    exists because the defect was precisely "the keyword was never supplied", and
    the default silently made that omission invisible.
    """

    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "workflow" / "runtime.py").read_text(encoding="utf-8")

    assert "peak_in_flight: int = 0" in source, "the default exists; every caller must override it"
    assert "peak_in_flight=overlap.peak" in source
    assert "peak_in_flight=group_overlap.peak" in source
    # The engine must not pretend to acquire the governor it never uses.
    assert ".slot()" not in source
    assert source.count("peak_in_flight=overlap.peak") == 1
    assert source.count("peak_in_flight=group_overlap.peak") == 1


def test_a_wide_wave_measures_more_than_a_single_node_wave() -> None:
    """End-to-end shape: overlap scales with real concurrency, not with width.

    Runs the probe through the same bounded-pool shape `execute_wave` uses.
    """

    probe = WaveOverlap()
    release = threading.Event()
    started = threading.Semaphore(0)

    def _slow(_item: int) -> None:
        with probe.track():
            started.release()
            release.wait(timeout=10.0)

    threads = [threading.Thread(target=_slow, args=(i,)) for i in range(3)]
    for thread in threads:
        thread.start()
    for _ in range(3):
        assert started.acquire(timeout=10.0)
    time.sleep(0.05)
    observed = probe.peak
    release.set()
    for thread in threads:
        thread.join(timeout=15.0)

    assert observed == 3
