from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException

from alpha.persistence.scheduled_task_runs import ActiveScheduledRunConflict, ScheduledTaskAdmissionRejected
from alpha.runtime import ConflictError, RunRecord
from alpha.runtime.lane_scheduler import (
    RUN_ADMISSION_REJECTED_CODE,
    capacity_refusal_detail,
    capacity_refusal_retry_after_seconds,
    has_run_capacity,
    is_run_capacity_refusal,
)
from alpha.scheduler.schedules import next_run_at
from alpha.trace_context import ensure_trace_context
from alpha.utils.thread_id import validate_thread_id

from .job_memory import JobMemoryReadError, JobMemoryStore, JobMemoryWriteError

logger = logging.getLogger(__name__)

# Shared so the active-row fast path and the atomic-admission conflict path
# return byte-identical outcomes for the same active-occurrence condition.
_ACTIVE_RUN_CONFLICT_ERROR = "task already has an active run"
_RESTART_RECOVERY_ERROR = "interrupted: gateway restarted before the run reached a terminal state"
_LEASE_RECOVERY_ERROR = "interrupted: the owning gateway stopped renewing its run lease"
_QUEUE_TIMEOUT_ERROR = "scheduled task queue wait timeout exceeded"

#: Journal/outcome verb for a launch that never ran because the Gateway had no
#: in-flight run slot to give it. ``requeued`` (not ``launch_failed``) because
#: the occurrence is still owed exactly the attempt it was supposed to get.
_CAPACITY_REQUEUED_STATUS = "requeued"

#: Error text returned to a manual trigger that was refused a run slot. The
#: occurrence is queued and will be retried, so the caller is told that rather
#: than shown a 502 that reads as "your trigger failed".
_CAPACITY_WAIT_ERROR = "gateway run capacity exhausted; the occurrence is queued and will be retried"

#: Ceiling on the per-occurrence capacity backoff. Bounded so a long-lived
#: occurrence cannot back off past the point where a human would want to see it
#: move; the durable ceiling on waiting is ``queue_timeout_seconds``.
_MAX_CAPACITY_BACKOFF_SECONDS = 300.0

#: Upper bound on how many occurrences are tracked for capacity backoff at once.
#: Generous next to a poller's per-cycle claim limit; exists so a process that
#: never launches anything cannot grow the map without limit.
_CAPACITY_TRACKING_LIMIT = 512


def _record_capacity_metric(*, waiting: int, refusals: int, waited_seconds: float) -> None:
    """Publish the capacity-stall signal to the shared metrics registry.

    Without this, "scheduled work is being starved" is only visible as an
    absence -- a task list that stops updating -- and absence is exactly the
    signal nobody pages on. The registry import is deferred for the same reason
    ``collect_queue_health`` defers it: admission control must not be what pays
    the process's import cost.
    """
    try:
        from alpha.ops.metrics import get_metrics_registry

        registry = get_metrics_registry()
        registry.counter(
            "alpha_scheduler_capacity_refusals",
            help="Scheduled occurrences refused a Gateway in-flight run slot (retryable; the occurrence stays queued).",
        ).inc()
        registry.gauge(
            "alpha_scheduler_capacity_waiting_occurrences",
            help="Distinct scheduled occurrences currently held back by Gateway run capacity.",
        ).set(float(waiting))
        registry.gauge(
            "alpha_scheduler_capacity_max_refusals",
            help="Longest consecutive Gateway capacity refusal count for a single scheduled occurrence.",
        ).set(float(refusals))
        registry.gauge(
            "alpha_scheduler_capacity_backoff_seconds",
            help="Backoff applied before the next launch attempt of the most recently refused occurrence.",
        ).set(float(waited_seconds))
    except Exception:  # noqa: BLE001 - a metric must never fail a scheduled occurrence
        logger.debug("Failed to record scheduler run-capacity metrics", exc_info=True)


class ScheduledTaskService:
    def __init__(
        self,
        *,
        task_repo,
        task_run_repo,
        launch_run,
        poll_interval_seconds: int,
        lease_seconds: int,
        max_concurrent_runs: int,
        queue_timeout_seconds: int = 3600,
        multi_instance: bool = False,
        run_lease_grace_seconds: int = 10,
        job_memory: JobMemoryStore | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._task_run_repo = task_run_repo
        self._launch_run = launch_run
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_concurrent_runs = max_concurrent_runs
        self._queue_timeout_seconds = queue_timeout_seconds
        self._multi_instance = multi_instance
        self._run_lease_grace_seconds = run_lease_grace_seconds
        self._lease_owner = f"{socket.gethostname()}:{uuid.uuid4().hex}"
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._skip_next_lease_reconciliation = False
        # Consecutive Gateway capacity refusals per occurrence. Backs off the
        # retry so a saturated gateway is not polled into a refusal storm, and
        # is the signal that a specific occurrence is being starved.
        self._capacity_refusals: dict[str, int] = {}
        # When each occurrence may next attempt a launch. Separate from the count
        # because the count answers "how bad is it" and this answers "when may
        # we look again".
        self._capacity_retry_at: dict[str, datetime] = {}
        # Durable per-job memory (Hermes-style). None disables journaling and
        # leaves every prompt byte-identical to the pre-journal behavior.
        self._job_memory = job_memory
        # Process-local launch timestamps used to measure real durations; the
        # journal itself stores only what this process actually observed.
        self._job_run_started_at: dict[str, datetime] = {}
        # Disclosures for journal writes that failed after a run was already
        # live/terminal (cannot fail closed there): surfaced verbatim in the
        # next run's prompt, capped at 4 notes plus one cap marker.
        self._job_memory_notes: list[str] = []
        self._job_memory_notes_presented = False
        self._job_memory_notes_capped = False

    async def run_once(self, *, now: datetime) -> None:
        if self._multi_instance:
            if self._skip_next_lease_reconciliation:
                self._skip_next_lease_reconciliation = False
            else:
                await self._reconcile_active_state(now=now)
        else:
            await self._task_run_repo.recover_expired_launch_claims(
                error=_LEASE_RECOVERY_ERROR,
                now=now,
            )
        await self._expire_waiting_runs(now=now)
        await self._drain_queue(now=now)
        # Admission and execution capacity are separate. Due occurrences are
        # persisted even when all execution slots are busy; claim_queued_run()
        # applies the global launch budget under the database lock.
        claimed = await self._task_repo.claim_due_tasks(
            now=now,
            lease_owner=self._lease_owner,
            lease_seconds=self._lease_seconds,
            limit=self._max_concurrent_runs,
        )
        for task in claimed:
            await self.dispatch_task(task, now=now, trigger="scheduled")

    @staticmethod
    def _is_overlap_conflict(exc: Exception) -> bool:
        if isinstance(exc, ConflictError):
            return True
        return isinstance(exc, HTTPException) and exc.status_code == 409

    def _capacity_allows_launch(self, task_run_id: str, *, now: datetime) -> bool:
        """May this occurrence attempt a Gateway run admission right now?

        A free slot answers it immediately, so a recovered system is not made to
        wait out a backoff set while it was broken. Otherwise the occurrence is
        held until its own backoff window expires, and only then is the wait
        re-recorded and reported.

        The backoff is what stops a refusal storm. Without it every poll would
        re-attempt every queued occurrence against a gateway that has already
        said no, turning "I am early" into a burst of 429s that competes with the
        interactive traffic actually using the slots.
        """
        # Capacity is consulted first, so a freed slot launches immediately
        # rather than waiting out a backoff set while the system was busy.
        if has_run_capacity():
            return True
        if now < self._capacity_retry_at.get(task_run_id, now):
            # Already inside a known wait window. Nothing to add: the refusal
            # that set it is what an operator needs to see, once, not once per
            # poll for the whole window.
            return False
        return self._note_capacity_refusal(
            task_run_id,
            now=now,
            hint_seconds=float(self._poll_interval_seconds),
            advance=False,
        )

    def _note_capacity_refusal(
        self,
        task_run_id: str,
        *,
        now: datetime,
        hint_seconds: float,
        advance: bool = True,
    ) -> bool:
        """Record a capacity wait and schedule the next attempt behind a backoff.

        Always returns ``False``: the caller asked whether it may launch, and a
        recorded wait always means it may not. ``advance`` distinguishes the two
        callers. A refusal the Gateway actually returned moves the exponential
        counter, because the work really did try and really was turned away. The
        pre-flight gate does not, because nothing was attempted and counting it
        would inflate the backoff for a wait that cost nothing.
        """
        refusals = self._capacity_refusals.get(task_run_id, 0)
        if advance:
            refusals += 1
            self._capacity_refusals[task_run_id] = refusals
        else:
            # The gate still has to establish an entry so the occurrence is
            # visible as waiting; count it as one without deepening the backoff.
            self._capacity_refusals.setdefault(task_run_id, 1)
            refusals = self._capacity_refusals[task_run_id]
        # Exponential from the poll interval, capped, and never shorter than the
        # hint the Gateway itself sent: the server knows better than we do how
        # long its own slots are likely to be busy.
        backoff = min(
            max(self._poll_interval_seconds * (2 ** min(refusals - 1, 16)), hint_seconds),
            _MAX_CAPACITY_BACKOFF_SECONDS,
        )
        self._capacity_retry_at[task_run_id] = now + timedelta(seconds=backoff)
        self._prune_capacity_tracking()
        logger.warning(
            "Scheduled occurrence %s is waiting on Gateway run capacity (%s); it stays queued and retries in %.1fs",
            task_run_id,
            RUN_ADMISSION_REJECTED_CODE,
            backoff,
        )
        _record_capacity_metric(
            waiting=len(self._capacity_refusals),
            refusals=refusals,
            waited_seconds=backoff,
        )
        return False

    def _forget_capacity(self, task_run_id: str) -> None:
        """An occurrence launched: it is no longer waiting on capacity."""
        if self._capacity_refusals.pop(task_run_id, None) is not None:
            self._capacity_retry_at.pop(task_run_id, None)
            _record_capacity_metric(
                waiting=len(self._capacity_refusals),
                refusals=0,
                waited_seconds=0.0,
            )

    def _prune_capacity_tracking(self) -> None:
        """Keep the refusal bookkeeping bounded.

        Entries normally disappear when the occurrence launches or when the
        queue-timeout sweep terminalizes it, but a long-lived process can outlive
        both bookkeeping paths. Dropping the entries whose backoff expires soonest
        costs nothing but one extra poll each: they are the ones about to be
        retried anyway.
        """
        overflow = len(self._capacity_refusals) - _CAPACITY_TRACKING_LIMIT
        if overflow <= 0:
            return
        soonest = sorted(self._capacity_retry_at.items(), key=lambda item: item[1])[:overflow]
        for task_run_id, _retry_at in soonest:
            self._capacity_refusals.pop(task_run_id, None)
            self._capacity_retry_at.pop(task_run_id, None)

    async def _requeue_for_capacity(
        self,
        task: dict[str, Any],
        task_run_id: str,
        *,
        exc: Exception,
        trigger: str,
        now: datetime,
    ) -> dict[str, Any]:
        """Return a capacity-refused occurrence to the durable queue.

        Deliberately shares the overlap-conflict requeue (and the journal verb)
        with the other "this attempt never ran" case, because that is exactly
        what it is. The one thing that differs is that we keep a refusal count:
        the occurrence is not merely unlucky, it is losing a race for slots, and
        an operator needs to be able to see that.
        """
        detail = capacity_refusal_detail(exc)
        requeued = await self._task_run_repo.requeue_claimed_run(
            task_run_id,
            lease_owner=self._lease_owner,
            error=detail,
        )
        if not requeued:
            logger.warning(
                "Scheduled task-run %s lost its launch claim to a Gateway capacity refusal; leaving recovery-owned state unchanged",
                task_run_id,
            )
            return self._queued_result(task_run_id, str(task.get("thread_id") or ""), error=detail)
        await self._journal_job_attempt_outcome(
            task,
            task_run_id=task_run_id,
            status=_CAPACITY_REQUEUED_STATUS,
            error=detail,
        )
        # Start the retry countdown only now that the occurrence is durably back
        # in the queue, so the poll that observes the requeue cannot be the poll
        # that retries it.
        self._note_capacity_refusal(
            task_run_id,
            now=now,
            hint_seconds=capacity_refusal_retry_after_seconds(exc, default=float(self._poll_interval_seconds)),
        )
        logger.warning(
            "Scheduled occurrence %s (trigger %s) was refused a Gateway run slot: %s; it stays queued and is retried, not failed",
            task_run_id,
            trigger,
            detail,
        )
        return self._queued_result(task_run_id, str(task.get("thread_id") or ""), error=detail)

    @staticmethod
    def _task_status_for_failure(task: dict[str, Any], *, trigger: str) -> str:
        if trigger == "manual":
            # A failed manual trigger must not consume the task's scheduled
            # future: a `once` task with run_at still ahead would otherwise be
            # flipped to "failed" and never claimed again.
            return task.get("status") or "enabled"
        if task["schedule_type"] == "once":
            return "failed"
        return "enabled"

    @staticmethod
    def _task_status_for_launch(task: dict[str, Any], *, trigger: str) -> str:
        # The task-level status to write once _launch_run has produced a live
        # run. A `once` task stays "running" until handle_run_completion
        # observes the real terminal outcome; declaring "completed" at launch
        # would stick if the run fails or the process dies (startup
        # reconciliation is cancel_stuck_once_tasks).
        if task["schedule_type"] == "once":
            return "running"
        if trigger == "manual" and task.get("status") == "paused":
            return "paused"
        return "enabled"

    async def dispatch_task(
        self,
        task: dict[str, Any],
        *,
        now: datetime,
        trigger: str,
    ) -> dict[str, Any]:
        expected_lease_owner = self._lease_owner if trigger == "scheduled" else None
        execution_thread_id = task.get("thread_id")
        if task.get("context_mode") == "fresh_thread_per_run" or execution_thread_id is None:
            execution_thread_id = str(uuid.uuid4())
        try:
            validate_thread_id(execution_thread_id)
        except ValueError as exc:
            # Rows persisted before the thread-id contract was centralized may
            # hold IDs that were valid then (dots, unlimited length) but fail
            # the canonical pattern now. Route through the normal failure
            # bookkeeping instead of raising: an uncaught ValueError here would
            # surface as HTTP 500 on manual trigger and, in the poller, abort
            # the rest of the claimed batch every cycle while the task itself
            # is never marked with last_error.
            task_status = self._task_status_for_failure(task, trigger=trigger)
            await self._task_repo.update_after_launch(
                task["id"],
                status=task_status,
                next_run_at=next_run_at(
                    task["schedule_type"],
                    task["schedule_spec"],
                    task["timezone"],
                    now=now,
                ),
                last_run_at=now,
                last_run_id=None,
                last_thread_id=execution_thread_id,
                last_error=str(exc),
                increment_run_count=False,
                expected_lease_owner=expected_lease_owner,
            )
            return {
                "outcome": "failed",
                "task_run_id": None,
                "run_id": None,
                "thread_id": execution_thread_id,
                "error": str(exc),
            }
        active = await self._task_run_repo.get_active_run(task["id"])
        if active is not None:
            if trigger == "scheduled":
                await self._release_admission_lease(task, trigger=trigger)
            return self._existing_active_result(active, execution_thread_id, trigger=trigger)

        task_run_id = f"task-run-{uuid.uuid4().hex}"
        try:
            await self._task_run_repo.create(
                run_record_id=task_run_id,
                task_id=task["id"],
                thread_id=execution_thread_id,
                scheduled_for=now,
                trigger=trigger,
                status="queued",
                coordinate_with_task=True,
                expected_task_user_id=task.get("user_id"),
                expected_task_status=task.get("status") if trigger == "manual" else None,
                expected_task_updated_at=task.get("updated_at") if trigger == "manual" else None,
                expected_task_lease_owner=self._lease_owner if trigger == "scheduled" else None,
                release_task_lease_status="enabled" if trigger == "scheduled" else None,
            )
        except ActiveScheduledRunConflict:
            active = await self._task_run_repo.get_active_run(task["id"])
            if trigger == "scheduled":
                await self._release_admission_lease(task, trigger=trigger)
            if active is None:
                return self._active_run_conflict_result(execution_thread_id)
            return self._existing_active_result(active, execution_thread_id, trigger=trigger)
        except ScheduledTaskAdmissionRejected as exc:
            if exc.reason == "not_found":
                return {
                    "outcome": "not_found",
                    "task_run_id": None,
                    "run_id": None,
                    "thread_id": execution_thread_id,
                    "error": "scheduled task no longer exists",
                }
            return {
                "outcome": "conflict",
                "task_run_id": None,
                "run_id": None,
                "thread_id": execution_thread_id,
                "error": "scheduled task changed before trigger admission",
            }

        # Scheduled admission inserted the queue row and released its parent
        # lease in one transaction. Manual admission verified that this task
        # snapshot was still current under the same parent lock.
        #
        queued = {
            "id": task_run_id,
            "task_id": task["id"],
            "thread_id": execution_thread_id,
            "trigger": trigger,
        }
        return await self._attempt_queued_run(task, queued, now=now)

    async def _release_admission_lease(self, task: dict[str, Any], *, trigger: str) -> None:
        status = "enabled" if trigger == "scheduled" else (task.get("status") or "enabled")
        await self._task_repo.release_dispatch_lease(
            task["id"],
            expected_lease_owner=self._lease_owner if trigger == "scheduled" else None,
            status=status,
        )

    async def _attempt_queued_run(
        self,
        task: dict[str, Any],
        queued: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        """Turn one queued occurrence into a live run under its own trace scope.

        The poller is a non-HTTP entry point, so no ``TraceMiddleware`` has
        bound anything: each occurrence opens its own scope rather than
        sharing one id across a whole poll cycle. A manual trigger arrives
        inside a Gateway request and keeps that request's trace instead, so
        the launched run stays correlated with the call that asked for it.
        """
        with ensure_trace_context():
            return await self._launch_queued_occurrence(task, queued, now=now)

    async def _launch_queued_occurrence(
        self,
        task: dict[str, Any],
        queued: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        task_run_id = queued["id"]
        execution_thread_id = queued["thread_id"]
        trigger = queued["trigger"]
        # Gateway run admission, part one: do not claim the occurrence at all
        # while the process-wide in-flight run budget is full. Claiming costs a
        # durable state transition and a launch attempt whose only possible
        # outcome is a refusal, and a refusal has already consumed the durable
        # run row that this occurrence's deterministic idempotency key resolves
        # to. Waiting in ``queued`` -- the state this row is already in, and the
        # one the queue-timeout sweep already governs -- costs nothing and keeps
        # the occurrence intact.
        #
        # A slot can still be taken between this gate and the launch below. That
        # race is what ``_requeue_for_capacity`` exists for, and it cannot be
        # closed here: by the time the Gateway answers, the run row is already
        # written.
        if not self._capacity_allows_launch(task_run_id, now=now):
            return self._queued_result(task_run_id, execution_thread_id, error=_CAPACITY_WAIT_ERROR)
        claimed = await self._task_run_repo.claim_queued_run(
            task_run_id,
            lease_owner=self._lease_owner,
            now=now,
            lease_seconds=self._lease_seconds,
            global_max_concurrent_runs=self._max_concurrent_runs,
        )
        if claimed is None:
            return self._queued_result(task_run_id, execution_thread_id)

        # Track whether _launch_run has produced a live run. A bookkeeping
        # failure after launch must retain the non-terminal slot so a later
        # poll cannot start the same occurrence twice.
        launched_run_id: str | None = None
        launched_thread_id: str | None = None
        launch_succeeded = False
        try:
            # Hermes-style durable memory: read prior context first (any read
            # error fails closed), then journal the dispatch before a run
            # exists (any write error fails closed too). Both route to
            # fail_launching_run below with no run ever launched; once a run
            # is live, later journal failures degrade to disclosures instead.
            run_prompt = await self._prepare_job_memory_prompt(task)
            await self._journal_job_dispatch(task, task_run_id=task_run_id, trigger=trigger)
            result = await self._launch_run(
                thread_id=execution_thread_id,
                assistant_id=task.get("assistant_id"),
                prompt=run_prompt,
                owner_user_id=task.get("user_id"),
                metadata={
                    "scheduled_task_id": task["id"],
                    "scheduled_task_run_id": task_run_id,
                    "scheduled_trigger": trigger,
                },
            )
            launch_succeeded = True
            launched_run_id = result["run_id"]
            launched_thread_id = result["thread_id"]
            # Real capacity, real run: the occurrence is no longer waiting, and
            # its backoff must not follow it into the next occurrence.
            self._forget_capacity(task_run_id)
            next_at = next_run_at(
                task["schedule_type"],
                task["schedule_spec"],
                task["timezone"],
                now=now,
            )
            task_status = self._task_status_for_launch(task, trigger=trigger)
            await self._record_launched_run(
                task_run_id=task_run_id,
                task_id=task["id"],
                run_id=launched_run_id,
                started_at=now,
            )
            await self._task_repo.update_after_launch(
                task["id"],
                status=task_status,
                next_run_at=next_at,
                last_run_at=now,
                last_run_id=launched_run_id,
                last_thread_id=launched_thread_id,
                last_error=None,
                increment_run_count=True,
                task_run_id=task_run_id,
                # Same race as the run-row write above: a fast-failing run's
                # completion hook may have already finalized a `once` task.
                protect_terminal=True,
            )
            return {
                "outcome": "launched",
                "task_run_id": task_run_id,
                "run_id": launched_run_id,
                "thread_id": launched_thread_id,
                "error": None,
            }
        except Exception as exc:
            if not launch_succeeded and self._is_overlap_conflict(exc):
                await self._task_run_repo.requeue_claimed_run(
                    task_run_id,
                    lease_owner=self._lease_owner,
                    error=str(exc),
                )
                # This attempt never ran and will be retried from the queue:
                # journal that honestly instead of leaving a dangling
                # dispatch with no outcome.
                await self._journal_job_attempt_outcome(
                    task,
                    task_run_id=task_run_id,
                    status=_CAPACITY_REQUEUED_STATUS,
                    error=str(exc),
                )
                return self._queued_result(task_run_id, execution_thread_id, error=str(exc))

            if not launch_succeeded and is_run_capacity_refusal(exc):
                # The Gateway declined to admit a run because it is at its
                # in-flight budget. Nothing about the occurrence went wrong, so
                # it must not be recorded as though it did: no `failed` status,
                # no parent `last_error`, no `launch_failed` journal entry, no
                # consumed `run_count`. The occurrence is still owed its
                # attempt, so it goes back to `queued` -- the durable waiting
                # state the queue-timeout sweep already governs.
                return await self._requeue_for_capacity(task, task_run_id, exc=exc, trigger=trigger, now=now)

            next_at = next_run_at(
                task["schedule_type"],
                task["schedule_spec"],
                task["timezone"],
                now=now,
            )

            if launch_succeeded:
                # _launch_run succeeded, so a run is live even though
                # post-launch bookkeeping raised. Keep the task-run row
                # "running" so it keeps holding the task's single active slot
                # (preventing a duplicate launch on the next dispatch) and
                # persist the run_id on the parent task for recovery /
                # reconciliation / cancellation. These writes are best-effort:
                # if the DB is still down the row stays "queued" -- still
                # active, still holding the slot -- so we log and still report
                # the run as launched so callers know a run is in flight.
                task_status = self._task_status_for_launch(task, trigger=trigger)
                try:
                    await self._record_launched_run(
                        task_run_id=task_run_id,
                        task_id=task["id"],
                        run_id=launched_run_id,
                        started_at=now,
                    )
                except Exception:
                    logger.exception(
                        "Scheduled task-run %s: post-launch bookkeeping failed; run %s is still live (task %s)",
                        task_run_id,
                        launched_run_id,
                        task["id"],
                    )
                try:
                    await self._task_repo.update_after_launch(
                        task["id"],
                        status=task_status,
                        next_run_at=next_at,
                        last_run_at=now,
                        last_run_id=launched_run_id,
                        last_thread_id=launched_thread_id,
                        # The bookkeeping exception is an infrastructure-level
                        # transient, not a run-level failure: the run launched
                        # and is still in flight. Clear last_error like the
                        # success path so the task list does not show an error
                        # on a task whose run is actively running; the real
                        # terminal outcome is written by handle_run_completion.
                        # The transient itself is logged above.
                        last_error=None,
                        increment_run_count=True,
                        task_run_id=task_run_id,
                        protect_terminal=True,
                    )
                except Exception:
                    logger.exception(
                        "Scheduled task %s: post-launch update failed; run %s is still live",
                        task["id"],
                        launched_run_id,
                    )
                return {
                    "outcome": "launched",
                    "task_run_id": task_run_id,
                    "run_id": launched_run_id,
                    "thread_id": launched_thread_id,
                    "error": str(exc),
                }

            # _launch_run itself failed (or a step before it did): no live run
            # was created, so it is safe to release the active slot. Journal
            # the failed attempt first so the next run sees a real outcome
            # instead of a dangling dispatch with no outcome -- unless the
            # failure WAS the journal read (corrupt/unreadable store): then
            # no dispatch entry exists and appending would interleave with
            # bytes no reader can parse, leaving the corrupt store exactly
            # as found for manual inspection. The run-row error below already
            # discloses the failure either way.
            if not isinstance(exc, JobMemoryReadError):
                await self._journal_job_attempt_outcome(
                    task,
                    task_run_id=task_run_id,
                    status="launch_failed",
                    error=str(exc),
                )
            finalized = await self._task_run_repo.fail_launching_run(
                task_run_id,
                task_id=task["id"],
                lease_owner=self._lease_owner,
                error=str(exc),
                now=now,
            )
            if not finalized:
                logger.warning(
                    "Scheduled task-run %s lost its launch claim before failure bookkeeping; leaving recovery-owned state unchanged",
                    task_run_id,
                )
                return self._queued_result(task_run_id, execution_thread_id, error=str(exc))
            return {
                "outcome": "failed",
                "task_run_id": task_run_id,
                "run_id": None,
                "thread_id": execution_thread_id,
                "error": str(exc),
            }

    async def _record_launched_run(
        self,
        *,
        task_run_id: str,
        task_id: str,
        run_id: str,
        started_at: datetime,
    ) -> None:
        # Remember the observed launch time so handle_run_completion can
        # journal a measured duration; only tracked when the durable journal
        # exists (otherwise the map would grow without ever being read).
        if self._job_memory is not None:
            self._job_run_started_at[task_run_id] = started_at
        updated = await self._task_run_repo.update_status(
            task_run_id,
            status="running",
            run_id=run_id,
            started_at=started_at,
            protect_terminal=True,
            expected_lease_owner=self._lease_owner,
        )
        if updated:
            return
        reconciled = await self._task_run_repo.reconcile_launched_run(
            task_run_id,
            task_id=task_id,
            run_id=run_id,
            started_at=started_at,
        )
        if not reconciled:
            logger.error(
                "Scheduled task-run %s launched durable run %s but could not restore its occurrence association",
                task_run_id,
                run_id,
            )

    def _active_run_conflict_result(self, thread_id: str) -> dict[str, Any]:
        """Manual-trigger response when the task already has an active run.

        Nothing was scheduled to happen, so no run-history row is recorded; the
        router maps this to a 409.
        """
        return {
            "outcome": "conflict",
            "task_run_id": None,
            "run_id": None,
            "thread_id": thread_id,
            "error": _ACTIVE_RUN_CONFLICT_ERROR,
        }

    def _existing_active_result(
        self,
        active: dict[str, Any],
        thread_id: str,
        *,
        trigger: str,
    ) -> dict[str, Any]:
        if active["status"] == "queued":
            return self._queued_result(active["id"], active["thread_id"])
        return self._active_run_conflict_result(thread_id)

    @staticmethod
    def _queued_result(
        task_run_id: str,
        thread_id: str,
        *,
        error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "outcome": "queued",
            "task_run_id": task_run_id,
            "run_id": None,
            "thread_id": thread_id,
            "error": error,
        }

    async def _drain_queue(self, *, now: datetime) -> None:
        queued_rows = await self._task_run_repo.list_queued_runs(limit=max(16, self._max_concurrent_runs * 4))
        for queued in queued_rows:
            await self._task_repo.release_queued_admission_lease(queued["task_id"])
            task = await self._task_repo.get_internal(queued["task_id"])
            if task is None:
                await self._task_run_repo.update_status(
                    queued["id"],
                    status="interrupted",
                    error="scheduled task was deleted while queued",
                    finished_at=now,
                )
                continue
            # Pausing suppresses automatic occurrences, but a manual trigger is
            # an explicit request and has always been allowed to run without
            # resuming the schedule. A later pause still cancels an already
            # queued manual row atomically in pause_with_queue_cancellation().
            if task.get("status") == "paused" and queued["trigger"] != "manual":
                await self._task_run_repo.update_status(
                    queued["id"],
                    status="interrupted",
                    error="scheduled task was paused while queued",
                    finished_at=now,
                )
                continue
            await self._attempt_queued_run(task, queued, now=now)

    async def _expire_waiting_runs(self, *, now: datetime) -> None:
        await self._task_run_repo.expire_queued_runs(
            created_before=now - timedelta(seconds=self._queue_timeout_seconds),
            error=_QUEUE_TIMEOUT_ERROR,
            now=now,
        )

    async def handle_run_completion(self, record: RunRecord) -> None:
        metadata = record.metadata or {}
        task_id = metadata.get("scheduled_task_id")
        task_run_id = metadata.get("scheduled_task_run_id")
        user_id = record.user_id
        if not isinstance(task_id, str) or not isinstance(task_run_id, str) or not user_id:
            return

        terminal_status: Literal["success", "failed", "interrupted"] | None
        if record.status.value == "success":
            terminal_status = "success"
            error = None
        elif record.status.value == "interrupted":
            # Distinct from "failed": an interrupt (user cancel, same-thread
            # takeover) carries no error and is not an execution failure.
            terminal_status = "interrupted"
            error = record.error or "run was interrupted before completion"
        elif record.status.value in {"error", "timeout"}:
            terminal_status = "failed"
            error = record.error
        else:
            terminal_status = None
            error = record.error
        if terminal_status is None:
            return

        finished_at = datetime.now(UTC)
        await self._task_repo.complete_run(
            task_id,
            user_id=user_id,
            task_run_id=task_run_id,
            run_id=record.run_id,
            status=terminal_status,
            error=error,
            finished_at=finished_at,
        )
        await self._journal_job_terminal_outcome(
            record,
            task_id=task_id,
            task_run_id=task_run_id,
            terminal_status=terminal_status,
            error=error,
            finished_at=finished_at,
        )

    async def _prepare_job_memory_prompt(self, task: dict[str, Any]) -> str:
        """Build the launch prompt with prior job context injected.

        Fail closed: any journal read error propagates to the launch
        try/except, so the occurrence fails before a run exists instead of
        launching with silently missing prior context. Process-local write
        disclosures are appended verbatim so a failed journal write is never
        invisible to the run that follows it.
        """
        base_prompt = task["prompt"]
        if self._job_memory is None:
            return base_prompt
        block = await asyncio.to_thread(self._job_memory.prior_context_block, task["id"])
        sections = [block]
        if self._job_memory_notes:
            sections.append("Disclosures from scheduled job memory journal writes in this process:")
            sections.extend(f"- {note}" for note in self._job_memory_notes)
            self._job_memory_notes_presented = True
        return f"{base_prompt}\n\n<memory>\n" + "\n".join(sections) + "\n</memory>"

    async def _journal_job_dispatch(
        self,
        task: dict[str, Any],
        *,
        task_run_id: str,
        trigger: str,
    ) -> None:
        """Journal the dispatch *before* the run exists; failures fail closed."""
        if self._job_memory is None:
            return
        entry = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "event": "dispatched",
            "task_id": task["id"],
            "task_run_id": task_run_id,
            "trigger": trigger,
        }
        # JobMemoryWriteError propagates on purpose: the occurrence must not
        # launch when its dispatch cannot be durably recorded.
        await asyncio.to_thread(self._job_memory.append, task["id"], entry)

    async def _journal_job_attempt_outcome(
        self,
        task: dict[str, Any],
        *,
        task_run_id: str,
        status: str,
        error: str | None,
    ) -> None:
        """Journal a terminal observation for an attempt that never launched."""
        now = datetime.now(UTC)
        entry = {
            "recorded_at": now.isoformat(),
            "event": "outcome",
            "task_id": task["id"],
            "task_run_id": task_run_id,
            "run_id": None,
            "status": status,
            "error": error,
            "started_at": None,
            "finished_at": now.isoformat(),
            "duration_seconds": None,
            "duration_note": "run never launched; no duration exists",
        }
        await self._journal_job_outcome_entry(task["id"], entry, task_run_id=task_run_id)

    async def _journal_job_terminal_outcome(
        self,
        record: RunRecord,
        *,
        task_id: str,
        task_run_id: str,
        terminal_status: str,
        error: str | None,
        finished_at: datetime,
    ) -> None:
        """Journal a real terminal outcome after complete_run succeeded."""
        if self._job_memory is None:
            return
        started_at = self._job_run_started_at.pop(task_run_id, None)
        started_iso: str | None = None
        duration_seconds: float | None = None
        duration_note: str | None = None
        if started_at is not None:
            started_iso = started_at.isoformat()
            duration_seconds = round((finished_at - started_at).total_seconds(), 3)
        else:
            duration_note = "run start time not observed by this process (e.g., launched before a restart)"
        entry = {
            "recorded_at": finished_at.isoformat(),
            "event": "outcome",
            "task_id": task_id,
            "task_run_id": task_run_id,
            "run_id": record.run_id,
            "status": terminal_status,
            "error": error,
            "started_at": started_iso,
            "finished_at": finished_at.isoformat(),
            "duration_seconds": duration_seconds,
            "duration_note": duration_note,
        }
        await self._journal_job_outcome_entry(task_id, entry, task_run_id=task_run_id)

    async def _journal_job_outcome_entry(
        self,
        task_id: str,
        entry: dict[str, Any],
        *,
        task_run_id: str,
    ) -> None:
        """Append an outcome entry; a write failure is disclosed, never faked.

        By the time an outcome exists the run is already live or terminal, so
        failing closed here is impossible; the failure is logged at ERROR and
        carried as a process-local note into the next run's prompt. No
        substitute entry is written.
        """
        if self._job_memory is None:
            return
        try:
            await asyncio.to_thread(self._job_memory.append, task_id, entry)
        except JobMemoryWriteError as exc:
            self._note_job_memory_write_failure(task_run_id=task_run_id, error=exc)
            return
        self._clear_job_memory_notes_when_presented()

    def _note_job_memory_write_failure(self, *, task_run_id: str, error: Exception) -> None:
        logger.error(
            "Scheduled task-run %s: scheduled job memory journal write failed (entry not persisted): %s",
            task_run_id,
            error,
        )
        note = f"journal write failed for occurrence {task_run_id}: {error}"
        if len(self._job_memory_notes) < 4:
            self._job_memory_notes.append(note)
        elif not self._job_memory_notes_capped:
            self._job_memory_notes.append(
                "further journal write failure notes are suppressed in prompts (each failure is still logged at ERROR)"
            )
            self._job_memory_notes_capped = True
        # A note added after the last prompt has not been presented yet.
        self._job_memory_notes_presented = False

    def _clear_job_memory_notes_when_presented(self) -> None:
        # Notes are cleared only after they have actually appeared in a run
        # prompt AND a subsequent append succeeded, so a failure note can
        # never vanish without being surfaced at least once.
        if not self._job_memory_notes_presented:
            return
        self._job_memory_notes.clear()
        self._job_memory_notes_capped = False
        self._job_memory_notes_presented = False

    async def start(self) -> None:
        if self._task is not None:
            return
        restart_error = _RESTART_RECOVERY_ERROR
        if self._multi_instance:
            await self._reconcile_active_state(now=datetime.now(UTC))
            self._skip_next_lease_reconciliation = True
        else:
            # This destructive sweep is safe only while Gateway lifespan awaits
            # start(): no request or poll admission can create a run owned by
            # this process yet. Complete occurrence -> parent recovery before
            # returning; moving either pass into run_once() can interrupt live
            # work or race manual admission.
            try:
                stale = await self._task_run_repo.mark_stale_active_runs(error=restart_error)
                if stale:
                    logger.warning("Marked %d stale scheduled task run(s) as interrupted after restart", stale)
            except Exception:
                logger.exception("Failed to sweep stale scheduled task runs at startup")
                raise
            try:
                # The run rows above are only half the story: a launched `once`
                # task is parked in "running" until the (now dead) completion hook
                # would have finalized it.
                stuck = await self._task_repo.cancel_stuck_once_tasks(error=restart_error)
                if stuck:
                    logger.warning("Reconciled %d stuck once task(s) after restart", stuck)
            except Exception:
                logger.exception("Failed to reconcile stuck once tasks at startup")
                raise
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def _reconcile_active_state(self, *, now: datetime) -> None:
        error = _LEASE_RECOVERY_ERROR
        try:
            stale = await self._task_run_repo.reconcile_active_runs(
                error=error,
                now=now,
                lease_grace_seconds=self._run_lease_grace_seconds,
            )
            if stale:
                logger.warning("Marked %d stale scheduled task run(s) as interrupted after lease reconciliation", stale)
        except Exception:
            logger.exception("Failed to reconcile scheduled task runs with leases")
        try:
            stuck = await self._task_repo.reconcile_stuck_once_tasks(
                error=error,
                now=now,
                lease_grace_seconds=self._run_lease_grace_seconds,
            )
            if stuck:
                logger.warning("Reconciled %d stuck once task(s) after lease reconciliation", stuck)
        except Exception:
            logger.exception("Failed to reconcile once tasks with leases")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once(now=datetime.now(UTC))
            except Exception:
                # A transient DB error (e.g. SQLite "database is locked") must
                # not kill the poller task for the rest of the process life.
                logger.exception("Scheduled task poll failed; retrying next interval")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue
