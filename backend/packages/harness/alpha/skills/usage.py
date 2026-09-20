"""Skill usage telemetry + provenance sidecar for the curator.

A ``.usage.json`` file keyed by skill name (never frontmatter — keeps
telemetry out of user-authored SKILL.md). Counter bumps are best-effort and
never break the caller; writes are atomic tmp+replace under a lock.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

CreatedBy = Literal["human", "agent", "builtin"]


def _skills_root() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "skills"
    except Exception:
        return Path.cwd() / ".alpha" / "skills"


def _usage_file() -> Path:
    return _skills_root() / ".usage.json"


@dataclass
class SkillUsage:
    name: str
    uses: int = 0
    last_used_at: float | None = None
    created_by: CreatedBy = "human"
    first_seen_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillUsage:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class SkillUsageTracker:
    def __init__(self, usage_file: str | Path | None = None):
        self._path = Path(usage_file).resolve() if usage_file else _usage_file()
        self._lock = threading.Lock()
        self._rows: dict[str, SkillUsage] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("usage", []):
                row = SkillUsage.from_dict(item)
                self._rows[row.name] = row
        except Exception:
            logger.warning("Skill usage load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "usage": [r.to_dict() for r in self._rows.values()]}, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            logger.warning("Skill usage save failed", exc_info=True)

    def record_use(self, skill_name: str, *, created_by: CreatedBy | None = None) -> None:
        """Best-effort bump. Never raises — telemetry must not break tool calls."""
        try:
            key = skill_name.strip()
            if not key:
                return
            with self._lock:
                row = self._rows.get(key)
                if row is None:
                    row = SkillUsage(name=key, created_by=created_by or "human")
                    self._rows[key] = row
                row.uses += 1
                row.last_used_at = time.time()
                if created_by:
                    row.created_by = created_by
                self._save()
        except Exception:
            logger.debug("Skill usage bump failed for %s", skill_name, exc_info=True)

    def mark_created_by(self, skill_name: str, created_by: CreatedBy) -> None:
        with self._lock:
            row = self._rows.get(skill_name.strip())
            if row is None:
                row = SkillUsage(name=skill_name.strip(), created_by=created_by)
                self._rows[skill_name.strip()] = row
            else:
                row.created_by = created_by
            self._save()

    def stats(self, skill_name: str) -> SkillUsage | None:
        with self._lock:
            return self._rows.get(skill_name.strip())

    def all_stats(self) -> list[SkillUsage]:
        with self._lock:
            return list(self._rows.values())

    def stale_candidates(self, known_skills: list[str], *, stale_after_days: float = 14.0) -> list[str]:
        """Agent-created skills unused for a while (or never) are stale candidates."""
        cutoff = time.time() - stale_after_days * 86400.0
        out: list[str] = []
        with self._lock:
            for name in known_skills:
                row = self._rows.get(name)
                if row is None:
                    continue
                if row.created_by != "agent":
                    continue
                if (row.last_used_at or 0.0) < cutoff:
                    out.append(name)
        return out


_tracker: SkillUsageTracker | None = None
_tracker_lock = threading.Lock()


def get_skill_usage_tracker() -> SkillUsageTracker:
    global _tracker
    with _tracker_lock:
        try:
            live = str(_usage_file().resolve())
        except Exception:
            live = None
        if _tracker is None or (live and str(_tracker._path.resolve()) != live):
            _tracker = SkillUsageTracker()
        return _tracker
