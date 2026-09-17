"""Tests for Frontier Phase 2 Capabilities:
- Gateway Zero-Touch Autopilot preset resolution
- Visual Multimodal Grounding & Self-Verification (visual_verify_artifact)
- Episodic Reflexion & Failure Memory Engine (ReflexionEngine, manage_reflexion_memory)
- Long-Horizon Multi-Session Boulder Handoff (SessionHandoffManager)
"""

import json
from pathlib import Path
import tempfile
import pytest

from agent_workspace.config.agent_preset_config import resolve_agent_preset
from agent_workspace.tools.builtins.visual_verification_tool import visual_verify_artifact
from agent_workspace.learning.reflexion import ReflexionEngine, get_reflexion_engine
from agent_workspace.tools.builtins.reflexion_tool import manage_reflexion_memory
from agent_workspace.state.handoff import SessionHandoffManager, SessionHandoffPackage, get_handoff_manager
from agent_workspace.tools.builtins.boulder_checkpoint_tool import boulder_checkpoint_manage


class TestAutopilotGatewayPresetResolution:
    def test_auto_preset_resolves_deep_code_for_coding_prompt(self):
        name, preset = resolve_agent_preset("auto", prompt="Fix the failing unit tests and refactor auth controller")
        assert name == "deep_code"

    def test_auto_preset_resolves_research_for_investigation_prompt(self):
        name, preset = resolve_agent_preset("auto", prompt="Survey the market landscape and investigate competitor architectures")
        assert name == "research"

    def test_auto_preset_resolves_discipline_for_safety_prompt(self):
        name, preset = resolve_agent_preset("auto", prompt="Audit compliance invariants, safety gates, and plan gap analysis")
        assert name == "discipline"

    def test_auto_preset_without_prompt_falls_back_to_standard(self):
        name, preset = resolve_agent_preset("auto", prompt="")
        assert name == "standard"


class TestVisualVerificationTool:
    def test_verify_valid_responsive_html(self, tmp_path: Path):
        html_file = tmp_path / "dashboard.html"
        html_file.write_text(
            """<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>.container { padding: 20px; color: #333; }</style>
</head>
<body>
  <div class="container" id="main-content">
    <h1>System Status</h1>
    <canvas id="chart"></canvas>
  </div>
  <script>const ctx = document.getElementById('chart').getContext('2d');</script>
</body>
</html>""",
            encoding="utf-8"
        )
        res_raw = visual_verify_artifact.invoke({
            "artifact_path": str(html_file),
            "expected_elements": ["main-content", "chart"]
        })
        res = json.loads(res_raw)
        assert res["passed"] is True
        assert res["score"] >= 80
        assert res["status"] == "PASS"
        assert res["checks"]["valid_html_structure"] is True
        assert res["checks"]["viewport_configured"] is True
        assert res["checks"]["expected_elements_present"] is True

    def test_verify_missing_file(self, tmp_path: Path):
        res_raw = visual_verify_artifact.invoke({"artifact_path": str(tmp_path / "non_existent.html")})
        res = json.loads(res_raw)
        assert res["passed"] is False
        assert "not found" in res["error"]
        assert res["score"] == 0

    def test_verify_malformed_html_missing_viewport_and_doctype(self, tmp_path: Path):
        html_file = tmp_path / "bad.html"
        html_file.write_text("<div>Just a floating div with no html tag</div>", encoding="utf-8")
        res_raw = visual_verify_artifact.invoke({"artifact_path": str(html_file)})
        res = json.loads(res_raw)
        assert res["passed"] is False
        assert res["checks"]["valid_html_structure"] is False
        assert res["checks"]["viewport_configured"] is False
        assert len(res["warnings"]) > 0

    def test_verify_valid_svg_artifact(self, tmp_path: Path):
        svg_file = tmp_path / "graphic.svg"
        svg_file.write_text(
            '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="40" fill="blue"/></svg>',
            encoding="utf-8"
        )
        res_raw = visual_verify_artifact.invoke({"artifact_path": str(svg_file)})
        res = json.loads(res_raw)
        assert res["passed"] is True
        assert res["checks"]["valid_svg"] is True
        assert res["checks"]["viewbox_defined"] is True


class TestReflexionMemoryEngine:
    def test_record_and_query_reflections(self, tmp_path: Path):
        db_path = tmp_path / "test_reflexions.db"
        engine = ReflexionEngine(db_path)

        entry = engine.record_reflection(
            problem_signature="pytest_windows_path_error",
            observed_failure="CommandNotFoundException: C:\\Users\\PREM not recognized",
            root_cause="Unquoted path with spaces executed in PowerShell without call operator",
            lesson="Always wrap paths containing spaces in quotes and use PowerShell call operator &",
            confidence=0.95,
            workspace=str(tmp_path)
        )
        assert entry.id is not None
        assert entry.problem_signature == "pytest_windows_path_error"

        # Query reflections
        matches = engine.query_reflections("PowerShell path spaces")
        assert len(matches) >= 1
        assert matches[0].problem_signature == "pytest_windows_path_error"

        # Format for prompt context
        prompt_block = engine.format_reflections_for_prompt(matches)
        assert "<historical_reflexion_lessons>" in prompt_block
        assert "Crucial Lesson: Always wrap paths containing spaces" in prompt_block

    def test_manage_reflexion_memory_tool(self, tmp_path: Path):
        db_path = tmp_path / "test_tool_reflexions.db"
        # Point singleton to temporary db
        get_reflexion_engine(db_path)

        # Record via tool
        record_res = manage_reflexion_memory.invoke({
            "action": "record",
            "problem_signature": "git_detached_head_commit",
            "observed_failure": "Commit made on detached HEAD lost after checkout",
            "root_cause": "Direct git checkout of SHA instead of branch creation",
            "lesson": "Always create a named candidate branch or micro-checkpoint before edits",
            "confidence": 0.90
        })
        rec = json.loads(record_res)
        assert rec["status"] == "RECORDED"

        # Query via tool
        query_res = manage_reflexion_memory.invoke({
            "action": "query",
            "query": "detached HEAD commit"
        })
        q = json.loads(query_res)
        assert q["matches_found"] >= 1
        assert "git_detached_head_commit" in q["reflections"][0]["problem_signature"]


class TestSessionHandoffManager:
    def test_handoff_creation_and_restore(self, tmp_path: Path):
        mgr = SessionHandoffManager(tmp_path)
        pkg = SessionHandoffPackage(
            work_id="mission_alpha_01",
            task_objective="Architect enterprise gateway with zero false completion",
            completed_milestones=[
                {"step": "Setup AST Repo Mapper", "evidence": "Tested with ast.parse"},
                {"step": "Implement FinishFirst Verifier", "evidence": "Tested with test_autopilot_and_verifier.py"}
            ],
            pending_milestones=[
                "Build visual verification engine",
                "Deploy frontend mode selector"
            ],
            active_hypotheses=["Visual verification detects broken layout bounds"],
            key_artifacts=["outputs/dashboard.html", "src/core/threads/hooks.ts"],
            next_action="Build visual verification engine"
        )
        saved_file = mgr.save_handoff(pkg)
        assert saved_file.exists()

        loaded = mgr.load_latest_handoff()
        assert loaded is not None
        assert loaded.work_id == "mission_alpha_01"
        assert len(loaded.completed_milestones) == 2
        assert len(loaded.pending_milestones) == 2

        formatted = mgr.format_for_context(loaded)
        assert "<session_handoff_continuation>" in formatted
        assert "Active Objective: Architect enterprise gateway with zero false completion" in formatted
        assert "[x] Setup AST Repo Mapper (Evidence: Tested with ast.parse)" in formatted
        assert "[ ] Build visual verification engine" in formatted

    def test_boulder_checkpoint_tool_handoff_actions(self, tmp_path: Path):
        custom_boulder_file = str(tmp_path / "boulder.json")
        boulder_checkpoint_manage.invoke({
            "action": "create",
            "task": "Migrate system to frontier agent architecture",
            "checklist": ["Design core tools", "Test integration", "Verify doctor"],
            "custom_path": custom_boulder_file
        })
        boulder_checkpoint_manage.invoke({
            "action": "update_step",
            "step_index": 0,
            "completed": True,
            "evidence": "Tools implemented and exported",
            "custom_path": custom_boulder_file
        })

        # Test create_handoff action
        handoff_res = boulder_checkpoint_manage.invoke({
            "action": "create_handoff",
            "custom_path": custom_boulder_file
        })
        assert "Successfully created session handoff package" in handoff_res
        assert "<session_handoff_continuation>" in handoff_res
        assert "[x] Design core tools" in handoff_res

        # Test restore_handoff action
        restore_res = boulder_checkpoint_manage.invoke({
            "action": "restore_handoff",
            "custom_path": custom_boulder_file
        })
        assert "<session_handoff_continuation>" in restore_res
        assert "Active Objective: Migrate system to frontier agent architecture" in restore_res
