"""Tests for Master Feature Inventory extended Bot Mode engines (Master Inventory #1-#194).

Verifies heartbeat, liveness, org hierarchy, dynamic org generation, task handoffs, succession,
work discovery, reputation, quality gates, kill switch, and event streaming.
"""

from __future__ import annotations

import pytest

import agent_workspace.bots.health as health_mod
import agent_workspace.bots.kill_switch as kill_switch_mod
import agent_workspace.bots.registry as bot_reg
from agent_workspace.bots.events import get_org_event_store, query_org_events
from agent_workspace.bots.handoff import escalate_task, execute_handoff, resolve_succession
from agent_workspace.bots.health import BotHealthMonitor
from agent_workspace.bots.kill_switch import (
    is_bot_paused,
    is_kill_switch_active,
    pause_bot,
    resume_bot,
    set_global_kill_switch,
)
from agent_workspace.bots.organization import generate_organization_for_goal, get_organization_chart
from agent_workspace.bots.performance import get_bot_performance, record_task_outcome
from agent_workspace.bots.profile import BotProfile
from agent_workspace.bots.quality_gate import evaluate_quality_gate
from agent_workspace.bots.registry import BotRegistry
from agent_workspace.bots.templates import BOT_TEMPLATES, DEPARTMENTS
from agent_workspace.bots.work_discovery import claim_task, match_bot_for_task


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    health_mod._global_monitor = None
    kill_switch_mod._global_kill_switch_active = False
    kill_switch_mod._global_kill_switch_reason = ""
    kill_switch_mod._paused_bots.clear()
    yield
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    health_mod._global_monitor = None
    kill_switch_mod._global_kill_switch_active = False
    kill_switch_mod._global_kill_switch_reason = ""
    kill_switch_mod._paused_bots.clear()


def test_bot_profile_enriched_attributes():
    bot = BotProfile(
        name="lead-dev",
        display_name="Lead Dev",
        role="Senior Developer",
        soul="Build quality software.",
        department="engineering",
        reports_to="cto",
        responsibilities=["Core API", "Database schema"],
        capabilities=["python", "fastapi", "postgresql"],
        reputation_score=0.95,
        task_stats={"completed": 10, "failed": 0, "total_runs": 10, "avg_duration_sec": 4.5},
    )
    d = bot.to_dict()
    assert d["department"] == "engineering"
    assert d["reports_to"] == "cto"
    assert "Core API" in d["responsibilities"]
    assert "fastapi" in d["capabilities"]
    assert d["reputation_score"] == 0.95
    assert d["task_stats"]["completed"] == 10

    # Capability epoch remains a stable 12-hex string
    epoch = bot.capability_fingerprint()
    assert len(epoch) == 12

    # Roundtrip from dict
    restored = BotProfile.from_dict(d)
    assert restored.department == "engineering"
    assert restored.reports_to == "cto"


def test_bot_templates_enriched_fields():
    assert "executive" in DEPARTMENTS
    assert "engineering" in DEPARTMENTS
    assert "qa" in DEPARTMENTS

    ceo_spec = BOT_TEMPLATES["ceo"]
    assert ceo_spec["department"] == "executive"
    assert ceo_spec["reports_to"] is None
    assert len(ceo_spec["responsibilities"]) > 0

    coder_spec = BOT_TEMPLATES["coder"]
    assert coder_spec["department"] == "engineering"
    assert coder_spec["reports_to"] == "architect"
    assert "python" in coder_spec["capabilities"]


def test_bot_health_monitor_and_liveness():
    reg = BotRegistry()
    monitor = BotHealthMonitor()
    bot = reg.get_bot("coder")
    assert bot is not None

    # Fresh bot with no heartbeat
    liv = monitor.evaluate_liveness(bot)
    assert liv["liveness"] == "healthy"
    assert liv["is_responsive"] is True

    # Record heartbeat
    hb = monitor.record_heartbeat("coder", task_id="task-101", lease_seconds=120)
    assert hb.current_task_id == "task-101"
    assert hb.lease_expires_at is not None

    # Check liveness again
    liv2 = monitor.evaluate_liveness(bot)
    assert liv2["liveness"] == "healthy"
    assert liv2["active_task_id"] == "task-101"

    # Fleet health
    fleet = monitor.get_fleet_health(reg.list_bots())
    assert fleet["summary"]["total"] >= 5
    assert fleet["fleet_health_score"] >= 0.8


def test_bot_health_stalled_task_detection():
    reg = BotRegistry()
    monitor = BotHealthMonitor()
    bot = reg.get_bot("coder")
    assert bot is not None

    # Simulate expired lease
    monitor.record_heartbeat("coder", task_id="task-stuck", lease_seconds=-10)
    liv = monitor.evaluate_liveness(bot)
    assert liv["liveness"] == "stalled"
    assert liv["lease_expired"] is True

    stalled = monitor.check_stalled_tasks([bot])
    assert len(stalled) == 1
    assert stalled[0]["task_id"] == "task-stuck"
    assert stalled[0]["worker"] == "coder"


def test_organization_chart_and_tree():
    reg = BotRegistry()
    # Add cto and ceo
    reg.get_or_create("ceo", template="ceo")
    reg.get_or_create("cto", template="cto")

    chart = get_organization_chart(reg)
    assert "departments" in chart
    assert "tree" in chart
    assert "graph" in chart
    assert len(chart["departments"]["engineering"]) >= 1

    # Check graph edges
    edges = chart["graph"]["edges"]
    assert any(e["relation"] == "reports_to" for e in edges)


def test_dynamic_organization_generation_from_goal():
    reg = BotRegistry()
    res = generate_organization_for_goal(
        "Build and launch an automated SaaS company platform",
        registry=reg,
        auto_provision=True,
    )
    assert res["recommended_team_size"] >= 6
    roles = [r["slug"] for r in res["recommended_roles"]]
    assert "ceo" in roles
    assert "cto" in roles
    assert "coder" in roles
    assert "ceo" in res["auto_provisioned"]


def test_task_handoff_protocol():
    reg = BotRegistry()
    pkg = execute_handoff(
        task_id="task-999",
        from_bot="architect",
        to_bot="coder",
        objective="Implement authentication router",
        context_summary="Spec ready, use JWT and bcrypt",
        artifacts=["docs/AUTH.md"],
        acceptance_criteria=["All tests pass", "Zero lint errors"],
        handoff_notes="Ping me if requirements shift",
        registry=reg,
    )
    assert pkg.status == "accepted"
    assert pkg.from_bot == "architect"
    assert pkg.to_bot == "coder"
    assert len(pkg.acceptance_criteria) == 2


def test_succession_fallback_resolution():
    reg = BotRegistry()
    # If coder stalls, should fallback to peer or manager architect
    successor = resolve_succession("coder", registry=reg)
    assert successor is not None
    assert successor in ("architect", "cto", "reviewer", "tester", "researcher")


def test_hierarchical_task_escalation():
    reg = BotRegistry()
    esc = escalate_task(
        task_id="task-critical-bug",
        bot_name="coder",
        reason="Third-party vendor API changed authentication format breaking endpoints",
        registry=reg,
    )
    assert esc["escalated_by"] == "coder"
    assert esc["escalated_to"] == "architect"
    assert "API changed" in esc["reason"]


def test_work_discovery_capability_matching():
    reg = BotRegistry()
    matches = match_bot_for_task(
        "Refactor database queries and optimize SQL execution speed",
        required_skills=["python", "coding"],
        required_department="engineering",
        registry=reg,
    )
    assert len(matches) > 0
    top_bot = matches[0]
    # SQL-optimization tasks rank the SQL specialist (data-analyst) first,
    # ahead of the generalist coders — the expanded default roster at work.
    assert top_bot["bot_name"] in ("coder", "architect", "data-analyst")
    assert top_bot["match_score"] > 0.3


def test_task_claiming_and_lease():
    reg = BotRegistry()
    claim = claim_task("task-claim-1", "coder", lease_seconds=180, registry=reg)
    assert claim["task_id"] == "task-claim-1"
    assert claim["claimed_by"] == "coder"
    assert claim["lease_seconds"] == 180


def test_performance_and_reputation_updates():
    reg = BotRegistry()
    bot = reg.get_bot("coder")
    assert bot is not None
    init_rep = bot.reputation_score

    # Successful run
    updated = record_task_outcome(
        "coder",
        success=True,
        duration_sec=3.2,
        quality_score=0.9,
        task_id="task-succ",
        registry=reg,
    )
    assert updated is not None
    assert updated.task_stats["completed"] == 1
    assert updated.reputation_score >= init_rep

    perf = get_bot_performance("coder", registry=reg)
    assert perf["success_rate_percent"] == 100.0
    assert perf["reputation_tier"] in ("Elite Specialist", "Proven Teammate")
    rep_after_success = updated.reputation_score

    # Failed run lowers reputation
    updated2 = record_task_outcome(
        "coder",
        success=False,
        duration_sec=1.1,
        task_id="task-fail",
        registry=reg,
    )
    assert updated2 is not None
    assert updated2.task_stats["failed"] == 1
    assert updated2.reputation_score < rep_after_success


def test_quality_gate_verification():
    # Failing deliverable: empty
    empty_res = evaluate_quality_gate("", ["Implement endpoints"])
    assert empty_res["verdict"] == "rejected"

    # Failing deliverable: contains error trace
    err_res = evaluate_quality_gate(
        "Traceback (most recent call last): RuntimeError: timeout occurred",
        ["Implement endpoints"],
    )
    assert err_res["verdict"] == "rejected"

    # Passing deliverable
    pass_res = evaluate_quality_gate(
        "Successfully implemented authentication endpoints with JWT verification and comprehensive unit tests.",
        ["Implemented authentication endpoints", "JWT verification"],
    )
    assert pass_res["verdict"] == "passed"
    assert pass_res["score"] >= 0.6


def test_global_kill_switch_and_bot_pausing():
    assert is_kill_switch_active()[0] is False

    # Engage kill switch
    st = set_global_kill_switch(True, reason="Emergency drill")
    assert st["global_kill_switch_active"] is True
    assert is_kill_switch_active()[0] is True

    # Any bot is considered paused when kill switch is on
    assert is_bot_paused("coder")[0] is True

    # Disengage
    set_global_kill_switch(False)
    assert is_kill_switch_active()[0] is False
    assert is_bot_paused("coder")[0] is False

    # Per-bot pause
    pause_bot("tester", reason="Under maintenance")
    assert is_bot_paused("tester")[0] is True
    assert is_bot_paused("coder")[0] is False

    resume_bot("tester")
    assert is_bot_paused("tester")[0] is False


def test_org_event_store_and_query():
    store = get_org_event_store()
    evt = store.append_event("test_event", actor="architect", target="coder", details={"foo": "bar"})
    assert evt["id"] is not None
    assert evt["event_type"] == "test_event"

    recent = query_org_events(limit=5, event_type="test_event")
    assert len(recent) >= 1
    assert recent[0]["actor"] == "architect"


@pytest.mark.asyncio
async def test_gateway_bots_extended_endpoints():
    from types import SimpleNamespace

    from app.gateway.routers import bots

    admin_req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))

    # 1. Departments
    depts = await bots.list_departments()
    assert "engineering" in depts["departments"]

    # 2. Health overview
    h_res = await bots.get_fleet_health_overview()
    assert h_res["summary"]["total"] >= 5

    # 3. Org chart
    o_res = await bots.get_org_chart()
    assert "departments" in o_res
    assert "tree" in o_res

    # 4. Work discovery match
    m_res = await bots.match_bots_for_task(bots.WorkDiscoveryMatchRequest(task_description="Fix python test failures", required_skills=["python"]))
    assert len(m_res["matches"]) > 0

    # 5. Quality gate
    q_res = await bots.verify_quality_gate(
        bots.QualityGateVerifyRequest(
            deliverable="Completed all backend tasks cleanly with tests passed.",
            acceptance_criteria=["Complete backend tasks", "Tests passed"],
        )
    )
    assert q_res["verdict"] == "passed"

    # 6. Events query
    e_res = await bots.get_org_events()
    assert "events" in e_res

    # 7. Heartbeat
    hb_res = await bots.record_heartbeat("coder", bots.HeartbeatRequest(task_id="t-1", lease_seconds=60))
    assert hb_res["current_task_id"] == "t-1"

    # 8. Pause & Resume
    p_res = await bots.pause_specific_bot("coder", admin_req)
    assert p_res["is_paused"] is True
    r_res = await bots.resume_specific_bot("coder", admin_req)
    assert r_res["is_paused"] is False

    # 9. Performance
    perf_res = await bots.get_performance("coder")
    assert "reputation_score" in perf_res


def test_bot_roster_tool_actions():
    from agent_workspace.tools.builtins.bot_roster_tool import bot_roster_tool

    # 1. Create with template
    created = bot_roster_tool.invoke(
        {
            "action": "create",
            "name": "data-lead",
            "template": "data-analyst",
            "department": "engineering",
            "reports_to": "architect",
        }
    )
    assert "Successfully provisioned" in created
    assert "@data-lead" in created

    # 2. Monitor
    mon = bot_roster_tool.invoke({"action": "monitor"})
    assert "AI Agent Fleet Health & Monitor" in mon
    assert "Fleet Health Score" in mon
    assert "@coder" in mon

    # 3. Inspect
    insp = bot_roster_tool.invoke({"action": "inspect", "name": "coder"})
    assert "Bot Profile: @coder" in insp
    assert "Reputation:" in insp

    # 4. Generate team
    gen = bot_roster_tool.invoke(
        {
            "action": "generate_team",
            "goal": "Build an automated vulnerability scanning and incident response system",
        }
    )
    assert "Dynamic Team Formulated" in gen
    assert "Recommended Team Size" in gen

    # 5. Handoff
    handoff_res = bot_roster_tool.invoke(
        {
            "action": "handoff",
            "name": "architect",
            "target_bot": "coder",
            "task_id": "task-security-patch",
            "objective": "Patch CVE-2026-9999",
        }
    )
    assert "Task handoff completed" in handoff_res

    # 6. Pause & Resume
    pause_res = bot_roster_tool.invoke({"action": "pause", "name": "coder", "reason": "Code review"})
    assert "paused" in pause_res
    resume_res = bot_roster_tool.invoke({"action": "resume", "name": "coder"})
    assert "resumed" in resume_res
