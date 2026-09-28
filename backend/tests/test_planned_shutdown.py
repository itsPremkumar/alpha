"""Planned shutdown: ordered, bounded, and honestly reported.

The property under test is that a *partial* shutdown can never be reported as a
clean one. That is the failure this module exists to prevent: a drain wrapped in
blanket `except` blocks that ends in "shutdown complete" whether or not the
queue was persisted.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.runtime.shutdown import (
    PLANNED_SHUTDOWN_ORDER,
    PlannedShutdown,
    ShutdownPhase,
    ShutdownStatus,
    default_shutdown_steps,
)


class TestOrder:
    def test_the_contract_order_is_the_documented_one(self) -> None:
        assert [phase.value for phase in PLANNED_SHUTDOWN_ORDER] == [
            "admission_closed",
            "operations_drained",
            "checkpoints_written",
            "queue_persisted",
            "permissions_persisted",
            "scheduler_persisted",
            "workers_stopped",
        ]

    def test_registration_order_does_not_change_execution_order(self) -> None:
        """A host wires its components up in whatever order suits it."""
        ran: list[ShutdownPhase] = []
        shutdown = PlannedShutdown()
        for phase in (ShutdownPhase.WORKERS_STOPPED, ShutdownPhase.ADMISSION_CLOSED, ShutdownPhase.QUEUE_PERSISTED):
            shutdown.register(phase, lambda phase=phase: ran.append(phase))
        asyncio.run(shutdown.shutdown())
        assert ran == [ShutdownPhase.ADMISSION_CLOSED, ShutdownPhase.QUEUE_PERSISTED, ShutdownPhase.WORKERS_STOPPED]

    def test_the_skeleton_covers_every_phase(self) -> None:
        assert {step.phase for step in default_shutdown_steps()} == set(PLANNED_SHUTDOWN_ORDER)

    def test_registering_a_phase_twice_replaces_rather_than_duplicates(self) -> None:
        calls: list[str] = []
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, lambda: calls.append("first"))
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, lambda: calls.append("second"))
        report = asyncio.run(shutdown.shutdown())
        assert calls == ["second"], "two writers to the same state is a race, not a feature"
        assert report.is_clean

    def test_an_unknown_phase_is_refused(self) -> None:
        shutdown = PlannedShutdown()
        with pytest.raises(ValueError, match="not a planned-shutdown phase"):
            shutdown.register(ShutdownPhase.COMPLETE)
        with pytest.raises(ValueError):
            shutdown.register(ShutdownPhase.COMPLETE)

    def test_a_negative_timeout_is_refused(self) -> None:
        shutdown = PlannedShutdown()
        with pytest.raises(ValueError, match="timeout_seconds"):
            shutdown.register(ShutdownPhase.QUEUE_PERSISTED, timeout_seconds=-1.0)


class TestHappyPath:
    def test_a_full_drain_reports_clean(self) -> None:
        shutdown = PlannedShutdown()
        for phase in PLANNED_SHUTDOWN_ORDER:
            shutdown.register(phase)
        report = asyncio.run(shutdown.shutdown())
        assert report.is_clean
        assert report.incomplete() == ()
        assert report.emergency is False
        assert report.to_dict()["is_clean"] is True

    def test_async_and_sync_actions_are_both_accepted(self) -> None:
        ran: list[str] = []

        async def async_step() -> None:
            ran.append("async")

        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED, lambda: ran.append("sync"))
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, async_step)
        report = asyncio.run(shutdown.shutdown())
        assert ran == ["sync", "async"]
        assert report.is_clean

    def test_admission_is_closed_before_anything_else_runs(self) -> None:
        """Every later step's safety depends on nothing new arriving."""
        shutdown = PlannedShutdown()
        seen: list[bool] = []
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED, lambda: seen.append(shutdown.admission_closed))
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, lambda: seen.append(shutdown.admission_closed))
        asyncio.run(shutdown.shutdown())
        assert seen[0] is True
        assert seen[1] is True

    def test_closing_admission_is_idempotent_and_needs_no_await(self) -> None:
        shutdown = PlannedShutdown()
        shutdown.close_admission()
        shutdown.close_admission()
        assert shutdown.admission_closed is True

    def test_the_next_phase_is_reported_before_the_drain(self) -> None:
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED)
        assert shutdown.pending() is ShutdownPhase.QUEUE_PERSISTED
        asyncio.run(shutdown.shutdown())
        assert shutdown.pending() is ShutdownPhase.COMPLETE


class TestHonesty:
    def test_a_failing_step_is_reported_and_the_rest_still_run(self) -> None:
        """One broken store must not strand the steps that protect other work."""
        ran: list[ShutdownPhase] = []

        def boom() -> None:
            raise RuntimeError("queue store unreachable")

        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED, lambda: ran.append(ShutdownPhase.ADMISSION_CLOSED))
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, boom)
        shutdown.register(ShutdownPhase.SCHEDULER_PERSISTED, lambda: ran.append(ShutdownPhase.SCHEDULER_PERSISTED))
        report = asyncio.run(shutdown.shutdown())

        assert ran == [ShutdownPhase.ADMISSION_CLOSED, ShutdownPhase.SCHEDULER_PERSISTED]
        assert report.is_clean is False
        assert report.status_of(ShutdownPhase.QUEUE_PERSISTED) is ShutdownStatus.FAILED
        details = {step["phase"]: step["detail"] for step in report.to_dict()["steps"]}
        assert "queue store unreachable" in details["queue_persisted"]

    def test_a_failed_step_never_yields_a_clean_report(self) -> None:
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED)
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, lambda: 1 / 0)
        report = asyncio.run(shutdown.shutdown())
        assert report.is_clean is False
        assert report.incomplete() == (ShutdownPhase.QUEUE_PERSISTED,)

    def test_a_report_never_claims_completion_for_an_unregistered_phase(self) -> None:
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED)
        report = asyncio.run(shutdown.shutdown())
        assert report.status_of(ShutdownPhase.QUEUE_PERSISTED) is ShutdownStatus.SKIPPED

    def test_the_text_rendering_says_incomplete_loudly(self) -> None:
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED)
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, lambda: 1 / 0)
        text = asyncio.run(shutdown.shutdown()).to_text()
        assert "INCOMPLETE" in text
        assert "queue_persisted: failed" in text


class TestDependencies:
    def test_a_step_is_skipped_when_its_prerequisite_failed(self) -> None:
        """Writing a scheduler snapshot before the queue it reads is how shutdown corrupts work."""
        ran: list[ShutdownPhase] = []
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED)
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, lambda: 1 / 0)
        shutdown.register(ShutdownPhase.SCHEDULER_PERSISTED, lambda: ran.append(ShutdownPhase.SCHEDULER_PERSISTED), requires=(ShutdownPhase.QUEUE_PERSISTED,))
        report = asyncio.run(shutdown.shutdown())
        assert ran == [], "a dependent step must not run against half-persisted state"
        assert report.status_of(ShutdownPhase.SCHEDULER_PERSISTED) is ShutdownStatus.SKIPPED
        assert "prerequisite" in report.to_dict()["steps"][-1]["detail"]

    def test_a_step_runs_when_its_prerequisite_completed(self) -> None:
        ran: list[ShutdownPhase] = []
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED)
        shutdown.register(ShutdownPhase.SCHEDULER_PERSISTED, lambda: ran.append(ShutdownPhase.SCHEDULER_PERSISTED), requires=(ShutdownPhase.QUEUE_PERSISTED,))
        report = asyncio.run(shutdown.shutdown())
        assert ran == [ShutdownPhase.SCHEDULER_PERSISTED]
        assert report.is_clean

    def test_a_prerequisite_nobody_registered_blocks_the_step(self) -> None:
        """Silently ignoring a declared prerequisite runs a step against unpersisted state."""
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.SCHEDULER_PERSISTED, requires=(ShutdownPhase.CHECKPOINTS_WRITTEN,))
        report = asyncio.run(shutdown.shutdown())
        assert report.status_of(ShutdownPhase.SCHEDULER_PERSISTED) is ShutdownStatus.SKIPPED
        assert report.is_clean is False


class TestDeadlines:
    def test_a_slow_step_is_bounded_and_recorded_as_timed_out(self) -> None:
        async def forever() -> None:
            await asyncio.sleep(30)

        shutdown = PlannedShutdown(per_step_timeout_seconds=0.05)
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED, forever)
        report = asyncio.run(shutdown.shutdown())
        assert report.status_of(ShutdownPhase.ADMISSION_CLOSED) is ShutdownStatus.TIMED_OUT
        assert report.is_clean is False

    def test_a_wedged_step_does_not_prevent_the_others_persisting(self) -> None:
        ran: list[ShutdownPhase] = []

        async def forever() -> None:
            await asyncio.sleep(30)

        shutdown = PlannedShutdown(per_step_timeout_seconds=0.05)
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED)
        shutdown.register(ShutdownPhase.OPERATIONS_DRAINED, forever)
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, lambda: ran.append(ShutdownPhase.QUEUE_PERSISTED))
        report = asyncio.run(shutdown.shutdown())
        assert ran == [ShutdownPhase.QUEUE_PERSISTED], "a wedged step must not strand the steps after it"
        assert report.status_of(ShutdownPhase.OPERATIONS_DRAINED) is ShutdownStatus.TIMED_OUT
        assert report.status_of(ShutdownPhase.QUEUE_PERSISTED) is ShutdownStatus.COMPLETED
        assert report.is_clean is False, "a step that timed out is never a clean shutdown"

    def test_the_overall_deadline_stops_the_drain_and_says_so(self) -> None:
        async def slow() -> None:
            await asyncio.sleep(5)

        # The per-step bound is generous, so the OVERALL bound is what ends the
        # drain: the first step is clamped to what is left of the whole budget.
        # The overall budget is deliberately not razor-thin -- a 50ms one races
        # event-loop scheduling under load and asserts on the scheduler rather
        # than on the drain.
        shutdown = PlannedShutdown(overall_timeout_seconds=0.2, per_step_timeout_seconds=30.0)
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED, slow)
        shutdown.register(ShutdownPhase.QUEUE_PERSISTED, lambda: None)
        report = asyncio.run(shutdown.shutdown())
        assert report.status_of(ShutdownPhase.ADMISSION_CLOSED) is ShutdownStatus.TIMED_OUT
        assert report.status_of(ShutdownPhase.QUEUE_PERSISTED) is ShutdownStatus.TIMED_OUT
        assert report.is_clean is False
        assert report.total_seconds < 3.0, "the overall bound must actually bound the drain"

    def test_a_zero_budget_marks_the_step_timed_out_rather_than_skipping_it_silently(self) -> None:
        shutdown = PlannedShutdown(overall_timeout_seconds=0.0)
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED, lambda: None)
        report = asyncio.run(shutdown.shutdown())
        assert report.status_of(ShutdownPhase.ADMISSION_CLOSED) is ShutdownStatus.TIMED_OUT
        assert report.is_clean is False


class TestEmergencyPath:
    def test_the_emergency_path_still_closes_admission(self) -> None:
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED)
        asyncio.run(shutdown.shutdown(emergency=True))
        assert shutdown.admission_closed is True

    def test_the_emergency_path_keeps_the_checkpoint_step(self) -> None:
        """It is the one that protects in-flight work."""
        ran: list[ShutdownPhase] = []
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.CHECKPOINTS_WRITTEN, lambda: ran.append(ShutdownPhase.CHECKPOINTS_WRITTEN))
        asyncio.run(shutdown.shutdown(emergency=True))
        assert ran == [ShutdownPhase.CHECKPOINTS_WRITTEN]

    def test_the_emergency_path_omits_the_reconstructable_phases_and_says_so(self) -> None:
        shutdown = PlannedShutdown()
        for phase in PLANNED_SHUTDOWN_ORDER:
            shutdown.register(phase)
        report = asyncio.run(shutdown.shutdown(emergency=True))
        assert report.emergency is True
        assert report.status_of(ShutdownPhase.QUEUE_PERSISTED) is ShutdownStatus.SKIPPED
        assert "emergency" in report.to_dict()["steps"][3]["detail"]

    def test_the_emergency_path_is_capped_regardless_of_the_configured_timeout(self) -> None:
        """A service manager's kill deadline must never be overrun."""
        shutdown = PlannedShutdown(overall_timeout_seconds=86_400.0)
        shutdown.register(ShutdownPhase.CHECKPOINTS_WRITTEN, lambda: None)
        report = asyncio.run(shutdown.shutdown(emergency=True))
        assert report.total_seconds < 10.0, "the emergency path must be bounded, not merely configured"


class TestReset:
    def test_a_drain_can_be_re_armed(self) -> None:
        """A shutdown that cannot be re-armed is a one-way door."""
        calls: list[int] = []
        shutdown = PlannedShutdown()
        shutdown.register(ShutdownPhase.ADMISSION_CLOSED, lambda: calls.append(1))
        assert asyncio.run(shutdown.shutdown()).is_clean
        shutdown.reset()
        assert shutdown.admission_closed is False
        assert shutdown.pending() is ShutdownPhase.ADMISSION_CLOSED
        assert asyncio.run(shutdown.shutdown()).is_clean
        assert calls == [1, 1]
