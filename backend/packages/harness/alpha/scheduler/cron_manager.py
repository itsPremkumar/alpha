"""Autonomous Background Cron & Periodic Task Scheduler inspired by Hermes Agent."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CronJob:
    name: str
    cron_expression: str
    command_or_prompt: str
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    is_enabled: bool = True
    last_run: float | None = None
    next_run: float | None = None
    run_count: int = 0
    last_status: str = "pending"
    last_output: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CronManager:
    """Manages scheduled background cron jobs with persistent storage and execution tracking."""

    def __init__(self, root_dir: Path | str | None = None):
        root = Path(root_dir or Path.cwd())
        self.cron_file = root / ".alpha" / "cron" / "jobs.json"
        self._jobs: dict[str, CronJob] = {}
        self._load()

    def _load(self) -> None:
        if self.cron_file.exists():
            try:
                data = json.loads(self.cron_file.read_text(encoding="utf-8"))
                for j in data:
                    job = CronJob(**j)
                    self._jobs[job.job_id] = job
            except Exception:
                pass

    def _save(self) -> None:
        self.cron_file.parent.mkdir(parents=True, exist_ok=True)
        payload = [j.to_dict() for j in self._jobs.values()]
        self.cron_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add_job(self, name: str, cron_expression: str, command_or_prompt: str) -> CronJob:
        job = CronJob(
            name=name,
            cron_expression=cron_expression,
            command_or_prompt=command_or_prompt,
            next_run=time.time(),  # Initially due immediately
        )
        self._jobs[job.job_id] = job
        self._save()
        return job

    def get_job(self, job_id: str) -> CronJob | None:
        return self._jobs.get(job_id)

    def remove_job(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._save()
            return True
        return False

    def pause_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.is_enabled = False
            self._save()
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.is_enabled = True
            self._save()
            return True
        return False

    def list_jobs(self) -> list[CronJob]:
        return list(self._jobs.values())

    def run_due(self, executor_fn: Callable[[CronJob], str] | None = None, wake_gate_fn: Callable[[CronJob], str] | None = None) -> list[dict[str, Any]]:
        """Run all jobs that are enabled and due.

        When ``wake_gate_fn`` is given, its output is parsed as a wake gate
        first: a declined gate skips the job without calling the executor.
        """
        from alpha.scheduler.wake_gate import should_wake

        now = time.time()
        results = []

        for job in self._jobs.values():
            if not job.is_enabled:
                continue
            if job.next_run is None or job.next_run <= now:
                if wake_gate_fn is not None:
                    try:
                        gate_out = wake_gate_fn(job)
                    except Exception as e:
                        gate_out = None
                        job.last_output = f"Wake gate error (failing open): {e}"
                    if gate_out is not None and not should_wake(gate_out):
                        job.last_status = "skipped"
                        job.last_output = "Skipped by wake gate (no LLM run)."
                        job.last_run = now
                        job.run_count += 1
                        job.next_run = now + 300.0
                        results.append(
                            {
                                "job_id": job.job_id,
                                "name": job.name,
                                "status": job.last_status,
                                "output": job.last_output,
                            }
                        )
                        continue
                # Execute job
                try:
                    if executor_fn:
                        output = executor_fn(job)
                    else:
                        output = f"Executed scheduled job '{job.name}' ({job.command_or_prompt})"
                    job.last_status = "success"
                    job.last_output = output
                except Exception as e:
                    job.last_status = "failed"
                    job.last_output = f"Error: {e}"

                job.last_run = now
                job.run_count += 1
                # Schedule next run (mock interval default 300s)
                job.next_run = now + 300.0
                results.append(
                    {
                        "job_id": job.job_id,
                        "name": job.name,
                        "status": job.last_status,
                        "output": job.last_output,
                    }
                )

        self._save()
        return results


_global_cron_manager: CronManager | None = None


def get_cron_manager(root_dir: Path | str | None = None) -> CronManager:
    global _global_cron_manager
    if _global_cron_manager is None or root_dir is not None:
        _global_cron_manager = CronManager(root_dir)
    return _global_cron_manager
