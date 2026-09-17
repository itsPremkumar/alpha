"""ExperienceStore: Persistent store for episodic experience records."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from agent_workspace.learning.experience.models import ExperienceRecord, OutcomeType

logger = logging.getLogger(__name__)


class ExperienceStore:
    """Persistent storage repository managing episodic experience memory records."""

    def __init__(self, storage_path: str | Path | None = None, load_defaults: bool = True):
        self.storage_path = Path(storage_path) if storage_path else None
        self._records: dict[str, ExperienceRecord] = {}

        if self.storage_path and self.storage_path.exists():
            self._load_from_disk()
        elif load_defaults:
            self._load_default_experiences()

    def record(self, record: ExperienceRecord) -> None:
        """Add or update an experience record and flush to disk if path configured."""
        self._records[record.experience_id] = record
        if self.storage_path:
            self._save_to_disk()

    def get(self, experience_id: str) -> ExperienceRecord | None:
        return self._records.get(experience_id)

    def list_all(self) -> list[ExperienceRecord]:
        return list(self._records.values())

    def clear(self) -> None:
        self._records.clear()
        if self.storage_path and self.storage_path.exists():
            try:
                os.remove(self.storage_path)
            except OSError:
                pass

    def _save_to_disk(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in self._records.values()]
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _load_from_disk(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    rec = ExperienceRecord.from_dict(item)
                    self._records[rec.experience_id] = rec
        except Exception as e:
            logger.warning(f"Failed to load experience store from {self.storage_path}: {e}")

    def _load_default_experiences(self) -> None:
        """Seed high-frequency enterprise SWE experience patterns."""
        self.record(
            ExperienceRecord(
                experience_id="exp_async_deadlock",
                task_goal="Refactor worker queues with asyncio and sync database client",
                outcome=OutcomeType.FAILURE,
                error_types=["RuntimeError", "EventLoopBlocked"],
                tags=["asyncio", "database", "deadlock"],
                lessons_learned=[
                    "Never invoke synchronous blocking DB calls inside async event loops; wrap with asyncio.to_thread.",
                    "Always set connect and query timeouts on database connection pools.",
                ],
                pitfalls_to_avoid=[
                    "Calling db.execute() synchronously within async FastAPI endpoints causes total server freeze.",
                ],
            )
        )
        self.record(
            ExperienceRecord(
                experience_id="exp_clean_patch_empty",
                task_goal="Fix bug in calculation module",
                outcome=OutcomeType.FAILURE,
                error_types=["EmptyPatchError", "CriticRejection"],
                tags=["patch", "critic", "verification"],
                lessons_learned=[
                    "Always run git diff --stat or git status --porcelain to confirm edits are committed to disk before finishing.",
                ],
                pitfalls_to_avoid=[
                    "Claiming task complete when only viewing files without writing changes.",
                ],
            )
        )
