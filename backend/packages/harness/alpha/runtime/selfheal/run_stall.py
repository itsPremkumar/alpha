"""Run stall watchdog: finalise live runs that stop making progress.

See ``tests/test_run_stall_watchdog.py`` for the production incident this
encodes (a run stuck ``status=running`` for 20+ minutes with no heartbeat and
no surfaced error).

Design constraints:

* **Pure consumer of public APIs.** ``runtime/runs/manager.py`` and
  ``worker.py`` are owned by another workstream; this module only calls
  ``RunStore.list_inflight``/``get`` and ``RunManager.cancel``/``set_status``.
* **``cancel()`` is the universal primitive.** Local-registry, durable
  multi-worker and lease-expired paths all converge inside it; rows this
  process does not own return ``not_active_locally`` and are left alone
  (startup orphan reconciliation and the lease heartbeat own those).
* **Progress wins races.** The row is re-read after the stale scan and again
  after the terminal write; a heartbeat that lands in between aborts the
  claim, and a late worker ``interrupted`` write is detected and re-asserted
  so the stall reason reaches every UI.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from alpha.config.run_stall_config import RunStallSettings
from alpha.runtime.runs.schemas import RunStatus

__all__ = ["STALL_STOP_REASON", "RunStallSettings", "RunStallWatchdog"]

logger = logging.getLogger(__name__)

#: ``stop_reason`` persisted with every watchdog-terminalised run. The value is
#: a stable machine token: the frontend and recovery tooling match on it.
STALL_STOP_REASON = "stalled"

#: ``RunManager.cancel`` outcomes after which this worker owns the row and may
#: persist the stall error. ``not_active_locally`` (foreign/store-only row),
#: ``not_cancellable`` (already terminal) and ``lease_valid_elsewhere`` are
#: explicitly *not* ours to annotate; ``unknown`` means durable cancel
#: persistence failed but local cancellation proceeded, so the local
#: terminalisation still applies.
_CANCELLED_BY_US = frozenset({"cancelled", "requested", "taken_over", "unknown"})

#: How many terminal-error writes are attempted when the worker's own
#: interrupted-finalisation races and clobbers the first one.
_MAX_ERROR_WRITE_ATTEMPTS = 3


def _parse_ts(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp defensively (naive values are UTC)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class RunStallWatchdog:
    """Periodically cancels running rows whose progress heartbeat went silent.

    ``now_fn`` returns a :class:`datetime` and exists so tests can drive the
    staleness comparison without real waiting; production uses
    ``datetime.now(UTC)``.
    """

    def __init__(
        self,
        *,
        run_manager: Any,
        run_store: Any,
        settings: RunStallSettings | None = None,
        now_fn: Callable[[], datetime] | None = None,
        settle_seconds: float = 0.5,
    ) -> None:
        self._manager = run_manager
        self._store = run_store
        self._settings = settings or RunStallSettings()
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._settle_seconds = settle_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    # ------------------------------------------------------------------ scan
    async def scan_once(self) -> list[str]:
        """Terminalise every stalled row found in one pass.

        Never raises: a store failure returns ``[]`` so the loop survives.
        """
        if self._store is None or self._manager is None:
            return []
        try:
            rows = await self._store.list_inflight()
        except Exception:
            logger.warning("run stall watchdog: inflight listing failed", exc_info=True)
            return []

        acted: list[str] = []
        for row in rows:
            try:
                if await self._claim_if_stalled(row):
                    acted.append(str(row.get("run_id")))
            except Exception:
                run_id = row.get("run_id") if isinstance(row, dict) else None
                logger.warning("run stall watchdog: skipping run %s", run_id, exc_info=True)
        return acted

    async def _claim_if_stalled(self, row: dict[str, Any]) -> bool:
        if row.get("status") != RunStatus.running:
            return False  # pending runs have no heartbeat by design; terminals are done
        run_id = str(row.get("run_id"))
        if not run_id or run_id == "None":
            return False

        if not self._is_stale(row):
            return False

        # Double-read: progress that landed between listing and now wins.
        fresh = await self._store.get(run_id)
        if fresh is None or fresh.get("status") != RunStatus.running or not self._is_stale(fresh):
            return False

        outcome = await self._manager.cancel(run_id, action="interrupt")
        outcome_value = getattr(outcome, "value", str(outcome))
        if outcome_value not in _CANCELLED_BY_US:
            logger.info(
                "run stall watchdog: run %s not cancelled by this worker (outcome=%s); leaving row untouched",
                run_id,
                outcome_value,
            )
            return False

        message = (
            f"Run stalled: no progress for {int(self._settings.timeout_seconds)}s "
            f"(last progress {fresh.get('updated_at') or fresh.get('created_at')}); "
            "the run stall watchdog cancelled the run."
        )
        await self._persist_stall_error(run_id, message)
        logger.warning(
            "run stall watchdog: cancelled run %s after %ss without progress",
            run_id,
            int(self._settings.timeout_seconds),
        )
        return True

    def _is_stale(self, row: dict[str, Any]) -> bool:
        ts = _parse_ts(row.get("updated_at")) or _parse_ts(row.get("created_at"))
        if ts is None:
            return False  # no trustworthy timestamp: never claim what we cannot prove
        age = (self._now_fn() - ts).total_seconds()
        return age > self._settings.timeout_seconds

    async def _persist_stall_error(self, run_id: str, message: str) -> None:
        """Write the terminal stall error, re-asserting against the worker's
        own late ``interrupted`` finalisation (which would hide the reason)."""
        for attempt in range(_MAX_ERROR_WRITE_ATTEMPTS):
            await self._manager.set_status(
                run_id,
                RunStatus.error,
                error=message,
                stop_reason=STALL_STOP_REASON,
            )
            if self._settle_seconds > 0:
                await asyncio.sleep(self._settle_seconds)
            try:
                row = await self._store.get(run_id)
            except Exception:
                logger.warning(
                    "run stall watchdog: could not verify terminal state for run %s", run_id, exc_info=True
                )
                return
            if row is None or row.get("status") == RunStatus.error:
                return
            logger.info(
                "run stall watchdog: terminal write for run %s was clobbered by status=%s (attempt %s); re-asserting",
                run_id,
                row.get("status"),
                attempt + 1,
            )
        logger.warning(
            "run stall watchdog: could not stabilise terminal error for run %s; leaving worker's final status",
            run_id,
        )

    # ------------------------------------------------------------------ loop
    async def run_forever(self, stop: asyncio.Event) -> None:
        """Scan every ``interval_seconds`` until ``stop`` is set. Never dies."""
        interval = self._settings.interval_seconds
        while not stop.is_set():
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("run stall watchdog: scan failed", exc_info=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except (TimeoutError, asyncio.TimeoutError):
                continue

    def start(self) -> None:
        """Start the background loop (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self.run_forever(self._stop), name="run-stall-watchdog")

    async def stop(self, timeout: float = 5.0) -> None:
        """Signal the loop and wait for it to finish (idempotent)."""
        task, stop = self._task, self._stop
        if task is None:
            return
        if stop is not None:
            stop.set()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            self._task = None
            self._stop = None
