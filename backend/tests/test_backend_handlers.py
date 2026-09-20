"""Automated tests for Concrete Backend Slash Command Handlers.

Verifies:
1. /skill:create, /skill:list, /skill:test against real SkillStorage
2. /loop:start, /loop:status, /loop:pause, /loop:resume against ContinuousGoalRunner
3. /goal:decompose against CognitiveMetaPlanner
4. /subagent:spawn, /subagent:list against SubagentLifecycleManager
5. /doctor, /compact, /security-review against system runtime
6. Gateway API dispatch execution via TestClient
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gateway.app import create_app
from agent_workspace.commands import command_registry


@pytest.fixture(autouse=True)
def _isolated_lifecycle_home(tmp_path, monkeypatch):
    """Keep spawn records out of the developer's real AGENT_WORKSPACE_HOME.

    The lifecycle manager persists every spawn to disk and reloads them on
    construction; without isolation, repeated runs accumulate RUNNING records
    until the per-parent cap rejects new spawns.
    """
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    import agent_workspace.subagents.lifecycle as lifecycle_mod

    monkeypatch.setattr(lifecycle_mod, "_GLOBAL_LIFECYCLE_MANAGER", None)
    yield


def test_skill_creator_handlers():
    # 1. Create skill
    res_create = command_registry.execute("/skill:create test-parser description: Extract structured invoice data from scanned documents")
    assert res_create.status == "success"
    assert "test-parser" in res_create.output
    assert res_create.data.get("skill_name") == "test-parser"

    # 2. List skills
    res_list = command_registry.execute("/skill:list")
    assert res_list.status == "success"
    assert res_list.data.get("total", 0) > 0

    # 3. Test skill validity
    res_test = command_registry.execute("/skill:test test-parser")
    assert res_test.status == "success"
    assert res_test.data.get("passed") is True


def test_loop_handlers():
    # 1. Start loop
    res_start = command_registry.execute("/loop:start Deploy high-availability Kubernetes cluster")
    assert res_start.status == "success"
    goal_id = res_start.data.get("goal_id")
    assert goal_id is not None
    assert "Continuous Autonomous Loop active" in res_start.output

    # 2. Loop status
    res_status = command_registry.execute("/loop:status")
    assert res_status.status == "success"
    assert res_status.data.get("active_loops", 0) > 0

    # 3. Pause loop
    res_pause = command_registry.execute("/loop:pause")
    assert res_pause.status == "success"

    # 4. Resume loop
    res_resume = command_registry.execute("/loop:resume")
    assert res_resume.status == "success"


def test_goal_decompose_handler():
    res_decomp = command_registry.execute("/goal:decompose Build an automated trading execution engine")
    assert res_decomp.status == "success"
    assert "Autonomous Goal Decomposition" in res_decomp.output
    assert "decision" in res_decomp.data
    assert "execution_waves" in res_decomp.data


def test_subagent_handlers():
    # 1. Spawn subagent
    res_spawn = command_registry.execute("/subagent:spawn security-auditor Inspect smart contract bytecode")
    assert res_spawn.status == "success"
    subagent_id = res_spawn.data.get("subagent_id")
    assert subagent_id is not None
    assert "Subagent spawned successfully" in res_spawn.output

    # 2. List subagents
    res_list = command_registry.execute("/subagent:list")
    assert res_list.status == "success"
    assert res_list.data.get("total", 0) > 0


def test_doctor_and_security_handlers():
    # Doctor check
    res_doc = command_registry.execute("/doctor")
    assert res_doc.status == "success"
    assert "Alpha System Doctor" in res_doc.output
    assert res_doc.data.get("status") == "ready"

    # Security review
    res_sec = command_registry.execute("/security-review")
    assert res_sec.status == "success"
    assert "Security Review Gate" in res_sec.output
    assert res_sec.data.get("secure") is True

    # Compact
    res_comp = command_registry.execute("/compact")
    assert res_comp.status == "success"


def test_gateway_backend_dispatch():
    app = create_app()
    client = TestClient(app)

    # Dispatch /skill:list through HTTP
    resp = client.post("/api/commands/execute", json={"command": "/skill:list"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "total" in data["data"]

    # Dispatch /doctor through HTTP
    resp_doc = client.post("/api/commands/execute", json={"command": "/doctor"})
    assert resp_doc.status_code == 200
    data_doc = resp_doc.json()
    assert data_doc["status"] == "success"
    assert data_doc["data"]["status"] == "ready"
