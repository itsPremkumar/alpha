"""Automated tests for the Master Slash Command System (418+ commands across 28 categories).

Verifies:
1. Registry initialization and command catalog integrity
2. 28 CommandCategory representation and counts
3. Search capabilities (substring, multi-token, category-based)
4. Flexible command parsing (/cmd, /cmd subcommand, /cmd:subcommand)
5. Execution intent resolution and autonomous directive generation
6. Gateway API endpoints (GET /api/commands, GET /api/commands/categories, GET /api/commands/search, POST /api/commands/execute)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gateway.app import create_app
from agent_workspace.commands import CommandCategory, SlashCommandDef, command_registry
from agent_workspace.commands.registry import CommandExecutionResult


def test_registry_initialization():
    commands = command_registry.list_commands()
    assert len(commands) >= 400, f"Expected at least 400 commands, got {len(commands)}"

    # Check that key core command families exist
    expected_families = [
        "/goal",
        "/plan",
        "/swarm",
        "/agent",
        "/research",
        "/memory",
        "/context",
        "/skills",
        "/model",
        "/tools",
        "/mcp",
        "/verify",
        "/evidence",
        "/code",
        "/browser",
        "/learn",
        "/improve",
        "/evolve",
        "/avo",
        "/cron",
        "/security",
        "/runtime",
        "/trace",
        "/session",
        "/approve",
        "/deny",
        "/artifact",
        "/world",
    ]
    command_names = {c.command for c in commands}
    for fam in expected_families:
        assert any(c.startswith(fam) for c in command_names), f"Missing family: {fam}"


def test_all_28_categories_present():
    categories = command_registry.get_categories()
    assert len(categories) == 28, f"Expected 28 categories, got {len(categories)}"

    for entry in categories:
        assert entry["count"] > 0, f"Category {entry['category']} has 0 commands"

    cat_values = {c["category"] for c in categories}
    for expected_cat in CommandCategory:
        assert expected_cat.value in cat_values


def test_category_filtering():
    mission_cmds = command_registry.list_commands(category=CommandCategory.MISSION)
    assert len(mission_cmds) > 0
    for cmd in mission_cmds:
        assert cmd.category == CommandCategory.MISSION

    swarm_cmds = command_registry.list_commands(category=CommandCategory.SWARM)
    assert len(swarm_cmds) > 0
    for cmd in swarm_cmds:
        assert cmd.category == CommandCategory.SWARM


def test_core_filtering():
    core_cmds = command_registry.list_commands(only_core=True)
    assert len(core_cmds) > 0
    for cmd in core_cmds:
        assert cmd.is_core is True


def test_search_functionality():
    # Search by exact or partial name
    goal_results = command_registry.search("goal")
    assert len(goal_results) >= 5
    assert all("goal" in c.command.lower() or "goal" in c.description.lower() for c in goal_results)

    # Search by concept
    synth_results = command_registry.search("synthesize")
    assert len(synth_results) > 0


def test_flexible_command_parsing():
    # 1. Base command with arguments
    cmd_def, args = command_registry.find_command("/goal deploy product")
    assert cmd_def is not None
    # Can match /goal or /goal deploy depending on registration
    assert cmd_def.command.startswith("/goal")

    # 2. Colon syntax resolution
    cmd_def_colon, args_colon = command_registry.find_command("/goal:status")
    assert cmd_def_colon is not None
    assert cmd_def_colon.command == "/goal status"

    # 3. Two-word command resolution
    cmd_def_sub, args_sub = command_registry.find_command("/goal status")
    assert cmd_def_sub is not None
    assert cmd_def_sub.command == "/goal status"

    # 4. Unknown command returns None
    cmd_def_unknown, _ = command_registry.find_command("/unknown_command_xyz")
    assert cmd_def_unknown is None


def test_command_execution():
    # Autonomous trigger command
    res = command_registry.execute("/goal create Build Mars Rover")
    assert res.status == "success"
    assert res.command == "/goal create"
    assert "Build Mars Rover" in res.data["arguments"]
    assert res.data["is_autonomous_trigger"] is True
    assert len(res.autonomous_directives) > 0

    # Custom handler registration & execution
    def dummy_handler(args: str, context=None):
        return CommandExecutionResult(
            status="success",
            command="/custom_test",
            output=f"Custom executed with: {args}",
            data={"custom": True},
        )

    test_cmd = SlashCommandDef(
        command="/custom_test",
        category=CommandCategory.CORE,
        description="A test command",
        usage="/custom_test <args>",
    )
    command_registry.register(test_cmd, handler=dummy_handler)

    custom_res = command_registry.execute("/custom_test foo bar")
    assert custom_res.status == "success"
    assert custom_res.output == "Custom executed with: foo bar"
    assert custom_res.data.get("custom") is True


def test_gateway_endpoints():
    app = create_app()
    client = TestClient(app)

    # 1. GET /api/commands
    resp = client.get("/api/commands")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 400
    assert len(data["commands"]) == data["total"]

    # 2. GET /api/commands with filter
    resp_filtered = client.get("/api/commands?category=swarm")
    assert resp_filtered.status_code == 200
    data_filtered = resp_filtered.json()
    assert data_filtered["total"] > 0
    assert all(c["category"] == "swarm" for c in data_filtered["commands"])

    # 3. GET /api/commands/categories
    resp_cats = client.get("/api/commands/categories")
    assert resp_cats.status_code == 200
    data_cats = resp_cats.json()
    assert data_cats["total_categories"] == 28

    # 4. GET /api/commands/search
    resp_search = client.get("/api/commands/search?q=browser")
    assert resp_search.status_code == 200
    data_search = resp_search.json()
    assert data_search["total"] > 0

    # 5. POST /api/commands/execute
    resp_exec = client.post("/api/commands/execute", json={"command": "/goal:status"})
    assert resp_exec.status_code == 200
    data_exec = resp_exec.json()
    assert data_exec["status"] == "success"
    assert data_exec["command"] == "/goal status"
