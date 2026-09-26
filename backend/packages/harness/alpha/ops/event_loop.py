"""Event-loop liveness sampler for the Gateway process.

Ports the observation model OpenClaw documents for its Gateway health sampler
onto asyncio:

* the ticker schedules itself with ``loop.call_at`` on absolute expected
  instants, so a stalled loop surfaces as scheduling delay instead of being
  hidden by self-rescheduling ``call_later`` drift;
* delay accumulates into fixed windows; each completed window records the
  maximum scheduling delay seen inside it. Percentiles below therefore
  describe **window maxima**, not the raw sampled delay distribution;
* whole-process CPU comes from ``time.process_time()`` over the same window
  and is reported in core equivalents: ``1.0`` means one CPU core fully
  occupied over the interval, threaded work can exceed ``1.0``, and it is
  not a percentage of the host's total capacity.

Honesty rules:

* :meth:`EventLoopSampler.snapshot` omits every delay/CPU field until the
  first window completes. A missing field means "no signal yet", never zero.
* One sampler instance binds to one running event loop; binding to a
  different loop (test loops, multi-loop embedders) cancels the previous
  ticker generation and resets the accumulated state.

The sampler is passive: importing this module never schedules work. Ops
surfaces call ``ensure_running()`` before reading a snapshot, so the ticker
only runs once somebody is actually observing.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

DEFAULT_INTERVAL_SECONDS = 0.05
DEFAULT_WINDOW_SECONDS = 1.0
DEFAULT_HISTORY_WINDOWS = 60


def _percentile(values: list[float], quantile: float) -> float:
    """Linear-interpolated percentile of a non-empty sequence."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be within [0, 1]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


class EventLoopSampler:
    """Samples event-loop scheduling delay and process CPU in windows."""

    def __init__(
        self,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        history_windows: int = DEFAULT_HISTORY_WINDOWS,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if history_windows < 1:
            raise ValueError("history_windows must be at least 1")
        self._interval_seconds = float(interval_seconds)
        self._window_seconds = float(window_seconds)
        self._history: deque[dict[str, float]] = deque(maxlen=history_windows)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handle: asyncio.Handle | None = None
        self._generation = 0
        self._reset_accumulators()

    # -- lifecycle ---------------------------------------------------

    def ensure_running(self) -> None:
        """Bind (or rebind) the sampler to the current running event loop.

        A no-op when already bound to this loop with a live ticker. Binding
        to a new loop cancels the previous generation and resets state.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if loop is self._loop and self._handle is not None:
            return
        if self._handle is not None:
            self._handle.cancel()
        self._generation += 1
        self._loop = loop
        self._reset_accumulators()
        now = time.monotonic()
        self._window_start = now
        self._window_deadline = now + self._window_seconds
        self._cpu_base = time.process_time()
        self._next_expected = now + self._interval_seconds
        self._schedule()

    def stop(self) -> None:
        """Cancel the ticker and unbind the sampler (tests/embedders)."""
        if self._handle is not None:
            self._handle.cancel()
        self._handle = None
        self._loop = None
        self._generation += 1

    # -- observation -------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return the liveness snapshot; delay/CPU fields appear only after
        the first completed window ("no signal" is omission, not zero)."""
        snap: dict[str, Any] = {
            "interval_seconds": self._interval_seconds,
            "window_seconds": self._window_seconds,
            "windows_completed": self._windows_completed,
            "observed_seconds": round(self._observed_seconds, 3),
        }
        if not self._history:
            return snap
        maxima = [window["max_delay_ms"] for window in self._history]
        last = self._history[-1]
        snap["delay_last_max_ms"] = round(last["max_delay_ms"], 3)
        snap["delay_max_ms"] = round(max(maxima), 3)
        snap["delay_p99_ms"] = round(_percentile(maxima, 0.99), 3)
        if last["cpu_ratio"] is not None:
            snap["cpu_core_ratio"] = round(last["cpu_ratio"], 4)
        return snap

    # -- internals ---------------------------------------------------

    def _reset_accumulators(self) -> None:
        self._history.clear()
        self._windows_completed = 0
        self._observed_seconds = 0.0
        self._window_max_delay_ms = 0.0
        self._window_start = 0.0
        self._window_deadline = 0.0
        self._cpu_base = 0.0
        self._next_expected = 0.0

    def _schedule(self) -> None:
        loop = self._loop
        if loop is None:
            return
        generation = self._generation
        try:
            self._handle = loop.call_at(self._next_expected, self._tick, generation)
        except RuntimeError:
            # Loop is closed/closing: drop the ticker instead of raising
            # inside a callback the runtime may still deliver.
            self._handle = None

    def _tick(self, generation: int) -> None:
        if generation != self._generation or self._loop is None:
            return
        now = time.monotonic()
        delay_ms = max(0.0, (now - self._next_expected) * 1000.0)
        if delay_ms > self._window_max_delay_ms:
            self._window_max_delay_ms = delay_ms
        self._next_expected += self._interval_seconds
        if self._next_expected <= now:
            # A long stall left the schedule in the past; the stall itself is
            # already recorded as this window's delay. Resume from "now"
            # instead of firing a catch-up burst of overdue ticks.
            self._next_expected = now + self._interval_seconds
        if now >= self._window_deadline:
            self._close_window(now)
        self._schedule()

    def _close_window(self, now: float) -> None:
        wall = now - self._window_start
        cpu_ratio: float | None = None
        if wall > 0:
            cpu_ratio = (time.process_time() - self._cpu_base) / wall
        self._history.append(
            {
                "max_delay_ms": self._window_max_delay_ms,
                "wall_seconds": wall,
                "cpu_ratio": cpu_ratio,
            }
        )
        self._windows_completed += 1
        self._observed_seconds += max(wall, 0.0)
        # Roll the deadline forward past `now`; no catch-up windows.
        deadline = self._window_deadline + self._window_seconds
        while deadline <= now:
            deadline += self._window_seconds
        self._window_deadline = deadline
        self._window_start = now
        self._cpu_base = time.process_time()
        self._window_max_delay_ms = 0.0


_SAMPLER = EventLoopSampler()


def get_event_loop_sampler() -> EventLoopSampler:
    """Process-wide sampler instance used by the ops surfaces."""
    return _SAMPLER
