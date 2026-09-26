from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alpha.persistence.subagent_batches.model import SubagentBatchItemRow, SubagentBatchRow
from alpha.subagents.acceptance_checks import AcceptanceVerdict, validate_acceptance_verdict
from alpha.subagents.batch_runtime import BatchItemInput
from alpha.subagents.report_contract import normalize_acceptance_criteria
from alpha.utils.time import coerce_iso

logger = logging.getLogger(__name__)

BATCH_ACTIVE_STATUSES = ("queued", "running", "paused")
BATCH_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
ITEM_ACTIVE_STATUSES = ("queued", "leased", "running")
ITEM_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
_BATCH_PUBLIC_FIELDS = (
    "id",
    "thread_id",
    "title",
    "subagent_type",
    "status",
    "total_items",
    "max_live_items",
    "max_running_items",
    "max_attempts",
    "created_at",
    "updated_at",
    "completed_at",
)
_BATCH_TIMESTAMP_FIELDS = ("created_at", "updated_at", "completed_at")
_ITEM_PUBLIC_FIELDS = (
    "id",
    "batch_id",
    "item_key",
    "position",
    "status",
    "attempt",
    "model_name",
    "result_preview",
    "result_truncated",
    "error",
    "stop_reason",
    "token_usage",
    "acceptance_criteria",
    "started_at",
    "completed_at",
    "created_at",
    "updated_at",
)
_ITEM_TIMESTAMP_FIELDS = ("started_at", "completed_at", "created_at", "updated_at")


def classify_item_failure(error: str | None) -> str:
    """Typed reason code for a batch item's free-text ``error``.

    The item row keeps its free-text error for operators; the machine-readable
    decision is derived from the SHARED taxonomy in
    :mod:`alpha.bots.failure_reasons`, so a batch failure, a swarm failure and
    a bot failure are all classified by one rule set instead of each subsystem
    substring-matching its own favourites.
    """
    from alpha.bots.failure_reasons import classify_work_failure

    return classify_work_failure(error)


def _record_item_failure(
    *,
    item_id: str,
    batch_id: str,
    subagent_type: str,
    attempt: int,
    max_attempts: int,
    error: str | None,
    reason: str,
    outcome: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Ledger + escalation for one failed item. Runs OFF the event loop.

    Called through ``asyncio.to_thread`` because the ledger and the escalation
    store are durable file writes; a batch of 1000 items must not block the
    loop while they land. Failures inside are logged, never raised: the item's
    own terminal state is already committed.
    """
    from alpha.runtime.escalation import DOMAIN_BATCH, record_failure

    record_failure(
        DOMAIN_BATCH,
        item_id,
        f"batch:{batch_id}:{subagent_type}" if subagent_type else f"batch:{batch_id}",
        attempt=attempt,
        max_attempts=max_attempts,
        error=error,
        reason=reason,
        detail=f"batch item {item_id} ({outcome}) after attempt {attempt}/{max_attempts}",
        details={"batch_id": batch_id, "subagent_type": subagent_type, "outcome": outcome, **dict(extra or {})},
    )


def _acknowledge_item_escalations(item_id: str) -> None:
    """Resolve open escalations for a manually retried item. Off the loop."""
    try:
        from alpha.bots.failure_reasons import ATTEMPTS_EXHAUSTED
        from alpha.runtime.escalation import DOMAIN_BATCH, HUMAN, get_escalation_store, get_handoff_ledger

        store = get_escalation_store()
        for record in store.list(status="open", domain=DOMAIN_BATCH, task_id=item_id):
            store.resolve(record.escalation_id, by=HUMAN, note="operator retried this item; budget reset")
        get_handoff_ledger().append(
            kind="handoff",
            domain=DOMAIN_BATCH,
            task_id=item_id,
            from_ref=HUMAN,
            to_ref="operator_retry",
            reason=ATTEMPTS_EXHAUSTED,
            details={"note": "attempt budget reset by an operator", "action": "operator_retry"},
        )
    except Exception:  # noqa: BLE001 - the retry itself already committed
        logger.warning("Failed to close escalations for retried batch item %s", item_id, exc_info=True)


class SubagentBatchRepository:
    """Durable batch/item state with lease-based multi-worker claiming."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _batch_dict(row: SubagentBatchRow) -> dict[str, Any]:
        """Return the stable owner-facing projection, never execution context."""
        data = {key: getattr(row, key) for key in _BATCH_PUBLIC_FIELDS}
        for key in _BATCH_TIMESTAMP_FIELDS:
            if data.get(key) is not None:
                data[key] = coerce_iso(data[key])
        return data

    @staticmethod
    def _execution_batch_dict(row: SubagentBatchRow) -> dict[str, Any]:
        """Return worker-only fields required to reconstruct an execution."""
        return {
            "id": row.id,
            "user_id": row.user_id,
            "thread_id": row.thread_id,
            "run_id": row.run_id,
            "execution_spec": row.execution_spec,
        }

    @staticmethod
    def _item_dict(row: SubagentBatchItemRow, *, include_result: bool = False) -> dict[str, Any]:
        data = {key: getattr(row, key) for key in _ITEM_PUBLIC_FIELDS}
        data["acceptance_verdict"] = validate_acceptance_verdict(row.acceptance_verdict)
        if include_result:
            data["result"] = row.result
        for key in _ITEM_TIMESTAMP_FIELDS:
            if data.get(key) is not None:
                data[key] = coerce_iso(data[key])
        return data

    async def create_batch(
        self,
        *,
        batch_id: str,
        user_id: str,
        thread_id: str,
        run_id: str | None,
        tool_call_id: str | None,
        submission_key: str,
        title: str,
        subagent_type: str,
        items: list[BatchItemInput],
        max_live_items: int,
        max_running_items: int,
        max_attempts: int,
        execution_spec: dict[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        batch = SubagentBatchRow(
            id=batch_id,
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            submission_key=submission_key,
            title=title,
            subagent_type=subagent_type,
            status="queued",
            total_items=len(items),
            max_live_items=max_live_items,
            max_running_items=max_running_items,
            max_attempts=max_attempts,
            execution_spec=execution_spec,
            created_at=now,
            updated_at=now,
        )
        rows = [
            SubagentBatchItemRow(
                id=f"batch-item-{uuid.uuid4().hex}",
                batch_id=batch_id,
                item_key=item["key"],
                position=position,
                prompt=item["prompt"],
                acceptance_criteria=normalize_acceptance_criteria(item.get("acceptance_criteria")) or None,
                status="pending",
                attempt=0,
                result_truncated=False,
                created_at=now,
                updated_at=now,
            )
            for position, item in enumerate(items)
        ]
        async with self._sf() as session:
            try:
                session.add(batch)
                # The models intentionally do not declare an ORM relationship;
                # flush the parent explicitly so SQLite's immediate FK check
                # never observes item inserts before their batch row. Keep the
                # flush inside the idempotency handler: a duplicate submission
                # key can fail here before commit.
                await session.flush()
                session.add_all(rows)
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (
                    await session.execute(
                        select(SubagentBatchRow).where(
                            SubagentBatchRow.user_id == user_id,
                            SubagentBatchRow.submission_key == submission_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return await self._with_counts(session, existing)
                raise
            return await self._with_counts(session, batch)

    async def _counts(self, session: AsyncSession, batch_id: str) -> Counter[str]:
        rows = await session.execute(select(SubagentBatchItemRow.status, func.count()).where(SubagentBatchItemRow.batch_id == batch_id).group_by(SubagentBatchItemRow.status))
        return Counter({status: int(count) for status, count in rows})

    async def _with_counts(self, session: AsyncSession, batch: SubagentBatchRow) -> dict[str, Any]:
        counts = await self._counts(session, batch.id)
        data = self._batch_dict(batch)
        data["counts"] = {status: counts.get(status, 0) for status in ("pending", "queued", "leased", "running", "succeeded", "failed", "cancelled")}
        return data

    async def get_batch(self, batch_id: str, *, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            batch = await session.get(SubagentBatchRow, batch_id)
            if batch is None or batch.user_id != user_id:
                return None
            return await self._with_counts(session, batch)

    async def list_by_thread(self, thread_id: str, *, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        async with self._sf() as session:
            rows = list(
                (
                    await session.execute(
                        select(SubagentBatchRow)
                        .where(
                            SubagentBatchRow.thread_id == thread_id,
                            SubagentBatchRow.user_id == user_id,
                        )
                        .order_by(SubagentBatchRow.created_at.desc(), SubagentBatchRow.id.desc())
                        .limit(limit)
                    )
                ).scalars()
            )
            return [await self._with_counts(session, row) for row in rows]

    async def list_items(
        self,
        batch_id: str,
        *,
        user_id: str,
        offset: int = 0,
        limit: int = 100,
        status: str | None = None,
        include_prompt: bool = False,
        include_result: bool = False,
    ) -> list[dict[str, Any]] | None:
        async with self._sf() as session:
            batch = await session.get(SubagentBatchRow, batch_id)
            if batch is None or batch.user_id != user_id:
                return None
            stmt = select(SubagentBatchItemRow).where(SubagentBatchItemRow.batch_id == batch_id)
            if status is not None:
                stmt = stmt.where(SubagentBatchItemRow.status == status)
            stmt = stmt.order_by(SubagentBatchItemRow.position).offset(offset).limit(limit)
            rows = list((await session.execute(stmt)).scalars())
            values = []
            for row in rows:
                value = self._item_dict(row, include_result=include_result)
                if include_prompt:
                    value["prompt"] = row.prompt
                values.append(value)
            return values

    async def claim_items(
        self,
        *,
        now: datetime,
        lease_owner: str,
        lease_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Promote pending work and atomically claim runnable items."""
        if limit <= 0:
            return []
        # Imported here, not at module scope: a persistence module must not drag
        # the whole bot package in at import time. The code is the shared one.
        from alpha.bots.failure_reasons import WORKER_CRASH

        claimed: list[dict[str, Any]] = []
        crash_records: list[dict[str, Any]] = []
        async with self._sf() as session:
            batches = list((await session.execute(select(SubagentBatchRow).where(SubagentBatchRow.status.in_(("queued", "running"))).order_by(SubagentBatchRow.created_at, SubagentBatchRow.id).with_for_update(skip_locked=True))).scalars())
            for batch in batches:
                if len(claimed) >= limit:
                    break

                expired = list(
                    (
                        await session.execute(
                            select(SubagentBatchItemRow)
                            .where(
                                SubagentBatchItemRow.batch_id == batch.id,
                                SubagentBatchItemRow.status.in_(("leased", "running")),
                                SubagentBatchItemRow.lease_expires_at < now,
                            )
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars()
                )
                for item in expired:
                    item.lease_owner = None
                    item.lease_expires_at = None
                    item.updated_at = now
                    if item.cancel_requested_at is not None:
                        item.status = "cancelled"
                        item.completed_at = now
                    elif item.attempt >= batch.max_attempts:
                        # A dead worker's lease expiring is a crash, and the
                        # last one of them: the item is failed with a TYPED
                        # reason and escalated to a human rather than being
                        # left to fail silently against a spent ceiling.
                        item.status = "failed"
                        item.error = item.error or "Execution lease expired after the maximum retry count"
                        item.completed_at = now
                        crash_records.append(
                            {
                                "item_id": item.id,
                                "batch_id": batch.id,
                                "subagent_type": batch.subagent_type,
                                "attempt": item.attempt,
                                "max_attempts": batch.max_attempts,
                                "error": item.error,
                                "reason": WORKER_CRASH,
                                "outcome": "lease_expired_exhausted",
                            }
                        )
                    else:
                        item.status = "queued"
                        item.error = "Previous worker lease expired; retrying"
                        crash_records.append(
                            {
                                "item_id": item.id,
                                "batch_id": batch.id,
                                "subagent_type": batch.subagent_type,
                                "attempt": item.attempt,
                                "max_attempts": batch.max_attempts,
                                "error": item.error,
                                "reason": WORKER_CRASH,
                                "outcome": "lease_expired_requeued",
                            }
                        )

                counts = await self._counts(session, batch.id)
                live = counts["queued"] + counts["leased"] + counts["running"]
                promote_count = max(0, batch.max_live_items - live)
                if promote_count:
                    pending = list(
                        (
                            await session.execute(
                                select(SubagentBatchItemRow)
                                .where(
                                    SubagentBatchItemRow.batch_id == batch.id,
                                    SubagentBatchItemRow.status == "pending",
                                )
                                .order_by(SubagentBatchItemRow.position)
                                .limit(promote_count)
                                .with_for_update(skip_locked=True)
                            )
                        ).scalars()
                    )
                    for item in pending:
                        item.status = "queued"
                        item.updated_at = now

                counts = await self._counts(session, batch.id)
                batch_available = max(0, batch.max_running_items - counts["leased"] - counts["running"])
                take = min(limit - len(claimed), batch_available)
                if take <= 0:
                    continue
                runnable = list(
                    (
                        await session.execute(
                            select(SubagentBatchItemRow)
                            .where(
                                SubagentBatchItemRow.batch_id == batch.id,
                                SubagentBatchItemRow.status == "queued",
                                SubagentBatchItemRow.cancel_requested_at.is_(None),
                            )
                            .order_by(SubagentBatchItemRow.position)
                            .limit(take)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars()
                )
                expires_at = now + timedelta(seconds=lease_seconds)
                for item in runnable:
                    item.status = "leased"
                    item.attempt += 1
                    item.lease_owner = lease_owner
                    item.lease_expires_at = expires_at
                    item.started_at = now
                    item.updated_at = now
                    item.error = None
                    value = self._item_dict(item)
                    value["prompt"] = item.prompt
                    value["batch"] = self._execution_batch_dict(batch)
                    claimed.append(value)
                if runnable:
                    batch.status = "running"
                    batch.updated_at = now
            await session.commit()
        for record in crash_records:
            await asyncio.to_thread(_record_item_failure, **record)
        return claimed

    async def renew_item_lease(
        self,
        item_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
        now: datetime,
    ) -> dict[str, bool]:
        async with self._sf() as session:
            item = (
                await session.execute(
                    select(SubagentBatchItemRow)
                    .where(
                        SubagentBatchItemRow.id == item_id,
                        SubagentBatchItemRow.status.in_(("leased", "running")),
                        SubagentBatchItemRow.lease_owner == lease_owner,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if item is None:
                return {"valid": False, "cancel_requested": True}
            batch = await session.get(SubagentBatchRow, item.batch_id)
            cancel_requested = item.cancel_requested_at is not None or batch is None or batch.status == "cancelled"
            if not cancel_requested:
                item.lease_expires_at = now + timedelta(seconds=lease_seconds)
                item.updated_at = now
                await session.commit()
            return {"valid": not cancel_requested, "cancel_requested": cancel_requested}

    async def mark_item_running(self, item_id: str, *, lease_owner: str, now: datetime) -> bool:
        async with self._sf() as session:
            item = (
                await session.execute(
                    select(SubagentBatchItemRow)
                    .where(
                        SubagentBatchItemRow.id == item_id,
                        SubagentBatchItemRow.status == "leased",
                        SubagentBatchItemRow.lease_owner == lease_owner,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if item is None or item.cancel_requested_at is not None:
                return False
            item.status = "running"
            item.started_at = now
            item.updated_at = now
            await session.commit()
            return True

    async def finalize_item(
        self,
        item_id: str,
        *,
        lease_owner: str,
        succeeded: bool,
        result: str | None,
        result_preview: str | None,
        result_truncated: bool,
        error: str | None,
        stop_reason: str | None,
        token_usage: dict[str, Any] | None,
        model_name: str | None,
        completed_at: datetime,
        acceptance_verdict: AcceptanceVerdict | None = None,
    ) -> bool:
        async with self._sf() as session:
            item = (
                await session.execute(
                    select(SubagentBatchItemRow)
                    .where(
                        SubagentBatchItemRow.id == item_id,
                        SubagentBatchItemRow.status.in_(("leased", "running")),
                        SubagentBatchItemRow.lease_owner == lease_owner,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if item is None:
                return False
            batch = await session.get(SubagentBatchRow, item.batch_id, with_for_update=True)
            cancelled = item.cancel_requested_at is not None or batch is None or batch.status == "cancelled"
            item.lease_owner = None
            item.lease_expires_at = None
            item.model_name = model_name
            item.stop_reason = stop_reason
            item.token_usage = token_usage
            item.updated_at = completed_at
            item.acceptance_verdict = None
            failure_record: dict[str, Any] | None = None
            if cancelled:
                item.status = "cancelled"
                item.error = "Cancelled by user"
                item.completed_at = completed_at
            elif succeeded:
                item.status = "succeeded"
                item.acceptance_verdict = validate_acceptance_verdict(acceptance_verdict)
                item.result = result
                item.result_preview = result_preview
                item.result_truncated = result_truncated
                item.error = None
                item.completed_at = completed_at
            elif item.attempt < batch.max_attempts:
                item.status = "queued"
                item.error = error
                item.started_at = None
                failure_record = {
                    "item_id": item.id,
                    "batch_id": item.batch_id,
                    "subagent_type": batch.subagent_type,
                    "attempt": item.attempt,
                    "max_attempts": batch.max_attempts,
                    "error": error,
                    "reason": classify_item_failure(error),
                    "outcome": "retry_queued",
                }
            else:
                item.status = "failed"
                item.error = error
                item.completed_at = completed_at
                failure_record = {
                    "item_id": item.id,
                    "batch_id": item.batch_id,
                    "subagent_type": batch.subagent_type,
                    "attempt": item.attempt,
                    "max_attempts": batch.max_attempts,
                    "error": error,
                    "reason": classify_item_failure(error),
                    "outcome": "failed",
                }
            if batch is not None:
                await self._refresh_batch_status(session, batch, now=completed_at)
            await session.commit()
            if failure_record is not None:
                await asyncio.to_thread(_record_item_failure, **failure_record)
            return True

    async def requeue_item_after_admission_failure(
        self,
        item_id: str,
        *,
        lease_owner: str,
        error: str | None,
        now: datetime,
    ) -> bool:
        """Undo a claim rejected before execution admission.

        Claiming increments ``attempt`` so crash recovery can bound real
        executions. A process-wide capacity rejection happens before an
        execution starts, so it must release the lease and restore that
        attempt instead of consuming the batch's retry budget.
        """
        async with self._sf() as session:
            item = (
                await session.execute(
                    select(SubagentBatchItemRow)
                    .where(
                        SubagentBatchItemRow.id == item_id,
                        SubagentBatchItemRow.status.in_(("leased", "running")),
                        SubagentBatchItemRow.lease_owner == lease_owner,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if item is None:
                return False
            batch = await session.get(SubagentBatchRow, item.batch_id, with_for_update=True)
            cancelled = item.cancel_requested_at is not None or batch is None or batch.status == "cancelled"
            item.lease_owner = None
            item.lease_expires_at = None
            item.updated_at = now
            failure_record: dict[str, Any] | None = None
            if cancelled:
                item.status = "cancelled"
                item.error = "Cancelled by user"
                item.completed_at = now
            else:
                item.status = "queued"
                item.attempt = max(0, item.attempt - 1)
                item.started_at = None
                item.error = error
                # The attempt was RESTORED, so this failure is recorded against
                # the restored attempt count: a capacity rejection must not
                # consume the batch's retry budget nor page a human.
                failure_record = {
                    "item_id": item.id,
                    "batch_id": item.batch_id,
                    "subagent_type": batch.subagent_type if batch is not None else "",
                    "attempt": item.attempt,
                    "max_attempts": batch.max_attempts if batch is not None else 0,
                    "error": error,
                    "reason": classify_item_failure(error),
                    "outcome": "admission_failed_requeued",
                }
            if batch is not None:
                await self._refresh_batch_status(session, batch, now=now)
            await session.commit()
            if failure_record is not None:
                await asyncio.to_thread(_record_item_failure, **failure_record)
            return True

    async def _refresh_batch_status(self, session: AsyncSession, batch: SubagentBatchRow, *, now: datetime) -> None:
        counts = await self._counts(session, batch.id)
        terminal = sum(counts[state] for state in ITEM_TERMINAL_STATUSES)
        if terminal >= batch.total_items:
            if batch.status != "cancelled":
                batch.status = "failed" if counts["failed"] > 0 and counts["succeeded"] == 0 else "completed"
            batch.completed_at = now
        elif batch.status not in ("paused", "cancelled"):
            batch.status = "running"
        batch.updated_at = now

    async def pause_batch(self, batch_id: str, *, user_id: str) -> dict[str, Any] | None:
        return await self._set_control(batch_id, user_id=user_id, action="pause")

    async def resume_batch(self, batch_id: str, *, user_id: str) -> dict[str, Any] | None:
        return await self._set_control(batch_id, user_id=user_id, action="resume")

    async def cancel_batch(self, batch_id: str, *, user_id: str) -> dict[str, Any] | None:
        return await self._set_control(batch_id, user_id=user_id, action="cancel")

    async def _set_control(self, batch_id: str, *, user_id: str, action: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        async with self._sf() as session:
            batch = await session.get(SubagentBatchRow, batch_id, with_for_update=True)
            if batch is None or batch.user_id != user_id:
                return None
            if action == "pause" and batch.status in ("queued", "running"):
                batch.status = "paused"
            elif action == "resume" and batch.status == "paused":
                batch.status = "queued"
            elif action == "cancel" and batch.status not in BATCH_TERMINAL_STATUSES:
                batch.status = "cancelled"
                batch.completed_at = now
                items = list(
                    (
                        await session.execute(
                            select(SubagentBatchItemRow)
                            .where(
                                SubagentBatchItemRow.batch_id == batch_id,
                                SubagentBatchItemRow.status.not_in(ITEM_TERMINAL_STATUSES),
                            )
                            .with_for_update()
                        )
                    ).scalars()
                )
                for item in items:
                    item.cancel_requested_at = now
                    item.updated_at = now
                    item.status = "cancelled"
                    item.error = "Cancelled by user"
                    item.lease_owner = None
                    item.lease_expires_at = None
                    item.completed_at = now
            batch.updated_at = now
            await session.commit()
            return await self._with_counts(session, batch)

    async def retry_item(self, batch_id: str, item_id: str, *, user_id: str) -> dict[str, Any] | None:
        """Operator-initiated retry: clears the spent ceiling on purpose.

        A human pressing retry is the sanctioned way to give an escalated item
        a fresh budget, so the attempt counter is reset and any open escalation
        for that item is acknowledged as resumed-by-operator rather than left
        open forever next to work that is running again.
        """
        now = datetime.now(UTC)
        async with self._sf() as session:
            batch = await session.get(SubagentBatchRow, batch_id, with_for_update=True)
            if batch is None or batch.user_id != user_id:
                return None
            item = await session.get(SubagentBatchItemRow, item_id, with_for_update=True)
            if item is None or item.batch_id != batch_id or item.status != "failed":
                return None
            item.status = "pending"
            item.attempt = 0
            item.error = None
            item.result = None
            item.result_preview = None
            item.result_truncated = False
            item.acceptance_verdict = None
            item.completed_at = None
            item.cancel_requested_at = None
            item.updated_at = now
            batch.status = "queued"
            batch.completed_at = None
            batch.updated_at = now
            await session.commit()
            await asyncio.to_thread(_acknowledge_item_escalations, item_id)
            return self._item_dict(item)
