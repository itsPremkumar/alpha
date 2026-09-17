"""Unit tests for LangChain tools: bot_roster, group_chat, and kanban_board."""


from uuid import uuid4

from agent_workspace.tools.builtins.bot_roster_tool import bot_roster_tool
from agent_workspace.tools.builtins.group_chat_tool import group_chat_tool
from agent_workspace.tools.builtins.kanban_board_tool import kanban_board_tool


def test_bot_roster_tool_actions():
    # 1. List bots
    res_list = bot_roster_tool.invoke({"action": "list"})
    assert "Autonomous AI Bot Roster" in res_list
    assert "@coder" in res_list

    # 2. Create custom bot with unique name
    bot_name = f"bot_{uuid4().hex[:6]}"
    res_create = bot_roster_tool.invoke({
        "action": "create",
        "name": bot_name,
        "role": "Pipeline Specialist",
        "display_name": "Data Engineer",
        "soul": "Write high-throughput ETL pipelines.",
    })
    assert f"Successfully provisioned AI Agent: @{bot_name} (Data Engineer)" in res_create
    assert "Epoch:" in res_create

    # 3. Inspect bot
    res_inspect = bot_roster_tool.invoke({"action": "inspect", "name": bot_name})
    assert f"=== Bot Profile: @{bot_name}" in res_inspect
    assert "Pipeline Specialist" in res_inspect
    assert "ETL pipelines" in res_inspect

    # 4. Update SOUL
    res_update = bot_roster_tool.invoke({
        "action": "update_soul",
        "name": bot_name,
        "soul": "Optimize SQL and PySpark streaming jobs.",
    })
    assert f"Updated SOUL for @{bot_name}" in res_update


def test_group_chat_tool_workflow():
    # 1. Create a room
    res_create = group_chat_tool.invoke({
        "action": "create",
        "room_name": "sprint-room",
        "members": "architect,coder,tester",
        "mode": "mention",
    })
    assert "Room 'sprint-room' active" in res_create
    assert "architect" in res_create

    # 2. Send a message with mention
    res_send = group_chat_tool.invoke({
        "action": "send",
        "room_name": "sprint-room",
        "sender": "user",
        "message": "Let's kick off sprint 1 @coder",
    })
    assert "Message posted to room 'sprint-room'" in res_send
    assert "@coder" in res_send

    # 3. View room history
    res_hist = group_chat_tool.invoke({"action": "history", "room_name": "sprint-room"})
    assert "=== Recent Messages in 'sprint-room'" in res_hist
    assert "sprint 1 @coder" in res_hist

    # 4. Propose consensus vote
    res_prop = group_chat_tool.invoke({
        "action": "propose_vote",
        "room_name": "sprint-room",
        "sender": "architect",
        "question": "Adopt pytest-split for CI parallelization?",
    })
    assert "Proposal created successfully" in res_prop
    prop_id = res_prop.split("Proposal ID: ")[1].strip()

    # 5. Cast vote
    res_vote = group_chat_tool.invoke({
        "action": "cast_vote",
        "room_name": "sprint-room",
        "proposal_id": prop_id,
        "sender": "coder",
        "vote": "agree",
    })
    assert "Vote recorded for @coder: agree" in res_vote

    # 6. Tally vote
    res_tally = group_chat_tool.invoke({
        "action": "tally_vote",
        "room_name": "sprint-room",
        "proposal_id": prop_id,
    })
    assert "Proposal Tally:" in res_tally
    assert "Agree: 1" in res_tally


def test_kanban_board_tool_workflow():
    # 1. Create a task
    res_create = kanban_board_tool.invoke({
        "action": "create_task",
        "board_id": "test-board",
        "title": "Setup distributed caching",
        "priority": "high",
    })
    assert "Created Task TASK-" in res_create
    assert "Column: todo" in res_create
    task_id = res_create.split("Created Task ")[1].split(" on board")[0].strip()

    # 2. Claim task
    res_claim = kanban_board_tool.invoke({
        "action": "claim",
        "board_id": "test-board",
        "task_id": task_id,
        "assignee": "coder",
    })
    assert f"Task {task_id} claimed by @coder" in res_claim
    assert "in_progress" in res_claim

    # 3. Submit review
    res_sub = kanban_board_tool.invoke({
        "action": "submit_review",
        "board_id": "test-board",
        "task_id": task_id,
        "assignee": "coder",
        "reviewer": "reviewer",
    })
    assert "submitted for review" in res_sub
    assert "in_review" in res_sub

    # 4. Approve task
    res_app = kanban_board_tool.invoke({
        "action": "approve",
        "board_id": "test-board",
        "task_id": task_id,
        "reviewer": "reviewer",
        "feedback": "Redis clustering configs verified.",
    })
    assert f"Task {task_id} APPROVED by @reviewer" in res_app
    assert "Column: done" in res_app

    # 5. List tasks
    res_list = kanban_board_tool.invoke({
        "action": "list",
        "board_id": "test-board",
    })
    assert "=== Kanban Board: 'test-board'" in res_list
    assert task_id in res_list
