"""The scenario catalogue and plan-preview routes on `/api/subagents`.

These are the first consumers of `alpha.subagents.plan`, so what they must not
do matters more than what they return: neither route may dispatch, and the
preview route must not let a client talk itself into an executable plan.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.gateway.app import app


def _client() -> TestClient:
    return TestClient(app)


class TestScenarioCatalogue(unittest.TestCase):
    def test_the_catalogue_is_served_with_its_shadow_policy(self):
        with _client() as client:
            response = client.get("/api/subagents/scenarios")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["shadow_default"], "the catalogue must carry the policy rather than a client copying it")
        ids = {row["id"] for row in payload["scenarios"]}
        self.assertIn("direct", ids)
        self.assertIn("tournament", ids)

    def test_every_row_declares_its_own_preconditions(self):
        with _client() as client:
            rows = client.get("/api/subagents/scenarios").json()["scenarios"]
        for row in rows:
            with self.subTest(scenario=row["id"]):
                self.assertTrue(row["summary"])
                if row["id"] != "direct":
                    self.assertTrue(row["requirements"])


class TestPlanPreview(unittest.TestCase):
    def _plan(self, **body) -> dict:
        with _client() as client:
            response = client.post("/api/subagents/plan", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_preview_is_never_executable(self):
        """The single most important property of this route.

        A resolved plan here is a recommendation. If this ever returned
        `is_executable: true` the UI would be telling an operator their request
        was routed by a classifier that cannot route anything.
        """

        payload = self._plan(
            prompt="run a tournament between these two designs",
            delegation_enabled=True,
            arena_configured=True,
        )
        self.assertTrue(payload["shadow"])
        self.assertFalse(payload["is_executable"])

    def test_a_delegation_off_run_never_proposes_a_child(self):
        payload = self._plan(prompt="audit every module in parallel", delegation_enabled=False)
        self.assertEqual(payload["scenario"], "direct")
        self.assertEqual(payload["admitted_children"], 0)

    def test_an_unmeasured_capability_reads_as_unknown_not_absent(self):
        """Omitting the field is `None`, and `None` must say so.

        Sending no Arena measurement is an unanswered question, and answering it
        "Arena is not available" would be a claim nobody made.
        """

        payload = self._plan(prompt="run a tournament between these two designs", delegation_enabled=True)
        considered = dict(payload["considered"])
        self.assertIn("tournament", considered)
        self.assertIn("could not be read", considered["tournament"])

    def test_an_unknown_scenario_is_refused_rather_than_routed(self):
        payload = self._plan(prompt="do a thing", delegation_enabled=True, scenario="telepathy")
        self.assertEqual(payload["admitted_children"], 0)
        self.assertTrue(any("unknown to this build" in d for d in payload["degradations"]))

    def test_a_client_cannot_ask_for_an_executable_plan(self):
        """`extra="forbid"` is what makes the no-shadow-field claim real.

        Without it a client could post `shadow: false` and receive a field the
        route silently dropped - an accepted-then-ignored write.
        """

        with _client() as client:
            response = client.post(
                "/api/subagents/plan",
                json={"prompt": "do a thing", "shadow": False},
            )
        self.assertEqual(response.status_code, 422)

    def test_a_budget_reduction_names_its_bound(self):
        payload = self._plan(
            prompt="run a tournament between these two designs",
            delegation_enabled=True,
            arena_configured=True,
            child_budget=3,
        )
        self.assertEqual(payload["admitted_children"], 3)
        self.assertLess(payload["admitted_children"], payload["estimated_children"])
        self.assertTrue(any("budget of 3" in d for d in payload["degradations"]))

    def test_an_empty_prompt_is_refused_before_any_work(self):
        with _client() as client:
            response = client.post("/api/subagents/plan", json={"prompt": ""})
        self.assertEqual(response.status_code, 422)


class TestNoDispatchPathExists(unittest.TestCase):
    """A preview route must not have grown a side effect.

    Parsed from source rather than grepped, so a docstring *naming* an engine
    (which this route's own comment does) is not mistaken for a call to one.
    """

    def test_the_plan_route_does_not_call_an_engine(self):
        import ast
        import inspect

        from app.gateway.routers import subagents as module

        source = inspect.getsource(module.resolve_execution_plan)
        tree = ast.parse(source.lstrip() if source.startswith(" ") else source)
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called.add(func.attr)
        for forbidden in ("start", "execute", "dispatch", "create_arena_run", "start_arena_run"):
            self.assertNotIn(forbidden, called)

    def test_the_catalogue_route_reads_no_configuration(self):
        """The catalogue is static data.

        If it ever started reading config it would be reporting deployment state
        under a name that promises a vocabulary.
        """

        import inspect

        from app.gateway.routers import subagents as module

        source = inspect.getsource(module.list_scenarios)
        self.assertNotIn("get_app_config", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
