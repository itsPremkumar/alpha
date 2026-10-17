"""Pre-launch cost projection and refusal for swarm spawns.

**The gap this closes.** ``SwarmBudget`` bounds a run *while* it runs and
``budget_pressure`` reports the ratio *after* spend.  Neither answers the only
question that decides whether to start: given this decomposition, will it fit
the ceiling I already declared?

Arena refuses a tournament above ``require_confirmation_over_calls`` for exactly
this reason.  The swarm had no equivalent, so a swarm could be spawned whose own
predicted makespan already exceeded its own wall budget - guaranteed to end in
``budget_exhausted`` no matter how well it ran.

Two honesty rules are load-bearing here:

- **Only wall-clock is projected.**  There is no per-task token or tool-call
  estimate anywhere in the plan, so those fields are ``None`` ("unmeasured"),
  never ``0`` and never an invented figure.
- **The refusal is self-consistent, not configurable.**  A plan is refused when
  its own prediction exceeds its own declared budget.  No new config key, and the
  check cannot drift from the budget it checks.
"""

from __future__ import annotations

import unittest

from alpha.swarm.models import SwarmBudget, SwarmPlan, SwarmTaskNode
from alpha.swarm.telemetry import SwarmTelemetry


def _plan(
    *,
    estimated: tuple[float, ...] = (10.0,),
    max_wall_seconds: float | None = None,
    max_tool_calls: int | None = None,
    max_tokens: int | None = None,
    max_concurrency: int = 4,
) -> SwarmPlan:
    """A plan whose decomposer has already produced its projection."""

    plan = SwarmPlan(swarm_id="s", goal="g", mode="auto", max_concurrency=max_concurrency)
    for index, seconds in enumerate(estimated):
        plan.tasks[f"t{index}"] = SwarmTaskNode(
            task_id=f"t{index}",
            objective=f"task {index}",
            estimated_seconds=seconds,
        )
    serial = float(sum(estimated))
    # Mirror what the decomposer writes for an independent chain.
    plan.metrics["estimated_serial_seconds"] = serial
    plan.metrics["estimated_parallel_seconds"] = serial
    plan.metrics["estimated_speedup"] = 1.0
    plan.metrics["estimate_method"] = "dependency_critical_path_plus_capacity_v1"
    plan.critical_path_seconds = serial
    plan.estimated_speedup = 1.0
    plan.budget = SwarmBudget(
        max_tokens=max_tokens,
        max_tool_calls=max_tool_calls,
        max_wall_seconds=max_wall_seconds,
    )
    return plan


class TestProjectionShape(unittest.TestCase):
    def test_it_reports_the_decomposers_own_numbers(self) -> None:
        plan = _plan(estimated=(120.0, 10.0))
        projection = SwarmTelemetry.cost_projection(plan)
        self.assertEqual(projection["projected_tasks"], 2)
        self.assertEqual(projection["projected_serial_seconds"], 130.0)
        self.assertEqual(projection["projected_wall_seconds"], 130.0)
        self.assertEqual(projection["estimated_speedup"], 1.0)
        self.assertEqual(projection["estimate_method"], "dependency_critical_path_plus_capacity_v1")

    def test_token_and_tool_projections_are_none_not_zero(self) -> None:
        """No per-task estimate exists, so guessing would be worse than absent."""

        projection = SwarmTelemetry.cost_projection(_plan(estimated=(5.0,), max_tokens=1000, max_tool_calls=10))
        self.assertIsNone(projection["projected_tokens"])
        self.assertIsNone(projection["projected_tool_calls"])
        # The declared ceilings are still reported, so a caller can see them.
        self.assertEqual(projection["tool_budget_calls"], 10)

    def test_a_plan_with_no_metrics_falls_back_to_the_plan_field(self) -> None:
        """A hand-built or legacy plan may carry only ``critical_path_seconds``."""

        plan = SwarmPlan(swarm_id="s", goal="g", mode="auto")
        plan.tasks["a"] = SwarmTaskNode(task_id="a", objective="x")
        plan.critical_path_seconds = 42.0
        projection = SwarmTelemetry.cost_projection(plan)
        self.assertEqual(projection["projected_wall_seconds"], 42.0)
        self.assertEqual(projection["estimate_method"], "not_computed")


class TestRefusalRule(unittest.TestCase):
    def test_a_plan_that_cannot_honour_its_own_budget_is_refused(self) -> None:
        projection = SwarmTelemetry.cost_projection(_plan(estimated=(120.0, 10.0), max_wall_seconds=60.0))
        self.assertIs(projection["wall_budget_exceeded"], True)

    def test_a_plan_inside_its_budget_is_not_refused(self) -> None:
        projection = SwarmTelemetry.cost_projection(_plan(estimated=(10.0,), max_wall_seconds=60.0))
        self.assertIs(projection["wall_budget_exceeded"], False)

    def test_no_wall_budget_means_no_refusal(self) -> None:
        """``None`` is "unbounded", which is not the same as "exceeded"."""

        projection = SwarmTelemetry.cost_projection(_plan(estimated=(9999.0,)))
        self.assertIsNone(projection["wall_budget_exceeded"])
        self.assertEqual(projection["wall_budget_seconds"], None)

    def test_an_exact_fit_is_not_refused(self) -> None:
        """The boundary is strict: equal is achievable, over is not."""

        projection = SwarmTelemetry.cost_projection(_plan(estimated=(60.0,), max_wall_seconds=60.0))
        self.assertIs(projection["wall_budget_exceeded"], False)

    def test_a_zero_wall_budget_refuses_any_work(self) -> None:
        """A zero limit is meaningful and fails closed, per ``SwarmBudget``."""

        projection = SwarmTelemetry.cost_projection(_plan(estimated=(1.0,), max_wall_seconds=0.0))
        self.assertIs(projection["wall_budget_exceeded"], True)


class TestRefusalIsReachableFromTheTool(unittest.TestCase):
    """The projection is only useful if the spawn path actually consults it.

    The module is loaded with ``importlib.import_module`` rather than
    ``from alpha.tools.builtins import swarm_tool`` on purpose: that facade
    re-exports the *tool* under the same name, so the plain import binds the
    tool object and ``inspect.getsource`` fails with "is a built-in module".
    """

    @staticmethod
    def _source() -> str:
        import importlib
        import inspect

        return inspect.getsource(importlib.import_module("alpha.tools.builtins.swarm_tool"))

    def test_the_spawn_path_imports_the_projection(self) -> None:
        source = self._source()
        self.assertIn("SwarmTelemetry.cost_projection", source)
        self.assertIn('projection["wall_budget_exceeded"] is True', source)

    def test_the_refusal_message_names_the_numbers_and_the_remedy(self) -> None:
        """A refusal that says "no" without saying why is not actionable."""

        source = self._source()
        self.assertIn("refused before start", source)
        self.assertIn("projected_wall_seconds", source)
        self.assertIn("wall_budget_seconds", source)


class TestProjectionIsPure(unittest.TestCase):
    def test_it_never_mutates_the_plan(self) -> None:
        plan = _plan(estimated=(10.0,), max_wall_seconds=5.0)
        before = plan.to_dict()
        SwarmTelemetry.cost_projection(plan)
        self.assertEqual(plan.to_dict(), before)

    def test_it_handles_an_empty_plan(self) -> None:
        projection = SwarmTelemetry.cost_projection(SwarmPlan(swarm_id="s", goal="g", mode="auto"))
        self.assertEqual(projection["projected_tasks"], 0)
        self.assertEqual(projection["projected_wall_seconds"], 0.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
