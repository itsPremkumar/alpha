"""Built-in LangChain tool for Decoupled External Job operations."""

from __future__ import annotations

import asyncio
import json

from langchain.tools import tool

from alpha.jobs.models import JobPriority, JobSpec, JobStatus
from alpha.jobs.queue import PersistentJobQueue
from alpha.jobs.runner import ExternalJobRunner

_GLOBAL_QUEUE = PersistentJobQueue(max_concurrency=4)
_GLOBAL_RUNNER = ExternalJobRunner(queue=_GLOBAL_QUEUE)


@tool("external_job", parse_docstring=True)
def job_tool(
    action: str,
    command: str = "",
    job_id: str = "",
    priority: str = "normal",
    timeout_seconds: float = 300.0,
    working_dir: str = "",
    status_filter: str = "",
) -> str:
    """Manage asynchronous background OS jobs decoupled from reasoning loops.

    Model-submitted jobs are gated: submit() discloses ``execution_blocked``
    plus the ``gate_reason`` when the operator gate will block execution (the
    queued job still reaches status 'failed' carrying that same ``error``), and
    logs()/status() surface ``error`` so a failed job always explains itself.

    Args:
        action: 'submit', 'status', 'logs', 'cancel', 'list'.
        command: Command string to execute asynchronously (e.g. 'pytest tests/' or 'npm run build').
        job_id: Target job identifier for status, logs, or cancellation.
        priority: Scheduling priority ('critical', 'high', 'normal', 'low').
        timeout_seconds: Maximum run duration before timeout.
        working_dir: Subprocess working directory.
        status_filter: Optional filter when listing jobs ('queued', 'running', 'completed', 'failed').
    """
    try:
        if action == "submit":
            if not command:
                return "Error: 'command' argument is required for submit action."

            p_enum = JobPriority.NORMAL
            try:
                p_enum = JobPriority(priority.lower())
            except ValueError:
                pass

            spec = JobSpec(
                command=command,
                priority=p_enum,
                working_dir=working_dir or None,
            )
            spec.resources.timeout_seconds = timeout_seconds

            # Probe the exact gate execute_spec() will hit later, so submit
            # discloses a blocked run up front instead of luring the model into
            # polling a job that can never execute. authorized_operator=False
            # mirrors the tool's own execution path (the model is not an
            # authenticated operator); the queued job still reaches status
            # 'failed' with this same string as its error.
            gate_reason: str | None = None
            try:
                _GLOBAL_RUNNER.require_host_execution(authorized_operator=False)
            except PermissionError as exc:
                gate_reason = str(exc)

            _GLOBAL_QUEUE.enqueue(spec)

            def _run_in_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(_GLOBAL_RUNNER.execute_spec(spec))
                finally:
                    loop.close()

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_GLOBAL_RUNNER.execute_spec(spec))
            except RuntimeError:
                import threading

                threading.Thread(target=_run_in_thread, daemon=True).start()

            payload: dict[str, object] = {
                "status": "job_submitted",
                "job_id": spec.job_id,
                "command": command,
                "priority": spec.priority.value,
                "timeout_seconds": timeout_seconds,
                "execution_blocked": gate_reason is not None,
            }
            if gate_reason is not None:
                payload["gate_reason"] = gate_reason
                payload["note"] = (
                    "This job is queued but will not execute: the host-job gate denied it before any command ran. Do not poll for output — read the failure via action='logs' (field 'error'), or use an allowed execution tool instead."
                )
            return json.dumps(payload, indent=2)

        elif action == "status":
            if not job_id:
                return "Error: 'job_id' argument is required for status action."
            res = _GLOBAL_QUEUE.get_status(job_id)
            if not res:
                return f"Error: Job '{job_id}' not found."
            return json.dumps(res.model_dump(), indent=2)

        elif action == "logs":
            if not job_id:
                return "Error: 'job_id' argument is required for logs action."
            res = _GLOBAL_QUEUE.get_status(job_id)
            if not res:
                return f"Error: Job '{job_id}' not found."
            return json.dumps(
                {
                    "job_id": job_id,
                    "status": res.status.value,
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                    "exit_code": res.exit_code,
                    "error": res.error,
                },
                indent=2,
            )

        elif action == "cancel":
            if not job_id:
                return "Error: 'job_id' argument is required for cancel action."
            cancelled = _GLOBAL_RUNNER.cancel(job_id)
            return json.dumps({"job_id": job_id, "cancelled": cancelled}, indent=2)

        elif action == "list":
            s_enum = None
            if status_filter:
                try:
                    s_enum = JobStatus(status_filter.lower())
                except ValueError:
                    pass
            jobs = _GLOBAL_QUEUE.list_jobs(status=s_enum)
            return json.dumps([j.model_dump() for j in jobs], indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error executing external_job tool: {exc}"
