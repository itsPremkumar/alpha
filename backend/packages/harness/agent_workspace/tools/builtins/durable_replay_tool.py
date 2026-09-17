"""Built-in Durable Task Orchestration and Replay LangChain Tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.orchestration.durable_replay import (
    DurableReplayEngine,
)

_GLOBAL_REPLAY = DurableReplayEngine()


@tool("manage_durable_orchestration", parse_docstring=True)
def manage_durable_orchestration(
    action: str,
    task_id: str = "",
    event_type: str = "TOOL_EXECUTED",
    payload_json: str = "{}",
    idempotency_key: str = "",
    step_index: int = 0,
    state: str = "running",
) -> str:
    """Manage durable event journals, task checkpointing, and crash-resilient replay.

    Args:
        action: 'append_event', 'save_checkpoint', 'check_idempotency', 'recover_task', 'get_journal'.
        task_id: Identifier of the task being managed.
        event_type: Type of lifecycle event ('TASK_STARTED', 'TOOL_EXECUTED', 'STATE_TRANSITION', 'TASK_COMPLETED').
        payload_json: JSON data payload for event or checkpoint variables.
        idempotency_key: Unique idempotency key preventing duplicate side effects.
        step_index: Sequential step number for checkpointing.
        state: State of task when creating checkpoint.
    """
    try:
        data = json.loads(payload_json) if payload_json else {}
    except Exception:
        data = {"raw": payload_json}

    try:
        if action == "append_event":
            ev = _GLOBAL_REPLAY.append_event(
                task_id=task_id,
                event_type=event_type,
                payload=data,
                idempotency_key=idempotency_key or None,
            )
            return json.dumps({"status": "event_appended", "event": ev.to_dict()}, indent=2)

        elif action == "save_checkpoint":
            cp = _GLOBAL_REPLAY.save_checkpoint(
                task_id=task_id,
                step_index=step_index,
                state=state,
                variables=data,
            )
            return json.dumps({"status": "checkpoint_saved", "checkpoint": cp.to_dict()}, indent=2)

        elif action == "check_idempotency":
            done = _GLOBAL_REPLAY.is_action_completed(idempotency_key)
            return json.dumps({"idempotency_key": idempotency_key, "already_completed": done}, indent=2)

        elif action == "recover_task":
            recovery = _GLOBAL_REPLAY.recover_task(task_id)
            return json.dumps({"status": "task_recovered", "recovery": recovery}, indent=2)

        elif action == "get_journal":
            journal = _GLOBAL_REPLAY.get_journal(task_id)
            return json.dumps(journal, indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error managing durable orchestration: {exc}"
