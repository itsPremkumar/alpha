"""Typed, bounded communication plane for Alpha swarms.

The communication plane is intentionally separate from the DAG scheduler.  A
worker can publish observations and request context without becoming a peer
router, while the scheduler remains the only component allowed to change task
state.  Messages are append-only within a bounded in-memory window and are
checkpointed by :class:`~alpha.swarm.coordinator.SwarmCoordinator` as data.

All message payloads are treated as untrusted data at the worker boundary.  A
message can inform a worker, but it cannot grant tools, alter policy, or change
another task's state.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from alpha.blackboard.federated_blackboard import FederatedBlackboard


class SwarmMessageValidationError(ValueError):
    """Raised when a message cannot be safely admitted to the bus."""


@dataclass(frozen=True)
class SwarmMessage:
    """One bounded, auditable message on a swarm blackboard."""

    message_id: str
    swarm_id: str
    sequence: int
    topic: str
    sender: str
    kind: str
    content: str
    data: dict[str, Any]
    task_id: str | None
    trust: str
    idempotency_key: str | None
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "swarm_id": self.swarm_id,
            "sequence": self.sequence,
            "topic": self.topic,
            "sender": self.sender,
            "kind": self.kind,
            "content": self.content,
            "data": dict(self.data),
            "task_id": self.task_id,
            "trust": self.trust,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SwarmMessage:
        return cls(
            message_id=str(data.get("message_id", "")),
            swarm_id=str(data.get("swarm_id", "")),
            sequence=int(data.get("sequence", 0)),
            topic=str(data.get("topic", "general")),
            sender=str(data.get("sender", "system")),
            kind=str(data.get("kind", "observation")),
            content=str(data.get("content", "")),
            data=dict(data.get("data", {})) if isinstance(data.get("data", {}), Mapping) else {},
            task_id=data.get("task_id"),
            trust=str(data.get("trust", "untrusted")),
            idempotency_key=data.get("idempotency_key"),
            created_at=float(data.get("created_at", 0.0)),
        )


class SwarmSubscription:
    """A small polling subscription for one agent.

    It is deliberately synchronous and process-local.  Gateway streaming can
    bridge it to SSE later without making the core worker protocol depend on an
    event loop.
    """

    def __init__(self, bus: SwarmMessageBus, agent_id: str, max_queue: int = 64):
        self._bus = bus
        self.agent_id = agent_id
        self._queue: queue.Queue[SwarmMessage] = queue.Queue(maxsize=max_queue)
        self._closed = False
        self._unsubscribe = bus._add_subscriber(self)

    def poll(self, limit: int = 20) -> list[SwarmMessage]:
        if self._closed:
            return []
        result: list[SwarmMessage] = []
        while len(result) < max(1, int(limit)):
            try:
                result.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return result

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._unsubscribe(self)

    def __enter__(self) -> SwarmSubscription:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class SwarmMessageBus:
    """Bounded, idempotent, topic-partitioned blackboard for one swarm."""

    def __init__(
        self,
        swarm_id: str,
        *,
        messages: Iterable[SwarmMessage | Mapping[str, Any]] | None = None,
        max_messages: int = 256,
        max_content_chars: int = 12_000,
        blackboard: FederatedBlackboard | None = None,
        max_idempotency_keys: int = 1_024,
        max_data_chars: int = 16_000,
    ) -> None:
        if not swarm_id or not str(swarm_id).strip():
            raise SwarmMessageValidationError("swarm_id must be non-empty")
        self.swarm_id = str(swarm_id)
        self.max_messages = max(1, int(max_messages))
        self.max_content_chars = max(1, int(max_content_chars))
        self.max_idempotency_keys = max(1, int(max_idempotency_keys))
        self.max_data_chars = max(1, int(max_data_chars))
        self.blackboard = blackboard or FederatedBlackboard()
        self._lock = threading.RLock()
        self._messages: list[SwarmMessage] = []
        self._idempotency: dict[str, str] = {}
        self._subscribers: dict[int, tuple[str, queue.Queue[SwarmMessage]]] = {}
        self._next_subscriber_id = 0
        for raw in messages or ():
            try:
                message = raw if isinstance(raw, SwarmMessage) else SwarmMessage.from_dict(raw)
                if message.swarm_id != self.swarm_id or not message.message_id or not message.topic or len(message.topic) > 128 or not message.sender or len(message.sender) > 128 or not message.kind or len(message.kind) > 64:
                    continue
                text = str(message.content or "")
                if len(text) > self.max_content_chars:
                    marker = "\n[truncated by swarm message bound]"
                    text = (text[: max(0, self.max_content_chars - len(marker))] + marker)[: self.max_content_chars]
                data = dict(message.data) if isinstance(message.data, Mapping) else {}
                try:
                    encoded_data = json.dumps(data, ensure_ascii=False)
                except (TypeError, ValueError):
                    data = {"invalid_data": True}
                    encoded_data = json.dumps(data)
                if len(encoded_data) > self.max_data_chars:
                    data = {"truncated": True, "original_chars": len(encoded_data)}
                if message.trust not in {"untrusted", "internal"}:
                    message = replace(message, trust="untrusted")
                if text != message.content or data != message.data:
                    message = replace(message, content=text, data=data)
                self._append_loaded(message)
            except (TypeError, ValueError, AttributeError):
                # A corrupt persisted message must not bypass the message
                # bounds or prevent the rest of a swarm from recovering.
                continue

    def _append_loaded(self, message: SwarmMessage) -> None:
        self._messages.append(message)
        if len(self._messages) > self.max_messages:
            del self._messages[: len(self._messages) - self.max_messages]
        if message.idempotency_key:
            self._idempotency[message.idempotency_key] = message.message_id
            while len(self._idempotency) > self.max_idempotency_keys:
                self._idempotency.pop(next(iter(self._idempotency)))
        self.blackboard.write_entry(
            f"swarm:{self.swarm_id}:{message.topic}",
            message.message_id,
            message.to_dict(),
            agent_id=message.sender,
        )

    def _add_subscriber(self, subscription: SwarmSubscription) -> callable:
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            # The subscription owns the queue; keep a bounded reference here.
            self._subscribers[subscriber_id] = (subscription.agent_id, getattr(subscription, "_queue"))
            return lambda: self._remove_subscriber(subscriber_id)

    def _remove_subscriber(self, subscriber_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def subscribe(self, agent_id: str, *, max_queue: int = 64) -> SwarmSubscription:
        if not agent_id or not agent_id.strip():
            raise SwarmMessageValidationError("agent_id must be non-empty")
        return SwarmSubscription(self, agent_id, max_queue=max_queue)

    def publish(
        self,
        *,
        topic: str,
        sender: str,
        kind: str = "observation",
        content: str = "",
        data: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        trust: str = "untrusted",
        idempotency_key: str | None = None,
    ) -> SwarmMessage:
        """Publish one message, returning the existing message on idempotent retry."""

        topic = str(topic or "").strip()
        sender = str(sender or "").strip()
        kind = str(kind or "observation").strip()
        trust = str(trust or "untrusted").strip()
        if not topic or len(topic) > 128:
            raise SwarmMessageValidationError("topic must be 1..128 characters")
        if not sender or len(sender) > 128:
            raise SwarmMessageValidationError("sender must be 1..128 characters")
        if not kind or len(kind) > 64:
            raise SwarmMessageValidationError("kind must be 1..64 characters")
        if trust not in {"untrusted", "internal"}:
            raise SwarmMessageValidationError("trust must be untrusted or internal; verification is server-derived")
        text = str(content or "")
        if len(text) > self.max_content_chars:
            marker = "\n[truncated by swarm message bound]"
            text = (text[: max(0, self.max_content_chars - len(marker))] + marker)[: self.max_content_chars]
        payload = dict(data or {})
        try:
            encoded_payload = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise SwarmMessageValidationError(f"message data must be JSON serializable: {exc}") from exc
        if len(encoded_payload) > self.max_data_chars:
            raise SwarmMessageValidationError(f"message data exceeds the {self.max_data_chars}-character swarm bound")
        if idempotency_key is not None:
            idempotency_key = str(idempotency_key).strip() or None
            if idempotency_key and len(idempotency_key) > 256:
                raise SwarmMessageValidationError("idempotency_key is too long")

        with self._lock:
            if idempotency_key and idempotency_key in self._idempotency:
                existing_id = self._idempotency[idempotency_key]
                for existing in reversed(self._messages):
                    if existing.message_id == existing_id:
                        return existing
                # The bounded window evicted the original; retain a small
                # tombstone mapping and report a clear duplicate refusal.
                raise SwarmMessageValidationError("idempotent message is no longer in the retained window")
            sequence = (self._messages[-1].sequence + 1) if self._messages else 1
            message = SwarmMessage(
                message_id=f"msg-{uuid.uuid4().hex[:12]}",
                swarm_id=self.swarm_id,
                sequence=sequence,
                topic=topic,
                sender=sender,
                kind=kind,
                content=text,
                data=payload,
                task_id=str(task_id) if task_id else None,
                trust=trust,
                idempotency_key=idempotency_key,
                created_at=time.time(),
            )
            self._append_loaded(message)
            for _subscriber_id, (_agent_id, subscriber_queue) in self._subscribers.items():
                try:
                    subscriber_queue.put_nowait(message)
                except queue.Full:
                    # A slow consumer must not block producers.  Its queue is
                    # bounded and the durable message remains readable by poll.
                    continue
            return message

    def read(
        self,
        *,
        topic: str | None = None,
        task_id: str | None = None,
        since_sequence: int = 0,
        limit: int = 50,
    ) -> list[SwarmMessage]:
        limit = max(1, min(int(limit), self.max_messages))
        with self._lock:
            result = [message for message in self._messages if message.sequence > int(since_sequence) and (topic is None or message.topic == topic) and (task_id is None or message.task_id == task_id)]
            return result[-limit:]

    def context_for(self, task_id: str, context_refs: Iterable[str] | None = None, *, limit: int = 12) -> list[dict[str, Any]]:
        """Return a bounded, explicitly untrusted context slice for a worker."""

        refs = {str(ref) for ref in (context_refs or []) if ref}
        with self._lock:
            candidates = [message for message in self._messages if message.task_id in (None, task_id) or message.topic in refs or message.message_id in refs]
            selected = candidates[-max(1, min(int(limit), self.max_messages)) :]
            return [
                {
                    "message_id": message.message_id,
                    "sequence": message.sequence,
                    "topic": message.topic,
                    "sender": message.sender,
                    "kind": message.kind,
                    "content": message.content[:4000],
                    "task_id": message.task_id,
                    "trust": message.trust,
                }
                for message in selected
            ]

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "swarm_id": self.swarm_id,
                "next_sequence": (self._messages[-1].sequence + 1) if self._messages else 1,
                "messages": [message.to_dict() for message in self._messages],
            }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None, *, swarm_id: str = "") -> SwarmMessageBus:
        data = data or {}
        return cls(
            str(data.get("swarm_id") or swarm_id),
            messages=(raw for raw in data.get("messages", []) if isinstance(raw, Mapping)),
        )

    def count(self, *, topic: str | None = None) -> int:
        with self._lock:
            return sum(1 for message in self._messages if topic is None or message.topic == topic)
