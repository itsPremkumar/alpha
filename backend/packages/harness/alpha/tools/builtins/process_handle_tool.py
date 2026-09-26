"""Built-in tool for managing asynchronous background processes and handles."""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from alpha.sandbox.process_manager import START_SETTLE_SECONDS, get_process_manager


def _handle_header(handle, command: str) -> str:
    return (
        f"Handle ID: {handle.handle_id}\n"
        f"PID: {handle.pid}\n"
        f"Command: {command}\n"
        f"Use process_handle(action='poll', handle_id='{handle.handle_id}') to check status."
    )


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
        try:
            handle = pm.start_background(command)
        except Exception as e:
            # The spawn never happened. Reporting a handle here would be a
            # success claim with nothing behind it.
            return f"Error: failed to start background process: {type(e).__name__}: {e}"

        # Verify the outcome instead of asserting it. A command that fails
        # immediately (bad binary, non-zero exit) is a failure, and the start
        # result must say so with the real exit code rather than claiming a
        # successful spawn. Success is only ever claimed from an observed exit
        # code of 0, and "still running" never implies the command worked.
        code = handle.await_outcome(timeout=START_SETTLE_SECONDS)
        captured = handle.tail(lines=20)
        if code is None:
            return (
                f"Process is RUNNING in the background "
                f"(spawn verified: PID {handle.pid} alive after {START_SETTLE_SECONDS:g}s; "
                f"exit status not yet known).\n"
                f"{_handle_header(handle, command)}"
            )
        if code == 0:
            report = f"Process completed successfully in the background (exit code 0).\n{_handle_header(handle, command)}"
        else:
            report = f"Process FAILED in the background (exit code {code}).\n{_handle_header(handle, command)}"
        if captured:
            report += f"\n=== Output ===\n{captured}"
        return report

    elif action == "list":
        procs = pm.list_all()
        if not procs:
            return "No background processes currently tracked."
        out = ["=== Tracked Background Processes ==="]
        for p in procs:
            code = p.poll()
            status = "RUNNING" if code is None else f"EXITED({code})"
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
        if code != 0:
            output = handle.tail(lines=20)
            suffix = f"\n{output}" if output else ""
            return f"Process [{handle_id}] PID {handle.pid} has TERMINATED with exit code {code} (non-zero: the command FAILED).{suffix}"
        return f"Process [{handle_id}] PID {handle.pid} has TERMINATED with exit code {code} (success)."

    elif action == "tail":
        if not handle_id:
            return "Error: 'handle_id' parameter is required for 'tail'."
        handle = pm.get(handle_id)
        if not handle:
            return f"Error: No process handle found with ID '{handle_id}'."
        out = handle.tail(lines=lines)
        code = handle.poll()
        status = "RUNNING" if code is None else f"EXITED({code})"
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
            return f"Successfully terminated process [{handle_id}] PID {handle.pid} (death verified; exit code {handle.poll()})."
        return f"Error: Failed to terminate process [{handle_id}] PID {handle.pid} - the process is still running."

    return f"Error: Unknown action '{action}'."
