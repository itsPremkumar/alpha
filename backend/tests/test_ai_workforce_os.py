"""Automated test suite for AI Workforce OS and Bot Multiple Profile Mode enhancements.

Verifies:
1. Three-Level Context Router (Level 1 Bot Memory, Level 2 Project Memory, Level 3 Task Scratchpad, Cross-project isolation)
2. Role-Based Tool Permission Rings (Researcher blocked from code/bash, Coder allowed, approval gate)
3. Automatic Worktree Execution Lifecycle Hook
4. Dynamic DAG Workflow Compiler & Wave Advancer
5. Gateway War Room Endpoint (GET /api/projects/{id}/war-room)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.gateway.app import create_app
from alpha.bots.permissions import get_permission_gate
from alpha.planning.dag_orchestrator import get_dag_orchestrator
from alpha.projects.context_router import ThreeLevelContextRouter
from alpha.projects.worktree_hook import WorktreeTaskContext


def test_three_level_context_router():
    with tempfile.TemporaryDirectory() as tmp_dir:
        router = ThreeLevelContextRouter(base_dir=tmp_dir)

        # 1. Update Level 1: Bot Global Memory
        router.update_bot_memory("coder", {"preferred_style": "clean PEP8", "heuristics": ["avoid raw SQL"]})
        bot_mem = router.get_bot_memory("coder")
        assert bot_mem["preferred_style"] == "clean PEP8"

        # 2. Update Level 2: Project Shared Memory
        router.update_project_memory("project-alpha", {"architecture_pattern": "Event-Driven Microservices"})
        router.update_project_memory("project-beta", {"architecture_pattern": "Modular Monolith"})

        # 3. Verify Project Isolation (Project Alpha does not receive Project Beta memory)
        ctx_alpha = router.build_isolated_context(
            project_id="project-alpha",
            bot_name="coder",
            task_id="task-01",
            task_scratchpad={"diff": "+ import asyncio"},
        )
        assert ctx_alpha.level1_bot_memory["preferred_style"] == "clean PEP8"
        assert ctx_alpha.level2_project_memory["architecture_pattern"] == "Event-Driven Microservices"
        assert "Modular Monolith" not in str(ctx_alpha.level2_project_memory)

        snippet = ctx_alpha.system_prompt_snippet()
        assert "Level 1: Bot Personal Memory" in snippet
        assert "Level 2: Shared Project State & Constraints" in snippet
        assert "Level 3: Task Execution Scratchpad" in snippet


def test_tool_permission_gate():
    gate = get_permission_gate()

    # Researcher: allowed to read and search, blocked from modifying files or running arbitrary bash
    ok, reason, approval = gate.check_permission("researcher", "web_search")
    assert ok is True
    assert approval is False

    ok, reason, approval = gate.check_permission("researcher", "view_file")
    assert ok is True

    ok, reason, approval = gate.check_permission("researcher", "replace_file_content")
    assert ok is False
    assert "Permission Denied" in reason

    ok, reason, approval = gate.check_permission("researcher", "run_command")
    assert ok is False

    # Coder: allowed to edit files, write files, run tests
    ok, reason, approval = gate.check_permission("coder", "replace_file_content")
    assert ok is True

    ok, reason, approval = gate.check_permission("coder", "write_to_file")
    assert ok is True

    # Coder: approval required for high-risk destructive tools
    ok, reason, approval = gate.check_permission("coder", "deploy_production")
    assert ok is False
    assert approval is True

    # Supervisor / Admin: allow all
    ok, reason, approval = gate.check_permission("supervisor", "any_arbitrary_tool")
    assert ok is True


def test_worktree_execution_lifecycle():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir) / "test_repo"
        repo.mkdir()
        # Initialize a basic git repo
        import subprocess
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=False)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(repo), capture_output=True, check=False)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True, check=False)
        (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=False)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo), capture_output=True, check=False)

        # Run WorktreeTaskContext
        with WorktreeTaskContext(
            repo_path=repo,
            agent="coder",
            project_id="proj-test",
            task_id="feat-login",
            cleanup_on_exit=True,
            generate_patch_on_exit=True,
        ) as wt:
            assert wt.path.exists()
            (wt.path / "login.py").write_text("def login(): return True\n", encoding="utf-8")

        # After exit, worktree should be safely cleaned up
        assert not wt.path.exists()


def test_dynamic_dag_orchestrator():
    orchestrator = get_dag_orchestrator()
    schedule = orchestrator.compile_plan_to_dag("Build distributed multi-agent payment gateway")

    assert schedule.total_waves >= 1
    assert len(schedule.all_nodes) > 0

    # Ready tasks in wave 1
    wave1_ready = schedule.ready_tasks_for_wave(1)
    assert len(wave1_ready) > 0

    # Advance wave 1
    advanced = orchestrator.advance_wave(schedule.dag_id, 1)
    assert len(advanced) == len(wave1_ready)
    assert all(t.status == "completed" for t in advanced)


def test_war_room_gateway_endpoint():
    app = create_app()
    with TestClient(app) as client:
        # 1. Create project first
        create_resp = client.post("/api/projects", json={"name": "WarRoom Test Project", "instructions": "Testing War Room"})
        assert create_resp.status_code == 201
        project_id = create_resp.json()["id"]

        # 2. Join a bot to the project
        join_resp = client.post(f"/api/projects/{project_id}/join", json={"bot_name": "coder", "role_in_project": "backend"})
        assert join_resp.status_code == 200

        # 3. Query War Room endpoint
        war_resp = client.get(f"/api/projects/{project_id}/war-room")
        assert war_resp.status_code == 200
        data = war_resp.json()

        assert data["project_id"] == project_id
        assert data["status"] == "active"
        assert "members" in data
        assert any(m["bot_name"] == "coder" for m in data["members"])
        assert "active_locks" in data
        assert "handoffs" in data
        assert "decisions" in data
        assert "events" in data
        assert "kill_switch" in data

