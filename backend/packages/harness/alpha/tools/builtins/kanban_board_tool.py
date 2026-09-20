"""Built-in tool for managing collaborative Kanban boards with DAG unblocking and peer review."""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from alpha.kanban.models import TaskPriority
from alpha.kanban.store import get_kanban_store


@tool("kanban_board", parse_docstring=True)
def kanban_board_tool(
    action: Literal["create_task", "claim", "submit_review", "approve", "request_changes", "list"],
    board_id: str = "main",
    task_id: str = "",
    title: str = "",
    description: str = "",
    priority: TaskPriority = "medium",
    dependencies: str = "",
    assignee: str = "",
    reviewer: str = "reviewer",
    feedback: str = "",
) -> str:
    """Manage tasks on the collaborative Kanban board with review gates and DAG unblocking.

    Tasks progress across columns: backlog -> todo -> in_progress -> in_review -> done.
    Prerequisite tasks in 'dependencies' automatically place tasks in 'blocked' until done.
    All events broadcast in real-time to the bound Group Chat room.

    Args:
        action: Operation ('create_task', 'claim', 'submit_review', 'approve', 'request_changes', 'list').
        board_id: Identifier of the Kanban board (defaults to 'main'). Auto-created if missing.
        task_id: ID of the task (required for claim, submit_review, approve, request_changes).
        title: Title of the new task (required for 'create_task').
        description: Description/spec of the task (optional for 'create_task').
        priority: Priority ('low', 'medium', 'high', 'critical'). Defaults to 'medium'.
        dependencies: Comma-separated task IDs this task depends on (e.g. 'TASK-A,TASK-B').
        assignee: Bot handle claiming the task (required for 'claim').
        reviewer: Bot handle reviewing the task (defaults to 'reviewer').
        feedback: Review comments or acceptance notes (for 'approve' and 'request_changes').
    """
    store = get_kanban_store()

    if action == "create_task":
        if not title:
            return "Error: 'title' is required for 'create_task'."
        deps = [d.strip() for d in dependencies.split(",") if d.strip()] if dependencies else None
        task = store.create_task(
            board_id=board_id,
            title=title,
            description=description,
            priority=priority,
            dependencies=deps,
        )
        return (
            f"Created Task {task.task_id} on board '{board_id}'.\n"
            f"- Title: {task.title}\n"
            f"- Column: {task.column}"
            + (f" (BLOCKED: {task.blocked_reason})" if task.column == "blocked" else "")
        )

    elif action == "claim":
        if not task_id or not assignee:
            return "Error: 'task_id' and 'assignee' are required for 'claim'."
        res = store.claim_task(board_id=board_id, task_id=task_id, bot_name=assignee)
        if res.get("status") == "error":
            return f"Error: {res.get('error')}"
        return f"Task {task_id} claimed by @{assignee} -> Column: in_progress."

    elif action == "submit_review":
        if not task_id:
            return "Error: 'task_id' is required for 'submit_review'."
        res = store.submit_for_review(board_id=board_id, task_id=task_id, bot_name=assignee or "worker", reviewer_name=reviewer)
        if res.get("status") == "error":
            return f"Error: {res.get('error')}"
        return f"Task {task_id} submitted for review to @{reviewer} -> Column: in_review."

    elif action == "approve":
        if not task_id:
            return "Error: 'task_id' is required for 'approve'."
        res = store.approve_task(board_id=board_id, task_id=task_id, reviewer_name=reviewer, comment=feedback or "Accepted.")
        if res.get("status") == "error":
            return f"Error: {res.get('error')}"
        unblocked_msg = f" (Auto-unblocked {res.get('unblocked_count')} dependent tasks: {res.get('unblocked_tasks')})" if res.get("unblocked_count") else ""
        return f"Task {task_id} APPROVED by @{reviewer} -> Column: done.{unblocked_msg}"

    elif action == "request_changes":
        if not task_id:
            return "Error: 'task_id' is required for 'request_changes'."
        res = store.request_changes(board_id=board_id, task_id=task_id, reviewer_name=reviewer, feedback=feedback or "Revisions requested.")
        if res.get("status") == "error":
            return f"Error: {res.get('error')}"
        return f"Changes requested for {task_id} by @{reviewer} -> Returned to: in_progress."

    elif action == "list":
        tasks = store.list_tasks(board_id=board_id)
        if not tasks:
            return f"No tasks on board '{board_id}'."
        out = [f"=== Kanban Board: '{board_id}' ({len(tasks)} Tasks) ==="]
        for t in tasks:
            assignee_str = f"@{t.assignee}" if t.assignee else "unassigned"
            out.append(f"- [{t.task_id}] ({t.column.upper()}) {t.title} [Assignee: {assignee_str}]")
        return "\n".join(out)

    return f"Error: Unknown action '{action}'."
