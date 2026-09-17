"""Built-in tool for managing asynchronous background processes and handles."""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from agent_workspace.sandbox.process_manager import get_process_manager


@tool("process_handle", parse_docstring=True)
def process_handle_tool(
    action: Literal["start", "poll", "tail", "kill", "list"],
    command: str = "",
    handle_id: str = "",
    lines: int = 20,
) -> str:
    """Manage asynchronous background processes, check handles, and tail output.

    Inspired by Prime Agent's rlm/bash.py background execution model. Allows long-running
    builds, tests, or servers to run without blocking the current agent turn.

    Args:
        action: Operation to perform ('start', 'poll', 'tail', 'kill', 'list').
        command: Shell command to run in the background (required for 'start').
        handle_id: Identifier of the process handle (required for 'poll', 'tail', 'kill').
        lines: Number of recent lines to tail (defaults to 20).
    """
    pm = get_process_manager()

    if action == "start":
        if not command.strip():
            return "Error: 'command' parameter is required for 'start' action."
        handle = pm.start_background(command)
        return (
            f"Process started in background.\n"
            f"Handle ID: {handle.handle_id}\n"
            f"PID: {handle.pid}\n"
            f"Command: {command}\n"
            f"Use process_handle(action='poll', handle_id='{handle.handle_id}') to check status."
        )

    elif action == "list":
        procs = pm.list_all()
        if not procs:
            return "No background processes currently tracked."
        out = ["=== Tracked Background Processes ==="]
        for p in procs:
            status = "RUNNING" if p.is_running() else f"EXITED({p.poll()})"
            out.append(f"- [{p.handle_id}] PID {p.pid} ({status}): `{p.command}`")
        return "\n".join(out)

    elif action == "poll":
        if not handle_id:
            return "Error: 'handle_id' parameter is required for 'poll'."
        handle = pm.get(handle_id)
        if not handle:
            return f"Error: No process handle found with ID '{handle_id}'."
        code = handle.poll()
        if code is None:
            return f"Process [{handle_id}] PID {handle.pid} is still RUNNING."
        return f"Process [{handle_id}] PID {handle.pid} has TERMINATED with exit code {code}."

    elif action == "tail":
        if not handle_id:
            return "Error: 'handle_id' parameter is required for 'tail'."
        handle = pm.get(handle_id)
        if not handle:
            return f"Error: No process handle found with ID '{handle_id}'."
        out = handle.tail(lines=lines)
        status = "RUNNING" if handle.is_running() else f"EXITED({handle.poll()})"
        return f"=== Output for [{handle_id}] ({status}, last {lines} lines) ===\n{out or '(No output captured yet)'}"

    elif action == "kill":
        if not handle_id:
            return "Error: 'handle_id' parameter is required for 'kill'."
        handle = pm.get(handle_id)
        if not handle:
            return f"Error: No process handle found with ID '{handle_id}'."
        if not handle.is_running():
            return f"Process [{handle_id}] has already exited with code {handle.poll()}."
        success = handle.kill()
        if success:
            return f"Successfully terminated process [{handle_id}] PID {handle.pid}."
        return f"Error: Failed to terminate process [{handle_id}]."

    return f"Error: Unknown action '{action}'."
