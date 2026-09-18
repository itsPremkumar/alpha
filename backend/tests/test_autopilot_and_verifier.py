"""Automated test suite for ExecutiveAutopilot, FinishFirstVerifier, and AVO Backtracking."""

import json
import os
import shutil
import tempfile
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from agent_workspace.orchestration.autopilot import ExecutiveAutopilot
from agent_workspace.agents.middlewares.finish_first_verifier_middleware import FinishFirstVerifierMiddleware
from agent_workspace.tools.builtins.code_agentic_core import (
    auto_test_and_repair,
    manage_code_checkpoint,
)


class TestAutopilotAndVerifier(unittest.TestCase):
    def setUp(self):
        self.autopilot = ExecutiveAutopilot()
        self.test_dir = tempfile.mkdtemp(prefix="agent_workspace_ap_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_autopilot_coding_intent(self):
        prompt = "Fix the TypeError in backend/server.py and make sure pytest passes"
        plan = self.autopilot.classify_intent(prompt)
        self.assertEqual(plan.intent, "coding")
        self.assertEqual(plan.recommended_preset, "deep_code")
        self.assertIn("auto_test_and_repair", plan.requires_tools)

    def test_autopilot_research_intent(self):
        prompt = "Deep research: survey recent papers on agentic variation operators and market analysis"
        plan = self.autopilot.classify_intent(prompt)
        self.assertEqual(plan.intent, "research")
        self.assertEqual(plan.recommended_preset, "research")

    def test_autopilot_governance_intent(self):
        prompt = "Perform a plan invariant review and gap analysis audit for our deployment plan"
        plan = self.autopilot.classify_intent(prompt)
        self.assertEqual(plan.intent, "governance")
        self.assertEqual(plan.recommended_preset, "discipline")

    def test_autopilot_plan_only_intent(self):
        prompt = "Draft an implementation plan only. Do not modify files yet."
        plan = self.autopilot.classify_intent(prompt)
        self.assertEqual(plan.intent, "planning")
        self.assertEqual(plan.recommended_preset, "plan")

    def test_autopilot_respects_explicit_preset(self):
        prompt = "Fix the bug in main.py"
        preset = self.autopilot.resolve_preset(prompt, requested_preset="minimal")
        self.assertEqual(preset, "minimal")

        preset_auto = self.autopilot.resolve_preset(prompt, requested_preset="auto")
        self.assertEqual(preset_auto, "deep_code")

    def test_finish_first_verifier_middleware(self):
        middleware = FinishFirstVerifierMiddleware(enabled=True)

        class DummyRuntime:
            context = {"thread_id": "test_t1", "run_id": "r1"}

        runtime = DummyRuntime()

        # Case 1: Code written without test verification
        state_unverified = {
            "messages": [
                HumanMessage(content="Refactor the auth handler"),
                ToolMessage(content="File updated", name="write_file", tool_call_id="c1"),
                AIMessage(content="I have refactored the auth handler successfully!"),
            ]
        }
        res_unverified = middleware.after_model(state_unverified, runtime)
        self.assertIsNotNone(res_unverified)
        updated_msg = res_unverified["messages"][0]
        self.assertIn("[Finish-First Notice]", updated_msg.content)

        # Case 2: Code written WITH test verification
        state_verified = {
            "messages": [
                HumanMessage(content="Refactor the auth handler"),
                ToolMessage(content="File updated", name="write_file", tool_call_id="c1"),
                ToolMessage(content=json.dumps({"status": "passed"}), name="auto_test_and_repair", tool_call_id="c2"),
                AIMessage(content="I have refactored the auth handler and all tests pass!"),
            ]
        }
        res_verified = middleware.after_model(state_verified, runtime)
        self.assertIsNone(res_verified)

    def test_avo_auto_rollback_on_failure(self):
        test_file = os.path.join(self.test_dir, "lib.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def stable_fn(): return True\n")

        # Create baseline checkpoint
        manage_code_checkpoint.invoke({
            "action": "create",
            "root_path": self.test_dir,
            "target_files": ["lib.py"],
            "label": "stable baseline",
        })

        # Mutate file into broken state
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def broken_syntax(:\n")

        # Execute auto_rollback_on_failure
        res = manage_code_checkpoint.invoke({
            "action": "auto_rollback_on_failure",
            "root_path": self.test_dir,
        })
        data = json.loads(res)
        self.assertEqual(data.get("status"), "rolled_back_to_passing")

        # Verify restoration
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "def stable_fn(): return True\n")


if __name__ == "__main__":
    unittest.main()
