"""Skill trust tiers plus quarantine for fresh installs.

Tiers: builtin (ships with the harness) > trusted (reviewed) > community
(unreviewed). Fresh community installs land in quarantine until they pass
the review gate; quarantined skills never load into a live agent.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

SkillTier = Literal["builtin", "trusted", "community"]
TIER_RANK: dict[str, int] = {"builtin": 0, "trusted": 1, "community": 2}


def _default_storage_path() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "skills" / "tiers.json"
    except Exception:
        return Path.cwd() / ".deerflow" / "skills" / "tiers.json"


def quarantine_dir() -> Path:
    try:
        from agent_workspace.config.runtime_paths import runtime_home

        return runtime_home() / "skills" / "quarantine"
    except Exception:
        return Path.cwd() / ".deerflow" / "skills" / "quarantine"


@dataclass
class TierRecord:
    skill_name: str
    tier: SkillTier
    quarantined: bool = False
    reviewed_at: float | None = None
    reviewer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TierRecord:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class TierRegistry:
    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rows: dict[str, TierRecord] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in data.get("tiers", []):
                r = TierRecord.from_dict(item)
                self._rows[r.skill_name] = r
        except Exception:
            logger.warning("Skill tier load failed; starting empty", exc_info=True)

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "tiers": [r.to_dict() for r in self._rows.values()]}, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Skill tier save failed", exc_info=True)

    def quarantine(self, skill_name: str) -> TierRecord:
        with self._lock:
            rec = TierRecord(skill_name=skill_name, tier="community", quarantined=True)
            self._rows[skill_name] = rec
            self._save()
            quarantine_dir().mkdir(parents=True, exist_ok=True)
            return rec

    def graduate(self, skill_name: str, *, reviewer: str, tier: SkillTier = "trusted") -> TierRecord | None:
        with self._lock:
            rec = self._rows.get(skill_name)
            if not rec:
                return None
            rec.tier = tier
            rec.quarantined = False
            rec.reviewed_at = time.time()
            rec.reviewer = reviewer
            self._save()
            return rec

    def get(self, skill_name: str) -> TierRecord | None:
        with self._lock:
            return self._rows.get(skill_name)

    def list(self) -> list[TierRecord]:
        with self._lock:
            return list(self._rows.values())

    def loadable(self, skill_name: str, *, min_tier: SkillTier = "community") -> bool:
        """A skill loads iff it is known, out of quarantine, and meets min tier."""
        rec = self.get(skill_name)
        if not rec or rec.quarantined:
            return False
        return TIER_RANK[rec.tier] <= TIER_RANK[min_tier]


_registry: TierRegistry | None = None
_registry_path: str | None = None
_registry_lock = threading.Lock()


def get_tier_registry() -> TierRegistry:
    global _registry, _registry_path
    with _registry_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _registry is None or _registry_path != live:
            _registry = TierRegistry()
            try:
                _registry_path = str(_registry.storage_path.resolve())
            except Exception:
                _registry_path = live
        return _registry
