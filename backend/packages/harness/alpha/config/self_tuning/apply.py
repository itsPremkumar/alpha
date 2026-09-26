"""Atomic, serialized, recoverable config application.

The transaction follows the prepare/validate/promote pattern in Google SRE's
*Release Engineering*: a candidate is written beside the live file, validated
through the complete configuration model, and only then atomically replaced.
A durable pending marker keeps the exact previous bytes until health evidence
is confirmed.  Startup recovery restores those bytes after an interrupted
transaction, implementing the fail-closed rollback guidance in the AWS
Builders Library's *Making retries safe with idempotent APIs*.

The write lock is both in-process and OS-backed.  Every proposal revalidates
its observed previous value after acquiring the lock, so a serialized loser is
told it lost instead of overwriting the winner.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal, Protocol, runtime_checkable

import yaml

from ._locking import atomic_write_bytes as _atomic_write
from ._locking import fsync_directory as _fsync_directory
from ._locking import os_file_lock as _os_file_lock
from .config import SelfTuningConfig
from .models import ApplyOutcome, ApplyResult, ChangeSet, Clock, ValidationErrorCode
from .targets import TargetRegistry
from .validate import ConfigChangeValidator, changes_already_applied, materialize_validated_changes

ApplyStage = Literal["after_temp_write", "before_atomic_replace", "after_atomic_replace", "after_reload"]
FaultInjectionHook = Callable[[ApplyStage, ChangeSet], None]
JournalStage = Literal["prepared", "applied", "verified"]


@runtime_checkable
class ConfigFileLoader(Protocol):
    """Injected validator for a concrete candidate file path."""

    def __call__(self, path: Path) -> object:
        """Load and fully validate the file, raising on any failure."""
        ...


@dataclass(frozen=True, slots=True)
class AppliedTransaction:
    """In-memory handle for the exact pre/post bytes of an applied change."""

    change_set_id: str
    config_path: Path
    previous_content: bytes
    applied_content: bytes
    backup_path: Path
    journal_path: Path


@dataclass(frozen=True, slots=True)
class _Journal:
    change_set_id: str
    config_path: str
    backup_path: str
    previous_sha256: str
    candidate_sha256: str
    stage: JournalStage


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_config_file(path: Path) -> object:
    """Side-effect-free equivalent of the real full AppConfig model validation."""
    from alpha.config.app_config import CONFIG_FILE_DATABASE_DEFAULTS, AppConfig

    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    if not isinstance(document, dict):
        raise ValueError("full config root must be a mapping")
    document = AppConfig.resolve_env_variables(document)
    database = document.get("database")
    if database is None:
        database = {}
        document["database"] = database
    if isinstance(database, dict):
        for key, value in CONFIG_FILE_DATABASE_DEFAULTS.items():
            database.setdefault(key, value)
    return AppConfig.model_validate(document)


class AtomicConfigApplier:
    """Apply a validated proposal transaction with a durable pending marker."""

    def __init__(
        self,
        config_path: Path,
        config: SelfTuningConfig,
        clock: Clock,
        *,
        validator: ConfigChangeValidator | None = None,
        registry: TargetRegistry | None = None,
        config_file_loader: ConfigFileLoader | None = None,
        fault_hook: FaultInjectionHook | None = None,
    ) -> None:
        self.config_path = config_path.resolve()
        self.config = config
        self.clock = clock
        self.registry = registry or TargetRegistry(config)
        self.validator = validator or ConfigChangeValidator(config, registry=self.registry)
        self.config_file_loader = config_file_loader or validate_config_file
        self.fault_hook = fault_hook
        self._transactions: dict[str, AppliedTransaction] = {}
        self._thread_locks: dict[Path, RLock] = {}
        self._thread_locks_guard = RLock()

    @property
    def journal_path(self) -> Path:
        return self.config_path.with_name(f".{self.config_path.name}.self-tuning.pending.json")

    @property
    def lock_path(self) -> Path:
        return self.config_path.with_name(f".{self.config_path.name}.self-tuning.lock")

    def _thread_lock(self) -> RLock:
        with self._thread_locks_guard:
            return self._thread_locks.setdefault(self.config_path, RLock())

    def _hook(self, stage: ApplyStage, change_set: ChangeSet) -> None:
        if self.fault_hook is not None:
            self.fault_hook(stage, change_set)

    def _result(self, change_set: ChangeSet, status: ApplyOutcome, reason: str, *, verified: bool = False) -> ApplyResult:
        return ApplyResult(change_set_id=change_set.id, status=status, reason=reason, at=self.clock.now(), verified_reload=verified)

    def _read_document(self, content: bytes) -> dict[str, Any]:
        document = yaml.safe_load(content.decode("utf-8")) or {}
        if not isinstance(document, dict):
            raise ValueError("config root must be a mapping")
        return document

    def _write_journal(self, journal: _Journal) -> None:
        _atomic_write(self.journal_path, (json.dumps(asdict(journal), sort_keys=True) + "\n").encode("utf-8"))

    def _read_journal(self) -> _Journal | None:
        if not self.journal_path.exists():
            return None
        payload = json.loads(self.journal_path.read_text(encoding="utf-8"))
        return _Journal(
            change_set_id=str(payload["change_set_id"]),
            config_path=str(payload["config_path"]),
            backup_path=str(payload["backup_path"]),
            previous_sha256=str(payload["previous_sha256"]),
            candidate_sha256=str(payload["candidate_sha256"]),
            stage=payload["stage"],
        )

    def _cleanup_transaction(self, journal: _Journal) -> None:
        Path(journal.backup_path).unlink(missing_ok=True)
        self.journal_path.unlink(missing_ok=True)
        self._transactions.pop(journal.change_set_id, None)

    def recover_pending(self) -> ApplyResult | None:
        """Restore an interrupted unverified transaction to known-good bytes.

        Call this during Gateway startup before self-tuning is exposed.  A
        health-confirmed transaction is only cleaned up; it is never rewritten.
        """
        with self._thread_lock(), _os_file_lock(self.lock_path):
            journal = self._read_journal()
            if journal is None:
                return None
            if Path(journal.config_path).resolve() != self.config_path:
                raise RuntimeError("pending self-tuning journal points at a different config path")
            current = self.config_path.read_bytes()
            current_digest = _digest(current)
            if current_digest == journal.previous_sha256:
                self._cleanup_transaction(journal)
                return None
            if current_digest == journal.candidate_sha256:
                if journal.stage == "verified":
                    self._cleanup_transaction(journal)
                    return None
                previous = Path(journal.backup_path).read_bytes()
                if _digest(previous) != journal.previous_sha256:
                    raise RuntimeError("pending rollback backup digest does not match its journal")
                _atomic_write(self.config_path, previous)
                self.config_file_loader(self.config_path)
                self._cleanup_transaction(journal)
                change_set = journal.change_set_id
                return ApplyResult(
                    change_set_id=change_set,
                    status=ApplyOutcome.ROLLED_BACK,
                    reason="recovered interrupted transaction by restoring the exact previous config",
                    at=self.clock.now(),
                )
            raise RuntimeError("config content matches neither pending transaction side; refusing ambiguous recovery")

    def apply(self, change_set: ChangeSet, *, allow_pending: bool = False) -> ApplyResult:
        """Serialize, validate, atomically replace, and reload one proposal.

        ``allow_pending`` is reserved for the recorded rollback path, which must
        be able to reverse the still-unconfirmed transaction it is rolling back.
        """
        with self._thread_lock(), _os_file_lock(self.lock_path):
            try:
                previous = self.config_path.read_bytes()
                current = self._read_document(previous)
            except Exception as exc:
                return self._result(change_set, ApplyOutcome.FAILED, f"known-good config could not be read: {exc}")

            section = current.get("self_tuning")
            if not self.config.enabled or not isinstance(section, Mapping) or section.get("enabled") is not True:
                return self._result(change_set, ApplyOutcome.NO_OP, "self_tuning_disabled")

            validation = self.validator.validate(change_set, current)
            stale = next((error for error in validation.errors if error.code is ValidationErrorCode.STALE_PREVIOUS_VALUE), None)
            if stale is not None:
                return self._result(change_set, ApplyOutcome.FAILED, f"concurrent_update_lost: {stale.message}")
            if not validation.ok:
                status = ApplyOutcome.NEEDS_HUMAN if validation.refusals else ApplyOutcome.FAILED
                return self._result(change_set, status, f"pre-apply validation refused change: {validation.reason}")
            if not allow_pending and self.journal_path.exists():
                return self._result(change_set, ApplyOutcome.FAILED, "pending_transaction_unverified: recover or resolve the existing change before applying another")
            if changes_already_applied(current, change_set, self.registry):
                return self._result(change_set, ApplyOutcome.NO_OP, "proposed values already match the current config")

            try:
                candidate = materialize_validated_changes(current, change_set, self.registry)
            except Exception as exc:
                return self._result(change_set, ApplyOutcome.FAILED, f"validated change could not be materialized: {exc}")
            if candidate == current:
                return self._result(change_set, ApplyOutcome.NO_OP, "proposed values already match the current config")

            serialized = yaml.safe_dump(candidate, allow_unicode=True, default_flow_style=False, sort_keys=False).encode("utf-8")
            transaction_id = hashlib.sha256(f"{change_set.id}:{_digest(previous)}:{_digest(serialized)}".encode()).hexdigest()[:20]
            backup_path = self.config_path.with_name(f".{self.config_path.name}.self-tuning.{transaction_id}.rollback")
            journal = _Journal(
                change_set_id=change_set.id,
                config_path=str(self.config_path),
                backup_path=str(backup_path),
                previous_sha256=_digest(previous),
                candidate_sha256=_digest(serialized),
                stage="prepared",
            )

            descriptor, raw_temp = tempfile.mkstemp(prefix=f".{self.config_path.name}.{transaction_id}.", suffix=".candidate", dir=self.config_path.parent)
            temp_path = Path(raw_temp)
            replaced = False
            descriptor_open = True
            try:
                _atomic_write(backup_path, previous)
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor_open = False
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._hook("after_temp_write", change_set)

                self.config_file_loader(temp_path)
                actual_temp = temp_path.read_bytes()
                if _digest(actual_temp) != journal.candidate_sha256:
                    raise RuntimeError("candidate file changed during validation")
                self._hook("before_atomic_replace", change_set)

                self._write_journal(journal)
                os.replace(temp_path, self.config_path)
                replaced = True
                _fsync_directory(self.config_path.parent)
                self._hook("after_atomic_replace", change_set)

                self.config_file_loader(self.config_path)
                if _digest(self.config_path.read_bytes()) != journal.candidate_sha256:
                    raise RuntimeError("canonical config bytes differ from the validated candidate")
                self._hook("after_reload", change_set)
                applied_journal = _Journal(**{**asdict(journal), "stage": "applied"})
                self._write_journal(applied_journal)
                self._transactions[change_set.id] = AppliedTransaction(
                    change_set_id=change_set.id,
                    config_path=self.config_path,
                    previous_content=previous,
                    applied_content=serialized,
                    backup_path=backup_path,
                    journal_path=self.journal_path,
                )
                return self._result(change_set, ApplyOutcome.APPLIED, "candidate atomically replaced and fully reloaded", verified=True)
            except Exception as exc:
                reason = f"atomic apply failed: {exc}"
                if replaced:
                    try:
                        rollback_bytes = backup_path.read_bytes()
                        _atomic_write(self.config_path, rollback_bytes)
                        self.config_file_loader(self.config_path)
                        reason = f"{reason}; restored exact previous config"
                    except Exception as rollback_exc:
                        return self._result(change_set, ApplyOutcome.FAILED, f"{reason}; rollback restore failed: {rollback_exc}")
                    finally:
                        if self.journal_path.exists():
                            self._cleanup_transaction(journal)
                    return self._result(change_set, ApplyOutcome.ROLLED_BACK, reason)
                backup_path.unlink(missing_ok=True)
                self.journal_path.unlink(missing_ok=True)
                return self._result(change_set, ApplyOutcome.FAILED, reason)
            finally:
                if descriptor_open:
                    os.close(descriptor)
                temp_path.unlink(missing_ok=True)

    def confirm(self, change_set_id: str) -> None:
        """Commit a healthy transaction and remove its rollback material."""
        with self._thread_lock(), _os_file_lock(self.lock_path):
            journal = self._read_journal()
            if journal is None or journal.change_set_id != change_set_id:
                raise RuntimeError(f"no pending self-tuning transaction for {change_set_id!r}")
            if _digest(self.config_path.read_bytes()) != journal.candidate_sha256:
                raise RuntimeError("cannot confirm a config that no longer matches the applied candidate")
            self._write_journal(_Journal(**{**asdict(journal), "stage": "verified"}))
            self._cleanup_transaction(journal)

    def transaction(self, change_set_id: str) -> AppliedTransaction:
        """Return exact previous/applied bytes for an unconfirmed transaction."""
        try:
            return self._transactions[change_set_id]
        except KeyError as exc:
            raise KeyError(f"no in-memory transaction for {change_set_id!r}") from exc
