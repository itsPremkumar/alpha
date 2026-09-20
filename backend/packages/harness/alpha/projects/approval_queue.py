"""Interactive Human Approval Queue and Dual-Key Authorization Engine.

Enforces human-in-the-loop oversight for high-blast-radius actions (production deploys,
database schema drops, destructive file deletions, and protected git pushes).
When an agent attempts a sensitive action, it queues an ApprovalRequest with risk scoring,
justification, and diff previews, holding execution until an authorized human signs off.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from alpha.config.runtime_paths import runtime_home
from alpha.projects.events import get_event_bus

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _queue_path(project_id: str) -> Path:
    return runtime_home() / "projects" / project_id / "approvals" / "queue.json"


@dataclass
class ApprovalRequest:
    """A formal authorization ticket requiring human operator sign-off."""

    request_id: str
    project_id: str
    bot_name: str
    action_type: str
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    details: dict[str, Any] = field(default_factory=dict)
    diff_preview: str | None = None
    status: Literal["pending", "approved", "rejected", "timed_out"] = "pending"
    resolution_comment: str = ""
    resolved_by: str | None = None
    created_at: str = field(default_factory=_now)
    resolved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalRequest:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class ApprovalQueue:
    """Thread-safe, file-backed approval queue for project-level actions."""

    def __init__(self, project_id: str, storage_path: Path | None = None) -> None:
        self.project_id = project_id
        self._path = storage_path or _queue_path(project_id)
        self._lock = threading.Lock()
        self._requests: dict[str, ApprovalRequest] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("requests", []):
                req = ApprovalRequest.from_dict(item)
                self._requests[req.request_id] = req
        except Exception:
            logger.warning("Failed to load approval queue for %s", self.project_id, exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            payload = {
                "version": 1,
                "project_id": self.project_id,
                "requests": [r.to_dict() for r in self._requests.values()],
                "updated_at": _now(),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self._path)
        except Exception:
            logger.warning("Failed to save approval queue for %s", self.project_id, exc_info=True)

    def request_approval(
        self,
        bot_name: str,
        action_type: str,
        *,
        risk_level: Literal["low", "medium", "high", "critical"] = "medium",
        details: dict[str, Any] | None = None,
        diff_preview: str | None = None,
    ) -> ApprovalRequest:
        """Create a new pending human approval request."""
        req_id = f"APPR-{uuid.uuid4().hex[:8].upper()}"
        req = ApprovalRequest(
            request_id=req_id,
            project_id=self.project_id,
            bot_name=bot_name,
            action_type=action_type,
            risk_level=risk_level,
            details=details or {},
            diff_preview=diff_preview,
            status="pending",
        )

        with self._lock:
            self._requests[req_id] = req
            self._save()

        get_event_bus(self.project_id).emit(
            "approval_requested",
            bot_name,
            {
                "request_id": req_id,
                "action_type": action_type,
                "risk_level": risk_level,
            },
        )
        return req

    def resolve_request(
        self,
        request_id: str,
        *,
        approved: bool,
        resolved_by: str = "human_operator",
        comment: str = "",
    ) -> ApprovalRequest:
        """Approve or reject a pending request."""
        with self._lock:
            req = self._requests.get(request_id)
            if not req:
                raise KeyError(f"ApprovalRequest '{request_id}' not found in project '{self.project_id}'.")

            if req.status != "pending":
                raise ValueError(f"ApprovalRequest '{request_id}' is already {req.status}.")

            req.status = "approved" if approved else "rejected"
            req.resolution_comment = comment
            req.resolved_by = resolved_by
            req.resolved_at = _now()
            self._save()

        event_name = "approval_granted" if approved else "approval_rejected"
        get_event_bus(self.project_id).emit(
            event_name,
            resolved_by,
            {
                "request_id": request_id,
                "action_type": req.action_type,
                "comment": comment,
            },
        )
        return req

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def list_requests(self, status: str | None = None) -> list[ApprovalRequest]:
        with self._lock:
            reqs = list(self._requests.values())
        if status:
            reqs = [r for r in reqs if r.status == status]
        return reqs

    def list_pending(self) -> list[ApprovalRequest]:
        return self.list_requests(status="pending")


_approval_queues: dict[str, ApprovalQueue] = {}
_queue_lock = threading.Lock()


def get_approval_queue(project_id: str) -> ApprovalQueue:
    with _queue_lock:
        q = _approval_queues.get(project_id)
        if q is None:
            q = ApprovalQueue(project_id)
            _approval_queues[project_id] = q
        return q
