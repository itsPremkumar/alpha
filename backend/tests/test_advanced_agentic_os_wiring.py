import json

from app.gateway.app import app
from agent_workspace.tools.builtins import (
    a2a_tool,
    cognitive_plan,
    company_tool,
    deliberation_tool,
    goal_integrity_tool,
    job_tool,
    subagent_control,
    supervision_tool,
    swarm_tool,
)
from agent_workspace.tools.tools import BUILTIN_TOOLS, SUBAGENT_TOOLS, get_available_tools


def test_builtin_tools_registration():
    """Verify all 8 advanced agentic OS tools are registered in BUILTIN_TOOLS with correct tool names."""
    builtin_names = {t.name for t in BUILTIN_TOOLS}
    expected_builtins = {
        "cognitive_plan": "cognitive_plan",
        "deliberation_tool": "deliberate",
        "company_tool": "company_os",
        "a2a_tool": "a2a_protocol",
        "goal_integrity_tool": "goal_integrity",
        "job_tool": "external_job",
        "supervision_tool": "supervisor_watchdog",
        "swarm_tool": "swarm",
    }
    for tool_var, tool_name in expected_builtins.items():
        assert tool_name in builtin_names, f"Missing {tool_name} ({tool_var}) in BUILTIN_TOOLS"


def test_subagent_tools_registration():
    """Verify subagent control, swarm, A2A, and company tools are registered in SUBAGENT_TOOLS."""
    subagent_names = {t.name for t in SUBAGENT_TOOLS}
    expected_subagents = {
        "subagent_control": "subagent_control",
        "swarm_tool": "swarm",
        "a2a_tool": "a2a_protocol",
        "company_tool": "company_os",
    }
    for tool_var, tool_name in expected_subagents.items():
        assert tool_name in subagent_names, f"Missing {tool_name} ({tool_var}) in SUBAGENT_TOOLS"


def test_get_available_tools_includes_advanced_tools():
    """Verify get_available_tools exposes both lead and subagent suites when requested."""
    lead_tools = get_available_tools(include_mcp=False, subagent_enabled=False)
    lead_names = {t.name for t in lead_tools}
    assert "cognitive_plan" in lead_names
    assert "deliberate" in lead_names
    assert "company_os" in lead_names
    assert "a2a_protocol" in lead_names
    assert "external_job" in lead_names
    assert "supervisor_watchdog" in lead_names
    assert "goal_integrity" in lead_names
    assert "swarm" in lead_names

    subagent_tools = get_available_tools(include_mcp=False, subagent_enabled=True)
    subagent_names = {t.name for t in subagent_tools}
    assert "subagent_control" in subagent_names
    assert "swarm" in subagent_names
    assert "a2a_protocol" in subagent_names
    assert "company_os" in subagent_names


def test_fastapi_gateway_routes_all_10_subsystems():
    """Verify FastAPI gateway mounts all 10 advanced routers with correct prefix paths."""
    all_paths = {getattr(r, "path", "") for r in app.routes}

    # 1. Swarms
    assert any("/api/swarms" in p for p in all_paths)
    assert "/api/swarms/governor/status" in all_paths

    # 2. Plan Mode
    assert "/api/plan-mode/evaluate" in all_paths
    assert "/api/plan-mode/dispatch" in all_paths

    # 3. Subagents Control
    assert "/api/subagents/control/spawn" in all_paths
    assert "/api/subagents/control/adopt" in all_paths

    # 4. Deliberation
    assert "/api/deliberation/evaluate" in all_paths
    assert "/api/deliberation/run" in all_paths

    # 5. Jobs
    assert "/api/jobs" in all_paths

    # 6. Supervision
    assert "/api/supervision/fleet" in all_paths
    assert "/api/supervision/anomalies" in all_paths

    # 7. Goal Integrity
    assert "/api/goal-integrity/audit" in all_paths

    # 8. A2A Protocols
    assert "/api/protocols/a2a/cards" in all_paths
    assert "/api/protocols/a2a/delegate" in all_paths

    # 9. Company
    assert "/api/company/status" in all_paths
    assert "/api/company/groups" in all_paths
    assert "/api/company/attendance/status" in all_paths
    assert "/api/company/kanban/tasks" in all_paths

    # 10. Bots (Roster & Profiles)
    assert "/api/bots" in all_paths
    assert "/api/bots/departments" in all_paths
    assert "/api/bots/organization-chart" in all_paths


def test_tool_invocability_end_to_end():
    """Directly test invoking all 9 new tools to ensure zero runtime crashes."""
    # 1. cognitive_plan
    plan_res = cognitive_plan.invoke({"action": "evaluate", "prompt": "Build an ultra-reliable event broker"})
    assert "Cognitive" in str(plan_res) or "Evaluation" in str(plan_res) or "Strategy" in str(plan_res)

    # 2. deliberation_tool
    delib_res = deliberation_tool.invoke({
        "action": "evaluate",
        "prompt": "Microservices vs Modular Monolith for financial core banking",
    })
    assert "Deliberation" in str(delib_res) or "Evaluation" in str(delib_res) or "Strategy" in str(delib_res)

    # 3. company_tool
    comp_res = company_tool.invoke({"action": "archetypes"})
    assert "company" in str(comp_res).lower()

    # 4. a2a_tool
    a2a_res = a2a_tool.invoke({"action": "list_agents"})
    assert "A2A" in str(a2a_res) or "agent" in str(a2a_res).lower()

    # 5. goal_integrity_tool
    goal_res = goal_integrity_tool.invoke({
        "action": "audit_plan",
        "mission_goal": "Maintain zero-defect builds",
        "subtasks_json": '["run unit tests", "check linter"]',
    })
    assert "score" in str(goal_res).lower() or "passed" in str(goal_res).lower() or "integrity" in str(goal_res).lower()

    # 6. job_tool
    job_res = job_tool.invoke({"action": "list"})
    assert isinstance(json.loads(job_res), list)

    # 7. supervision_tool
    sup_res = supervision_tool.invoke({"action": "fleet_health"})
    assert isinstance(json.loads(sup_res), dict)

    # 8. swarm_tool
    swarm_res = swarm_tool.invoke({"action": "evaluate", "goal": "Index 1000 repositories in parallel"})
    assert "swarm" in str(swarm_res).lower() or "speedup" in str(swarm_res).lower()

    # 9. subagent_control
    sub_res = subagent_control.invoke({"action": "archetypes"})
    assert "archetypes" in str(sub_res).lower() or "specialist" in str(sub_res).lower()
