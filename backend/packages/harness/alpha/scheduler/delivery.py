"""Exactly-once delivery ledger for scheduled occurrences.

The scheduler decides *when* work runs; this ledger records what each
occurrence produced and where it was delivered, so crash-replay delivers
once and operators can audit every firing.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DeliveryStatus = Literal["pending", "delivered", "failed", "skipped"]


def _default_storage_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "scheduler" / "delivery.json"
    except Exception:
        return Path.cwd() / ".alpha" / "scheduler" / "delivery.json"


@dataclass
class DeliveryRecord:
    delivery_id: str
    task_id: str
    occurrence_id: str
    status: DeliveryStatus = "pending"
    artifact_ref: str | None = None
    channel: str | None = None
    attempts: int = 0
    last_error: str | None = None
    created_at: float = field(default_factory=time.time)
    delivered_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeliveryRecord:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class DeliveryLedger:
    """Idempotent per-occurrence ledger: one record per occurrence id."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._by_occurrence: dict[str, DeliveryRecord] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in data.get("deliveries", []):
                r = DeliveryRecord.from_dict(item)
                self._by_occurrence[r.occurrence_id] = r
        except Exception:
            logger.warning("Delivery ledger load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "deliveries": [r.to_dict() for r in self._by_occurrence.values()]}, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Delivery ledger save failed", exc_info=True)

    def claim(self, task_id: str, occurrence_id: str) -> tuple[DeliveryRecord, bool]:
        """Return (record, is_new). Replays of the same occurrence get the same row."""
        with self._lock:
            existing = self._by_occurrence.get(occurrence_id)
            if existing:
                return existing, False
            rec = DeliveryRecord(delivery_id=f"dlv-{uuid.uuid4().hex[:10]}", task_id=task_id, occurrence_id=occurrence_id)
            self._by_occurrence[occurrence_id] = rec
            self._save()
            return rec, True

    def mark(self, occurrence_id: str, status: DeliveryStatus, *, artifact_ref: str | None = None, channel: str | None = None, error: str | None = None) -> DeliveryRecord | None:
        with self._lock:
            rec = self._by_occurrence.get(occurrence_id)
            if not rec:
                return None
            if rec.status == "delivered" and status == "delivered":
                return rec
            rec.status = status
            rec.attempts += 1
            if artifact_ref is not None:
                rec.artifact_ref = artifact_ref
            if channel is not None:
                rec.channel = channel
            if error is not None:
                rec.last_error = error
            if status == "delivered":
                rec.delivered_at = time.time()
            self._save()
            return rec

    def get(self, occurrence_id: str) -> DeliveryRecord | None:
        with self._lock:
            return self._by_occurrence.get(occurrence_id)

    def for_task(self, task_id: str) -> list[DeliveryRecord]:
        with self._lock:
            return [r for r in self._by_occurrence.values() if r.task_id == task_id]

    def pending(self) -> list[DeliveryRecord]:
        with self._lock:
            return [r for r in self._by_occurrence.values() if r.status == "pending"]


_ledger: DeliveryLedger | None = None
_ledger_path: str | None = None
_ledger_lock = threading.Lock()


def get_delivery_ledger() -> DeliveryLedger:
    global _ledger, _ledger_path
    with _ledger_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _ledger is None or _ledger_path != live:
            _ledger = DeliveryLedger()
            try:
                _ledger_path = str(_ledger.storage_path.resolve())
            except Exception:
                _ledger_path = live
        return _ledger
