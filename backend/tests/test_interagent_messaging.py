"""Unit tests for direct agent-to-agent messaging and family roster."""


from alpha.subagents.messaging import AgentRoster, get_agent_roster
from alpha.tools.builtins.agent_message_tool import agent_message_tool, agent_observe_tool


def test_agent_roster_registration_and_status():
    roster = AgentRoster("thread_test_1")

    a1 = roster.register_agent("coder", role="software_engineer", status="idle")
    a2 = roster.register_agent("tester", role="qa_engineer", status="busy")

    agents = roster.list_agents()
    assert len(agents) == 2
    assert {a.name for a in agents} == {"coder", "tester"}

    roster.update_status("coder", "busy")
    assert roster._agents["coder"].status == "busy"

    roster.deregister_agent("tester")
    assert roster._agents["tester"].status == "completed"


def test_interagent_messaging_delivery_modes():
    roster = AgentRoster("thread_test_2")
    roster.register_agent("planner", role="planner", status="idle")
    roster.register_agent("coder", role="coder", status="busy")
    roster.register_agent("reviewer", role="reviewer", status="idle")

    # 1. Mode 'auto' to idle agent -> delivered immediately
    res_idle = roster.send_message("planner", "reviewer", "Please review PR #42", mode="auto")
    assert res_idle["status"] == "ok"
    assert res_idle["receipts"][0]["delivery_status"] == "delivered"

    # 2. Mode 'auto' to busy agent -> queued for later
    res_busy = roster.send_message("planner", "coder", "Fix the failing test", mode="auto")
    assert res_busy["status"] == "ok"
    assert res_busy["receipts"][0]["delivery_status"] == "queued"

    # 3. Mode 'steer' to busy agent -> forced immediate delivery
    res_steer = roster.send_message("planner", "coder", "Stop! Critical vulnerability", mode="steer")
    assert res_steer["receipts"][0]["delivery_status"] == "delivered"

    # Check coder inbox
    inbox = roster.get_inbox("coder", mark_as_read=True)
    assert len(inbox) == 2
    assert all(m.status == "read" for m in inbox)


def test_interagent_broadcast():
    roster = AgentRoster("thread_test_3")
    roster.register_agent("orchestrator", role="lead", status="busy")
    roster.register_agent("worker_1", role="worker", status="idle")
    roster.register_agent("worker_2", role="worker", status="idle")

    res = roster.send_message("orchestrator", "all", "All workers pause for sync")
    assert res["status"] == "ok"
    assert len(res["receipts"]) == 2
    receivers = {r["receiver"] for r in res["receipts"]}
    assert receivers == {"worker_1", "worker_2"}


def test_agent_message_and_observe_tools():
    roster = get_agent_roster("default_thread")
    roster.register_agent("api_expert", role="specialist", status="idle")

    observe_out = agent_observe_tool.invoke({})
    assert "api_expert" in observe_out

    msg_out = agent_message_tool.invoke({
        "receiver_name": "api_expert",
        "content": "Verify OpenAPI schemas",
        "sender_name": "tester",
        "mode": "auto",
    })
    assert "Message sent successfully" in msg_out
    assert "delivered" in msg_out
