"""Built-in Cron Job management tool inspired by Hermes Agent."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.scheduler.cron_manager import get_cron_manager


@tool("cronjob_manage", parse_docstring=True)
def cronjob_manage(
    action: str = "list",
    name: str = "",
    schedule: str = "*/15 * * * *",
    command_or_prompt: str = "",
    job_id: str = "",
) -> str:
    """Manage autonomous background cron tasks and periodic health/lint checks.

    Args:
        action: Action to perform: 'add', 'list', 'remove', 'pause', 'resume', or 'run_due'.
        name: Name of the scheduled job when adding.
        schedule: Cron expression (e.g., '0 * * * *' for hourly, '*/15 * * * *' for every 15m).
        command_or_prompt: Task prompt or command to execute on schedule.
        job_id: Specific job ID to pause, resume, or remove.
    """
    manager = get_cron_manager()
    act = action.strip().lower()

    if act == "add":
        job = manager.add_job(name=name or "unnamed_cron", cron_expression=schedule, command_or_prompt=command_or_prompt)
        return f"Scheduled job '{job.name}' added successfully [ID: {job.job_id}] with schedule '{job.cron_expression}'."

    elif act == "list":
        jobs = manager.list_jobs()
        return json.dumps([j.to_dict() for j in jobs], indent=2)

    elif act == "remove":
        ok = manager.remove_job(job_id)
        return f"Job '{job_id}' removed: {ok}"

    elif act == "pause":
        ok = manager.pause_job(job_id)
        return f"Job '{job_id}' paused: {ok}"

    elif act == "resume":
        ok = manager.resume_job(job_id)
        return f"Job '{job_id}' resumed: {ok}"

    elif act == "run_due":
        results = manager.run_due()
        return json.dumps(results, indent=2)

    return f"Unknown action '{action}'. Use 'add', 'list', 'remove', 'pause', 'resume', or 'run_due'."
