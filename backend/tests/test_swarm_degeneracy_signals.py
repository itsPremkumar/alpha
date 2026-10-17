"""Detect the two degenerate parallel policies.

Kimi's PARL needed two dedicated reward terms to suppress pathologies that every
parallel orchestrator is vulnerable to:

- **Serial collapse** - the orchestrator defaults to single-agent execution,
  because sequential is easier to credit-assign.  A swarm can spawn thirty
  subagents and run them one at a time, paying thirty times the coordination for
  one agent's throughput.
- **Spurious parallelism** - the orchestrator spawns many subagents without
  meaningful decomposition, inflating a parallel metric while completing little.

Alpha's ``loop_indicator`` catches *repeated* work but not either of these, so a
collapsed or hollow run was indistinguishable from a correct one.  Both are
**advisory**: a flagged run is reported, never aborted, because a serial run
that succeeded is still a success - just an expensive one.
"""

from __future__ import annotations

import unittest

from alpha.swarm.models import SwarmPlan, SwarmTaskNode, TaskNodeState
from alpha.swarm.telemetry import SwarmTelemetry


def _completed(task_id: str, *, started: float, ended: float, result: str = "done") -> SwarmTaskNode:
    return SwarmTaskNode(
        task_id=task_id,
        objective=f"do {task_id}",
        state=TaskNodeState.COMPLETED,
        started_at=started,
        completed_at=ended,
        result_summary=result,
    )


def _plan(tasks: list[SwarmTaskNode]) -> SwarmPlan:
    plan = SwarmPlan(swarm_id="s", goal="g", mode="parallel")
    for task in tasks:
        plan.tasks[task.task_id] = task
    return plan


class TestSerialCollapse(unittest.TestCase):
    def test_non_overlapping_tasks_are_flagged(self) -> None:
        plan = _plan(
            [
                _completed("a", started=100.0, ended=105.0),
                _completed("b", started=110.0, ended=115.0),
                _completed("c", started=120.0, ended=125.0),
            ]
        )
        result = SwarmTelemetry.parallelism_realised(plan)
        self.assertEqual(result["max_concurrent_observed"], 1)
        self.assertIs(result["flagged"], True)
        self.assertEqual(result["state"], "collapsed")

    def test_overlapping_tasks_are_not_flagged(self) -> None:
        plan = _plan([_completed(f"t{i}", started=100.0, ended=110.0) for i in range(3)])
        result = SwarmTelemetry.parallelism_realised(plan)
        self.assertEqual(result["max_concurrent_observed"], 3)
        self.assertIs(result["flagged"], False)
        self.assertEqual(result["state"], "parallel")

    def test_partial_overlap_counts_the_peak_not_the_average(self) -> None:
        """Two at once is real parallelism, even if the third waits."""

        plan = _plan(
            [
                _completed("a", started=100.0, ended=110.0),
                _completed("b", started=100.0, ended=110.0),
                _completed("c", started=120.0, ended=125.0),
            ]
        )
        self.assertEqual(SwarmTelemetry.parallelism_realised(plan)["max_concurrent_observed"], 2)

    def test_a_single_task_is_not_collapse(self) -> None:
        """Collapse needs something that *could* have overlapped."""

        plan = SwarmPlan(swarm_id="s", goal="g", mode="parallel")
        plan.tasks["a"] = _completed("a", started=100.0, ended=110.0)
        self.assertIs(SwarmTelemetry.parallelism_realised(plan)["flagged"], False)

    def test_missing_timestamps_are_unknown_not_collapsed(self) -> None:
        """Absent is ``unknown``; assuming serial would be a fabricated verdict."""

        plan = _plan([SwarmTaskNode(task_id="a", objective="x"), SwarmTaskNode(task_id="b", objective="y")])
        result = SwarmTelemetry.parallelism_realised(plan)
        self.assertIsNone(result["max_concurrent_observed"])
        self.assertEqual(result["state"], "unknown")
        self.assertIs(result["flagged"], False)

    def test_an_inverted_interval_is_skipped(self) -> None:
        plan = _plan([_completed("a", started=110.0, ended=100.0)])
        self.assertEqual(SwarmTelemetry.parallelism_realised(plan)["state"], "unknown")


class TestSpuriousParallelism(unittest.TestCase):
    def test_a_wide_fan_out_that_completes_little_is_flagged(self) -> None:
        tasks = [
            SwarmTaskNode(task_id="t0", objective="o0", state=TaskNodeState.COMPLETED, result_summary="only one"),
            *[SwarmTaskNode(task_id=f"t{i}", objective=f"o{i}", state=TaskNodeState.FAILED) for i in range(1, 6)],
        ]
        result = SwarmTelemetry.subtask_completion_ratio(_plan(tasks))
        self.assertAlmostEqual(result["completion_ratio"], round(1 / 6, 6))
        self.assertIs(result["flagged"], True)
        self.assertEqual(result["state"], "spurious")

    def test_a_healthy_fan_out_is_not_flagged(self) -> None:
        tasks = [_completed(f"t{i}", started=100.0, ended=110.0) for i in range(6)]
        result = SwarmTelemetry.subtask_completion_ratio(_plan(tasks))
        self.assertEqual(result["completion_ratio"], 1.0)
        self.assertIs(result["flagged"], False)

    def test_a_small_fan_out_is_never_flagged(self) -> None:
        """Two of three completing is not spurious parallelism."""

        tasks = [
            _completed("a", started=100.0, ended=110.0),
            _completed("b", started=100.0, ended=110.0),
            SwarmTaskNode(task_id="c", objective="o", state=TaskNodeState.FAILED),
        ]
        self.assertIs(SwarmTelemetry.subtask_completion_ratio(_plan(tasks))["flagged"], False)

    def test_an_empty_plan_is_unknown(self) -> None:
        result = SwarmTelemetry.subtask_completion_ratio(SwarmPlan(swarm_id="s", goal="g", mode="parallel"))
        self.assertIsNone(result["completion_ratio"])
        self.assertEqual(result["state"], "unknown")

    def test_a_freshly_spawned_fan_out_is_not_yet_spurious(self) -> None:
        """The regression: zero completions is not evidence of hollowness.

        A plan with four pending tasks and nothing finished has not *failed* to
        parallelise - it has not started.  Flagging it reported a verdict nobody
        measured, which is the same class of lie as a fabricated zero.
        """

        plan = _plan([SwarmTaskNode(task_id=f"t{i}", objective=f"o{i}") for i in range(4)])
        result = SwarmTelemetry.subtask_completion_ratio(plan)
        self.assertEqual(result["tasks_spawned"], 4)
        self.assertEqual(result["tasks_completed"], 0)
        # The ratio is a real measurement (zero of four produced a result), so it
        # is 0.0 rather than None.  What must NOT happen is the verdict: with no
        # terminal state there is no evidence the fan-out was ever hollow.
        self.assertEqual(result["completion_ratio"], 0.0)
        self.assertIs(result["flagged"], False)
        self.assertEqual(result["state"], "unknown")

    def test_failures_alone_count_as_evidence(self) -> None:
        """Four spawned, all failed: evidence exists, and it is not parallelism."""

        plan = _plan([SwarmTaskNode(task_id=f"t{i}", objective=f"o{i}", state=TaskNodeState.FAILED) for i in range(4)])
        result = SwarmTelemetry.subtask_completion_ratio(plan)
        self.assertEqual(result["tasks_failed"], 4)
        self.assertIs(result["flagged"], True)
        self.assertEqual(result["state"], "spurious")

    def test_a_blank_result_summary_does_not_count_as_meaningful(self) -> None:
        """``completed`` means execution ended, not that it produced anything."""

        tasks = [SwarmTaskNode(task_id=f"t{i}", objective=f"o{i}", state=TaskNodeState.COMPLETED, result_summary="   ") for i in range(4)]
        result = SwarmTelemetry.subtask_completion_ratio(_plan(tasks))
        self.assertEqual(result["tasks_completed"], 4)
        self.assertEqual(result["tasks_with_results"], 0)
        self.assertIs(result["flagged"], True)


class TestSnapshotSurfacesBoth(unittest.TestCase):
    def test_a_collapsed_plan_reports_the_risk_signal(self) -> None:
        plan = _plan(
            [
                _completed("a", started=100.0, ended=105.0),
                _completed("b", started=110.0, ended=115.0),
            ]
        )
        snapshot = SwarmTelemetry.snapshot(plan)
        self.assertIn("serial_collapse", snapshot["risk_signals"])
        self.assertIn("parallelism_realised", snapshot)
        self.assertIn("subtask_completion_ratio", snapshot)

    def test_a_healthy_parallel_plan_reports_neither(self) -> None:
        plan = _plan([_completed(f"t{i}", started=100.0, ended=110.0) for i in range(4)])
        snapshot = SwarmTelemetry.snapshot(plan)
        self.assertNotIn("serial_collapse", snapshot["risk_signals"])
        self.assertNotIn("spurious_parallelism", snapshot["risk_signals"])

    def test_the_snapshot_basis_disclaims_token_conversion(self) -> None:
        """A round count is not a cost; the snapshot must not imply one."""

        self.assertIn("no estimated token conversion", SwarmTelemetry.snapshot(_plan([]))["basis"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
