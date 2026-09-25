"""Safe durable continuation for runs interrupted by infrastructure/model failures.

The service resumes only a model node from the current checkpoint.  A pending
tool/custom node may represent an external side effect that completed before its
result reached durable storage, so replaying it automatically would violate the
safe policy.  Such runs are terminalized with an explicit confirmation reason
instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from alpha.config.run_ownership_config import RunOwnershipConfig
from alpha.runtime.checkpoint_mode import CheckpointModeMismatchError, CheckpointModeReconfigurationError
from alpha.runtime.runs.manager import (
    MODEL_FAILURE_RECOVERY_REASON,
    RECOVERABLE_RUN_STOP_REASONS,
    RunRecord,
    RunStatus,
)
from alpha.trace_context import ensure_trace_context
from app.gateway.auth_disabled import AUTH_SOURCE_INTERNAL
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, get_internal_user
from app.gateway.run_models import RunCreateRequest

logger = logging.getLogger(__name__)

RECOVERY_CONFIRMATION_REASON = "recovery_confirmation_required"
RECOVERY_EXHAUSTED_REASON = "recovery_exhausted"
RECOVERY_SUPERSEDED_REASON = "recovery_superseded"
RECOVERY_NO_WORK_REASON = "recovery_no_work"
RECOVERY_OWNER_MISSING_REASON = "recovery_owner_missing"
RECOVERY_BLOCKED_REASON = "recovery_blocked"


class RecoveryCheckpointAssemblyError(RuntimeError):
    """The graph needed to prove safe pending work could not be assembled."""


_TERMINAL_RECOVERY_STATUSES = (RunStatus.error.value, RunStatus.interrupted.value)
_RECOVERABLE_LLM_FALLBACK_REASONS = frozenset({"transient", "burst_rate", "busy", "circuit_open"})
_RECOVERY_SCAN_LIMIT = 100
_RECOVERY_CONCURRENCY_LIMIT = 32


class RecoveryDisposition(StrEnum):
    RESUME = "resume"
    REQUIRE_CONFIRMATION = "require_confirmation"
    STOP = "stop"


@dataclass(frozen=True)
class RecoveryAssessment:
    disposition: RecoveryDisposition
    checkpoint_id: str | None
    pending_nodes: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class RecoveryPassResult:
    scanned: int = 0
    resumed: tuple[str, ...] = ()
    awaiting_confirmation: tuple[str, ...] = ()
    stopped: tuple[str, ...] = ()
    exhausted: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


CheckpointReader = Callable[[RunRecord], Awaitable[Any]]
RecoveryLauncher = Callable[..., Awaitable[Any]]


def _node_names(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value if str(item))
    except TypeError:
        return (str(value),)


def _is_model_node(name: str) -> bool:
    """Return True only for graph nodes known to be side-effect free.

    Substring matching is deliberately avoided: an unknown custom node such as
    ``charge_customer_model`` can perform effects despite containing ``model``.
    Recovery must fail closed until that node has an explicit safe identity.
    """

    normalized = name.strip().lower().replace("-", "_")
    return normalized in {"model", "agent", "lead_agent"}


def _snapshot_checkpoint_id(snapshot: Any) -> str | None:
    snapshot_config = getattr(snapshot, "config", {}) or {}
    configurable = snapshot_config.get("configurable", {}) if isinstance(snapshot_config, dict) else {}
    raw_checkpoint_id = configurable.get("checkpoint_id") if isinstance(configurable, dict) else None
    return raw_checkpoint_id if isinstance(raw_checkpoint_id, str) and raw_checkpoint_id else None


def _terminal_llm_fallback_reason(snapshot: Any) -> str | None:
    values = getattr(snapshot, "values", None)
    messages = values.get("messages") if isinstance(values, dict) else None
    if not isinstance(messages, list) or not messages:
        return None
    last_message = messages[-1]
    additional_kwargs = last_message.get("additional_kwargs") if isinstance(last_message, dict) else getattr(last_message, "additional_kwargs", None)
    if not isinstance(additional_kwargs, dict) or additional_kwargs.get("agent_workspace_error_fallback") is not True:
        return None
    reason = additional_kwargs.get("error_reason")
    return reason if isinstance(reason, str) else None


def _has_terminal_llm_fallback(snapshot: Any) -> bool:
    return _terminal_llm_fallback_reason(snapshot) in _RECOVERABLE_LLM_FALLBACK_REASONS


def select_recovery_snapshot(*, record: RunRecord, head: Any, history: tuple[Any, ...] | list[Any]) -> Any:
    """Select the exact safe replay base for a run.

    Provider fallback is represented by a terminal assistant message. For that
    one explicitly recoverable failure class, the parent checkpoint is the state
    immediately before the failed model call. Every other terminal reason stays
    on the head; a generic crash must never silently roll back user-visible work.
    """
    if record.stop_reason != MODEL_FAILURE_RECOVERY_REASON or not _has_terminal_llm_fallback(head):
        return head
    head_id = _snapshot_checkpoint_id(head)
    for candidate in history:
        if candidate is head:
            continue
        candidate_id = _snapshot_checkpoint_id(candidate)
        if candidate_id and candidate_id != head_id:
            return candidate
    return head


def assess_checkpoint_snapshot(snapshot: Any | None) -> RecoveryAssessment:
    """Classify whether a checkpoint can be resumed without guessing side effects."""

    if snapshot is None:
        return RecoveryAssessment(
            RecoveryDisposition.STOP,
            None,
            (),
            "checkpoint is unavailable",
        )

    raw_names = list(_node_names(getattr(snapshot, "next", ())))
    for task in getattr(snapshot, "tasks", ()) or ():
        name = getattr(task, "name", None)
        if name:
            raw_names.append(str(name))

    pending_nodes = tuple(dict.fromkeys(raw_names))
    checkpoint_id = _snapshot_checkpoint_id(snapshot)
    values = getattr(snapshot, "values", None)
    goal = values.get("goal") if isinstance(values, dict) else None
    goal_active = isinstance(goal, dict) and goal.get("status") == "active"

    if checkpoint_id is None:
        return RecoveryAssessment(
            RecoveryDisposition.STOP,
            None,
            pending_nodes,
            "checkpoint has no addressable id",
        )
    if not pending_nodes:
        if goal_active:
            return RecoveryAssessment(
                RecoveryDisposition.RESUME,
                checkpoint_id,
                ("goal_continuation",),
                "checkpoint has an active durable goal and needs the goal continuation loop",
            )
        return RecoveryAssessment(
            RecoveryDisposition.STOP,
            checkpoint_id,
            (),
            "checkpoint has no pending graph work",
        )
    if all(_is_model_node(name) for name in pending_nodes):
        return RecoveryAssessment(
            RecoveryDisposition.RESUME,
            checkpoint_id,
            pending_nodes,
            f"pending side-effect-free node(s): {', '.join(pending_nodes)}",
        )
    return RecoveryAssessment(
        RecoveryDisposition.REQUIRE_CONFIRMATION,
        checkpoint_id,
        pending_nodes,
        f"pending node(s) may have external side effects: {', '.join(pending_nodes)}",
    )


def _recovery_attempt(record: RunRecord) -> int:
    raw = (record.metadata or {}).get("recovery_attempt", 0)
    if isinstance(raw, bool):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _with_recovery_note(existing: str | None, note: str) -> str:
    if existing:
        return f"{existing}\n{note}"
    return note


class SafeRunRecoveryService:
    """Bounded single-flight scanner that resumes only provably safe checkpoints."""

    def __init__(
        self,
        *,
        app: Any,
        run_manager: Any,
        config: RunOwnershipConfig,
        checkpoint_reader: CheckpointReader | None = None,
        launcher: RecoveryLauncher | None = None,
    ) -> None:
        self._app = app
        self._run_manager = run_manager
        self._config = config
        self._checkpoint_reader = checkpoint_reader or self._read_checkpoint
        self._launcher = launcher or self._launch_recovery
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._pass_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._config.auto_resume

    async def start(self) -> None:
        if not self.enabled or (self._task is not None and not self._task.done()):
            return
        self._stop.clear()
        # Run the first pass in the supervised loop rather than blocking Gateway
        # readiness on graph assembly across every recovered thread. The pass
        # starts immediately and remains bounded by the configured semaphore.
        self._task = asyncio.create_task(self._loop(), name="alpha-safe-run-recovery")
        self._task.add_done_callback(self._task_done)

    async def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        _, pending = await asyncio.wait((task,), timeout=max(0.0, timeout))
        if pending:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            logger.warning("Safe run recovery drain exceeded %.1fs; cancelled active pass", timeout)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        if self._task is task:
            self._task = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.warning("Safe run recovery loop failed", exc_info=True)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.recover_once()
            except Exception:
                logger.warning("Safe run recovery pass failed", exc_info=True)
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._config.resume_poll_interval_seconds,
                )
                return
            except TimeoutError:
                pass

    async def recover_once(self) -> RecoveryPassResult:
        if not self.enabled:
            return RecoveryPassResult()
        async with self._pass_lock:
            try:
                records = await self._run_manager.list_recovery_candidates(
                    statuses=set(_TERMINAL_RECOVERY_STATUSES),
                    stop_reasons=set(RECOVERABLE_RUN_STOP_REASONS),
                    limit=_RECOVERY_SCAN_LIMIT,
                )
            except Exception:
                logger.warning("Failed to list safe run recovery candidates", exc_info=True)
                return RecoveryPassResult()

            if not records:
                return RecoveryPassResult()

            semaphore = asyncio.Semaphore(min(self._config.max_concurrent_resumes, _RECOVERY_CONCURRENCY_LIMIT))

            async def run_one(record: RunRecord) -> tuple[str, str]:
                async with semaphore:
                    return await self._recover_one(record)

            raw_outcomes = await asyncio.gather(
                *(run_one(record) for record in records),
                return_exceptions=True,
            )
            outcomes: list[tuple[str, str]] = []
            for record, outcome in zip(records, raw_outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    logger.warning(
                        "Safe recovery failed for run %s",
                        record.run_id,
                        exc_info=outcome,
                    )
                    outcomes.append((record.run_id, "failed"))
                else:
                    outcomes.append(outcome)
            grouped: dict[str, list[str]] = {
                "resumed": [],
                "awaiting_confirmation": [],
                "stopped": [],
                "exhausted": [],
                "deferred": [],
                "failed": [],
            }
            for run_id, outcome in outcomes:
                grouped[outcome].append(run_id)
            return RecoveryPassResult(
                scanned=len(records),
                resumed=tuple(grouped["resumed"]),
                awaiting_confirmation=tuple(grouped["awaiting_confirmation"]),
                stopped=tuple(grouped["stopped"]),
                exhausted=tuple(grouped["exhausted"]),
                deferred=tuple(grouped["deferred"]),
                failed=tuple(grouped["failed"]),
            )

    async def _transition(
        self,
        record: RunRecord,
        stop_reason: str,
        note: str,
    ) -> bool:
        return await self._run_manager.transition_recovery_stop_reason(
            record.run_id,
            expected_status=record.status.value,
            expected_stop_reason=record.stop_reason or "",
            stop_reason=stop_reason,
            error=_with_recovery_note(record.error, note),
        )

    async def _resolve_owner(self, record: RunRecord) -> str | None:
        if record.user_id:
            return record.user_id
        thread_store = getattr(self._app.state, "thread_store", None)
        if thread_store is None:
            return None
        try:
            row = await thread_store.get(record.thread_id)
        except Exception:
            logger.warning("Failed to resolve owner for recovery of run %s", record.run_id, exc_info=True)
            return None
        if not isinstance(row, dict):
            return None
        owner = row.get("user_id") or row.get("owner_user_id")
        return owner.strip() if isinstance(owner, str) and owner.strip() else None

    async def _recover_one(self, record: RunRecord) -> tuple[str, str]:
        source_metadata = record.metadata if isinstance(record.metadata, dict) else {}
        if any(key in source_metadata for key in ("scheduled_task_id", "scheduled_task_run_id", "mcp_task_notification")):
            await self._transition(
                record,
                RECOVERY_BLOCKED_REASON,
                "This run is owned by a durable scheduled/MCP dispatcher and must be recovered by that service, not chat auto-resume.",
            )
            return record.run_id, "stopped"
        if "replay_kind" in source_metadata or "regenerate_from_run_id" in source_metadata:
            await self._transition(
                record,
                RECOVERY_BLOCKED_REASON,
                "Automatic recovery does not replay edit/regenerate lineage without its original replacement input; regenerate manually.",
            )
            return record.run_id, "stopped"

        attempt = _recovery_attempt(record)
        if attempt >= self._config.max_resume_attempts:
            await self._transition(
                record,
                RECOVERY_EXHAUSTED_REASON,
                f"Automatic recovery stopped after {attempt} attempt(s). Resume manually after reviewing the checkpoint.",
            )
            return record.run_id, "exhausted"

        if attempt > 0 and self._config.resume_backoff_seconds > 0:
            updated_at = _parse_timestamp(record.updated_at)
            if updated_at is not None:
                due_at = updated_at + timedelta(seconds=self._config.resume_backoff_seconds)
                if datetime.now(UTC) < due_at:
                    return record.run_id, "deferred"

        latest = await self._run_manager.list_by_thread(
            record.thread_id,
            user_id=record.user_id,
            limit=1,
        )
        if not latest or latest[0].run_id != record.run_id:
            await self._transition(
                record,
                RECOVERY_SUPERSEDED_REASON,
                "A newer run advanced this thread, so the interrupted run was not replayed.",
            )
            return record.run_id, "stopped"

        owner_user_id = await self._resolve_owner(record)
        if owner_user_id is None:
            await self._transition(
                record,
                RECOVERY_OWNER_MISSING_REASON,
                "Automatic recovery could not prove the owning user; resume manually after verifying ownership.",
            )
            return record.run_id, "stopped"

        try:
            snapshot = await self._checkpoint_reader(record)
        except (RecoveryCheckpointAssemblyError, CheckpointModeMismatchError, CheckpointModeReconfigurationError):
            logger.warning("Permanent recovery assembly failure for run %s", record.run_id, exc_info=True)
            await self._transition(
                record,
                RECOVERY_BLOCKED_REASON,
                "Automatic recovery cannot assemble this checkpoint safely; fix the provider/checkpoint configuration and resume manually.",
            )
            return record.run_id, "stopped"
        except Exception:
            logger.warning("Checkpoint inspection failed for recovery of run %s", record.run_id, exc_info=True)
            # A checkpointer/database outage may be transient. Keep the durable
            # candidate reason unchanged so a later pass can recover it after the
            # dependency returns; no run is admitted on unreadable state.
            return record.run_id, "failed"

        if record.stop_reason == MODEL_FAILURE_RECOVERY_REASON:
            failure_reason = _terminal_llm_fallback_reason(snapshot)
            if failure_reason is not None and failure_reason not in _RECOVERABLE_LLM_FALLBACK_REASONS:
                await self._transition(
                    record,
                    RECOVERY_BLOCKED_REASON,
                    f"Automatic recovery will not replay a non-transient model failure ({failure_reason}); fix the provider/model configuration and regenerate or resume manually.",
                )
                return record.run_id, "stopped"

        assessment = assess_checkpoint_snapshot(snapshot)
        if assessment.disposition is RecoveryDisposition.REQUIRE_CONFIRMATION:
            await self._transition(
                record,
                RECOVERY_CONFIRMATION_REASON,
                "Automatic recovery paused because the interrupted external action may already have taken effect. Verify it before resuming.",
            )
            return record.run_id, "awaiting_confirmation"
        if assessment.disposition is RecoveryDisposition.STOP or assessment.checkpoint_id is None:
            await self._transition(
                record,
                RECOVERY_NO_WORK_REASON,
                f"Automatic recovery stopped: {assessment.reason}.",
            )
            return record.run_id, "stopped"

        try:
            await self._launcher(
                source=record,
                checkpoint_id=assessment.checkpoint_id,
                attempt=attempt + 1,
                reason=record.stop_reason or "recoverable_failure",
                owner_user_id=owner_user_id,
            )
        except HTTPException as exc:
            if exc.status_code == 409:
                return record.run_id, "deferred"
            if exc.status_code in {400, 404, 422, 501}:
                await self._transition(
                    record,
                    RECOVERY_BLOCKED_REASON,
                    f"Automatic recovery admission was rejected safely (HTTP {exc.status_code}); fix the run configuration and resume manually.",
                )
                return record.run_id, "stopped"
            raise
        except ValidationError:
            logger.warning("Automatic recovery metadata is invalid for run %s", record.run_id, exc_info=True)
            await self._transition(
                record,
                RECOVERY_BLOCKED_REASON,
                "Automatic recovery cannot rebuild the run request from its persisted metadata; repair the metadata and resume manually.",
            )
            return record.run_id, "stopped"
        except Exception:
            logger.warning("Automatic recovery launch failed for run %s", record.run_id, exc_info=True)
            return record.run_id, "failed"
        return record.run_id, "resumed"

    def _internal_request(self, owner_user_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            app=self._app,
            headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: owner_user_id},
            state=SimpleNamespace(
                user=get_internal_user(),
                auth_source=AUTH_SOURCE_INTERNAL,
            ),
            cookies={},
        )

    async def _read_checkpoint(self, record: RunRecord) -> Any:
        from app.gateway.services import abuild_checkpoint_state_accessor

        owner_user_id = await self._resolve_owner(record)
        if owner_user_id is None:
            raise RuntimeError("recovery owner is unavailable")
        request = self._internal_request(owner_user_id)
        try:
            accessor, config = await abuild_checkpoint_state_accessor(
                request,
                thread_id=record.thread_id,
                assistant_id=record.assistant_id,
                require_graph=True,
            )
        except (CheckpointModeMismatchError, CheckpointModeReconfigurationError):
            raise
        except Exception as exc:
            raise RecoveryCheckpointAssemblyError(f"checkpoint graph assembly failed: {exc}") from exc
        head = await accessor.aget(config)
        if head is None or record.stop_reason != MODEL_FAILURE_RECOVERY_REASON:
            return head
        history = await accessor.ahistory(config, limit=2)
        return select_recovery_snapshot(record=record, head=head, history=history)

    async def _launch_recovery(
        self,
        *,
        source: RunRecord,
        checkpoint_id: str,
        attempt: int,
        reason: str,
        owner_user_id: str,
    ) -> Any:
        from app.gateway.services import start_run

        source_metadata = source.metadata if isinstance(source.metadata, dict) else {}
        raw_criteria = source_metadata.get("acceptance_criteria")
        criteria = raw_criteria if isinstance(raw_criteria, list) else None
        recovery_context: dict[str, Any] = {"user_id": owner_user_id}
        if source_metadata.get("autonomous") is True:
            recovery_context["non_interactive"] = True
        body = RunCreateRequest(
            assistant_id=source.assistant_id,
            input=None,
            metadata={
                "auto_recovery": {
                    "source_run_id": source.run_id,
                    "attempt": attempt,
                    "reason": reason,
                    "source_model_name": source.model_name,
                },
                "resumed_from_run_id": source.run_id,
                "recovery_attempt": attempt,
                "recovery_reason": reason,
            },
            acceptance_criteria=criteria,
            autonomous=source_metadata.get("autonomous") is True,
            config={"recursion_limit": 1000},
            context=recovery_context,
            checkpoint_id=checkpoint_id,
            on_disconnect="continue",
            multitask_strategy="reject",
        )
        request = self._internal_request(owner_user_id)
        with ensure_trace_context():
            return await start_run(
                body,
                source.thread_id,
                request,
                idempotency_key=f"auto-recovery:{source.run_id}",
                require_existing_thread=True,
            )
