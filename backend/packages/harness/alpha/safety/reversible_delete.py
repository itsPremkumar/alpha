"""Approval-gated, reversible file quarantine for autonomous mutations.

Deletion is never hard-delete in this module. A plan is read-only with respect
to user files; execution requires an explicit approval reference, moves targets
into a local quarantine vault, verifies target fingerprints, and records a
receipt that can restore the files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Any, Literal

MAX_BATCH_FILES = 10
MAX_TREE_FILES = 10_000
MAX_TREE_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024

logger = logging.getLogger(__name__)

DeleteStatus = Literal["planned", "approval_required", "quarantined", "restored", "target_changed", "failed", "conflict", "not_found"]


@dataclass(frozen=True)
class DeleteTarget:
    path: str
    kind: str
    size_bytes: int
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeletePlan:
    plan_id: str
    root: str
    targets: tuple[DeleteTarget, ...]
    max_files: int
    approval_required: bool = True
    created_at: float = 0.0

    @property
    def target_count(self) -> int:
        return len(self.targets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "root": self.root,
            "targets": [target.to_dict() for target in self.targets],
            "target_count": self.target_count,
            "max_files": self.max_files,
            "approval_required": self.approval_required,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeletePlan:
        return cls(
            plan_id=str(data["plan_id"]),
            root=str(data["root"]),
            targets=tuple(DeleteTarget(**target) for target in data.get("targets", [])),
            max_files=int(data.get("max_files", MAX_BATCH_FILES)),
            approval_required=bool(data.get("approval_required", True)),
            created_at=float(data.get("created_at", 0.0)),
        )


@dataclass(frozen=True)
class DeleteEntry:
    original_path: str
    quarantine_path: str
    kind: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeleteEntry:
        return cls(**data)


@dataclass(frozen=True)
class DeleteReceipt:
    receipt_id: str
    plan_id: str
    status: DeleteStatus
    entries: tuple[DeleteEntry, ...] = ()
    approval_reference: str = ""
    error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "status": self.status,
            "entries": [entry.to_dict() for entry in self.entries],
            "approval_reference": self.approval_reference,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeleteReceipt:
        return cls(
            receipt_id=str(data["receipt_id"]),
            plan_id=str(data["plan_id"]),
            status=str(data.get("status", "failed")),
            entries=tuple(DeleteEntry.from_dict(entry) for entry in data.get("entries", [])),
            approval_reference=str(data.get("approval_reference", "")),
            error=str(data.get("error", "")),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )


class ReversibleDeleteService:
    """Plan, quarantine, and restore bounded deletion batches."""

    def __init__(
        self,
        root: str | Path,
        *,
        quarantine_root: str | Path | None = None,
        max_files: int = MAX_BATCH_FILES,
        protected_roots: list[str | Path] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("delete root must be an existing directory")
        if not 1 <= int(max_files) <= MAX_BATCH_FILES:
            raise ValueError(f"max_files must be between 1 and {MAX_BATCH_FILES}")
        self.max_files = int(max_files)
        if quarantine_root is None:
            unresolved_quarantine = self.root / ".alpha" / "quarantine"
            for candidate in (self.root / ".alpha", unresolved_quarantine):
                if candidate.is_symlink():
                    raise ValueError("quarantine vault cannot contain symlinks")
            self.quarantine_root = unresolved_quarantine.resolve(strict=False)
            if not self.quarantine_root.is_relative_to(self.root):
                raise ValueError("quarantine vault escaped the workspace")
        else:
            unresolved_quarantine = Path(quarantine_root).expanduser()
            if unresolved_quarantine.is_symlink():
                raise ValueError("quarantine vault cannot be a symlink")
            self.quarantine_root = unresolved_quarantine.resolve()
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        self.protected_roots = tuple(Path(item).expanduser().resolve() for item in (protected_roots or []))
        self._plans_path = self.quarantine_root / "plans.json"
        self._receipts_path = self.quarantine_root / "receipts.json"
        self._plans: dict[str, DeletePlan] = {}
        self._receipts: dict[str, DeleteReceipt] = {}
        self._lock = RLock()
        self._load_metadata()

    @staticmethod
    def _atomic_json_write(path: Path, data: Any) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _valid_id(value: str, prefix: str) -> bool:
        return isinstance(value, str) and re.fullmatch(rf"{prefix}_[0-9a-f]{{12}}", value) is not None

    @staticmethod
    def _valid_fingerprint(value: str) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    def _quarantine_path(self, value: str) -> Path:
        path = Path(value)
        resolved = path.resolve(strict=False) if path.is_absolute() else (self.root / path).resolve(strict=False)
        if resolved == self.quarantine_root or not resolved.is_relative_to(self.quarantine_root):
            raise ValueError("quarantine path escapes vault")
        return resolved

    def _validate_plan(self, plan: DeletePlan) -> DeletePlan:
        if not self._valid_id(plan.plan_id, "plan"):
            raise ValueError("invalid plan id")
        if plan.root != str(self.root) or plan.max_files != self.max_files or not plan.approval_required:
            raise ValueError("plan scope mismatch")
        if not 1 <= plan.target_count <= self.max_files:
            raise ValueError("invalid plan target count")
        seen: set[str] = set()
        for target in plan.targets:
            if self._relative(target.path) != target.path or target.path in seen:
                raise ValueError("invalid plan target path")
            if target.kind not in {"file", "directory"} or type(target.size_bytes) is not int or target.size_bytes < 0:
                raise ValueError("invalid plan target metadata")
            if not self._valid_fingerprint(target.fingerprint):
                raise ValueError("invalid plan fingerprint")
            seen.add(target.path)
        return plan

    def _validate_receipt(self, receipt: DeleteReceipt) -> DeleteReceipt:
        if not self._valid_id(receipt.receipt_id, "receipt") or not self._valid_id(receipt.plan_id, "plan"):
            raise ValueError("invalid receipt id")
        if receipt.status not in {
            "planned",
            "approval_required",
            "quarantined",
            "restored",
            "target_changed",
            "failed",
            "conflict",
            "not_found",
        }:
            raise ValueError("invalid receipt status")
        if len(receipt.entries) > self.max_files:
            raise ValueError("invalid receipt entry count")
        seen_originals: set[str] = set()
        seen_quarantine: set[str] = set()
        for entry in receipt.entries:
            if self._relative(entry.original_path) != entry.original_path or entry.original_path in seen_originals:
                raise ValueError("invalid receipt target path")
            quarantine_path = self._quarantine_path(entry.quarantine_path)
            if quarantine_path in seen_quarantine:
                raise ValueError("duplicate receipt quarantine path")
            if entry.kind not in {"file", "directory"} or type(entry.size_bytes) is not int or entry.size_bytes < 0:
                raise ValueError("invalid receipt target metadata")
            if not self._valid_fingerprint(entry.sha256):
                raise ValueError("invalid receipt fingerprint")
            seen_originals.add(entry.original_path)
            seen_quarantine.add(quarantine_path)
        return receipt

    def _read_metadata(self, path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise ValueError("quarantine metadata cannot be a symlink")
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise ValueError("quarantine metadata exceeds size limit")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("quarantine metadata must be an object")
        return value

    def _load_metadata(self) -> None:
        if self._plans_path.exists():
            try:
                raw = self._read_metadata(self._plans_path)
                plans = raw.get("plans", {})
                if not isinstance(plans, dict):
                    raise ValueError("invalid plans metadata")
                for key, value in plans.items():
                    try:
                        plan = self._validate_plan(DeletePlan.from_dict(value))
                    except (OSError, ValueError, TypeError, KeyError):
                        logger.warning("Ignoring invalid reversible-delete plan %s", key)
                        continue
                    if key == plan.plan_id:
                        self._plans[plan.plan_id] = plan
            except (OSError, ValueError, TypeError, KeyError):
                self._plans = {}
        if self._receipts_path.exists():
            try:
                raw = self._read_metadata(self._receipts_path)
                receipts = raw.get("receipts", {})
                if not isinstance(receipts, dict):
                    raise ValueError("invalid receipts metadata")
                for key, value in receipts.items():
                    try:
                        receipt = self._validate_receipt(DeleteReceipt.from_dict(value))
                    except (OSError, ValueError, TypeError, KeyError):
                        logger.warning("Ignoring invalid reversible-delete receipt %s", key)
                        continue
                    if key == receipt.receipt_id:
                        self._receipts[receipt.receipt_id] = receipt
            except (OSError, ValueError, TypeError, KeyError):
                self._receipts = {}

    def _persist(self) -> None:
        with self._lock:
            self._atomic_json_write(
                self._plans_path,
                {"version": 1, "plans": {key: value.to_dict() for key, value in self._plans.items()}},
            )
            self._atomic_json_write(
                self._receipts_path,
                {"version": 1, "receipts": {key: value.to_dict() for key, value in self._receipts.items()}},
            )

    def _relative(self, value: str) -> str:
        raw = str(value or "").replace("\\", "/")
        if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":") or ".." in Path(raw).parts:
            raise ValueError("target is outside root")
        unresolved = self.root / raw
        if unresolved.is_symlink():
            raise ValueError("symlink targets are not supported")
        candidate = unresolved.resolve(strict=False)
        if not candidate.is_relative_to(self.root):
            raise ValueError("target is outside root")
        if candidate == self.root:
            raise ValueError("delete root itself is not a valid target")
        if candidate == self.quarantine_root or candidate.is_relative_to(self.quarantine_root):
            raise ValueError("quarantine vault is not a deletion target")
        if any(candidate == protected or candidate.is_relative_to(protected) for protected in self.protected_roots):
            raise ValueError("target is protected")
        return candidate.relative_to(self.root).as_posix()

    @staticmethod
    def _tree_fingerprint(path: Path) -> tuple[str, int, str]:
        if path.is_symlink():
            raise ValueError("symlink targets are not supported")
        if path.is_file():
            data = path.read_bytes()
            return hashlib.sha256(data).hexdigest(), len(data), "file"
        if not path.is_dir():
            raise ValueError("target is not a regular file or directory")
        digest = hashlib.sha256()
        total = 0
        files = 0
        for current, directories, filenames in os.walk(path, topdown=True, followlinks=False):
            current_path = Path(current)
            for directory in directories:
                if (current_path / directory).is_symlink():
                    raise ValueError("symlink targets are not supported")
            for filename in sorted(filenames):
                file_path = current_path / filename
                if file_path.is_symlink():
                    raise ValueError("symlink targets are not supported")
                size = file_path.stat().st_size
                total += size
                files += 1
                if files > MAX_TREE_FILES or total > MAX_TREE_BYTES:
                    raise ValueError("target tree exceeds reversible-delete limits")
                relative = file_path.relative_to(path).as_posix().encode("utf-8")
                digest.update(relative + b"\0")
                with file_path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest.update(b"\0")
        return digest.hexdigest(), total, "directory"

    def plan(self, paths: list[str]) -> DeletePlan:
        if not isinstance(paths, list) or not paths:
            raise ValueError("at least one target is required")
        if len(paths) > self.max_files:
            raise ValueError("batch exceeds maximum file count")
        with self._lock:
            targets: list[DeleteTarget] = []
            for raw_path in paths:
                relative = self._relative(raw_path)
                absolute = self.root / relative
                if not absolute.exists():
                    raise FileNotFoundError(f"target does not exist: {relative}")
                fingerprint, size, kind = self._tree_fingerprint(absolute)
                targets.append(DeleteTarget(relative, kind, size, fingerprint))
            plan = DeletePlan(
                plan_id=f"plan_{uuid.uuid4().hex[:12]}",
                root=str(self.root),
                targets=tuple(targets),
                max_files=self.max_files,
                approval_required=True,
                created_at=time.time(),
            )
            self._plans[plan.plan_id] = plan
            self._persist()
            return plan

    def get_plan(self, plan_id: str) -> DeletePlan | None:
        with self._lock:
            return self._plans.get(str(plan_id))

    def get_receipt(self, receipt_id: str) -> DeleteReceipt | None:
        with self._lock:
            return self._receipts.get(str(receipt_id))

    @staticmethod
    def _approval_reference(value: str) -> str:
        return str(value or "").strip()[:200]

    def _receipt(
        self,
        plan: DeletePlan,
        status: DeleteStatus,
        *,
        entries: tuple[DeleteEntry, ...] = (),
        approval_reference: str = "",
        error: str = "",
    ) -> DeleteReceipt:
        now = time.time()
        receipt = DeleteReceipt(
            receipt_id=f"receipt_{uuid.uuid4().hex[:12]}",
            plan_id=plan.plan_id,
            status=status,
            entries=entries,
            approval_reference=self._approval_reference(approval_reference),
            error=error[:500],
            created_at=now,
            updated_at=now,
        )
        self._receipts[receipt.receipt_id] = receipt
        return receipt

    @staticmethod
    def _quarantine_label(root: Path, path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return str(path)

    def _verify_target(self, target: DeleteTarget) -> None:
        absolute = self.root / target.path
        if not absolute.exists() or absolute.is_symlink():
            raise ValueError("target disappeared or became a symlink")
        fingerprint, _, _ = self._tree_fingerprint(absolute)
        if fingerprint != target.fingerprint:
            raise ValueError("target changed after plan")

    def _rollback_to_original(self, entries: list[DeleteEntry]) -> None:
        for entry in reversed(entries):
            source = self.root / entry.original_path
            destination = self._quarantine_path(entry.quarantine_path)
            try:
                if destination.exists() and not source.exists():
                    shutil.move(str(destination), str(source))
            except OSError:
                logger.exception("Reversible delete rollback failed for %s", entry.original_path)

    def execute(
        self,
        plan: DeletePlan | str,
        *,
        approved: bool = False,
        approval_reference: str = "",
    ) -> DeleteReceipt:
        with self._lock:
            selected = self.get_plan(plan) if isinstance(plan, str) else plan
            if selected is None:
                raise KeyError("delete plan not found")
            try:
                selected = self._validate_plan(selected)
                if selected != self._plans.get(selected.plan_id):
                    raise ValueError("plan is not the persisted approved plan")
            except ValueError as exc:
                return self._receipt(selected, "approval_required", error=str(exc))
            if not approved or not self._approval_reference(approval_reference):
                return self._receipt(
                    selected,
                    "approval_required",
                    error="verified approval and approval_reference are required",
                )
            try:
                for target in selected.targets:
                    self._verify_target(target)
            except (OSError, ValueError) as exc:
                return self._receipt(
                    selected,
                    "target_changed",
                    approval_reference=approval_reference,
                    error=str(exc),
                )

            batch_name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
            batch_root = self.quarantine_root / batch_name
            batch_root.mkdir(parents=True, exist_ok=False)
            entries: list[DeleteEntry] = []
            try:
                for index, target in enumerate(selected.targets):
                    source = self.root / target.path
                    destination = batch_root / f"{index}_{target.path.replace('/', '__')}"
                    shutil.move(str(source), str(destination))
                    entry = DeleteEntry(
                        target.path,
                        self._quarantine_label(self.root, destination),
                        target.kind,
                        target.size_bytes,
                        target.fingerprint,
                    )
                    entries.append(entry)
                    fingerprint, size, kind = self._tree_fingerprint(destination)
                    if (fingerprint, size, kind) != (
                        target.fingerprint,
                        target.size_bytes,
                        target.kind,
                    ):
                        raise ValueError("target changed while it was being quarantined")
            except Exception as exc:
                self._rollback_to_original(entries)
                try:
                    batch_root.rmdir()
                except OSError:
                    pass
                receipt = self._receipt(
                    selected,
                    "failed",
                    approval_reference=approval_reference,
                    error=f"quarantine failed: {type(exc).__name__}: {exc}",
                )
                self._persist()
                return receipt

            receipt = self._receipt(
                selected,
                "quarantined",
                entries=tuple(entries),
                approval_reference=approval_reference,
            )
            try:
                self._persist()
            except Exception as exc:
                self._rollback_to_original(entries)
                receipt = self._receipt(
                    selected,
                    "failed",
                    approval_reference=approval_reference,
                    error=f"quarantine receipt persistence failed: {type(exc).__name__}: {exc}",
                )
                self._persist()
            return receipt

    def _rollback_to_quarantine(self, entries: list[DeleteEntry]) -> None:
        for entry in reversed(entries):
            original = self.root / entry.original_path
            quarantine = self._quarantine_path(entry.quarantine_path)
            try:
                if original.exists() and not quarantine.exists():
                    shutil.move(str(original), str(quarantine))
            except OSError:
                logger.exception("Reversible restore rollback failed for %s", entry.original_path)

    def restore(
        self,
        receipt_id: str,
        *,
        approved: bool = False,
        approval_reference: str = "",
    ) -> DeleteReceipt:
        with self._lock:
            receipt = self.get_receipt(receipt_id)
            if receipt is None:
                raise KeyError("delete receipt not found")
            try:
                receipt = self._validate_receipt(receipt)
            except ValueError as exc:
                return replace(
                    receipt,
                    status="failed",
                    error=str(exc),
                    updated_at=time.time(),
                )
            if not approved or not self._approval_reference(approval_reference):
                return replace(
                    receipt,
                    status="approval_required",
                    error="verified approval and approval_reference are required",
                    updated_at=time.time(),
                )
            if receipt.status != "quarantined":
                return receipt

            for entry in receipt.entries:
                original = self.root / entry.original_path
                if original.exists() or original.is_symlink():
                    updated = replace(
                        receipt,
                        status="conflict",
                        error=f"restore target already exists: {entry.original_path}",
                        updated_at=time.time(),
                    )
                    self._receipts[receipt.receipt_id] = updated
                    self._persist()
                    return updated
                quarantined = self._quarantine_path(entry.quarantine_path)
                if not quarantined.exists() or quarantined.is_symlink():
                    raise FileNotFoundError(f"quarantined target missing: {entry.original_path}")
                fingerprint, size, kind = self._tree_fingerprint(quarantined)
                if (fingerprint, size, kind) != (entry.sha256, entry.size_bytes, entry.kind):
                    raise ValueError(f"quarantined target changed: {entry.original_path}")

            moved: list[DeleteEntry] = []
            try:
                for entry in receipt.entries:
                    quarantined = self._quarantine_path(entry.quarantine_path)
                    shutil.move(str(quarantined), str(self.root / entry.original_path))
                    moved.append(entry)
            except Exception as exc:
                self._rollback_to_quarantine(moved)
                updated = replace(
                    receipt,
                    status="failed",
                    error=f"restore failed: {type(exc).__name__}: {exc}",
                    updated_at=time.time(),
                )
                self._receipts[receipt.receipt_id] = updated
                self._persist()
                return updated

            updated = replace(receipt, status="restored", error="", updated_at=time.time())
            self._receipts[receipt.receipt_id] = updated
            try:
                self._persist()
            except Exception as exc:
                self._rollback_to_quarantine(moved)
                updated = replace(
                    receipt,
                    status="failed",
                    error=f"restore receipt persistence failed: {type(exc).__name__}: {exc}",
                    updated_at=time.time(),
                )
                self._receipts[receipt.receipt_id] = updated
                self._persist()
            return updated

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "root": str(self.root),
                "quarantine_root": str(self.quarantine_root),
                "max_files": self.max_files,
                "plan_count": len(self._plans),
                "receipt_count": len(self._receipts),
                "receipts": [
                    receipt.to_dict()
                    for receipt in sorted(
                        self._receipts.values(),
                        key=lambda item: item.created_at,
                        reverse=True,
                    )[:50]
                ],
                "hard_delete": False,
            }


__all__ = [
    "DeleteEntry",
    "DeletePlan",
    "DeleteReceipt",
    "DeleteTarget",
    "MAX_BATCH_FILES",
    "ReversibleDeleteService",
]
