"""Persistent goal tree: one durable artifact per project goal.

A goal decomposes into subgoals with acceptance criteria, evidence links,
and status — surviving restarts for months. Failure of an approach never
fails the goal; the tree records the attempt and keeps the objective open.
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

GoalStatus = Literal["open", "in_progress", "blocked", "satisfied", "abandoned"]


def _projects_root() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "projects"
    except Exception:
        return Path.cwd() / ".alpha" / "projects"


@dataclass
class GoalNode:
    node_id: str
    title: str
    acceptance: list[str] = field(default_factory=list)
    status: GoalStatus = "open"
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    blocked_reason: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalNode:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class GoalTree:
    """File-backed goal hierarchy for one project goal."""

    def __init__(self, project_id: str, goal_id: str):
        self.project_id = project_id
        self.goal_id = goal_id
        self._path = _projects_root() / project_id / "tasks" / f"goal-{goal_id}.json"
        self._lock = threading.Lock()
        self._nodes: dict[str, GoalNode] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("nodes", []):
                n = GoalNode.from_dict(item)
                self._nodes[n.node_id] = n
        except Exception:
            logger.warning("Goal tree load failed for %s/%s", self.project_id, self.goal_id, exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "goal_id": self.goal_id, "nodes": [n.to_dict() for n in self._nodes.values()]}, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            logger.warning("Goal tree save failed for %s/%s", self.project_id, self.goal_id, exc_info=True)

    def add_root(self, title: str, *, acceptance: list[str] | None = None) -> GoalNode:
        with self._lock:
            node = GoalNode(node_id=f"g-{uuid.uuid4().hex[:10]}", title=title, acceptance=acceptance or [])
            self._nodes[node.node_id] = node
            self._save()
            return node

    def add_subgoal(self, parent_id: str, title: str, *, acceptance: list[str] | None = None) -> GoalNode:
        with self._lock:
            parent = self._nodes.get(parent_id)
            if not parent:
                raise ValueError(f"Parent goal '{parent_id}' not found.")
            node = GoalNode(node_id=f"g-{uuid.uuid4().hex[:10]}", title=title, acceptance=acceptance or [], parent_id=parent_id)
            self._nodes[node.node_id] = node
            parent.children.append(node.node_id)
            parent.updated_at = time.time()
            self._save()
            return node

    def set_status(self, node_id: str, status: GoalStatus, *, blocked_reason: str | None = None, evidence: dict[str, Any] | None = None, attempt: dict[str, Any] | None = None) -> GoalNode | None:
        with self._lock:
            node = self._nodes.get(node_id)
            if not node:
                return None
            node.status = status
            node.updated_at = time.time()
            if blocked_reason is not None:
                node.blocked_reason = blocked_reason or None
            if evidence:
                node.evidence.append(evidence)
            if attempt:
                node.attempts.append({**attempt, "at": time.time()})
            self._save()
            return node

    def progress(self) -> dict[str, Any]:
        with self._lock:
            nodes = list(self._nodes.values())
        total = len(nodes)
        done = sum(1 for n in nodes if n.status == "satisfied")
        return {"total": total, "satisfied": done, "blocked": sum(1 for n in nodes if n.status == "blocked"), "percent": round(done / total * 100, 1) if total else 100.0}

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {"goal_id": self.goal_id, "nodes": [n.to_dict() for n in self._nodes.values()]}
