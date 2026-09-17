"""Episodic Project Postmortem and Recursive Self-Improvement (RSI) Engine.

Analyzes task failures, breaks, and regressions to extract root causes, erroneous
assumptions, and preventative heuristics. Directly injects learned rules into Level 1
Bot Memory and Level 2 Project Memory to ensure the AI workforce continuously self-improves.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_workspace.config.runtime_paths import runtime_home
from agent_workspace.projects.context_router import get_three_level_router
from agent_workspace.projects.events import get_event_bus

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _postmortems_path(project_id: str) -> Path:
    return runtime_home() / "projects" / project_id / "postmortems" / "postmortems.json"


@dataclass
class PostmortemRecord:
    """Documented postmortem analysis for a failed or reworked task."""

    postmortem_id: str
    project_id: str
    task_id: str
    bot_name: str
    error_summary: str
    root_cause: str
    erroneous_assumptions: list[str] = field(default_factory=list)
    preventative_rule: str = ""
    applied_to_bot_memory: bool = False
    applied_to_project_memory: bool = False
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostmortemRecord:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class ProjectPostmortemEngine:
    """Extracts, persists, and propagates learned failure heuristics across workforce memory."""

    def __init__(self, project_id: str, storage_path: Path | None = None) -> None:
        self.project_id = project_id
        self._path = storage_path or _postmortems_path(project_id)
        self._lock = threading.Lock()
        self._records: list[PostmortemRecord] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("postmortems", []):
                self._records.append(PostmortemRecord.from_dict(item))
        except Exception:
            logger.warning("Failed to load postmortems for %s", self.project_id, exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            payload = {
                "version": 1,
                "project_id": self.project_id,
                "postmortems": [r.to_dict() for r in self._records],
                "updated_at": _now(),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self._path)
        except Exception:
            logger.warning("Failed to save postmortems for %s", self.project_id, exc_info=True)

    def analyze_failure(
        self,
        task_id: str,
        bot_name: str,
        error_summary: str,
        root_cause: str,
        *,
        erroneous_assumptions: list[str] | None = None,
        preventative_rule: str | None = None,
        sync_to_memory: bool = True,
    ) -> PostmortemRecord:
        """Record task postmortem and propagate heuristics to L1 (Bot) and L2 (Project) memory."""
        assumptions = erroneous_assumptions or []
        rule = (
            preventative_rule
            or f"Avoid {error_summary[:80]}: ensure preconditions and unit verifications are executed first."
        )

        with self._lock:
            pm_id = f"PM-{len(self._records) + 1:03d}"
            record = PostmortemRecord(
                postmortem_id=pm_id,
                project_id=self.project_id,
                task_id=task_id,
                bot_name=bot_name,
                error_summary=error_summary,
                root_cause=root_cause,
                erroneous_assumptions=assumptions,
                preventative_rule=rule,
            )

            # Recursive Self-Improvement (RSI): Propagate to 3-tier memory router
            if sync_to_memory:
                router = get_three_level_router()

                # Level 1: Bot Personal Memory
                try:
                    bot_mem = router.get_bot_memory(bot_name)
                    learned = list(bot_mem.get("learned_lessons", []))
                    lesson_entry = {
                        "task_id": task_id,
                        "heuristic": rule,
                        "learned_at": _now(),
                    }
                    learned.append(lesson_entry)
                    # Keep latest 25 lessons
                    router.update_bot_memory(bot_name, {"learned_lessons": learned[-25:]})
                    record.applied_to_bot_memory = True
                except Exception:
                    logger.debug("Failed to sync postmortem to bot memory", exc_info=True)

                # Level 2: Project Shared Memory
                try:
                    proj_mem = router.get_project_memory(self.project_id)
                    heuristics = list(proj_mem.get("failure_heuristics", []))
                    heuristics.append(
                        f"[{bot_name.capitalize()} on {task_id}]: {rule}"
                    )
                    # Keep latest 25 heuristics
                    router.update_project_memory(
                        self.project_id, {"failure_heuristics": heuristics[-25:]}
                    )
                    record.applied_to_project_memory = True
                except Exception:
                    logger.debug("Failed to sync postmortem to project memory", exc_info=True)

            self._records.append(record)
            self._save()

        # Emit audit event
        get_event_bus(self.project_id).emit(
            "task_postmortem_recorded",
            bot_name,
            {
                "postmortem_id": pm_id,
                "task_id": task_id,
                "error_summary": error_summary,
                "rule": rule,
            },
        )

        return record

    def list_postmortems(self) -> list[PostmortemRecord]:
        with self._lock:
            return list(self._records)

    def get_heuristics_summary(self) -> list[str]:
        with self._lock:
            return [r.preventative_rule for r in self._records if r.preventative_rule]


_postmortem_engines: dict[str, ProjectPostmortemEngine] = {}
_engine_lock = threading.Lock()


def get_postmortem_engine(project_id: str) -> ProjectPostmortemEngine:
    with _engine_lock:
        eng = _postmortem_engines.get(project_id)
        if eng is None:
            eng = ProjectPostmortemEngine(project_id)
            _postmortem_engines[project_id] = eng
        return eng
