from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from alpha.goals.models import AttemptStatus, GoalContract, GoalStatus, NonEmptyString, PlanVersion, TaskAttempt


class InvalidTransitionError(ValueError):
    pass


class RecordNotFoundError(LookupError):
    pass


class StoreCorruptionError(ValueError):
    pass


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(ge=1, le=1)
    owner_id: NonEmptyString
    contracts: list[GoalContract]
    plans: list[PlanVersion]
    attempts: list[TaskAttempt]

    @model_validator(mode="after")
    def _validate_relations(self):
        records = [*self.contracts, *self.plans, *self.attempts]
        if len({record.id for record in records}) != len(records):
            raise ValueError("Duplicate record ID")
        if any(record.owner_id != self.owner_id for record in records):
            raise ValueError("Owner mismatch")
        contracts = {contract.id for contract in self.contracts}
        plans = {plan.id for plan in self.plans}
        versions: dict[str, set[int]] = {}
        approved: set[str] = set()
        for plan in self.plans:
            if plan.contract_id not in contracts:
                raise ValueError("Missing contract")
            contract_versions = versions.setdefault(plan.contract_id, set())
            if plan.version in contract_versions:
                raise ValueError("Duplicate plan version")
            contract_versions.add(plan.version)
            if plan.status == "approved":
                if plan.contract_id in approved:
                    raise ValueError("Multiple approved plans")
                approved.add(plan.contract_id)
        if any(sorted(items) != list(range(1, len(items) + 1)) for items in versions.values()):
            raise ValueError("Non-contiguous plan versions")
        if any(attempt.plan_id not in plans for attempt in self.attempts):
            raise ValueError("Missing plan")
        return self


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


class GoalStore:
    def __init__(self, storage_dir: str | Path) -> None:
        self._storage_dir = Path(storage_dir).resolve()
        self._lock = threading.RLock()

    @contextmanager
    def _operation(self, owner_id: str, *, write: bool = False) -> Iterator[_Snapshot]:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must be a non-empty string")
        with self._lock:
            path = self._storage_dir / f"{hashlib.sha256(owner_id.encode('utf-8')).hexdigest()}.json"
            state = self._load(path, owner_id)
            yield state
            if write:
                self._save(path, state)

    def _load(self, path: Path, owner_id: str) -> _Snapshot:
        try:
            payload = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _Snapshot(schema_version=1, owner_id=owner_id, contracts=[], plans=[], attempts=[])
        except UnicodeError as exc:
            raise StoreCorruptionError("Invalid goal store snapshot") from exc
        try:
            data = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            state = _Snapshot.model_validate(data)
            if state.owner_id != owner_id:
                raise ValueError("Owner mismatch")
            return state
        except (ValueError, TypeError, RecursionError) as exc:
            raise StoreCorruptionError("Invalid goal store snapshot") from exc

    def _save(self, path: Path, state: _Snapshot) -> None:
        payload = json.dumps(state.model_dump(mode="python"), indent=2, allow_nan=False)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._storage_dir, prefix=".goals-", suffix=".tmp", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                # Cleanup must never replace the real failure: an exception
                # raised from a ``finally`` block suppresses the in-flight one,
                # so a locked temp file (Windows) would be reported instead of
                # the actual write/replace error. Mirrors ``skills/proposals.py``.
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _find[T: (GoalContract, PlanVersion, TaskAttempt)](records: list[T], record_id: str) -> T:
        for record in records:
            if record.id == record_id:
                return record
        raise RecordNotFoundError("Record not found")

    @staticmethod
    def _replace[T: (GoalContract, PlanVersion, TaskAttempt)](records: list[T], record: T, status: str) -> T:
        updated = type(record).model_validate({**record.model_dump(), "status": status, "updated_at": max(time.time(), record.updated_at)})
        records[records.index(record)] = updated
        return updated

    @staticmethod
    def _require_active(contract: GoalContract) -> None:
        if contract.status != "active":
            raise InvalidTransitionError("Contract is not active")

    def _require_approved(self, state: _Snapshot, plan: PlanVersion) -> None:
        self._require_active(self._find(state.contracts, plan.contract_id))
        if plan.status != "approved":
            raise InvalidTransitionError("Plan is not approved")

    def create_contract(self, objective: str, *, owner_id: str) -> GoalContract:
        with self._operation(owner_id, write=True) as state:
            now = time.time()
            contract = GoalContract(id=uuid.uuid4().hex, objective=objective, owner_id=owner_id, status="active", created_at=now, updated_at=now)
            state.contracts.append(contract)
            return contract

    def get_contract(self, contract_id: str, *, owner_id: str) -> GoalContract:
        with self._operation(owner_id) as state:
            return self._find(state.contracts, contract_id)

    def list_contracts(self, *, owner_id: str) -> list[GoalContract]:
        with self._operation(owner_id) as state:
            return state.contracts

    def set_contract_status(self, contract_id: str, status: GoalStatus, *, owner_id: str) -> GoalContract:
        with self._operation(owner_id, write=True) as state:
            contract = self._find(state.contracts, contract_id)
            self._require_active(contract)
            if status not in ("achieved", "abandoned"):
                raise InvalidTransitionError(f"Illegal contract transition: {contract.status} -> {status}")
            return self._replace(state.contracts, contract, status)

    def create_plan(self, contract_id: str, content: dict[str, JsonValue], *, owner_id: str) -> PlanVersion:
        with self._operation(owner_id, write=True) as state:
            self._require_active(self._find(state.contracts, contract_id))
            now = time.time()
            version = max((plan.version for plan in state.plans if plan.contract_id == contract_id), default=0) + 1
            plan = PlanVersion(id=uuid.uuid4().hex, contract_id=contract_id, owner_id=owner_id, version=version, content=content, status="draft", created_at=now, updated_at=now)
            state.plans.append(plan)
            return plan.model_copy(deep=True)

    def get_plan(self, plan_id: str, *, owner_id: str) -> PlanVersion:
        with self._operation(owner_id) as state:
            return self._find(state.plans, plan_id)

    def list_plans(self, contract_id: str, *, owner_id: str) -> list[PlanVersion]:
        with self._operation(owner_id) as state:
            self._find(state.contracts, contract_id)
            return sorted((plan for plan in state.plans if plan.contract_id == contract_id), key=lambda plan: plan.version)

    def approve_plan(self, plan_id: str, *, owner_id: str) -> PlanVersion:
        with self._operation(owner_id, write=True) as state:
            plan = self._find(state.plans, plan_id)
            self._require_active(self._find(state.contracts, plan.contract_id))
            if plan.status != "draft":
                raise InvalidTransitionError(f"Illegal plan transition: {plan.status} -> approved")
            for prior in list(state.plans):
                if prior.contract_id == plan.contract_id and prior.status == "approved":
                    self._replace(state.plans, prior, "superseded")
            return self._replace(state.plans, plan, "approved")

    def create_attempt(self, plan_id: str, intent: str, *, owner_id: str) -> TaskAttempt:
        with self._operation(owner_id, write=True) as state:
            self._require_approved(state, self._find(state.plans, plan_id))
            now = time.time()
            attempt = TaskAttempt(id=uuid.uuid4().hex, plan_id=plan_id, intent=intent, owner_id=owner_id, status="pending", created_at=now, updated_at=now)
            state.attempts.append(attempt)
            return attempt

    def get_attempt(self, attempt_id: str, *, owner_id: str) -> TaskAttempt:
        with self._operation(owner_id) as state:
            return self._find(state.attempts, attempt_id)

    def list_attempts(self, plan_id: str, *, owner_id: str) -> list[TaskAttempt]:
        with self._operation(owner_id) as state:
            self._find(state.plans, plan_id)
            return [attempt for attempt in state.attempts if attempt.plan_id == plan_id]

    def transition_attempt(self, attempt_id: str, status: AttemptStatus, *, owner_id: str) -> TaskAttempt:
        with self._operation(owner_id, write=True) as state:
            attempt = self._find(state.attempts, attempt_id)
            allowed = {"pending": ("running", "cancelled"), "running": ("succeeded", "failed", "cancelled")}
            if status not in allowed.get(attempt.status, ()):
                raise InvalidTransitionError(f"Illegal attempt transition: {attempt.status} -> {status}")
            if status == "running":
                self._require_approved(state, self._find(state.plans, attempt.plan_id))
            return self._replace(state.attempts, attempt, status)
