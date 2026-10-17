"""The governor reports measured models, never hardcoded names.

**The defect this pins.** ``SwarmResourceGovernor`` carried a ``MODEL_TIERS``
table of four hardcoded model names and a ``resolve_model_for_role`` that
matched role-name substrings.  Neither was called - the runner resolves models
through ``resolve_subagent_model_name(..., app_config=...)`` and
``task_node.model_override``.  But ``get_status()`` still *published* those
names, and it is served by ``GET /api/swarms/governor/status`` and by the swarm
tool's own ``governor`` action.

So an operator surface, and the model itself, were told the swarm routes to
models Alpha does not use.  That also breaks the root rule that every model name
lives in ``config.yaml``, never in code.

The load-bearing assertions are the negative ones: no hardcoded name survives,
and an unmeasured state is ``None`` rather than a fallback.
"""

from __future__ import annotations

import unittest

from alpha.swarm.governor import (
    SwarmResourceGovernor,
    _requested_models,
    _used_models,
)
from alpha.swarm.models import SwarmPlan, SwarmTaskNode

ROUTING_METHOD = SwarmResourceGovernor.ROUTING_METHOD

# The four names that used to be hardcoded.  If any of these reappears in the
# module source, the defect is back.
FORBIDDEN_MODEL_NAMES = (
    "claude-3-7-sonnet",
    "gemini-2.5-flash",
    "gpt-4o",
    "ollama/qwen2.5-coder",
)


def _plan(**task_specs: dict) -> SwarmPlan:
    plan = SwarmPlan(swarm_id="s1", goal="g", mode="auto")
    for task_id, spec in task_specs.items():
        plan.tasks[task_id] = SwarmTaskNode(task_id=task_id, objective="do a thing", **spec)
    return plan


class TestNoHardcodedModelNames(unittest.TestCase):
    def test_executable_code_carries_no_hardcoded_model_name(self) -> None:
        """Check executable code, not prose.

        The governor's docstring *records* the removal and names the four values
        it used to hardcode - that is documentation of the defect, not the
        defect.  What must never happen is a name reaching a routing field, so
        docstrings are stripped before the search.
        """

        import ast
        import inspect

        from alpha.swarm import governor as module

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                    if isinstance(node.body[0].value.value, str):
                        node.body = node.body[1:]
        executable = ast.unparse(tree)
        for name in FORBIDDEN_MODEL_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(name, executable, f"{name} must not appear in executable governor code")

    def test_status_never_emits_a_hardcoded_model_name(self) -> None:
        """The defect as the operator and the model actually experienced it."""

        governor = SwarmResourceGovernor()
        for plan in (None, SwarmPlan(swarm_id="s", goal="g", mode="auto")):
            status = governor.get_status(plan)
            emitted = str(status)
            for name in FORBIDDEN_MODEL_NAMES:
                with self.subTest(plan=plan, name=name):
                    self.assertNotIn(name, emitted)

    def test_the_resolver_is_gone(self) -> None:
        self.assertFalse(hasattr(SwarmResourceGovernor, "resolve_model_for_role"))
        self.assertFalse(hasattr(SwarmResourceGovernor, "MODEL_TIERS"))

    def test_the_routing_method_names_the_real_path(self) -> None:
        self.assertEqual(ROUTING_METHOD, "config_yaml_task_override_v1")


class TestUnmeasuredIsNoneNotFallback(unittest.TestCase):
    def test_no_plan_reports_none_for_both_model_lists(self) -> None:
        """``None`` means "not measured yet".  ``[]`` would mean "nothing ran"."""

        governor = SwarmResourceGovernor()
        status = governor.get_status()
        self.assertIsNone(status["models_requested"])
        self.assertIsNone(status["models_used"])

    def test_an_empty_plan_reports_empty_lists(self) -> None:
        governor = SwarmResourceGovernor()
        status = governor.get_status(SwarmPlan(swarm_id="s", goal="g", mode="auto"))
        self.assertEqual(status["models_requested"], [])
        self.assertEqual(status["models_used"], [])


class TestRequestedModels(unittest.TestCase):
    def test_distinct_non_none_overrides_are_reported(self) -> None:
        plan = _plan(a={"model_override": "union-alpha"}, b={"model_override": None}, c={"model_override": "union-alpha"})
        self.assertEqual(_requested_models(plan), ["union-alpha"])

    def test_none_overrides_are_omitted_not_invented(self) -> None:
        """``None`` means "inherit the configured default" - not a model name."""

        plan = _plan(a={"model_override": None})
        self.assertEqual(_requested_models(plan), [])

    def test_no_plan_is_none(self) -> None:
        self.assertIsNone(_requested_models(None))


class TestUsedModels(unittest.TestCase):
    def test_models_are_read_from_worker_evidence(self) -> None:
        plan = _plan(a={"model_override": "union-alpha"})
        plan.tasks["a"].evidence = [{"model": "union-alpha", "source": "worktree"}]
        self.assertEqual(_used_models(plan), ["union-alpha"])

    def test_a_requested_model_that_never_ran_is_not_reported_as_used(self) -> None:
        """The distinction the whole change exists for.

        A plan can request a model and never reach it - a refused launch, a
        failed worker, a fallback.  Reporting it as "used" would attribute work
        to a model that did not do it.
        """

        plan = _plan(a={"model_override": "union-alpha"})
        plan.tasks["a"].evidence = [{"model": "some-other-model", "source": "ephemeral"}]
        self.assertEqual(_requested_models(plan), ["union-alpha"])
        self.assertEqual(_used_models(plan), ["some-other-model"])

    def test_evidence_without_a_model_label_is_skipped(self) -> None:
        plan = _plan(a={"model_override": "union-alpha"})
        plan.tasks["a"].evidence = [{"source": "worktree"}, {"model": ""}, {"model": 42}]
        self.assertEqual(_used_models(plan), [])

    def test_duplicate_labels_are_deduped_in_order(self) -> None:
        plan = _plan(a={"model_override": "m1"}, b={"model_override": "m2"})
        plan.tasks["a"].evidence = [{"model": "m1"}]
        plan.tasks["b"].evidence = [{"model": "m2"}, {"model": "m1"}]
        self.assertEqual(_used_models(plan), ["m1", "m2"])


class TestStatusShape(unittest.TestCase):
    def test_status_carries_both_lists_and_the_routing_method(self) -> None:
        governor = SwarmResourceGovernor()
        status = governor.get_status(_plan(a={"model_override": "union-alpha"}))
        self.assertEqual(status["models_requested"], ["union-alpha"])
        self.assertEqual(status["routing_method"], "config_yaml_task_override_v1")
        # Throttling state is unchanged by this work.
        self.assertIn("throttled_providers", status)
        self.assertIn("is_throttling_active", status)
        self.assertIn("last_success", status)

    def test_status_is_safe_without_a_lock_free_plan(self) -> None:
        """The route may be read before any plan exists; it must not raise."""

        SwarmResourceGovernor().get_status(None)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
