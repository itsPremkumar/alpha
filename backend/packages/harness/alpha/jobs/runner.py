"""Asynchronous runner executing external OS jobs decoupled from LLM reasoning."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from alpha.config import get_app_config
from alpha.jobs.models import JobResult, JobSpec, JobStatus
from alpha.jobs.queue import PersistentJobQueue
from alpha.sandbox.env_policy import build_sandbox_env

logger = logging.getLogger(__name__)

MAX_RESULT_OUTPUT_CHARS = 200_000


def _bound_output(value: str) -> str:
    if len(value) > MAX_RESULT_OUTPUT_CHARS:
        return value[:MAX_RESULT_OUTPUT_CHARS]
    return value


class ExternalJobRunner:
    """Manages asynchronous execution of decoupled subprocess jobs."""

    def __init__(self, queue: PersistentJobQueue | None = None):
        self._queue = queue or PersistentJobQueue()
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._pending_ids: set[str] = set()
        self._completions: dict[str, asyncio.Future[JobResult]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue.add_listener(self._on_queue_change)

    @property
    def queue(self) -> PersistentJobQueue:
        return self._queue

    @staticmethod
    def require_host_execution(*, authorized_operator: bool = False) -> None:
        if authorized_operator is not True:
            raise PermissionError("Authenticated operator permission is required for host jobs")
        try:
            allowed = getattr(getattr(get_app_config(), "sandbox", None), "allow_host_bash", False) is True
        except Exception:
            allowed = False
        if not allowed:
            raise PermissionError("Host jobs require explicit sandbox.allow_host_bash: true")

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop and (self._pending_ids or self._running_tasks):
            raise RuntimeError("Active job runners must use a single event loop")
        self._loop = loop

    def _on_queue_change(self, job_id: str, status: JobStatus) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed() and status != JobStatus.RUNNING:
            loop.call_soon_threadsafe(self._handle_queue_change, job_id)

    def _handle_queue_change(self, job_id: str) -> None:
        result = self._queue.get_status(job_id)
        if result is not None and result.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            task = self._running_tasks.get(job_id)
            if task is not None:
                if result.status == JobStatus.CANCELLED and not task.done() and not task.cancelling():
                    task.cancel()
            else:
                self._pending_ids.discard(job_id)
                completion = self._completions.pop(job_id, None)
                if completion is not None and not completion.done():
                    completion.set_result(result)
        self._dispatch()

    def _dispatch(self) -> None:
        while len(self._running_tasks) < self._queue.max_concurrency:
            spec = self._queue.dequeue_next(job_ids=self._pending_ids)
            if spec is None:
                return
            self._pending_ids.discard(spec.job_id)
            task = asyncio.create_task(self.execute_spec(spec, authorized_operator=True))
            self._running_tasks[spec.job_id] = task
            task.add_done_callback(lambda done, job_id=spec.job_id: self._job_done(job_id, done))

    def _job_done(self, job_id: str, task: asyncio.Task[Any]) -> None:
        self._running_tasks.pop(job_id, None)
        if task.cancelled():
            self._queue.cancel_job(job_id)
            self._queue.complete_job(JobResult(job_id=job_id, status=JobStatus.CANCELLED, completed_at=time.time()))
        elif (error := task.exception()) is not None:
            self._queue.complete_job(JobResult(job_id=job_id, status=JobStatus.FAILED, exit_code=-1, error=str(error), completed_at=time.time()))
        result = self._queue.get_status(job_id)
        completion = self._completions.pop(job_id, None)
        if completion is not None and not completion.done():
            completion.set_result(result)
        self._dispatch()

    def _schedule(self, spec: JobSpec) -> asyncio.Future[JobResult]:
        completion = asyncio.get_running_loop().create_future()
        self._completions[spec.job_id] = completion
        self._pending_ids.add(spec.job_id)
        self._dispatch()
        return completion

    async def _wait(self, job_id: str, completion: asyncio.Future[JobResult]) -> JobResult:
        try:
            return await asyncio.shield(completion)
        except asyncio.CancelledError:
            self.cancel(job_id)
            while not completion.done():
                try:
                    await asyncio.shield(completion)
                except asyncio.CancelledError:
                    continue
            raise

    async def submit_and_wait(self, spec: JobSpec, *, authorized_operator: bool = False) -> JobResult:
        """Submit a job to queue and await completion."""
        self.require_host_execution(authorized_operator=authorized_operator)
        self._bind_loop()
        self._queue.enqueue(spec)
        return await self._wait(spec.job_id, self._schedule(spec))

    async def submit_async(self, spec: JobSpec, *, authorized_operator: bool = False) -> str:
        """Submit a job for bounded background processing."""
        self.require_host_execution(authorized_operator=authorized_operator)
        self._bind_loop()
        self._queue.enqueue(spec)
        self._schedule(spec)
        return spec.job_id

    async def execute_spec(self, spec: JobSpec, *, authorized_operator: bool = False) -> JobResult:
        """Execute the job specification with timeout and cancellation protection."""
        try:
            self.require_host_execution(authorized_operator=authorized_operator)
        except PermissionError as exc:
            result = JobResult(job_id=spec.job_id, status=JobStatus.FAILED, exit_code=-1, error=str(exc), completed_at=time.time())
            existing = self._queue.get_status(spec.job_id)
            if self._running_tasks.get(spec.job_id) is asyncio.current_task() or existing is None or (existing.status == JobStatus.QUEUED and spec.job_id not in self._completions):
                self._queue.complete_job(result)
            return result
        if self._running_tasks.get(spec.job_id) is not asyncio.current_task():
            self._bind_loop()
            completion = self._completions.get(spec.job_id)
            if completion is not None:
                return await self._wait(spec.job_id, completion)
            existing = self._queue.get_status(spec.job_id)
            if existing is None:
                self._queue.enqueue(spec)
            elif existing.status != JobStatus.QUEUED:
                if existing.status == JobStatus.RUNNING:
                    raise RuntimeError("Job is already claimed by another worker")
                return existing
            return await self._wait(spec.job_id, self._schedule(spec))
        if self._queue.is_cancelled(spec.job_id):
            result = JobResult(
                job_id=spec.job_id,
                status=JobStatus.CANCELLED,
                error="Job cancelled prior to execution",
            )
            self._queue.complete_job(result)
            return result

        start_time = time.time()
        timeout = spec.resources.timeout_seconds
        working_dir = spec.working_dir or os.getcwd()

        env = build_sandbox_env(spec.env)
        is_shell = isinstance(spec.command, str)
        logger.info("Executing external job %s", spec.job_id)

        proc = None
        try:
            if is_shell:
                proc = await asyncio.create_subprocess_shell(
                    spec.command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=working_dir,
                    env=env,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    spec.command[0],
                    *spec.command[1:],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=working_dir,
                    env=env,
                )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                stdout_str = _bound_output(stdout_bytes.decode("utf-8", errors="replace"))
                stderr_str = _bound_output(stderr_bytes.decode("utf-8", errors="replace"))
                exit_code = proc.returncode or 0

                status = JobStatus.COMPLETED if exit_code == 0 else JobStatus.FAILED
                execution_time = time.time() - start_time

                result = JobResult(
                    job_id=spec.job_id,
                    status=status,
                    exit_code=exit_code,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    execution_seconds=execution_time,
                    started_at=start_time,
                    completed_at=time.time(),
                )

            except TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass

                result = JobResult(
                    job_id=spec.job_id,
                    status=JobStatus.TIMED_OUT,
                    exit_code=-1,
                    error=f"Job timed out after {timeout} seconds",
                    execution_seconds=time.time() - start_time,
                    started_at=start_time,
                    completed_at=time.time(),
                )

        except asyncio.CancelledError:
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                cleanup = asyncio.create_task(proc.communicate())
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                cleanup.result()
            self._queue.cancel_job(spec.job_id)
            raise
        except Exception as exc:
            logger.error("Error launching job %s", spec.job_id, exc_info=True)
            result = JobResult(
                job_id=spec.job_id,
                status=JobStatus.FAILED,
                exit_code=-1,
                error=str(exc),
                execution_seconds=time.time() - start_time,
                started_at=start_time,
                completed_at=time.time(),
            )

        self._queue.complete_job(result)
        return result

    def cancel(self, job_id: str) -> bool:
        return self._queue.cancel_job(job_id)
