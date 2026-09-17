"""Unit tests for Collaborative Kanban Board, DAG dependency unblocking, and Group Bridge."""

from pathlib import Path

from agent_workspace.groups.service import GroupChatService
from agent_workspace.kanban.dependency import DependencyGraph
from agent_workspace.kanban.models import KanbanTask
from agent_workspace.kanban.store import KanbanStore


def test_dependency_graph_cycle_detection():
    # Healthy linear DAG: A -> B -> C
    tasks_ok = {
        "A": KanbanTask(task_id="A", board_id="b", title="A", column="done"),
        "B": KanbanTask(task_id="B", board_id="b", title="B", dependencies=["A"], column="done"),
        "C": KanbanTask(task_id="C", board_id="b", title="C", dependencies=["B"], column="todo"),
    }
    assert DependencyGraph.has_cycle(tasks_ok) is False
    assert DependencyGraph.are_dependencies_satisfied(tasks_ok["C"], tasks_ok) is True

    # Cyclic dependency: X depends on Y, Y depends on X
    tasks_cycle = {
        "X": KanbanTask(task_id="X", board_id="b", title="X", dependencies=["Y"]),
        "Y": KanbanTask(task_id="Y", board_id="b", title="Y", dependencies=["X"]),
    }
    assert DependencyGraph.has_cycle(tasks_cycle) is True


def test_kanban_store_task_lifecycle_and_dag_unblock(tmp_path: Path):
    board_file = tmp_path / "boards.json"
    store = KanbanStore(storage_path=board_file)

    # 1. Create prerequisite task 1
    t1 = store.create_task(
        board_id="project-alpha",
        title="Implement database schema",
        priority="high",
    )
    assert t1.column == "todo"
    assert t1.dependencies == []

    # 2. Create dependent task 2
    t2 = store.create_task(
        board_id="project-alpha",
        title="Implement API endpoints",
        dependencies=[t1.task_id],
    )
    # Since t1 is not yet 'done', t2 starts as 'blocked'
    assert t2.column == "blocked"
    assert "Waiting on prerequisite tasks" in (t2.blocked_reason or "")

    # 3. Bot claims t1
    claim_res = store.claim_task("project-alpha", t1.task_id, bot_name="coder")
    assert claim_res["status"] == "ok"
    assert claim_res["column"] == "in_progress"

    # Cannot claim blocked t2
    blocked_claim = store.claim_task("project-alpha", t2.task_id, bot_name="coder")
    assert blocked_claim["status"] == "error"
    assert "BLOCKED" in blocked_claim["error"]

    # 4. Submit t1 for review
    sub_res = store.submit_for_review(
        "project-alpha",
        t1.task_id,
        bot_name="coder",
        reviewer_name="reviewer",
        artifacts=["schema.sql"],
    )
    assert sub_res["status"] == "ok"
    assert sub_res["column"] == "in_review"

    # 5. Reviewer requests changes first
    rev_changes = store.request_changes(
        "project-alpha",
        t1.task_id,
        reviewer_name="reviewer",
        feedback="Add index on email column.",
    )
    assert rev_changes["status"] == "ok"
    assert rev_changes["column"] == "in_progress"

    # 6. Re-submit and approve
    store.submit_for_review("project-alpha", t1.task_id, bot_name="coder")
    app_res = store.approve_task(
        "project-alpha",
        t1.task_id,
        reviewer_name="reviewer",
        comment="Schema verified.",
    )
    assert app_res["status"] == "ok"
    assert app_res["column"] == "done"
    assert app_res["unblocked_count"] == 1
    assert t2.task_id in app_res["unblocked_tasks"]

    # Verify t2 is now automatically unblocked into 'todo'
    tasks = store.list_tasks("project-alpha")
    t2_fresh = next(t for t in tasks if t.task_id == t2.task_id)
    assert t2_fresh.column == "todo"
    assert t2_fresh.blocked_reason is None

    # Now t2 can be claimed
    t2_claim = store.claim_task("project-alpha", t2.task_id, bot_name="api_specialist")
    assert t2_claim["status"] == "ok"
    assert t2_claim["column"] == "in_progress"


def test_kanban_bridge_broadcast(tmp_path: Path):
    board_file = tmp_path / "boards.json"
    room_file = tmp_path / "rooms.json"

    # Configure services
    group_service = GroupChatService(storage_path=room_file)
    # Monkey-patch global group service for bridge testing
    import agent_workspace.kanban.bridge as bridge_module
    old_service_getter = bridge_module.get_group_chat_service
    bridge_module.get_group_chat_service = lambda: group_service

    try:
        store = KanbanStore(storage_path=board_file)
        board = store.get_or_create_board("feature-board", room_id="feature-room")

        task = store.create_task("feature-board", "Build Web UI")
        store.claim_task("feature-board", task.task_id, "frontend_bot")

        # Check room log received real-time broadcast events
        room = group_service.get_room("feature-room")
        assert room is not None
        assert len(room.log) >= 2
        # Check event contents
        created_msg = room.log[0]
        assert "[TASK CREATED]" in created_msg.content
        assert task.task_id in created_msg.content

        claimed_msg = room.log[1]
        assert "[TASK CLAIMED]" in claimed_msg.content
        assert "@frontend_bot" in claimed_msg.content
    finally:
        bridge_module.get_group_chat_service = old_service_getter
