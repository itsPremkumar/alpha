from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, replace
from itertools import islice
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .models import OUTCOMES, ActionIntent, ActionOutcome, ActionReceipt, ActionStatus, ErrorCategory, validate_text

MAX_LIST_LIMIT = 500
_STORE_LOCKS: dict[str, Any] = {}
_STORE_LOCKS_GUARD = RLock()


def _canonical_digest(args: dict[str, Any]) -> str:
    def validate(value: Any) -> None:
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("Arguments must be JSON")
                validate(item)
        elif type(value) is list:
            for item in value:
                validate(item)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("Arguments must be JSON")

    try:
        if type(args) is not dict:
            raise ValueError("Arguments must be a JSON object")
        validate(args)
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Arguments must be a finite JSON object") from None


def _bounded_limit(limit: int) -> int:
    if type(limit) is not int or limit < 0:
        raise ValueError("Invalid limit")
    return min(limit, MAX_LIST_LIMIT)


def default_action_ledger() -> ActionLedger:
    """Ledger rooted in the writable Alpha state directory (per host).

    Added by wave P3 so real action paths (e.g. the ESTOP tool) write
    intent/receipt pairs without each inventing its own storage path.
    """
    from alpha.config.runtime_paths import runtime_home

    return ActionLedger(runtime_home() / "action-ledger")


class ActionLedger:
    def __init__(self, storage_dir: str | Path) -> None:
        self._path = Path(storage_dir).resolve() / "actions.jsonl"
        key = os.path.normcase(str(self._path))
        with _STORE_LOCKS_GUARD:
            self._lock = _STORE_LOCKS.setdefault(key, RLock())
        self._intents: dict[str, dict[str, ActionIntent]] = {}
        self._receipts: dict[str, dict[str, ActionReceipt]] = {}
        self._offset = 0
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._refresh()

    def _refresh(self) -> None:
        try:
            stream = self._path.open("rb")
        except FileNotFoundError:
            if self._offset:
                raise OSError("Ledger disappeared") from None
            return
        with stream:
            if os.fstat(stream.fileno()).st_size < self._offset:
                raise OSError("Ledger was truncated")
            stream.seek(self._offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                try:
                    self._index(json.loads(line))
                except (ValueError, TypeError, KeyError, UnicodeError):
                    pass
                self._offset = stream.tell()

    def _index(self, envelope: dict[str, Any]) -> None:
        if not isinstance(envelope, dict):
            raise ValueError("Invalid ledger record")
        owner = envelope["owner_id"]
        validate_text(owner, "owner_id")
        record = envelope["record"]
        if envelope["type"] == "intent":
            intent = ActionIntent(**record)
            if intent.owner_id != owner or intent.status != "pending":
                raise ValueError("Invalid intent ownership or status")
            intents = self._intents.setdefault(owner, {})
            if intent.id in intents:
                raise ValueError("Duplicate intent")
            intents[intent.id] = intent
        elif envelope["type"] == "receipt":
            receipt = ActionReceipt(**record)
            intent = self._intents.get(owner, {}).get(receipt.intent_id)
            if intent is None or intent.status != "pending" or receipt.started_at < intent.created_at:
                raise ValueError("Invalid receipt reference")
            self._receipts.setdefault(owner, {})[intent.id] = receipt
            self._intents[owner][intent.id] = replace(intent, status=receipt.outcome)
        else:
            raise ValueError("Invalid ledger record type")

    def _append(self, owner_id: str, kind: str, record: ActionIntent | ActionReceipt) -> None:
        envelope = {"type": kind, "owner_id": owner_id, "record": asdict(record)}
        data = json.dumps(envelope, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._path.open("ab") as stream:
            if stream.tell() > self._offset:
                data = b"\n" + data
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        self._refresh()

    def record_intent(self, owner_id: str, tool_name: str, args: dict[str, Any], *, thread_id: str | None = None) -> ActionIntent:
        validate_text(owner_id, "owner_id")
        validate_text(tool_name, "tool_name")
        if thread_id is not None:
            validate_text(thread_id, "thread_id")
        digest = _canonical_digest(args)
        with self._lock:
            self._refresh()
            intent = ActionIntent(id=uuid4().hex, owner_id=owner_id, tool_name=tool_name, arguments_digest=digest, created_at=time.time(), thread_id=thread_id)
            self._append(owner_id, "intent", intent)
            return intent

    def record_receipt(
        self,
        intent_id: str,
        outcome: ActionOutcome,
        *,
        owner_id: str,
        error_category: ErrorCategory | None = None,
        started_at: float | None = None,
        completed_at: float | None = None,
        exit_ref: str | None = None,
    ) -> ActionReceipt:
        validate_text(owner_id, "owner_id")
        validate_text(intent_id, "intent_id")
        with self._lock:
            self._refresh()
            intent = self._intents.get(owner_id, {}).get(intent_id)
            if intent is None:
                raise LookupError("Intent not found")
            if intent.status != "pending":
                raise ValueError("Intent already completed")
            now = time.time()
            receipt = ActionReceipt(
                intent_id=intent_id,
                outcome=outcome,
                started_at=now if started_at is None else started_at,
                completed_at=now if completed_at is None else completed_at,
                error_category=error_category,
                exit_ref=exit_ref,
            )
            if receipt.started_at < intent.created_at:
                raise ValueError("Start precedes intent creation")
            self._append(owner_id, "receipt", receipt)
            return receipt

    def list_intents(
        self,
        owner_id: str,
        *,
        tool_name: str | None = None,
        thread_id: str | None = None,
        status: ActionStatus | None = None,
        limit: int = 100,
    ) -> list[ActionIntent]:
        validate_text(owner_id, "owner_id")
        limit = _bounded_limit(limit)
        self._validate_filters(tool_name, thread_id)
        if status is not None and status not in ("pending", *OUTCOMES):
            raise ValueError("Invalid status")
        with self._lock:
            self._refresh()
            matches = (
                intent
                for intent in reversed(self._intents.get(owner_id, {}).values())
                if (tool_name is None or intent.tool_name == tool_name) and (thread_id is None or intent.thread_id == thread_id) and (status is None or intent.status == status)
            )
            return list(islice(matches, limit))

    def list_receipts(
        self,
        owner_id: str,
        *,
        intent_id: str | None = None,
        outcome: ActionOutcome | None = None,
        tool_name: str | None = None,
        thread_id: str | None = None,
        limit: int = 100,
    ) -> list[ActionReceipt]:
        validate_text(owner_id, "owner_id")
        limit = _bounded_limit(limit)
        self._validate_filters(tool_name, thread_id)
        if intent_id is not None:
            validate_text(intent_id, "intent_id")
        if outcome is not None and outcome not in OUTCOMES:
            raise ValueError("Invalid outcome")
        with self._lock:
            self._refresh()
            intents = self._intents.get(owner_id, {})
            matches = (
                receipt
                for receipt in reversed(self._receipts.get(owner_id, {}).values())
                if (intent_id is None or receipt.intent_id == intent_id)
                and (outcome is None or receipt.outcome == outcome)
                and (tool_name is None or intents[receipt.intent_id].tool_name == tool_name)
                and (thread_id is None or intents[receipt.intent_id].thread_id == thread_id)
            )
            return list(islice(matches, limit))

    @staticmethod
    def _validate_filters(tool_name: str | None, thread_id: str | None) -> None:
        if tool_name is not None:
            validate_text(tool_name, "tool_name")
        if thread_id is not None:
            validate_text(thread_id, "thread_id")
