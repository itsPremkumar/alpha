"""Boulder Checkpoint Tool (Sisyphus Task Continuation).

Provides tools for the agent to save and resume multi-session checkpoint progress.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import tool

from alpha.state.boulder import (
    append_session_id,
    clear_boulder,
    complete_boulder,
    create_boulder,
    load_boulder,
    update_checklist_item,
)


@tool
def boulder_checkpoint_manage(
    action: str,
    task: str | None = None,
    checklist: list[str] | None = None,
    step_index: int | None = None,
    completed: bool = True,
    evidence: str | None = None,
    session_id: str | None = None,
    custom_path: str | None = None,
) -> str:
    """Manage persistent multi-session task checkpoints. Actions: 'create', 'status', 'update_step', 'append_session', 'complete', 'clear'."""
    path = Path(custom_path) if custom_path else None

    if action == "create":
        if not task or not checklist:
            return "Error: 'task' and 'checklist' are required for create."
        state = create_boulder(task, checklist, path=path, session_id=session_id)
        return f"Created Boulder checkpoint '{state.work_id}' for task: '{task}' with {len(checklist)} steps."

    elif action == "status":
        state = load_boulder(path)
        if not state:
            return "No active Boulder checkpoint found."
        return json.dumps(state.to_dict(), indent=2)

    elif action == "update_step":
        if step_index is None:
            return "Error: 'step_index' is required for update_step."
        try:
            state = update_checklist_item(step_index, completed, evidence or "", path=path)
            return f"Updated step {step_index}. Current boulder status: '{state.status}'."
        except Exception as e:
            return f"Error: {e}"

    elif action == "append_session":
        if not session_id:
            return "Error: 'session_id' is required."
        try:
            state = append_session_id(session_id, path=path)
            return f"Appended session '{session_id}'. Lineage: {state.session_ids}."
        except Exception as e:
            return f"Error: {e}"

    elif action == "complete":
        try:
            state = complete_boulder(path=path)
            return f"Boulder '{state.work_id}' marked as completed."
        except Exception as e:
            return f"Error: {e}"

    elif action == "clear":
        clear_boulder(path=path)
        return "Cleared active Boulder checkpoint."

    elif action == "create_handoff":
        from alpha.state.handoff import SessionHandoffPackage, get_handoff_manager

        state = load_boulder(path)
        base_dir = path.parent if (path and (path.is_file() or str(path).endswith(".json"))) else custom_path
        mgr = get_handoff_manager(base_dir)
        work_id = state.work_id if state else (task or "default")
        task_desc = getattr(state, "top_level_task", getattr(state, "task", task or "Ongoing multi-phase task"))
        completed_items = []
        pending_items = []
        if state:
            for item in state.checklist:
                item_name = getattr(item, "item", getattr(item, "step", str(item)))
                if item.completed:
                    completed_items.append({"step": item_name, "evidence": item.evidence})
                else:
                    pending_items.append(item_name)
        elif checklist:
            pending_items = list(checklist)

        pkg = SessionHandoffPackage(
            work_id=work_id,
            task_objective=task_desc,
            completed_milestones=completed_items,
            pending_milestones=pending_items,
            active_hypotheses=[evidence] if evidence else [],
            next_action=pending_items[0] if pending_items else "Task verification",
        )
        saved_file = mgr.save_handoff(pkg)
        return f"Successfully created session handoff package at {saved_file}.\n\nContext:\n{mgr.format_for_context(pkg)}"

    elif action == "restore_handoff":
        from alpha.state.handoff import get_handoff_manager

        base_dir = path.parent if (path and (path.is_file() or str(path).endswith(".json"))) else custom_path
        mgr = get_handoff_manager(base_dir)
        pkg = mgr.load_latest_handoff()
        if not pkg:
            return "No previous session handoff package found."
        return mgr.format_for_context(pkg)

    return f"Error: unknown action '{action}'."
