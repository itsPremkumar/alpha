"""Plan-time admission advisory for swarm plans.

A swarm plan can declare up to ``MAX_SWARM_PLAN_CONCURRENCY`` (1000) concurrent
workers, but every swarm worker executes through ``SubagentExecutor``, which
inherits the process-wide admission controller (``subagent_runtime``,
default 3 running / 64 queued).  The advisory written at ``create_swarm`` time
discloses that seam.  These pins are the contract:

- the advisory is **advisory**: a wide plan is still accepted, and the wall
  budget refusal is untouched by its presence;
- capacity the process did not report is ``None``, never ``0`` — unknown is not
  absent, and a zero would claim "no capacity";
- the numbers come from the *startup* admission schedule every worker will
  inherit, and the accessor returns a copy, so a caller cannot re-arm the
  process by mutating its disclosed reading;
- ``expected_realized_concurrency`` never exceeds the pool, and a plan wider
  than pool(+queue, when queueing) is labelled with the refusal risk instead of
  being silently accepted as wide as it asked for.
"""

from __future__ import annotations

import importlib
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alpha.config.subagent_runtime_config import SubagentRuntimeConfig
from alpha.subagents import capacity as capacity_module
from alpha.swarm import admission
from alpha.swarm.coordinator import SwarmCoordinator
from alpha.swarm.decomposer import SwarmTaskDecomposer
from alpha.swarm.models import SwarmBudget, SwarmMode, SwarmPlan
from alpha.swarm.scheduler import SwarmPlanValidationError


def _stub_decomposer(swarm_id: str = "advisory-plan", concurrency: int = 50, parallel_seconds: float = 30.0):
    """A deterministic light plan: no model calls, no 1000-task decomposition."""

    def decompose(**_kwargs: object) -> SwarmPlan:
        plan = SwarmPlan(swarm_id=swarm_id, goal="stub goal", mode=SwarmMode.AUTO)
        plan.max_concurrency = concurrency
        plan.critical_path_seconds = parallel_seconds
        plan.metrics["estimated_parallel_seconds"] = parallel_seconds
        return plan

    return decompose


class TestBuildAdmissionAdvisory(unittest.TestCase):
    def test_default_schedule_reports_pool_queue_and_expected_realization(self) -> None:
        advisory = admission.build_admission_advisory(50, SubagentRuntimeConfig())
        self.assertEqual(advisory["max_running"], 3)
        self.assertEqual(advisory["max_queued"], 64)
        self.assertEqual(advisory["admission_policy"], "queue")
        self.assertEqual(advisory["queue_timeout_seconds"], 300)
        self.assertEqual(advisory["expected_realized_concurrency"], 3)
        self.assertEqual(advisory["inflight_capacity"], 67)
        self.assertIs(advisory["wide_plan_risk"], False)
        self.assertIn("50-way", advisory["note"])
        self.assertIn("at most 3 subagents", advisory["note"])
        self.assertIn("queues up to 64", advisory["note"])

    def test_a_plan_wider_than_pool_plus_queue_is_flagged_not_refused(self) -> None:
        advisory = admission.build_admission_advisory(50, SubagentRuntimeConfig(max_running=2, max_queued=1))
        self.assertEqual(advisory["expected_realized_concurrency"], 2)
        self.assertEqual(advisory["inflight_capacity"], 3)
        self.assertIs(advisory["wide_plan_risk"], True)
        self.assertIn("beyond 3 concurrent tasks", advisory["note"])

    def test_reject_policy_counts_only_running_slots(self) -> None:
        advisory = admission.build_admission_advisory(
            10,
            SubagentRuntimeConfig(max_running=4, max_queued=64, admission_policy="reject"),
        )
        self.assertEqual(advisory["inflight_capacity"], 4)
        self.assertIs(advisory["wide_plan_risk"], True)
        self.assertIn("refuses admission rather than queueing", advisory["note"])

    def test_queue_policy_with_a_zero_queue_discloses_the_immediate_refusal(self) -> None:
        advisory = admission.build_admission_advisory(10, SubagentRuntimeConfig(max_running=2, max_queued=0))
        self.assertEqual(advisory["inflight_capacity"], 2)
        self.assertIs(advisory["wide_plan_risk"], True)
        self.assertIn("no queue", advisory["note"])

    def test_an_unresolved_schedule_is_unknown_never_zero(self) -> None:
        advisory = admission.build_admission_advisory(50, None)
        for key in (
            "max_running",
            "max_queued",
            "admission_policy",
            "queue_timeout_seconds",
            "expected_realized_concurrency",
            "inflight_capacity",
            "wide_plan_risk",
        ):
            self.assertIsNone(advisory[key], f"{key} must be unknown, not zero")
        self.assertIn("not resolvable", advisory["note"])
        self.assertNotIn("at most", advisory["note"])

    def test_a_nonpositive_declared_concurrency_floors_at_one(self) -> None:
        advisory = admission.build_admission_advisory(0, SubagentRuntimeConfig())
        self.assertEqual(advisory["expected_realized_concurrency"], 1)

    def test_an_unknown_policy_leaves_the_capacity_statements_unknown(self) -> None:
        schedule = SubagentRuntimeConfig()
        object.__setattr__(schedule, "admission_policy", "awkward")
        advisory = admission.build_admission_advisory(50, schedule)
        self.assertIsNone(advisory["admission_policy"])
        self.assertIsNone(advisory["inflight_capacity"])
        self.assertIsNone(advisory["wide_plan_risk"])


class TestCapacityScheduleAccessor(unittest.TestCase):
    def tearDown(self) -> None:
        capacity_module.configure_subagent_execution_capacity(SubagentRuntimeConfig())

    def test_the_disclosed_schedule_is_a_copy_of_the_armed_startup_snapshot(self) -> None:
        configured = SubagentRuntimeConfig(
            max_running=5,
            max_queued=7,
            admission_policy="reject",
            queue_timeout_seconds=42,
        )
        capacity_module.configure_subagent_execution_capacity(configured)
        disclosed = capacity_module.configured_subagent_admission_schedule()
        self.assertEqual(disclosed.max_running, 5)
        self.assertEqual(disclosed.max_queued, 7)
        self.assertEqual(disclosed.admission_policy, "reject")
        self.assertEqual(disclosed.queue_timeout_seconds, 42)
        # Mutating the disclosed reading must not re-arm the process.
        disclosed.max_running = 999
        self.assertEqual(capacity_module.configured_subagent_admission_schedule().max_running, 5)
        # The configured accessor and the schedule accessor agree.
        self.assertEqual(capacity_module.configured_subagent_max_running(), 5)


class TestCreateSwarmStampsTheAdvisory(unittest.TestCase):
    def setUp(self) -> None:
        capacity_module.configure_subagent_execution_capacity(SubagentRuntimeConfig(max_running=3, max_queued=64))

    def tearDown(self) -> None:
        capacity_module.configure_subagent_execution_capacity(SubagentRuntimeConfig())

    def test_an_accepted_plan_carries_the_advisory(self) -> None:
        with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_decomposer()):
            coordinator = SwarmCoordinator(storage_dir=storage)
            plan = coordinator.create_swarm(goal="stub goal")
        advisory = plan.metrics["admission_advisory"]
        self.assertEqual(advisory["expected_realized_concurrency"], 3)
        self.assertEqual(advisory["inflight_capacity"], 67)
        self.assertIs(advisory["wide_plan_risk"], False)
        self.assertIn("at most 3 subagents", advisory["note"])
        self.assertIsNotNone(coordinator.get_swarm(plan.swarm_id))

    def test_a_wide_plan_is_still_registered_and_labels_the_risk(self) -> None:
        capacity_module.configure_subagent_execution_capacity(SubagentRuntimeConfig(max_running=2, max_queued=1))
        with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_decomposer()):
            coordinator = SwarmCoordinator(storage_dir=storage)
            plan = coordinator.create_swarm(goal="stub goal")
        advisory = plan.metrics["admission_advisory"]
        self.assertIs(advisory["wide_plan_risk"], True)
        self.assertEqual(advisory["expected_realized_concurrency"], 2)
        self.assertIsNotNone(coordinator.get_swarm(plan.swarm_id))

    def test_an_unresolvable_schedule_writes_an_unknown_advisory(self) -> None:
        with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_decomposer()), patch.object(admission, "resolve_admission_schedule", lambda: None):
            coordinator = SwarmCoordinator(storage_dir=storage)
            plan = coordinator.create_swarm(goal="stub goal")
        advisory = plan.metrics["admission_advisory"]
        self.assertIsNone(advisory["max_running"])
        self.assertIsNone(advisory["expected_realized_concurrency"])
        self.assertIn("not resolvable", advisory["note"])

    def test_the_wall_budget_refusal_is_untouched_by_the_advisory(self) -> None:
        with TemporaryDirectory() as storage, patch.object(SwarmTaskDecomposer, "decompose", side_effect=_stub_decomposer(parallel_seconds=120.0)):
            coordinator = SwarmCoordinator(storage_dir=storage)
            with self.assertRaisesRegex(SwarmPlanValidationError, "refused before registration"):
                coordinator.create_swarm(goal="stub goal", budget=SwarmBudget(max_wall_seconds=60.0))
            self.assertEqual(coordinator.list_swarms(), [])


class TestToolResponseCarriesTheAdvisory(unittest.TestCase):
    def test_spawn_response_discloses_the_admission_reality(self) -> None:
        # ``import alpha.tools.builtins.swarm_tool as …`` is order-sensitive:
        # the lazy package facade caches the resolved export under the same
        # name, so once anything has resolved ``swarm_tool`` through the
        # facade this binds the *tool*, not the module, and ``patch.object``
        # then fails with AttributeError.  ``importlib`` reads the module from
        # ``sys.modules`` unconditionally -- the same workaround as
        # ``test_gro_tool.py``.
        swarm_tool_module = importlib.import_module("alpha.tools.builtins.swarm_tool")

        capacity_module.configure_subagent_execution_capacity(SubagentRuntimeConfig(max_running=3, max_queued=64))
        with TemporaryDirectory() as storage:
            coordinator = SwarmCoordinator(storage_dir=storage)
            with patch.object(swarm_tool_module, "get_swarm_coordinator", lambda: coordinator):
                try:
                    response = swarm_tool_module.swarm_tool.invoke(
                        {
                            "action": "spawn",
                            "goal": "one tiny bounded goal",
                            "max_concurrency": 50,
                        }
                    )
                finally:
                    capacity_module.configure_subagent_execution_capacity(SubagentRuntimeConfig())
        self.assertIn("Swarm", response)
        self.assertIn("Process admission (advisory)", response)
        self.assertIn("at most 3 subagents", response)
        self.assertIn("expected_realized_concurrency", response)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
