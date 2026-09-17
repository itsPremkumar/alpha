"""Tests for Group Communication Mesh, @mention routing, Silent Attendance Ledger, and Bot Medic Self-Healing."""

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.company import router
from agent_workspace.company.attendance import AttendanceStatus
from agent_workspace.company.models import OrgArchetype
from agent_workspace.company.organization import get_autonomous_company_engine
from agent_workspace.tools.builtins.company_tool import company_tool


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


@pytest.fixture
def company_engine():
    engine = get_autonomous_company_engine()
    # Bootstrap a clean test company
    engine.bootstrap_company(
        prompt="Autonomous FinTech infrastructure company with 24/7 reliability",
        archetype=OrgArchetype.COMPANY,
        owner="test-founder",
    )
    return engine


def test_group_chat_mesh_unlimited_bots(company_engine):
    """Verifies that company all-hands group supports unlimited bots, breaking the Hermes 7-bot limitation."""
    orgs = company_engine.list_companies()
    assert len(orgs) > 0
    state = orgs[0]
    org_id = state.org_id

    chat = company_engine.get_chat_engine(org_id)
    channels = chat.list_channels()
    assert len(channels) >= 5

    # Check Default All-Hands
    all_hands = chat.get_channel("all-hands")
    assert all_hands is not None
    assert all_hands.is_default is True
    # Verify no 7-bot limit! Has all company bots
    assert len(all_hands.member_bot_names) >= 9

    # Check Cognition Council (inspired by Hermes Bot Mode AGI)
    council = chat.get_channel("cognition-council")
    assert council is not None
    assert "bot-ceo" in council.member_bot_names
    assert "bot-cto" in council.member_bot_names


def test_group_chat_mention_parsing_and_history(company_engine):
    """Verifies @mention extraction, targeted message routing, and history tracking."""
    org_id = company_engine.list_companies()[0].org_id
    chat = company_engine.get_chat_engine(org_id)

    # Post message with multiple @mentions
    msg = chat.post_message(
        channel_id="all-hands",
        sender_bot="bot-cto",
        content="Urgent alert: @bot-backend-lead and @bot-sre-lead please inspect the redis connection pool!",
    )

    assert msg.channel_id == "all-hands"
    assert msg.sender_bot == "bot-cto"
    assert "bot-backend-lead" in msg.mentions
    assert "bot-sre-lead" in msg.mentions

    # Check history
    history = chat.get_channel_history("all-hands", limit=10)
    assert any(m.message_id == msg.message_id for m in history)

    # Check mention query
    mentions_sre = chat.get_mentions_for_bot("bot-sre-lead")
    assert len(mentions_sre) >= 1
    assert mentions_sre[0].message_id == msg.message_id


def test_dynamic_subgroup_creation(company_engine):
    """Verifies that agents can dynamically create sub-groups or war-rooms."""
    org_id = company_engine.list_companies()[0].org_id

    subgroup = company_engine.create_subgroup(
        org_id=org_id,
        channel_id="warroom-db-incident",
        name="#Incident-War-Room-101",
        member_bot_names=["bot-cto", "bot-backend-lead", "bot-sre-lead"],
        description="High-priority triage room for live database lock contention",
        created_by="bot-sre-lead",
    )

    assert subgroup.channel_id == "warroom-db-incident"
    assert len(subgroup.member_bot_names) == 3

    # Post inside sub-group
    msg = company_engine.post_group_message(
        org_id=org_id,
        channel_id="warroom-db-incident",
        sender_bot="bot-backend-lead",
        content="Applying read-replica failover now.",
    )
    assert msg.channel_id == "warroom-db-incident"


def test_silent_attendance_ledger_zero_spam(company_engine):
    """Verifies non-conversational protocol heartbeat pulses without generating chat spam."""
    org_id = company_engine.list_companies()[0].org_id
    chat = company_engine.get_chat_engine(org_id)

    initial_msg_count = len(chat.get_channel_history("all-hands"))

    # Record silent pulse
    hb = company_engine.record_bot_pulse(
        org_id=org_id,
        bot_name="bot-ai-engineer",
        status="busy",
        active_task_id="task-train-embedding-v3",
    )

    assert hb.bot_name == "bot-ai-engineer"
    assert hb.status == AttendanceStatus.BUSY
    assert hb.active_task_id == "task-train-embedding-v3"

    # Verify zero chat messages were posted (completely noise-free!)
    after_msg_count = len(chat.get_channel_history("all-hands"))
    assert after_msg_count == initial_msg_count


def test_attendance_evaluation_and_bot_medic_auto_healing(company_engine):
    """Verifies that an absent or stuck agent is detected and autonomously healed by Bot Medic."""
    org_id = company_engine.list_companies()[0].org_id
    att = company_engine.get_attendance_engine(org_id)

    # Register or set a bot with artificially stale pulse (> 300s ago)
    target_bot = "bot-backend-lead"
    hb = att.get_heartbeat(target_bot)
    assert hb is not None
    hb.last_pulse = time.time() - 350.0  # Simulated crash / absence

    # Run attendance check & auto-heal
    res = company_engine.check_attendance_and_heal(org_id=org_id, timeout_seconds=100.0)

    assert res["status"] == "healed_unresponsive_bots"
    assert res["absent_count"] >= 1
    assert len(res["healing_reports"]) >= 1

    healing = next(r for r in res["healing_reports"] if r["bot_name"] == target_bot)
    assert healing["success"] is True
    # Verify responsibility failover was triggered
    assert healing["backup_bot_assigned"] == "bot-sre-lead"

    # Verify bot state in ledger is now RECOVERED
    fresh_hb = att.get_heartbeat(target_bot)
    assert fresh_hb.status == AttendanceStatus.RECOVERED


def test_roll_call_digest_clean_format(company_engine):
    """Verifies that roll call produces an executive markdown digest."""
    org_id = company_engine.list_companies()[0].org_id
    digest = company_engine.get_roll_call_digest(org_id)

    assert "### 📋 Attendance Roll Call Digest" in digest
    assert "Total Workforce" in digest
    assert "Present & Active" in digest


def test_company_tool_groups_and_attendance(company_engine):
    """Verifies company_tool actions for group channels, posting, pulse, and roll call."""
    org_id = company_engine.list_companies()[0].org_id

    # List channels via tool
    res_channels = company_tool.invoke({"action": "group_channels", "org_id": org_id})
    channels_data = json.loads(res_channels)
    assert len(channels_data) >= 5

    # Post message via tool
    res_post = company_tool.invoke(
        {
            "action": "group_post",
            "org_id": org_id,
            "channel_id": "all-hands",
            "sender_bot": "bot-ceo",
            "message": "Welcome all @all agents to sprint kickoff!",
        }
    )
    post_data = json.loads(res_post)
    assert post_data["sender_bot"] == "bot-ceo"
    assert "all" in post_data["mentions"]

    # Pulse via tool
    res_pulse = company_tool.invoke(
        {
            "action": "attendance_pulse",
            "org_id": org_id,
            "bot_name": "bot-frontend-lead",
            "pulse_status": "present",
        }
    )
    pulse_data = json.loads(res_pulse)
    assert pulse_data["bot_name"] == "bot-frontend-lead"

    # Roll call via tool
    res_roll = company_tool.invoke({"action": "roll_call", "org_id": org_id})
    assert "Attendance Roll Call Digest" in res_roll


def test_gateway_rest_endpoints(client, company_engine):
    """Verifies REST endpoints for groups and attendance."""
    org_id = company_engine.list_companies()[0].org_id

    # 1. GET /api/company/groups
    res = client.get(f"/api/company/groups?org_id={org_id}")
    assert res.status_code == 200
    groups = res.json()
    assert len(groups) >= 5

    # 2. POST /api/company/groups/message
    res = client.post(
        "/api/company/groups/message",
        json={
            "org_id": org_id,
            "channel_id": "all-hands",
            "sender_bot": "bot-coo",
            "content": "Quarterly operational sync starting now @bot-cto",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["sender_bot"] == "bot-coo"
    assert "bot-cto" in data["mentions"]

    # 3. GET /api/company/groups/{channel_id}/messages
    res = client.get(f"/api/company/groups/all-hands/messages?org_id={org_id}")
    assert res.status_code == 200
    msgs = res.json()
    assert len(msgs) >= 1

    # 4. POST /api/company/attendance/pulse
    res = client.post(
        "/api/company/attendance/pulse",
        json={
            "org_id": org_id,
            "bot_name": "bot-product-manager",
            "status": "idle",
        },
    )
    assert res.status_code == 200
    hb = res.json()
    assert hb["status"] == "idle"

    # 5. POST /api/company/attendance/check-and-heal
    res = client.post(
        "/api/company/attendance/check-and-heal",
        json={
            "org_id": org_id,
            "timeout_seconds": 120.0,
        },
    )
    assert res.status_code == 200
    check_res = res.json()
    assert "status" in check_res
    assert "roll_call_digest" in check_res

    # 6. GET /api/company/attendance/roll-call
    res = client.get(f"/api/company/attendance/roll-call?org_id={org_id}")
    assert res.status_code == 200
    rc = res.json()
    assert "Attendance Roll Call Digest" in rc["roll_call_digest"]


def test_agent_kanban_check_in_and_task_lifecycle(company_engine):
    """Verifies that agents can regularly check in, claim work, update kanban, and add audit logs."""
    org_id = company_engine.list_companies()[0].org_id

    # 1. Sync company projects to kanban
    company_engine.sync_company_to_hermes_kanban(org_id)

    # 2. Agent check-in for 'cto' (inspect tasks)
    check_in_res = company_engine.agent_kanban_check_in(
        org_id=org_id,
        bot_name="bot-cto",
    )
    assert check_in_res["bot_name"] == "bot-cto"
    assigned = check_in_res["assigned_tasks"]
    assert len(assigned) >= 1
    task_id = assigned[0]["id"]

    # 3. Transition to in_progress with log
    update_res = company_engine.update_kanban_task(
        org_id=org_id,
        task_id=task_id,
        new_status="in_progress",
        bot_name="bot-cto",
        log_message="Started implementing architecture v2 specifications",
    )
    assert update_res["status"] == "in_progress"

    # 4. Add progress log
    log_event = company_engine.add_kanban_log(
        org_id=org_id,
        task_id=task_id,
        bot_name="bot-cto",
        message="Completed draft RFC for event-driven bus",
        kind="progress_log",
    )
    assert log_event["task_id"] == task_id
    assert log_event["bot_name"] == "bot-cto"

    # 5. Transition to done with deliverable result
    finish_res = company_engine.update_kanban_task(
        org_id=org_id,
        task_id=task_id,
        new_status="done",
        bot_name="bot-cto",
        log_message="Architecture v2 deliverable approved and signed off",
        result="https://github.com/org/arch/pull/101",
    )
    assert finish_res["status"] == "done"

    # 6. Verify event history
    events = company_engine.list_kanban_logs(org_id=org_id, task_id=task_id)
    assert len(events) >= 2


def test_company_tool_kanban_actions(company_engine):
    """Verifies company_tool actions for kanban task queries, updates, logging, and check-in."""
    org_id = company_engine.list_companies()[0].org_id

    # 1. kanban_tasks
    res_tasks = company_tool.invoke({"action": "kanban_tasks", "org_id": org_id})
    tasks = json.loads(res_tasks)
    assert isinstance(tasks, list)
    assert len(tasks) >= 1
    sample_tid = tasks[0]["id"]

    # 2. kanban_update
    res_update = company_tool.invoke(
        {
            "action": "kanban_update",
            "org_id": org_id,
            "task_id": sample_tid,
            "task_status": "in_progress",
            "bot_name": "bot-backend-lead",
            "message": "Profiling query latency under load",
        }
    )
    update_data = json.loads(res_update)
    assert update_data["status"] == "in_progress"

    # 3. kanban_log
    res_log = company_tool.invoke(
        {
            "action": "kanban_log",
            "org_id": org_id,
            "task_id": sample_tid,
            "bot_name": "bot-backend-lead",
            "message": "Identified missing index on user_id",
        }
    )
    log_data = json.loads(res_log)
    assert log_data["task_id"] == sample_tid

    # 4. kanban_events
    res_events = company_tool.invoke(
        {
            "action": "kanban_events",
            "org_id": org_id,
            "task_id": sample_tid,
        }
    )
    events_data = json.loads(res_events)
    assert len(events_data) >= 1

    # 5. kanban_check_in
    res_check_in = company_tool.invoke(
        {
            "action": "kanban_check_in",
            "org_id": org_id,
            "bot_name": "bot-backend-lead",
            "task_id": sample_tid,
            "message": "Check-in: index applied, validating performance",
        }
    )
    ci_data = json.loads(res_check_in)
    assert ci_data["bot_name"] == "bot-backend-lead"


def test_gateway_rest_kanban_endpoints(client, company_engine):
    """Verifies gateway REST API routes for Kanban task management, logging, and agent check-in."""
    org_id = company_engine.list_companies()[0].org_id

    # 1. GET /api/company/kanban/tasks
    res = client.get(f"/api/company/kanban/tasks?org_id={org_id}")
    assert res.status_code == 200
    tasks = res.json()
    assert len(tasks) >= 1
    target_task_id = tasks[0]["id"]

    # 2. POST /api/company/kanban/tasks/{task_id}/update
    res = client.post(
        f"/api/company/kanban/tasks/{target_task_id}/update",
        json={
            "org_id": org_id,
            "new_status": "review",
            "bot_name": "bot-qa-lead",
            "log_message": "QA regression test run completed with 100% pass rate",
            "result": "All 42 test suites passed",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "review"

    # 3. POST /api/company/kanban/events/log
    res = client.post(
        "/api/company/kanban/events/log",
        json={
            "org_id": org_id,
            "task_id": target_task_id,
            "bot_name": "bot-security-lead",
            "message": "SAST/DAST security scan clean with zero CVEs",
            "kind": "security_audit",
        },
    )
    assert res.status_code == 200
    ev = res.json()
    assert ev["task_id"] == target_task_id

    # 4. GET /api/company/kanban/events
    res = client.get(f"/api/company/kanban/events?org_id={org_id}&task_id={target_task_id}")
    assert res.status_code == 200
    ev_list = res.json()
    assert len(ev_list) >= 2

    # 5. POST /api/company/kanban/agent-check-in
    res = client.post(
        "/api/company/kanban/agent-check-in",
        json={
            "org_id": org_id,
            "bot_name": "bot-qa-lead",
            "current_task_id": target_task_id,
            "progress_notes": "Routine agent check-in: ready for deployment gate",
            "new_status": "done",
        },
    )
    assert res.status_code == 200
    ci_res = res.json()
    assert ci_res["bot_name"] == "bot-qa-lead"
