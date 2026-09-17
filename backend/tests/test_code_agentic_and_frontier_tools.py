"""Integration tests for Code Agentic Core and Frontier Governance Tools."""

import json
import os
import shutil
import tempfile
import unittest

from agent_workspace.tools.builtins.code_agentic_core import (
    auto_test_and_repair,
    generate_repo_map,
    manage_code_checkpoint,
)
from agent_workspace.tools.builtins.astra_security_tool import astra_security_manage
from agent_workspace.tools.builtins.discipline_team_tool import (
    consult_plan_gap_analysis,
    review_plan_invariant_gate,
)
from agent_workspace.tools.builtins.mission_hierarchy_tool import (
    manage_mission_hierarchy,
    schedule_work_queue,
)


class TestCodeAgenticAndFrontierTools(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="deerflow_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_generate_repo_map(self):
        pkg_dir = os.path.join(self.test_dir, "sample_pkg")
        os.makedirs(pkg_dir, exist_ok=True)
        py_file = os.path.join(pkg_dir, "module.py")
        with open(py_file, "w", encoding="utf-8") as f:
            f.write(
                "class AlphaService:\n"
                "    def execute(self):\n"
                "        pass\n\n"
                "def helper_func(x: int) -> int:\n"
                "    return x * 2\n"
            )

        repo_map = generate_repo_map.invoke({"root_path": self.test_dir, "max_depth": 3})
        self.assertIn("AlphaService", repo_map)
        self.assertIn("helper_func", repo_map)
        self.assertIn("[DIR] sample_pkg", repo_map)

    def test_manage_code_checkpoint(self):
        os.system(f'git init "{self.test_dir}" >nul 2>&1')
        test_file = os.path.join(self.test_dir, "file.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("initial content")

        os.system(f'git -C "{self.test_dir}" add . >nul 2>&1')
        os.system(f'git -C "{self.test_dir}" commit -m "initial" >nul 2>&1')

        res_save = manage_code_checkpoint.invoke({
            "action": "create",
            "root_path": self.test_dir,
            "target_files": ["file.txt"],
            "label": "before modification",
        })
        save_data = json.loads(res_save)
        self.assertEqual(save_data.get("status"), "created")

        with open(test_file, "w", encoding="utf-8") as f:
            f.write("broken content")

        res_rollback = manage_code_checkpoint.invoke({
            "action": "rollback",
            "root_path": self.test_dir,
        })
        rollback_data = json.loads(res_rollback)
        self.assertEqual(rollback_data.get("status"), "rolled_back")

        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "initial content")

    def test_astra_security_manage(self):
        check_path = astra_security_manage.invoke({
            "action": "check_path",
            "path": "backend/packages/harness/agent_workspace",
        })
        data = json.loads(check_path)
        self.assertEqual(data.get("status"), "allowed")

        text = "My secret token is sk-ant-api03-abcdef12345678901234567890"
        redacted = astra_security_manage.invoke({
            "action": "redact_text",
            "raw_text": text,
        })
        self.assertNotIn("abcdef12345678901234567890", redacted)
        self.assertIn("[REDACTED", redacted)

    def test_discipline_team_consultation_and_review(self):
        gap_res = consult_plan_gap_analysis.invoke({
            "task_title": "Build Autonomous Frontend Dashboard",
            "task_description": "Create canvas widget and reactive chart rendering",
            "proposed_steps_csv": "Draft spec, Build widget, Write tests",
            "is_visual_or_frontend": True,
        })
        gap_data = json.loads(gap_res)
        self.assertEqual(gap_data.get("model_family"), "anthropic/claude-3-7-sonnet")
        self.assertIn("gaps_identified", gap_data)

        inv_res = review_plan_invariant_gate.invoke({
            "task_goal": "Optimize search index",
            "proposed_steps_csv": "Benchmark existing, Refactor AST, Re-test",
            "constraints_csv": "No breaking API changes",
            "budget_cap_usd": 15.0,
        })
        inv_data = json.loads(inv_res)
        self.assertTrue(inv_data.get("approved"))
        self.assertEqual(inv_data.get("model_family"), "openai/gpt-4o")

    def test_mission_hierarchy_and_work_queue(self):
        goal_res = manage_mission_hierarchy.invoke({
            "action": "create_goal",
            "name": "Production Grade Agent Harness",
            "description": "Achieve high-end autonomous coding stability",
        })
        goal_data = json.loads(goal_res)
        self.assertEqual(goal_data.get("status"), "created")
        goal_id = goal_data["goal"]["id"]

        mission_res = manage_mission_hierarchy.invoke({
            "action": "create_mission",
            "parent_id": goal_id,
            "name": "Implement Core Agentic Tools",
            "description": "Register AST repo map and auto test runner",
            "constraints_csv": "Windows CP1252 safe, 0 type errors",
        })
        mission_data = json.loads(mission_res)
        self.assertEqual(mission_data.get("status"), "created")

        task_res = schedule_work_queue.invoke({
            "action": "add_task",
            "title": "Run doctor.py check",
            "priority_level": "high",
        })
        task_data = json.loads(task_res)
        self.assertEqual(task_data.get("status"), "enqueued")

    def test_auto_test_and_repair(self):
        # Create a passing test in test_dir
        test_py = os.path.join(self.test_dir, "test_sample.py")
        with open(test_py, "w", encoding="utf-8") as f:
            f.write("def test_ok():\n    assert 1 + 1 == 2\n")

        import sys
        res = auto_test_and_repair.invoke({
            "root_path": self.test_dir,
            "test_command": f'"{sys.executable}" -m pytest test_sample.py',
        })
        data = json.loads(res)
        self.assertEqual(data.get("status"), "passed")
        self.assertEqual(data.get("failure_count"), 0)


if __name__ == "__main__":
    unittest.main()
