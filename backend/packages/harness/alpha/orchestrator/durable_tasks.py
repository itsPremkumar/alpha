"""7+12+13. Durable task runtime + delivery queue + dedupe + governors.

Wraps (not replaces) runtime.runs.manager RunRecord + scheduler leases:

- DurableTaskRecord: submit/status/cancel envelope for MCP/subagent tasks
- DurableTaskRuntime: lease-fenced poll loop with exponential backoff,
  restart recovery via rehydrate(records), bounded result storage
- DeliveryQueue: exactly-once FIFO with idempotency keys (same-key
  replacement guard mirrors RunStore semantics)
- DedupeCache: TTL dedupe for inbound events / webhook retries
- ConcurrencyGovernor: max_running/max_queued with burst|wait|reject policy
- TokenBudgetGovernor: warn at threshold, hard-stop strips tool calls
  (mirrors config token_budget semantics; enforcement point calls back)
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Literal

TaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
CapacityPolicy = Literal["wait", "burst", "reject"]


@dataclass
class DurableTaskRecord:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: str = "mcp"  # mcp | subagent | automation
    status: TaskStatus = "queued"
    payload: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    attempts: int = 0
    next_poll_at: float = 0.0
    lease_owner: str = ""
    lease_expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class DurableTaskRuntime:
    base_poll_seconds: float = 5.0
    max_poll_seconds: float = 300.0
    max_result_chars: int = 65536
    _tasks: dict[str, DurableTaskRecord] = field(default_factory=dict)

    def submit(self, kind: str = "mcp", payload: dict[str, Any] | None = None) -> DurableTaskRecord:
        record = DurableTaskRecord(kind=kind, payload=dict(payload or {}))
        record.next_poll_at = time.time()
        self._tasks[record.task_id] = record
        return record

    def get(self, task_id: str) -> DurableTaskRecord | None:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        record = self._tasks.get(task_id)
        if record is None or record.status in ("succeeded", "failed", "cancelled"):
            return False
        record.status = "cancelled"
        return True

    def claim_due(self, owner: str, lease_seconds: float = 120.0, limit: int = 10) -> list[DurableTaskRecord]:
        now = time.time()
        due: list[DurableTaskRecord] = []
        for record in self._tasks.values():
            if len(due) >= limit:
                break
            if record.status not in ("queued", "running"):
                continue
            lease_live = record.lease_expires_at > now and record.lease_owner and record.lease_owner != owner
            if lease_live or record.next_poll_at > now:
                continue
            record.lease_owner = owner
            record.lease_expires_at = now + lease_seconds
            if record.status == "queued":
                record.status = "running"
            due.append(record)
        return due

    def report_success(self, task_id: str, result: Any) -> None:
        record = self._tasks.get(task_id)
        if record is None:
            return
        text = result if isinstance(result, str) else repr(result)
        if len(text) > self.max_result_chars:
            text = text[: self.max_result_chars] + "…[truncated]"
        record.result = text
        record.status = "succeeded"
        record.lease_expires_at = 0.0

    def report_retryable_error(self, task_id: str, error: str) -> None:
        record = self._tasks.get(task_id)
        if record is None:
            return
        record.attempts += 1
        record.error = error[:2000]
        backoff = min(self.base_poll_seconds * (2 ** min(record.attempts, 6)), self.max_poll_seconds)
        record.next_poll_at = time.time() + backoff

    def report_failed(self, task_id: str, error: str) -> None:
        record = self._tasks.get(task_id)
        if record is None:
            return
        record.error = error[:2000]
        record.status = "failed"
        record.lease_expires_at = 0.0

    def rehydrate(self, records: list[DurableTaskRecord]) -> int:
        """Restart recovery: re-register persisted non-terminal tasks."""
        restored = 0
        for record in records:
            if record.status in ("queued", "running"):
                record.status = "queued"
                record.lease_owner = ""
                record.lease_expires_at = 0.0
                record.next_poll_at = min(record.next_poll_at, time.time())
                self._tasks[record.task_id] = record
                restored += 1
        return restored


@dataclass
class DeliveryQueue:
    """Exactly-once FIFO with idempotency keys."""

    maxsize: int = 1000
    _queue: deque = field(default_factory=deque)
    _seen_keys: OrderedDict = field(default_factory=OrderedDict)

    def offer(self, item: Any, idempotency_key: str = "") -> bool:
        if idempotency_key:
            if idempotency_key in self._seen_keys:
                return False
            self._seen_keys[idempotency_key] = time.time()
            while len(self._seen_keys) > 5000:
                self._seen_keys.popitem(last=False)
        if len(self._queue) >= self.maxsize:
            return False
        self._queue.append(item)
        return True

    def poll(self) -> Any | None:
        return self._queue.popleft() if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)


@dataclass
class DedupeCache:
    ttl_seconds: float = 3600.0
    _entries: OrderedDict = field(default_factory=OrderedDict)

    def seen(self, key: str) -> bool:
        now = time.time()
        expired = [k for k, exp in self._entries.items() if exp <= now]
        for k in expired:
            self._entries.pop(k, None)
        if key in self._entries:
            return True
        self._entries[key] = now + self.ttl_seconds
        while len(self._entries) > 10000:
            self._entries.popitem(last=False)
        return False


@dataclass
class ConcurrencyGovernor:
    max_running: int = 3
    max_queued: int = 64
    burst_limit: int = 0
    policy: CapacityPolicy = "wait"
    _running: int = 0
    _queued: int = 0

    def try_acquire(self) -> Literal["run", "queue", "reject"]:
        if self._running < self.max_running:
            self._running += 1
            return "run"
        if self.policy == "burst" and (self._running - self.max_running) < self.burst_limit:
            self._running += 1
            return "run"
        if self.policy == "reject":
            return "reject"
        if self._queued < self.max_queued:
            self._queued += 1
            return "queue"
        return "reject"

    def release(self, was_queued: bool = False) -> None:
        if was_queued and self._queued > 0:
            self._queued -= 1
        elif self._running > 0:
            self._running -= 1


@dataclass
class TokenBudgetGovernor:
    max_tokens: int = 200000
    warn_threshold: float = 0.8
    warned: bool = False

    def observe(self, total_tokens: int) -> Literal["ok", "warn", "hard_stop"]:
        if total_tokens >= self.max_tokens:
            return "hard_stop"
        if not self.warned and total_tokens >= int(self.max_tokens * self.warn_threshold):
            self.warned = True
            return "warn"
        return "ok"
