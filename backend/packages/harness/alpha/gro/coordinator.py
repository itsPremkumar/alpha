"""GRO coordinator: bounded, fenced candidate execution.

CPython cannot kill a thread, so a timeout here is a **fence, not a kill**
(the same posture as ``alpha.workflow.execution.run_with_deadline``): the
candidate is declared ``timeout`` at its measured bound, its lane is fenced,
and a result that arrives after the fence is discarded rather than adopted.
A queued candidate whose lane was fenced before it started never executes at
all. The subagent runtime enforces its own execution timeout inside the lane.

Concurrency is admitted by this coordinator *in addition to* the process-wide
subagent admission controller: ``max_active`` bounds the group's own lanes,
and each lane that runs through ``SubagentExecutionAdapter`` still takes a
real subagent slot, so enabling GRO cannot exceed the operator's process
ceiling.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any

from alpha.gro.models import CandidateOutcome, CandidateSpec, CandidateStatus

#: Poll granularity for deadline/fence checks while waiting on lanes.
_WAIT_SLICE_SECONDS = 0.05

OutcomeCallback = Callable[[CandidateOutcome], None]
EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class _LaneState:
    """Shared state for one candidate lane; the fence is the load-bearing field."""

    spec: CandidateSpec
    fenced: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)
    outcome: CandidateOutcome | None = None
    announced: bool = False
    started_at: float | None = None

    def publish(self, outcome: CandidateOutcome) -> bool:
        """Record the worker's outcome unless the lane is already fenced.

        Returns whether the outcome was adopted. A late result from a fenced
        lane is dropped — adopting it would credit work the run already
        declared timed out.
        """

        with self.lock:
            if self.fenced.is_set():
                return False
            self.outcome = outcome
            return True

    def take_for_announcement(self) -> CandidateOutcome | None:
        with self.lock:
            if self.announced or self.outcome is None:
                return None
            self.announced = True
            return self.outcome


class GroCoordinator:
    """Executes one candidate group under concurrency, per-lane and run bounds."""

    def __init__(self, *, max_active: int, candidate_timeout_seconds: float, run_timeout_seconds: float, on_event: EventCallback | None = None) -> None:
        if max_active < 1:
            raise ValueError(f"max_active must be >= 1 (got {max_active})")
        if candidate_timeout_seconds <= 0 or run_timeout_seconds <= 0:
            raise ValueError("GRO timeouts must be > 0")
        self.max_active = max_active
        self.candidate_timeout_seconds = candidate_timeout_seconds
        self.run_timeout_seconds = run_timeout_seconds
        self._on_event = on_event

    def _emit(self, event_type: str, **fields: Any) -> None:
        if self._on_event is not None:
            self._on_event({"type": event_type, **fields})

    def execute_group(
        self,
        specs: Sequence[CandidateSpec],
        execute_one: Callable[[CandidateSpec], CandidateOutcome],
        *,
        started_at: float | None = None,
        on_lane_complete: OutcomeCallback | None = None,
    ) -> dict[str, CandidateOutcome]:
        """Run every spec under bounds; returns adopted outcomes keyed by id.

        Every spec gets an outcome — a candidate that never started, timed out
        or crashed is recorded as such, never omitted. Omission would read as
        success to any later ranker.
        """

        run_started = started_at if started_at is not None else time.time()
        run_deadline = run_started + self.run_timeout_seconds
        lanes: dict[str, _LaneState] = {}
        for spec in specs:
            if spec.candidate_id in lanes:
                raise ValueError(f"duplicate candidate id {spec.candidate_id!r} within one group")
            lanes[spec.candidate_id] = _LaneState(spec=spec)

        def _worker(lane: _LaneState) -> None:
            if lane.fenced.is_set():
                # Fenced before dispatch: the run already gave up on this lane.
                return
            spec = lane.spec
            lane.started_at = time.time()
            self._emit("candidate.started", candidate_id=spec.candidate_id, strategy_id=spec.strategy.strategy_id, attempt=spec.attempt)
            began = lane.started_at
            try:
                outcome = execute_one(spec)
            except BaseException as exc:  # noqa: BLE001 - a lane failure is recorded, never re-raised into the group
                outcome = CandidateOutcome(candidate_id=spec.candidate_id, status=CandidateStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            if outcome.duration_ms <= 0:
                outcome.duration_ms = (time.time() - began) * 1000.0
            if lane.publish(outcome):
                self._emit("candidate.finished", candidate_id=spec.candidate_id, status=outcome.status.value, duration_ms=round(outcome.duration_ms, 1))

        pool = ThreadPoolExecutor(max_workers=min(self.max_active, max(1, len(lanes))), thread_name_prefix="gro-lane")
        futures: list[Future[None]] = [pool.submit(_worker, lane) for lane in lanes.values()]
        # Do not block teardown on hung lanes: their results are fenced and
        # discarded, and their own runtime enforces its bounds.
        pool.shutdown(wait=False)

        self._await_group(lanes, futures, run_deadline, on_lane_complete=on_lane_complete)
        self._finalize(lanes, run_started, run_deadline)

        return {candidate_id: lane.outcome if lane.outcome is not None else CandidateOutcome(candidate_id=candidate_id, status=CandidateStatus.FAILED, error="lane produced no outcome") for candidate_id, lane in lanes.items()}

    def _await_group(self, lanes: dict[str, _LaneState], futures: Sequence[Future[None]], run_deadline: float, *, on_lane_complete: OutcomeCallback | None) -> None:
        """Poll until every lane resolves, times out, or the run deadline passes."""

        pending: set[Future[None]] = set(futures)
        while pending:
            done, pending = wait(pending, timeout=_WAIT_SLICE_SECONDS)
            for future in done:
                exc = future.exception()
                if exc is not None:
                    # Already recorded as a failed outcome inside _worker;
                    # emitting keeps the loop honest if that ever changes.
                    self._emit("lane.error", error=f"{type(exc).__name__}: {exc}")
            now = time.time()
            for lane in lanes.values():
                if lane.started_at is not None and lane.outcome is None and now - lane.started_at >= self.candidate_timeout_seconds:
                    self._fence_lane(lane, now, reason=f"candidate exceeded its {self.candidate_timeout_seconds:.0f}s wall-clock bound; the late result is fenced, never adopted")
            if on_lane_complete is not None:
                for lane in lanes.values():
                    outcome = lane.take_for_announcement()
                    if outcome is not None:
                        on_lane_complete(outcome)
            if now >= run_deadline:
                break
        if on_lane_complete is not None:
            for lane in lanes.values():
                outcome = lane.take_for_announcement()
                if outcome is not None:
                    on_lane_complete(outcome)

    def _fence_lane(self, lane: _LaneState, now: float, *, reason: str) -> None:
        """Fence one lane and record its honest timeout outcome, once."""

        with lane.lock:
            if lane.fenced.is_set() or lane.outcome is not None:
                return
            lane.fenced.set()
            lane.outcome = CandidateOutcome(candidate_id=lane.spec.candidate_id, status=CandidateStatus.TIMEOUT, duration_ms=max(0.0, (now - (lane.started_at or now)) * 1000.0), error=reason)
        self._emit("candidate.timeout", candidate_id=lane.spec.candidate_id, reason=reason)

    def _finalize(self, lanes: dict[str, _LaneState], run_started: float, run_deadline: float) -> None:
        """Fence every unresolved lane and record its honest terminal status."""

        now = time.time()
        for lane in lanes.values():
            if lane.outcome is not None:
                continue
            lane.fenced.set()
            if lane.started_at is None:
                reason = "the run deadline arrived before this candidate was dispatched; it never ran"
            else:
                reason = "the run deadline arrived before this candidate reported a result; its late result is fenced, never adopted"
            lane.outcome = CandidateOutcome(candidate_id=lane.spec.candidate_id, status=CandidateStatus.TIMEOUT, duration_ms=max(0.0, (now - run_started) * 1000.0), error=reason)
            self._emit("candidate.timeout", candidate_id=lane.spec.candidate_id, reason=reason)
