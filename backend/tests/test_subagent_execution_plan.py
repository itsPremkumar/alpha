"""The scenario classifier and its `ExecutionPlan`.

The load-bearing claims are all negative ones - things that must *not* happen:

- the plan never dispatches (shadow is on by default);
- the classifier cannot dispatch even by accident (it imports no engine);
- an unavailable scenario is skipped **and recorded**, never silently replaced;
- an unreadable capability read is reported as unknown, not as absent;
- a budget may only lower a plan, and the reduction is named.
"""

from __future__ import annotations

import ast
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from alpha.subagents.plan import SHADOW_BY_DEFAULT, ExecutionPlan, PlanContext, resolve
from alpha.subagents.scenarios import Dispatch, Requirement, ScenarioId, catalogue, get_scenario

_BACKEND = Path(__file__).resolve().parents[1]
_HARNESS = _BACKEND / "packages" / "harness" / "alpha"


class _Perceived:
    """The two fields `plan` reads off a `PerceivedIntent`.

    A stand-in rather than a real `PerceivedIntent`, so these tests pin
    `plan`'s own reading of perception and cannot break when the perception
    dataclass grows a field.
    """

    def __init__(self, complexity: float, tier: str = "deep_reasoning") -> None:
        self.complexity_score = complexity
        self.execution_tier = tier
        self.intent_type = "general"


def _everything_on() -> PlanContext:
    return PlanContext(
        delegation_enabled=True,
        arena_configured=True,
        council_configured=True,
        workflow_configured=True,
    )


class TestShadowMode(unittest.TestCase):
    def test_shadow_is_on_by_default(self):
        self.assertTrue(SHADOW_BY_DEFAULT, "a resolved plan must be advisory until an operator opts in")
        plan = resolve("do a thing", context=_everything_on())
        self.assertTrue(plan.shadow)
        self.assertFalse(plan.is_executable, "a shadow plan is never executable, even with every requirement met")

    def test_shadow_off_is_the_only_way_to_execute(self):
        plan = resolve("do a thing", context=_everything_on(), shadow=False)
        self.assertTrue(plan.is_executable)

    def test_the_flag_travels_with_the_plan(self):
        # A consumer that receives the dict must still be able to tell.
        payload = resolve("run a tournament between these two designs", context=_everything_on()).to_dict()
        self.assertTrue(payload["shadow"])
        self.assertFalse(payload["is_executable"])


class TestImmutability(unittest.TestCase):
    def test_a_resolved_plan_cannot_be_rewritten(self):
        plan = resolve("do a thing", context=_everything_on())
        with self.assertRaises(FrozenInstanceError):
            plan.scenario = ScenarioId.TOURNAMENT  # type: ignore[misc]

    def test_a_plan_can_only_be_replaced_wholesale(self):
        plan = resolve("do a thing", context=_everything_on())
        self.assertIsInstance(plan, ExecutionPlan)
        self.assertEqual(plan, resolve("do a thing", context=_everything_on()), "resolution is deterministic")


class TestNoSignalIsDirect(unittest.TestCase):
    def test_a_plain_request_is_answered_directly(self):
        plan = resolve("rename this variable", context=_everything_on())
        self.assertIs(plan.scenario, ScenarioId.DIRECT)
        self.assertIs(plan.dispatch, Dispatch.NONE)
        self.assertEqual(plan.admitted_children, 0)

    def test_direct_is_reached_by_exhaustion_not_by_a_guess(self):
        plan = resolve("rename this variable", context=_everything_on())
        self.assertIn("no delegation signal", plan.reason)


class TestDelegationRequiresDelegation(unittest.TestCase):
    def test_complexity_alone_cannot_delegate_a_run_that_has_no_task_tool(self):
        """The lead may want a fan-out; the run may not have the tool.

        `delegation_enabled` reads what the run was actually admitted with, not
        what a deployment could do - so a preset that narrows delegation away, or
        an operator who never opted in, must not silently acquire a fan-out.
        """

        plan = resolve(
            "audit every module in parallel",
            context=PlanContext(delegation_enabled=False),
            perceived=_Perceived(complexity=0.9),
        )
        self.assertIs(plan.scenario, ScenarioId.DIRECT)
        skipped = dict(plan.considered)
        self.assertIn("parallel_fan_out", skipped)
        self.assertIn("not available", skipped["parallel_fan_out"])

    def test_complexity_with_delegation_enabled_does_propose_a_child(self):
        plan = resolve(
            "audit every module",
            context=PlanContext(delegation_enabled=True),
            perceived=_Perceived(complexity=0.9),
        )
        self.assertIs(plan.scenario, ScenarioId.PARALLEL_FAN_OUT)
        self.assertIs(plan.dispatch, Dispatch.FAN_OUT)

    def test_moderate_complexity_stays_inline(self):
        plan = resolve("fix the typo", context=_everything_on(), perceived=_Perceived(complexity=0.1))
        self.assertIs(plan.scenario, ScenarioId.DIRECT)


class TestScenarioPriorityAndSkips(unittest.TestCase):
    def test_a_tournament_request_is_not_answered_by_a_fan_out(self):
        """Catalogue order is a priority, not an implementation detail.

        A tournament request also reads as "compare these two things", which the
        fan-out matcher would otherwise claim.
        """

        plan = resolve("run a tournament between these two designs", context=_everything_on())
        self.assertIs(plan.scenario, ScenarioId.TOURNAMENT)
        self.assertIs(plan.dispatch, Dispatch.ARENA)

    def test_an_unavailable_scenario_is_skipped_and_the_skip_is_recorded(self):
        """The property that makes a wrong plan detectable.

        Without `considered`, a request for a tournament that quietly ran direct
        looks exactly like a correct plan.
        """

        plan = resolve(
            "run a tournament between these two designs",
            context=PlanContext(delegation_enabled=True, arena_configured=False),
        )
        skipped = dict(plan.considered)
        self.assertIn("tournament", skipped)
        self.assertIn("Arena is not available", skipped["tournament"])
        self.assertIsNot(plan.scenario, ScenarioId.TOURNAMENT)

    def test_an_unreadable_capability_read_is_unknown_not_absent(self):
        """`None` is not `False`.

        A failed `GET /arena/config` means the operator could not be told, and
        reporting that as "Arena is not available" would be a claim nobody
        measured.
        """

        plan = resolve(
            "run a tournament between these two designs",
            context=PlanContext(delegation_enabled=True, arena_configured=None),
        )
        skipped = dict(plan.considered)
        self.assertIn("tournament", skipped)
        self.assertIn("could not be read", skipped["tournament"])
        self.assertIn("unknown rather than absent", skipped["tournament"])

    def test_a_council_request_needs_no_delegation_tool(self):
        """Deliberation is its own engine, not a fan-out.

        Requiring `delegation_enabled` here would be wrong: the council runs
        models, not subagents.
        """

        plan = resolve(
            "get a council to debate this design decision",
            context=PlanContext(delegation_enabled=False, council_configured=True),
        )
        self.assertIs(plan.scenario, ScenarioId.ADVISORY_COUNCIL)
        self.assertIs(plan.dispatch, Dispatch.COUNCIL)


class TestBudgetMayOnlyLower(unittest.TestCase):
    def test_a_tight_budget_reduces_the_plan_and_names_the_bound(self):
        plan = resolve(
            "run a tournament between these two designs",
            context=PlanContext(delegation_enabled=True, arena_configured=True, child_budget=3),
        )
        self.assertLess(plan.admitted_children, plan.estimated_children)
        self.assertEqual(plan.admitted_children, 3)
        self.assertTrue(any("budget of 3" in d for d in plan.degradations), plan.degradations)

    def test_a_generous_budget_never_raises_the_estimate(self):
        plan = resolve(
            "run a tournament between these two designs",
            context=PlanContext(delegation_enabled=True, arena_configured=True, child_budget=500),
        )
        self.assertLessEqual(plan.admitted_children, plan.estimated_children)

    def test_an_unstated_budget_is_not_unlimited(self):
        """`None` means unstated. It must not be read as "raise it"."""

        plan = resolve(
            "run a tournament between these two designs",
            context=PlanContext(delegation_enabled=True, arena_configured=True, child_budget=None),
        )
        self.assertEqual(plan.admitted_children, plan.estimated_children)
        self.assertEqual(plan.degradations, (), "nothing was lowered, so nothing is reported")


class TestExplicitRequest(unittest.TestCase):
    def test_an_unknown_scenario_is_refused_not_defaulted(self):
        """A caller naming a scenario this build lacks must be able to tell."""

        plan = resolve("do a thing", context=_everything_on(), scenario="telepathy")
        self.assertIs(plan.scenario, ScenarioId.DIRECT)
        self.assertEqual(plan.admitted_children, 0)
        self.assertTrue(any("unknown to this build" in d for d in plan.degradations))

    def test_an_explicit_scenario_honours_the_request_when_it_is_available(self):
        plan = resolve("just do it inline", context=_everything_on(), scenario=ScenarioId.PARALLEL_FAN_OUT)
        self.assertIs(plan.scenario, ScenarioId.PARALLEL_FAN_OUT)
        self.assertIn("requested explicitly", plan.reason)

    def test_an_explicit_scenario_with_an_unmet_requirement_degrades_visibly(self):
        plan = resolve("do it", context=PlanContext(delegation_enabled=False), scenario=ScenarioId.TOURNAMENT)
        self.assertTrue(any("not available" in d for d in plan.degradations), plan.degradations)

    def test_an_explicit_scenario_with_an_unreadable_requirement_says_so(self):
        plan = resolve("do it", context=PlanContext(delegation_enabled=True, arena_configured=None), scenario=ScenarioId.TOURNAMENT)
        self.assertTrue(any("could not be read" in d for d in plan.degradations), plan.degradations)


class TestCatalogue(unittest.TestCase):
    def test_every_scenario_is_projectable(self):
        rows = catalogue()
        self.assertEqual(len(rows), len({row["id"] for row in rows}), "scenario ids must be unique")
        for row in rows:
            with self.subTest(scenario=row["id"]):
                self.assertTrue(row["title"])
                self.assertTrue(row["summary"])
                self.assertIn(row["dispatch"], {str(d) for d in Dispatch})

    def test_direct_needs_nothing_and_others_declare_what_they_need(self):
        direct = get_scenario(str(ScenarioId.DIRECT))
        assert direct is not None
        self.assertEqual(direct.requirements, ())
        for row in catalogue():
            if row["id"] != str(ScenarioId.DIRECT):
                self.assertTrue(row["requirements"], f"{row['id']} must declare its preconditions")

    def test_an_unknown_lookup_returns_none_rather_than_a_default(self):
        self.assertIsNone(get_scenario("telepathy"))

    def test_arena_names_both_of_its_preconditions(self):
        arena = get_scenario(str(ScenarioId.TOURNAMENT))
        assert arena is not None
        self.assertIn(Requirement.ARENA_CONFIGURED, arena.requirements)
        self.assertIn(Requirement.DELEGATION_ENABLED, arena.requirements)


class TestTheClassifierCannotDispatch(unittest.TestCase):
    """The strongest property: an advisory planner must have no engine import.

    Parsed from the AST rather than grepped, because a docstring that *names* an
    engine is exactly what a text search would flag and what is perfectly
    harmless.
    """

    FORBIDDEN_PREFIXES = (
        "alpha.arena",
        "alpha.workflow",
        "alpha.deliberation",
        "alpha.gro",
        "alpha.swarm",
        "alpha.subagents.executor",
        "alpha.tools.builtins.task_tool",
        "alpha.runtime",
    )

    def _imported_modules(self, relative: str) -> set[str]:
        """Runtime imports only - `if TYPE_CHECKING:` bodies are excluded.

        A flat `ast.walk` counts a type-only import as a runtime dependency, which
        would fail the very pin that documents the type-only import as
        intentional. Tracking the guard is what makes the distinction real.
        """

        tree = ast.parse((_HARNESS / relative).read_text(encoding="utf-8"))
        modules: set[str] = set()

        def is_type_checking(node: ast.AST) -> bool:
            test = getattr(node, "test", None)
            if test is None:
                return False
            name = getattr(test, "id", None) or getattr(test, "attr", None)
            return name == "TYPE_CHECKING"

        def visit(body: list[ast.stmt], type_only: bool) -> None:
            for node in body:
                if isinstance(node, ast.If) and is_type_checking(node):
                    visit(node.body, True)
                    visit(node.orelse, type_only)
                    continue
                if type_only:
                    continue
                if isinstance(node, ast.Import):
                    modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules.add(node.module)

        visit(tree.body, False)
        return modules

    def test_the_planner_imports_no_engine(self):
        for relative in ("subagents/plan.py", "subagents/scenarios.py"):
            with self.subTest(module=relative):
                for imported in self._imported_modules(relative):
                    self.assertFalse(
                        imported.startswith(self.FORBIDDEN_PREFIXES),
                        f"{relative} imports {imported}; the planner must not be able to dispatch",
                    )

    def test_the_planner_does_not_import_runmanager(self):
        for relative in ("subagents/plan.py", "subagents/scenarios.py"):
            with self.subTest(module=relative):
                self.assertNotIn("app.gateway.deps", self._imported_modules(relative))

    def test_a_type_only_perception_import_is_not_a_runtime_dependency(self):
        """`PerceivedIntent` is a `TYPE_CHECKING` import on purpose.

        The planner reads two fields off perception; importing the module for real
        would drag the whole dynamic-workflow perception stack into a classifier
        that runs on every prompt.
        """

        source = (_HARNESS / "subagents" / "plan.py").read_text(encoding="utf-8")
        self.assertIn("if TYPE_CHECKING:", source)
        # The runtime import set must therefore not contain the engine module.
        self.assertNotIn("alpha.workflow.dynamic_perception", self._imported_modules("subagents/plan.py"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
