"""Automated tests for the Autonomous Slash Command Execution System.

Verifies:
1. Real-time automatic intent identification across lifecycle phases (Planning, Swarm, Research, Coding, Verification, Self-Heal, Reflection, Schedule)
2. Automatic command trigger and execution without requiring manual user typing
3. Phase transitions (automatic trigger on error, automatic trigger on code edit)
4. Built-in autonomous agent tools (execute_slash_command, identify_autonomous_command)
5. Gateway API endpoints (/api/commands/auto-trigger, /api/commands/phase-transition, /api/commands/lifecycle-rules)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gateway.app import create_app
from agent_workspace.commands import (
    AutonomousCommandEngine,
    LifecyclePhase,
    autonomous_command_engine,
    command_registry,
)
from agent_workspace.tools.builtins import (
    execute_slash_command_tool,
    identify_autonomous_command_tool,
)


def test_intent_identification_planning():
    # Complex objective from scratch should automatically trigger /goal create
    result = autonomous_command_engine.identify_and_trigger(
        "Build a complete multi-tier e-commerce platform from scratch",
        auto_execute=True,
    )
    assert result.matched is True
    assert result.phase == LifecyclePhase.PLANNING
    assert result.command in ["/goal create", "/plan mode:auto"]
    assert result.confidence >= 0.5
    assert len(result.autonomous_directives) > 0


def test_intent_identification_swarm():
    # Multi-agent collaboration should automatically trigger /swarm create
    result = autonomous_command_engine.identify_and_trigger(
        "Spawn a multi-agent swarm team of specialized workers to solve this problem",
        auto_execute=True,
    )
    assert result.matched is True
    assert result.phase == LifecyclePhase.SWARM
    assert result.command == "/swarm create"
    assert len(result.autonomous_directives) > 0


def test_intent_identification_research():
    # Research queries should automatically trigger /research deep
    result = autonomous_command_engine.identify_and_trigger(
        "Conduct deep research into the latest consensus protocols and compare solutions",
        auto_execute=True,
    )
    assert result.matched is True
    assert result.phase == LifecyclePhase.RESEARCH
    assert result.command == "/research deep"


def test_intent_identification_verification():
    # Request to test or check invariants triggers /verify all
    result = autonomous_command_engine.identify_and_trigger(
        "Please run pytest and check invariants across all modules",
        auto_execute=True,
    )
    assert result.matched is True
    assert result.phase == LifecyclePhase.VERIFICATION
    assert result.command == "/verify all"


def test_phase_transition_self_heal():
    # At error time, automatically triggers /self-heal
    result = autonomous_command_engine.trigger_phase_transition(
        LifecyclePhase.SELF_HEAL,
        details="Traceback: NullPointerException in worker.py line 42",
    )
    assert result.matched is True
    assert result.command == "/self-heal"
    assert result.phase == LifecyclePhase.SELF_HEAL
    assert any("SELF_HEAL" in d for d in result.autonomous_directives)


def test_phase_transition_verification():
    # After code edits, automatically triggers verification
    result = autonomous_command_engine.trigger_phase_transition(
        LifecyclePhase.VERIFICATION,
        details="Refactored database layer in db.py",
    )
    assert result.matched is True
    assert result.command == "/verify all"
    assert result.phase == LifecyclePhase.VERIFICATION


def test_autonomous_tools():
    # 1. execute_slash_command tool
    tool_out = execute_slash_command_tool.invoke({"command_line": "/verify all"})
    assert "Slash Command Result: /verify" in tool_out
    assert "SUCCESS" in tool_out

    # 2. identify_autonomous_command tool
    rec = identify_autonomous_command_tool.invoke({
        "current_intent_or_error": "Fix the broken build and handle exception",
        "phase": "self_heal",
    })
    assert "Autonomous Recommendation" in rec
    assert "/self-heal" in rec or "execute_slash_command" in rec


def test_gateway_autonomous_endpoints():
    app = create_app()
    client = TestClient(app)

    # 1. POST /api/commands/auto-trigger
    resp_auto = client.post(
        "/api/commands/auto-trigger",
        json={"prompt": "Design a fault-tolerant microservices architecture"},
    )
    assert resp_auto.status_code == 200
    data_auto = resp_auto.json()
    assert data_auto["matched"] is True
    assert data_auto["phase"] == "planning"

    # 2. POST /api/commands/phase-transition
    resp_trans = client.post(
        "/api/commands/phase-transition",
        json={"phase": "self_heal", "details": "Critical index out of bounds error"},
    )
    assert resp_trans.status_code == 200
    data_trans = resp_trans.json()
    assert data_trans["matched"] is True
    assert data_trans["command"] == "/self-heal"

    # 3. GET /api/commands/lifecycle-rules
    resp_rules = client.get("/api/commands/lifecycle-rules")
    assert resp_rules.status_code == 200
    data_rules = resp_rules.json()
    assert data_rules["total_rules"] >= 8
