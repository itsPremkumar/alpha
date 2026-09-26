from __future__ import annotations

import asyncio
import json
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from alpha.constants import (
    MCP_TASK_POLL_AFTER_MAX_SECONDS,
    MCP_TASK_REMOTE_ID_MAX_LENGTH,
    MCP_TASK_RESULT_ARTIFACT_MAX_BYTES,
)
from alpha.mcp.tasks import (
    McpTaskDriverRegistry,
    McpTaskProtocolError,
    TaskReference,
    TaskSnapshot,
    TaskStatus,
    TaskSubmitRequest,
)
from alpha.persistence.mcp_tasks import DuplicateMcpRemoteTaskError
from alpha.runtime.lane_scheduler import (
    RUN_ADMISSION_REJECTED_CODE,
    capacity_refusal_detail,
    capacity_refusal_retry_after_seconds,
    has_run_capacity,
    is_run_capacity_refusal,
)
from alpha.runtime.runs.manager import ConflictError
from alpha.runtime.runs.schemas import RunStatus
from app.mcp_tasks.errors import PermanentNotificationError

logger = logging.getLogger(__name__)

_MAX_PERSISTED_ERROR_CHARS = 4_000
_MAX_INPUT_REQUIRED_BYTES = 65_536
_MAX_NOTIFICATION_ATTEMPTS = 5
_UNTRACKED_TASK_COMPENSATION_WAIT_SECONDS = 5.0

#: Upper bound on how many (task, event version) pairs are tracked for capacity
#: backoff at once, so a Gateway that never frees a slot cannot grow the maps
#: without limit.
_CAPACITY_TRACKING_LIMIT = 512


def _record_capacity_metric(*, waiting: int, refusals: int, waited_seconds: float) -> None:
    """Publish "MCP task notifications are waiting on run capacity".

    Without it, starvation shows up only as a notification that never arrives --
    and a notification that never arrives is indistinguishable, to whoever is
    looking, from a notification that was never sent. The registry import is
    deferred so a capacity wait can never be what pays the poller's import cost.
    """
    try:
        from alpha.ops.metrics import get_metrics_registry

        registry = get_metrics_registry()
        registry.counter(
            "alpha_mcp_task_capacity_refusals",
            help="MCP task notification deliveries deferred because the Gateway had no free in-flight run slot.",
        ).inc()
        registry.gauge(
            "alpha_mcp_task_capacity_waiting_tasks",
            help="Distinct MCP tasks whose notification delivery is waiting on Gateway run capacity.",
        ).set(float(waiting))
        registry.gauge(
            "alpha_mcp_task_capacity_max_refusals",
            help="Longest consecutive Gateway capacity refusal count for a single MCP task notification.",
        ).set(float(refusals))
        registry.gauge(
            "alpha_mcp_task_capacity_backoff_seconds",
            help="Backoff applied before the next delivery attempt of the most recently deferred notification.",
        ).set(float(waited_seconds))
    except Exception:  # noqa: BLE001 - a metric must never fail a notification
        logger.debug("Failed to record MCP task run-capacity metrics", exc_info=True)


def _run_is_capacity_refused(run: Any) -> bool:
    """Did a launched notification run exist only to be refused admission?

    The Gateway creates a durable run row and then declines to attach a worker
    to it, so a run that carries the capacity code ran exactly zero times. It
    still has to be recognised: the same idempotency key resolves to that row on
    every retry, so a retry that trusted the returned ``run_id`` would report
    "dispatched" against a run that never executed and then count the resulting
    ``error`` status as a delivery failure.
    """
    if run is None:
        return False
    error = getattr(run, "error", None)
    return isinstance(error, str) and RUN_ADMISSION_REJECTED_CODE in error


def _bound_error(error: str | None) -> str | None:
    if error is None:
        return None
    return error[:_MAX_PERSISTED_ERROR_CHARS]


class McpTaskService:
    """Persist and poll long-running MCP tasks outside the Agent loop."""

    def __init__(
        self,
        *,
        repository,
        drivers: McpTaskDriverRegistry,
        poll_interval_seconds: int,
        lease_seconds: int,
        max_concurrent_polls: int,
        max_poll_backoff_seconds: int = 300,
        input_required_poll_interval_seconds: int = 60,
        tracking_degraded_after_errors: int = 3,
        max_result_bytes: int = 65_536,
        result_preview_max_chars: int = 2_000,
        launch_notification: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        get_run: Callable[..., Awaitable[Any | None]] | None = None,
    ) -> None:
        self._repository = repository
        self._drivers = drivers
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_concurrent_polls = max_concurrent_polls
        self._max_poll_backoff_seconds = max_poll_backoff_seconds
        self._input_required_poll_interval_seconds = input_required_poll_interval_seconds
        self._tracking_degraded_after_errors = tracking_degraded_after_errors
        self._max_result_bytes = max_result_bytes
        self._result_preview_max_chars = result_preview_max_chars
        self._launch_notification = launch_notification
        self._get_run = get_run
        self._lease_owner = f"{socket.gethostname()}:{uuid.uuid4().hex}"
        self._task: asyncio.Task[None] | None = None
        self._compensation_tasks: set[asyncio.Task[Any]] = set()
        self._stop = asyncio.Event()
        # Consecutive Gateway run-capacity waits per (task, event version). Two
        # distinct counts, because two different things are being counted:
        # ``_capacity_waits`` is every deferral (it drives the backoff and the
        # operator-visible gauge), while ``_capacity_spent_keys`` counts only the
        # deferrals that actually got as far as asking -- i.e. the ones that cost
        # a durable run row and therefore burned an idempotency key. Only those
        # may advance the delivery attempt number, or a task that was merely
        # waiting would launch under a key nobody has ever used.
        self._capacity_waits: dict[tuple[str, int], int] = {}
        self._capacity_spent_keys: dict[tuple[str, int], int] = {}
        self._capacity_retry_at: dict[tuple[str, int], datetime] = {}

    @property
    def drivers(self) -> McpTaskDriverRegistry:
        return self._drivers

    @property
    def tracking_degraded_after_errors(self) -> int:
        return self._tracking_degraded_after_errors

    async def submit(
        self,
        *,
        driver_name: str,
        request: TaskSubmitRequest,
        now: datetime | None = None,
    ) -> dict:
        """Submit through one driver and persist the remote handle before returning."""
        driver = self._drivers.get(driver_name)
        if driver is None:
            raise LookupError(f"No MCP task driver registered as {driver_name!r}")

        submitted_at = now or datetime.now(UTC)
        local_task_id = request.local_task_id or f"mcp-task-{uuid.uuid4().hex}"
        driver_request = replace(request, local_task_id=local_task_id)
        submission = await driver.submit(driver_request)
        driver_data = {**request.driver_data, **submission.driver_data}
        task_reference = TaskReference(
            local_task_id=local_task_id,
            user_id=request.user_id,
            thread_id=request.thread_id,
            server_name=request.server_name,
            remote_task_id=submission.remote_task_id,
            driver_data=driver_data,
        )
        try:
            if len(submission.remote_task_id) > MCP_TASK_REMOTE_ID_MAX_LENGTH:
                raise McpTaskProtocolError(f"MCP task remote_task_id must not exceed {MCP_TASK_REMOTE_ID_MAX_LENGTH} characters")
            snapshot = self._normalize_snapshot(submission.snapshot)
            next_poll_at = self._next_poll_at(snapshot, now=submitted_at)
            return await self._repository.create(
                task_id=local_task_id,
                user_id=request.user_id,
                thread_id=request.thread_id,
                run_id=request.run_id,
                tool_call_id=request.tool_call_id,
                server_name=request.server_name,
                driver_name=driver_name,
                remote_task_id=submission.remote_task_id,
                task_name=request.task_name,
                status=snapshot.status.value,
                result=snapshot.result,
                result_preview=snapshot.result_preview,
                result_truncated=snapshot.result_truncated,
                result_artifact=snapshot.result_artifact,
                error=snapshot.error,
                input_required=snapshot.input_required,
                next_poll_at=next_poll_at,
                driver_data=driver_data,
            )
        except DuplicateMcpRemoteTaskError:
            # This handle already has a durable owner. Cancelling it as
            # compensation would terminate the pre-existing tracked task.
            raise
        except asyncio.CancelledError:
            # Cancellation can race with a successful database commit. If it
            # did, the durable row will converge to cancelled on its next poll;
            # compensating is safer than leaving a live remote task untracked.
            await self._cancel_untracked_task(
                driver=driver,
                task_reference=task_reference,
                driver_name=driver_name,
                reason="caller cancellation during local persistence",
            )
            raise
        except Exception:
            await self._cancel_untracked_task(
                driver=driver,
                task_reference=task_reference,
                driver_name=driver_name,
                reason="local submission finalization failure",
            )
            raise

    async def _cancel_untracked_task(
        self,
        *,
        driver,
        task_reference: TaskReference,
        driver_name: str,
        reason: str,
    ) -> None:
        compensation = asyncio.create_task(
            driver.cancel(task_reference),
            name=f"mcp-submit-compensation-{task_reference.local_task_id}",
        )
        self._compensation_tasks.add(compensation)

        def finalize(task: asyncio.Task[Any]) -> None:
            self._compensation_tasks.discard(task)
            try:
                error = task.exception()
            except asyncio.CancelledError as exc:
                error = exc
            if error is None:
                return
            logger.error(
                "Failed to cancel untracked MCP task after %s (task_id=%s, driver=%s, remote_task_id=%s)",
                reason,
                task_reference.local_task_id,
                driver_name,
                task_reference.remote_task_id,
                exc_info=(type(error), error, error.__traceback__),
            )

        compensation.add_done_callback(finalize)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _UNTRACKED_TASK_COMPENSATION_WAIT_SECONDS
        while not compensation.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "Timed out after %.1f seconds waiting for untracked MCP task compensation after %s; cancellation continues in the background (task_id=%s, driver=%s, remote_task_id=%s)",
                    _UNTRACKED_TASK_COMPENSATION_WAIT_SECONDS,
                    reason,
                    task_reference.local_task_id,
                    driver_name,
                    task_reference.remote_task_id,
                )
                return
            try:
                await asyncio.wait({compensation}, timeout=remaining)
            except asyncio.CancelledError:
                # Repeated caller cancellation does not propagate through
                # asyncio.wait() to the compensation task. Keep waiting only
                # until the original deadline.
                continue

    async def run_once(self, *, now: datetime) -> None:
        await self._run_cancellations(now=now)

        claimed = await self._repository.claim_due_tasks(
            now=now,
            lease_owner=self._lease_owner,
            lease_seconds=self._lease_seconds,
            limit=self._max_concurrent_polls,
        )
        if claimed:
            results = await asyncio.gather(
                *(self._poll_one(task, now=now) for task in claimed),
                return_exceptions=True,
            )
            for record, result in zip(claimed, results, strict=True):
                if isinstance(result, BaseException):
                    logger.error(
                        "Unexpected MCP task poll failure (task_id=%s); the lease will expire for recovery",
                        record.get("id"),
                        exc_info=(type(result), result, result.__traceback__),
                    )

        await self._run_notifications(now=datetime.now(UTC))

    async def list_tasks(
        self,
        *,
        thread_id: str,
        user_id: str,
        limit: int = 50,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._repository.list_by_thread(
            thread_id,
            user_id=user_id,
            limit=limit,
            active_only=active_only,
        )

    async def cancel_task(
        self,
        *,
        task_id: str,
        thread_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        return await self._repository.request_cancel(
            task_id,
            user_id=user_id,
            thread_id=thread_id,
            requested_at=now or datetime.now(UTC),
        )

    async def cancel_matching_task(
        self,
        *,
        thread_id: str,
        user_id: str,
        task: str | None = None,
    ) -> dict[str, Any]:
        active = await self.list_tasks(thread_id=thread_id, user_id=user_id, active_only=True)
        if task:
            normalized = task.casefold().strip()
            matches = [item for item in active if item["id"] == task or str(item.get("task_name") or "").casefold() == normalized]
        else:
            matches = active
        if not matches:
            raise LookupError("No active background task matches this request")
        if len(matches) > 1:
            names = ", ".join(str(item.get("task_name") or item["id"]) for item in matches[:5])
            raise ValueError(f"More than one active background task matches; specify one task name: {names}")
        result = await self.cancel_task(
            task_id=matches[0]["id"],
            thread_id=thread_id,
            user_id=user_id,
        )
        if result is None:
            raise LookupError("The selected background task no longer exists")
        return result

    async def _run_cancellations(self, *, now: datetime) -> None:
        claim = getattr(self._repository, "claim_cancel_requests", None)
        if claim is None:
            return
        records = await claim(
            now=now,
            lease_owner=self._lease_owner,
            lease_seconds=self._lease_seconds,
            limit=self._max_concurrent_polls,
        )
        if records:
            results = await asyncio.gather(
                *(self._cancel_one(record) for record in records),
                return_exceptions=True,
            )
            for record, result in zip(records, results, strict=True):
                if isinstance(result, BaseException):
                    logger.error(
                        "Unexpected MCP task cancellation failure (task_id=%s); the lease will expire for recovery",
                        record.get("id"),
                        exc_info=(type(result), result, result.__traceback__),
                    )

    async def _cancel_one(self, record: dict[str, Any]) -> None:
        driver_name = str(record.get("driver_name") or "")
        driver = self._drivers.get(driver_name)
        try:
            if driver is None:
                raise LookupError(f"No MCP task driver registered as {driver_name!r}")
            snapshot = self._normalize_snapshot(await driver.cancel(TaskReference.from_record(record)))
            if snapshot.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                raise McpTaskProtocolError("MCP task cancellation must return a terminal status")
            await self._repository.apply_cancel_snapshot(
                record["id"],
                lease_owner=self._lease_owner,
                status=snapshot.status.value,
                result=snapshot.result,
                result_preview=snapshot.result_preview,
                result_truncated=snapshot.result_truncated,
                result_artifact=snapshot.result_artifact,
                error=snapshot.error,
                input_required=snapshot.input_required,
                completed_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001 - remote cancellation is retryable
            attempts = max(0, int(record.get("cancel_attempt_count") or 1) - 1)
            retry_seconds = min(self._poll_interval_seconds * (2 ** min(attempts, 16)), self._max_poll_backoff_seconds)
            failed_at = datetime.now(UTC)
            await self._repository.release_cancel_claim(
                record["id"],
                lease_owner=self._lease_owner,
                next_cancel_at=failed_at + timedelta(seconds=retry_seconds),
                error=_bound_error(str(exc) or type(exc).__name__),
            )

    async def _run_notifications(self, *, now: datetime) -> None:
        if self._launch_notification is None or self._get_run is None:
            return
        records = await self._repository.claim_notification_work(
            now=now,
            lease_owner=self._lease_owner,
            lease_seconds=self._lease_seconds,
            limit=self._max_concurrent_polls,
            tracking_degraded_after_errors=self._tracking_degraded_after_errors,
        )
        if records:
            results = await asyncio.gather(
                *(self._notify_one(record, now=now) for record in records),
                return_exceptions=True,
            )
            for record, result in zip(records, results, strict=True):
                if not isinstance(result, BaseException):
                    continue
                error = _bound_error(str(result) or type(result).__name__) or type(result).__name__
                logger.error(
                    "Unexpected MCP task notification failure (task_id=%s)",
                    record.get("id"),
                    exc_info=(type(result), result, result.__traceback__),
                )
                try:
                    await self._repository.release_notification_lease(
                        record["id"],
                        lease_owner=self._lease_owner,
                        next_notification_at=now + timedelta(seconds=self._notification_retry_seconds(record)),
                        error=error,
                        count_failure=True,
                    )
                except Exception:  # noqa: BLE001 - retain the original task-scoped failure
                    logger.exception(
                        "Failed to release MCP task notification lease (task_id=%s)",
                        record.get("id"),
                    )

    async def _notify_one(self, record: dict[str, Any], *, now: datetime) -> None:
        task_id = record["id"]
        dispatch_version = int(record.get("dispatch_version") or 0)
        capacity_key = (task_id, dispatch_version)
        # A refused admission is a statement about the Gateway's moment, not
        # about this task. Letting it reach the retry-budget check below would
        # make a busy Gateway consume the five attempts that exist to bound
        # genuinely broken deliveries, and dead-letter the notification without
        # ever having tried. So the budget question is skipped entirely while
        # capacity is short, and the refusal is re-armed below as a bounded wait.
        if self._capacity_blocked(capacity_key, now=now):
            await self._defer_for_capacity(record, capacity_key, now=now)
            return
        notification_attempts = max(0, int(record.get("notification_attempt_count") or 0))
        if notification_attempts >= _MAX_NOTIFICATION_ATTEMPTS:
            previous_error = record.get("notification_error") or "delivery failed"
            await self._repository.dead_letter_notification(
                task_id,
                lease_owner=self._lease_owner,
                dispatch_version=dispatch_version,
                error=_bound_error(f"Notification delivery stopped after {notification_attempts} failed attempts: {previous_error}"),
                count_failure=False,
                now=now,
            )
            return

        if record.get("notification_status") == "dispatched":
            run = await self._get_run(record.get("notification_run_id"), user_id=record["user_id"])
            status = getattr(run, "status", None)
            if run is None:
                run_id = record.get("notification_run_id")
                await self._repository.finish_notification_run(
                    task_id,
                    lease_owner=self._lease_owner,
                    dispatch_version=dispatch_version,
                    delivered=False,
                    next_notification_at=now + timedelta(seconds=self._notification_retry_seconds(record)),
                    error=_bound_error(f"Notification run {run_id!r} was not found"),
                    now=now,
                )
            elif status == RunStatus.success:
                await self._repository.finish_notification_run(
                    task_id,
                    lease_owner=self._lease_owner,
                    dispatch_version=dispatch_version,
                    delivered=True,
                    next_notification_at=None,
                    error=None,
                    now=now,
                )
            elif status in {RunStatus.error, RunStatus.timeout, RunStatus.interrupted}:
                await self._repository.finish_notification_run(
                    task_id,
                    lease_owner=self._lease_owner,
                    dispatch_version=dispatch_version,
                    delivered=False,
                    next_notification_at=now + timedelta(seconds=self._notification_retry_seconds(record)),
                    error=_bound_error(getattr(run, "error", None) or f"Notification run ended with {status}"),
                    now=now,
                )
            else:
                await self._repository.defer_dispatched_notification(
                    task_id,
                    lease_owner=self._lease_owner,
                    dispatch_version=dispatch_version,
                    next_notification_at=now + timedelta(seconds=self._poll_interval_seconds),
                    now=now,
                )
            return

        source_run = await self._get_run(record.get("run_id"), user_id=record["user_id"]) if record.get("run_id") else None
        try:
            result = await self._launch_notification(
                thread_id=record["thread_id"],
                assistant_id=getattr(source_run, "assistant_id", None),
                owner_user_id=record["user_id"],
                task_id=task_id,
                dispatch_version=dispatch_version,
                dispatch_attempt=self._dispatch_attempt_for(record, capacity_key),
                event=dict(record.get("dispatch_event") or {}),
            )
        except PermanentNotificationError as exc:
            await self._repository.dead_letter_notification(
                task_id,
                lease_owner=self._lease_owner,
                dispatch_version=dispatch_version,
                error=_bound_error(str(exc) or type(exc).__name__),
                count_failure=True,
                now=now,
            )
            return
        except ConflictError as exc:
            await self._repository.release_notification_claim(
                task_id,
                lease_owner=self._lease_owner,
                next_notification_at=now + timedelta(seconds=self._poll_interval_seconds),
                error=_bound_error(str(exc)),
                replace_with_latest=True,
            )
            return
        except Exception as exc:  # noqa: BLE001 - retry the same idempotency key
            if is_run_capacity_refusal(exc):
                await self._defer_for_capacity(record, capacity_key, now=now, exc=exc)
                return
            await self._repository.release_notification_claim(
                task_id,
                lease_owner=self._lease_owner,
                next_notification_at=now + timedelta(seconds=self._notification_retry_seconds(record)),
                error=_bound_error(str(exc) or type(exc).__name__),
                replace_with_latest=True,
                count_failure=True,
            )
            return
        # The launch returned a run id, which is not the same as "a run is
        # executing". An idempotency key the Gateway already spent on a refusal
        # resolves straight back to the refused run, so verify before recording
        # the notification as dispatched -- otherwise the durable row claims a
        # delivery that never happened and the next poll books it as a failure.
        launched = await self._get_run(result.get("run_id"), user_id=record["user_id"])
        if _run_is_capacity_refused(launched):
            await self._defer_for_capacity(record, capacity_key, now=now, run_id=result.get("run_id"))
            return
        self._clear_capacity(capacity_key)
        await self._repository.mark_notification_dispatched(
            task_id,
            lease_owner=self._lease_owner,
            dispatch_version=dispatch_version,
            run_id=result["run_id"],
            now=now,
        )

    # -- Gateway run-capacity handling ------------------------------------
    #
    # A notification delivery is a durable obligation, so being told "no free
    # run slot right now" has to stay a *wait*. The three properties below are
    # what make it one:
    #
    #   1. it never counts as a delivery attempt, so the five-attempt
    #      dead-letter budget stays reserved for deliveries that really failed;
    #   2. the next attempt gets a dispatch attempt number the refused one did
    #      not consume, because the refused attempt already spent the durable
    #      idempotency key it was launched under;
    #   3. it backs off, so a saturated Gateway is not polled into a 429 storm.

    def _capacity_blocked(self, capacity_key: tuple[str, int], *, now: datetime) -> bool:
        """Is this task waiting out a capacity backoff, or a full run budget?"""
        retry_at = self._capacity_retry_at.get(capacity_key)
        if retry_at is not None and now < retry_at:
            return True
        return not has_run_capacity()

    def _dispatch_attempt_for(self, record: dict[str, Any], capacity_key: tuple[str, int]) -> int:
        """The attempt number to launch this delivery under.

        Normally the persisted one. After a refusal that actually reached the
        Gateway it is offset by the number of keys those refusals spent, because
        the Gateway binds ``mcp-task:{task}:{version}:{attempt}`` to the run row
        it creates *before* it discovers it has no slot -- and that row is then
        failed with no worker ever attached. Reusing the key would resolve to the
        dead row forever, so the retry would report success while delivering
        nothing. Offsetting is what turns a retry into a real attempt.

        A wait that never got as far as asking (the backoff window, or a budget
        that was already full) spent no key and so moves nothing: otherwise a
        task that sat out a long saturation would come back and launch under a
        run id nothing has ever referenced.
        """
        persisted = int(record.get("dispatch_attempt") or 0)
        return persisted + self._capacity_spent_keys.get(capacity_key, 0)

    async def _defer_for_capacity(
        self,
        record: dict[str, Any],
        capacity_key: tuple[str, int],
        *,
        now: datetime,
        exc: Exception | None = None,
        run_id: str | None = None,
    ) -> None:
        """Record "waiting for a run slot" and come back later.

        ``count_failure=False`` is the whole point: the delivery budget bounds
        deliveries that failed, and this one did not fail, it did not start.

        ``exc``/``run_id`` distinguish the two ways of arriving here. Reaching
        the Gateway means the run row exists and its idempotency key is spent, so
        the next attempt must use a fresh one; declining to ask means no durable
        state was touched at all and only the clock moved.
        """
        task_id = record["id"]
        waits = self._capacity_waits.get(capacity_key, 0) + 1
        self._capacity_waits[capacity_key] = waits
        if exc is not None or run_id is not None:
            self._capacity_spent_keys[capacity_key] = self._capacity_spent_keys.get(capacity_key, 0) + 1
        self._prune_capacity_tracking()
        hint = (
            capacity_refusal_retry_after_seconds(exc, default=float(self._poll_interval_seconds))
            if exc is not None
            else float(self._poll_interval_seconds)
        )
        backoff = min(
            max(float(self._poll_interval_seconds) * (2 ** min(waits - 1, 16)), hint),
            float(self._max_poll_backoff_seconds),
        )
        self._capacity_retry_at[capacity_key] = now + timedelta(seconds=backoff)
        reason = capacity_refusal_detail(exc) if exc is not None else f"{RUN_ADMISSION_REJECTED_CODE}: waiting for a free Gateway run slot"
        if run_id is not None:
            reason = f"{reason} (idempotency key already spent on run {run_id})"
        logger.warning(
            "MCP task notification for task_id=%s is waiting on Gateway run capacity (%s); retrying in %.1fs without spending a delivery attempt",
            task_id,
            reason,
            backoff,
        )
        _record_capacity_metric(waiting=len(self._capacity_waits), refusals=waits, waited_seconds=backoff)
        await self._repository.release_notification_claim(
            task_id,
            lease_owner=self._lease_owner,
            next_notification_at=now + timedelta(seconds=backoff),
            error=_bound_error(reason),
            # "pending" makes the next claim rebuild the snapshot from the
            # current event, so a capacity wait can never pin delivery to a
            # stale payload while it waits.
            replace_with_latest=True,
            count_failure=False,
        )

    def _clear_capacity(self, capacity_key: tuple[str, int]) -> None:
        if self._capacity_waits.pop(capacity_key, None) is not None:
            self._capacity_spent_keys.pop(capacity_key, None)
            self._capacity_retry_at.pop(capacity_key, None)
            _record_capacity_metric(waiting=len(self._capacity_waits), refusals=0, waited_seconds=0.0)

    def _prune_capacity_tracking(self) -> None:
        """Bound the capacity bookkeeping so a stuck Gateway cannot grow it.

        Drops the entries whose backoff expires soonest: they are the ones about
        to be looked at again, so the only thing lost by forgetting them is one
        extra poll and, at worst, one attempt number that re-uses a key -- which
        the post-launch refused-run check still catches.
        """
        overflow = len(self._capacity_waits) - _CAPACITY_TRACKING_LIMIT
        if overflow <= 0:
            return
        soonest = sorted(self._capacity_retry_at.items(), key=lambda item: item[1])[:overflow]
        for capacity_key, _retry_at in soonest:
            self._capacity_waits.pop(capacity_key, None)
            self._capacity_spent_keys.pop(capacity_key, None)
            self._capacity_retry_at.pop(capacity_key, None)

    def _notification_retry_seconds(self, record: dict[str, Any]) -> int:
        failures = max(0, int(record.get("notification_attempt_count") or 0))
        return min(
            self._poll_interval_seconds * (2 ** min(failures, 16)),
            self._max_poll_backoff_seconds,
        )

    async def _poll_one(self, record: dict, *, now: datetime) -> None:
        driver_name = str(record.get("driver_name") or "")
        driver = self._drivers.get(driver_name)
        if driver is None:
            await self._release_after_error(
                record,
                now=now,
                error=f"No MCP task driver registered as {driver_name!r}",
            )
            return

        try:
            snapshot = self._normalize_snapshot(await driver.get_status(TaskReference.from_record(record)))
        except McpTaskProtocolError as exc:
            logger.error(
                "MCP task status contract failed permanently (task_id=%s, driver=%s): %s",
                record.get("id"),
                driver_name,
                exc,
            )
            await self._apply_snapshot(
                record,
                TaskSnapshot(status=TaskStatus.FAILED, error=_bound_error(str(exc))),
                polled_at=datetime.now(UTC),
            )
            return
        except Exception as exc:  # noqa: BLE001 - driver boundary; retry on the next poll
            polled_at = datetime.now(UTC)
            logger.warning(
                "MCP task status poll failed (task_id=%s, driver=%s); retrying",
                record.get("id"),
                driver_name,
                exc_info=True,
            )
            await self._release_after_error(record, now=polled_at, error=str(exc) or type(exc).__name__, count_current_failure=True)
            return

        polled_at = datetime.now(UTC)
        await self._apply_snapshot(record, snapshot, polled_at=polled_at)

    async def _apply_snapshot(
        self,
        record: dict,
        snapshot: TaskSnapshot,
        *,
        polled_at: datetime,
    ) -> None:
        applied = await self._repository.apply_snapshot(
            record["id"],
            lease_owner=self._lease_owner,
            status=snapshot.status.value,
            result=snapshot.result,
            result_preview=snapshot.result_preview,
            result_truncated=snapshot.result_truncated,
            result_artifact=snapshot.result_artifact,
            error=snapshot.error,
            input_required=snapshot.input_required,
            next_poll_at=self._next_poll_at(snapshot, now=polled_at),
            polled_at=polled_at,
        )
        if not applied:
            logger.info(
                "Discarded MCP task poll result after lease ownership changed or expired (task_id=%s)",
                record.get("id"),
            )

    def _next_poll_at(self, snapshot: TaskSnapshot, *, now: datetime) -> datetime | None:
        if not snapshot.is_pollable:
            return None
        interval = snapshot.poll_after_seconds or self._poll_interval_seconds
        if snapshot.status == TaskStatus.INPUT_REQUIRED:
            interval = max(interval, self._input_required_poll_interval_seconds)
        interval = min(interval, MCP_TASK_POLL_AFTER_MAX_SECONDS)
        return now + timedelta(seconds=interval)

    async def _release_after_error(self, record: dict, *, now: datetime, error: str, count_current_failure: bool = False) -> None:
        consecutive_errors = max(0, int(record.get("consecutive_poll_error_count") or 0))
        if count_current_failure:
            # The repository increments the persisted counter when this release
            # commits, so schedule against the post-increment value: a failed
            # poll attempt backs off immediately instead of retrying once at
            # the base interval before the growth starts. A missing driver
            # never attempted a poll, so it keeps the base interval.
            consecutive_errors += 1
        retry_seconds = min(
            self._poll_interval_seconds * (2 ** min(consecutive_errors, 16)),
            self._max_poll_backoff_seconds,
        )
        bounded_error = _bound_error(error)
        assert bounded_error is not None
        await self._repository.release_claim(
            record["id"],
            lease_owner=self._lease_owner,
            next_poll_at=now + timedelta(seconds=retry_seconds),
            error=bounded_error,
            tracking_degraded_after_errors=self._tracking_degraded_after_errors,
        )

    def _normalize_snapshot(self, snapshot: TaskSnapshot) -> TaskSnapshot:
        """Bound remote payloads without ever storing truncated JSON."""
        snapshot = replace(snapshot, error=_bound_error(snapshot.error))
        if snapshot.result_artifact is not None:
            encoded_artifact = self._encode_json_payload(
                snapshot.result_artifact,
                field_name="result_artifact",
            )
            if len(encoded_artifact) > MCP_TASK_RESULT_ARTIFACT_MAX_BYTES:
                raise McpTaskProtocolError(f"MCP task result_artifact payload exceeds the {MCP_TASK_RESULT_ARTIFACT_MAX_BYTES}-byte limit")
        if snapshot.input_required is not None:
            encoded_input = self._encode_json_payload(
                snapshot.input_required,
                field_name="input_required",
            )
            if len(encoded_input) > _MAX_INPUT_REQUIRED_BYTES:
                raise McpTaskProtocolError(f"MCP task input_required payload exceeds the {_MAX_INPUT_REQUIRED_BYTES}-byte limit")
        if snapshot.result is None:
            return snapshot
        encoded = self._encode_json_payload(snapshot.result, field_name="result")
        if len(encoded) <= self._max_result_bytes:
            return snapshot

        if isinstance(snapshot.result, str):
            preview_source = snapshot.result
        else:
            preview_source = encoded.decode("utf-8", errors="replace")
        return replace(
            snapshot,
            result=None,
            result_preview=preview_source[: self._result_preview_max_chars],
            result_truncated=True,
        )

    @staticmethod
    def _encode_json_payload(value, *, field_name: str) -> bytes:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise McpTaskProtocolError(f"MCP task {field_name} is not valid JSON: {exc}") from exc

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="alpha-mcp-task-poller")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                # The first pass runs immediately. Expired leases therefore
                # recover at startup without a separate destructive sweep.
                await self.run_once(now=datetime.now(UTC))
            except Exception:
                logger.exception("MCP task poll failed; retrying next interval")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue
