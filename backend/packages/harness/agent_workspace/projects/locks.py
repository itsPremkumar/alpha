"""Project-scoped resource ownership: file/dir/task/artifact locks.

Prevents concurrent agents from clobbering the same resource. A lock has one
owner; others may queue an access request the owner (or supervisor) resolves.
Locks expire via TTL so a dead agent cannot hold a project hostage.
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

LockScope = Literal["file", "dir", "task", "artifact"]
DEFAULT_TTL_SECONDS = 1800.0


def _default_storage_path() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "projects" / "locks.json"
    except Exception:
        return Path.cwd() / ".deerflow" / "projects" / "locks.json"


@dataclass
class ResourceLock:
    lock_id: str
    project_id: str
    scope: LockScope
    path: str
    owner_bot: str
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + DEFAULT_TTL_SECONDS)

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceLock:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class AccessRequest:
    request_id: str
    lock_id: str
    project_id: str
    requester_bot: str
    mode: str = "shared"
    status: str = "pending"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccessRequest:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class LockConflictError(ValueError):
    """Raised when a live lock already covers the resource."""

    def __init__(self, holder: ResourceLock):
        super().__init__(f"Locked by {holder.owner_bot}: {holder.reason or holder.path}")
        self.holder = holder


class LockManager:
    """Thread-safe, file-backed project resource locks."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._locks: dict[str, ResourceLock] = {}
        self._requests: dict[str, AccessRequest] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("locks", []):
                lk = ResourceLock.from_dict(item)
                if not lk.expired:
                    self._locks[lk.lock_id] = lk
            for item in data.get("requests", []):
                rq = AccessRequest.from_dict(item)
                self._requests[rq.request_id] = rq
        except Exception:
            logger.warning("Lock store load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": 1, "locks": [lk.to_dict() for lk in self._locks.values()], "requests": [r.to_dict() for r in self._requests.values()]},
                    f,
                    indent=2,
                )
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Lock store save failed", exc_info=True)

    def _live_holder(self, project_id: str, scope: str, path: str) -> ResourceLock | None:
        for lk in self._locks.values():
            if lk.expired:
                continue
            if lk.project_id == project_id and lk.scope == scope and lk.path == path:
                return lk
            # A dir lock covers everything beneath it.
            if lk.project_id == project_id and lk.scope == "dir" and scope in ("file", "dir") and path.startswith(lk.path.rstrip("/") + "/"):
                return lk
        return None

    def acquire(self, project_id: str, scope: LockScope, path: str, owner_bot: str, *, reason: str = "", ttl_seconds: float = DEFAULT_TTL_SECONDS) -> ResourceLock:
        owner = owner_bot.lower().strip()
        with self._lock:
            holder = self._live_holder(project_id, scope, path)
            if holder and holder.owner_bot != owner:
                raise LockConflictError(holder)
            if holder and holder.owner_bot == owner:
                holder.expires_at = time.time() + ttl_seconds
                if reason:
                    holder.reason = reason
                self._save()
                return holder
            lk = ResourceLock(lock_id=f"lk-{uuid.uuid4().hex[:10]}", project_id=project_id, scope=scope, path=path, owner_bot=owner, reason=reason, expires_at=time.time() + ttl_seconds)
            self._locks[lk.lock_id] = lk
            self._save()
            return lk

    def release(self, lock_id: str, requester_bot: str) -> bool:
        requester = requester_bot.lower().strip()
        with self._lock:
            lk = self._locks.get(lock_id)
            if not lk:
                return False
            if lk.owner_bot != requester and requester != "supervisor":
                return False
            del self._locks[lock_id]
            self._save()
            return True

    def request_access(self, project_id: str, scope: str, path: str, requester_bot: str, *, mode: str = "shared") -> AccessRequest:
        with self._lock:
            holder = self._live_holder(project_id, scope, path)
            if not holder:
                raise ValueError("Resource is not locked; acquire it directly.")
            rq = AccessRequest(request_id=f"lr-{uuid.uuid4().hex[:10]}", lock_id=holder.lock_id, project_id=project_id, requester_bot=requester_bot.lower().strip(), mode=mode)
            self._requests[rq.request_id] = rq
            self._save()
            return rq

    def resolve_request(self, request_id: str, approver_bot: str, *, approve: bool) -> AccessRequest | None:
        with self._lock:
            rq = self._requests.get(request_id)
            if not rq or rq.status != "pending":
                return None
            lk = self._locks.get(rq.lock_id)
            if not lk or (lk.owner_bot != approver_bot.lower().strip() and approver_bot.lower().strip() != "supervisor"):
                return None
            if approve and rq.mode == "transfer" and lk:
                lk.owner_bot = rq.requester_bot
            rq.status = "approved" if approve else "rejected"
            self._save()
            return rq

    def list_locks(self, project_id: str) -> list[ResourceLock]:
        with self._lock:
            return [lk for lk in self._locks.values() if lk.project_id == project_id and not lk.expired]

    def list_requests(self, project_id: str, *, pending_only: bool = True) -> list[AccessRequest]:
        with self._lock:
            out = [r for r in self._requests.values() if r.project_id == project_id]
            if pending_only:
                out = [r for r in out if r.status == "pending"]
            return out

    def sweep_expired(self) -> int:
        with self._lock:
            dead = [lid for lid, lk in self._locks.items() if lk.expired]
            for lid in dead:
                del self._locks[lid]
            if dead:
                self._save()
            return len(dead)


_manager: LockManager | None = None
_manager_path: str | None = None
_manager_lock = threading.Lock()


def get_lock_manager() -> LockManager:
    global _manager, _manager_path
    with _manager_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _manager is None or _manager_path != live:
            _manager = LockManager()
            try:
                _manager_path = str(_manager.storage_path.resolve())
            except Exception:
                _manager_path = live
        return _manager
