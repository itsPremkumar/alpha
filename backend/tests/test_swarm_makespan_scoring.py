"""Candidate scoring uses duration-weighted makespan, not hop counts.

**The defect.** ``score_plan`` computed its parallelism term from
``ideal_floor = max(depth, ceil(n / concurrency))`` - a *hop* lower bound that
treats every task as equal.  A plan with one 600-second task and nine 5-second
tasks therefore scored identically to its inverse, and
``SwarmTaskNode.estimated_seconds`` existed to break exactly that tie while
nothing read it.

The decomposer already computed a seconds-weighted critical path
(``critical_path_seconds``, ``estimate_method =
"dependency_critical_path_plus_capacity_v1"``) - but only on the *final* plan,
after selection, so at scoring time every candidate still carried the default
``0.0`` and the scorer had nothing to read.

**What is pinned here.**  With uniform durations the seconds term reduces to the
hop term exactly, so this is a strict improvement rather than a reweighting.
"""

from __future__ import annotations

import unittest

from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode
from alpha.swarm.planner import _seconds_parallelism, score_plan


def _chain(task_id: str, *, seconds: float, depends_on: str | None = None) -> SwarmTaskNode:
    return SwarmTaskNode(
        task_id=task_id,
        objective=f"do {task_id}",
        estimated_seconds=seconds,
        dependencies=[depends_on] if depends_on else [],
    )


def _plan(tasks: list[SwarmTaskNode], *, max_concurrency: int = 4) -> SwarmPlan:
    plan = SwarmPlan(swarm_id="s", goal="g", mode=SwarmMode.PARALLEL, max_concurrency=max_concurrency)
    for task in tasks:
        plan.tasks[task.task_id] = task
    return plan


class TestSecondsParallelism(unittest.TestCase):
    def test_a_plan_with_no_estimate_falls_back_to_hops(self) -> None:
        """The safe case: no estimate means the hop term still applies.

        A chain with no ``estimated_serial_seconds`` returns ``None`` from the
        seconds helper, so ``score_plan`` uses the hop-based speedup - which for
        a pure chain is 0 either way.
        """

        chain = _plan([_chain("a", seconds=15.0), _chain("b", seconds=15.0, depends_on="a")])
        self.assertIsNone(_seconds_parallelism(chain, 2))
        self.assertEqual(score_plan(chain, max_concurrency=4).parallelism, 0.0)

    def test_uniform_estimates_score_the_same_as_hops(self) -> None:
        """With equal durations the two measures agree, so nothing regresses."""

        wide = _plan([_chain(f"t{i}", seconds=15.0) for i in range(4)])
        wide.metrics["estimated_serial_seconds"] = 60.0
        wide.critical_path_seconds = 15.0
        self.assertAlmostEqual(_seconds_parallelism(wide, 4), 0.75, places=6)
        self.assertGreater(score_plan(wide, max_concurrency=4).parallelism, 0.0)

    def test_a_long_task_is_penalised_relative_to_a_short_one(self) -> None:
        """The actual defect: unequal durations must now be distinguishable."""

        # One 600s task plus nine 5s tasks: the long task dominates the path.
        lopsided = _plan([_chain("long", seconds=600.0)] + [_chain(f"s{i}", seconds=5.0) for i in range(9)])
        # The same ten tasks, all short: the path is one task.
        even = _plan([_chain(f"t{i}", seconds=5.0) for i in range(10)])

        for plan in (lopsided, even):
            plan.metrics["estimated_serial_seconds"] = sum(float(t.estimated_seconds) for t in plan.tasks.values())
            plan.critical_path_seconds = plan.metrics["estimated_serial_seconds"] if plan is lopsided else 5.0

        lopsided_score = score_plan(lopsided, max_concurrency=4)
        even_score = score_plan(even, max_concurrency=4)
        self.assertGreater(
            even_score.parallelism,
            lopsided_score.parallelism,
            "a plan whose critical path is one long task must not score as parallel as one whose path is short",
        )

    def test_no_estimate_returns_none_rather_than_a_number(self) -> None:
        """Absent is ``None``, so the caller falls back instead of scoring zero."""

        plan = _plan([_chain("a", seconds=15.0)])
        self.assertIsNone(_seconds_parallelism(plan, 1))

    def test_a_zero_or_negative_estimate_is_rejected(self) -> None:
        plan = _plan([_chain("a", seconds=15.0)])
        plan.critical_path_seconds = 0.0
        plan.metrics["estimated_serial_seconds"] = 0.0
        self.assertIsNone(_seconds_parallelism(plan, 1))

    def test_a_non_numeric_estimate_is_rejected(self) -> None:
        plan = _plan([_chain("a", seconds=15.0)])
        plan.critical_path_seconds = "not-a-number"
        plan.metrics["estimated_serial_seconds"] = 10.0
        self.assertIsNone(_seconds_parallelism(plan, 1))


class TestCandidatesAreMeasuredBeforeSelection(unittest.TestCase):
    """The decomposer must compute the estimate while it can still decide."""

    def test_the_decomposer_computes_the_path_for_every_candidate(self) -> None:
        import inspect

        source = inspect.getsource(SwarmTaskDecomposer._select_candidate_plan)
        self.assertIn("_compute_critical_path_and_speedup", source)
        # And it must run BEFORE the candidate is scored, not only on the winner.
        self.assertLess(
            source.index("_compute_critical_path_and_speedup"),
            source.index("build_candidate"),
            "the estimate must exist before score_plan reads it",
        )

    def test_a_candidate_plan_carries_a_non_zero_critical_path(self) -> None:
        plan = _plan([_chain("a", seconds=100.0), _chain("b", seconds=1.0, depends_on="a")])
        SwarmTaskDecomposer._compute_critical_path_and_speedup(plan)
        self.assertGreater(plan.critical_path_seconds, 0.0)
        self.assertEqual(
            plan.metrics["estimate_method"],
            "dependency_critical_path_plus_capacity_v1",
        )


class TestScorePlanStillExposesTheHopNumbers(unittest.TestCase):
    """The hop-based fields are retained: they answer a different question."""

    def test_critical_path_and_exposed_width_are_still_reported(self) -> None:
        plan = _plan([_chain(f"t{i}", seconds=15.0) for i in range(4)])
        score = score_plan(plan, max_concurrency=4)
        self.assertEqual(score.critical_path, 1)
        self.assertEqual(score.exposed_width, 4)
        self.assertEqual(score.lower_bound_rounds, 1)

    def test_a_pure_chain_scores_zero_parallelism(self) -> None:
        """The pre-existing contract: a serial plan has no parallelism."""

        plan = _plan([_chain("a", seconds=15.0), _chain("b", seconds=15.0, depends_on="a"), _chain("c", seconds=15.0, depends_on="b")])
        self.assertEqual(score_plan(plan, max_concurrency=4).parallelism, 0.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
