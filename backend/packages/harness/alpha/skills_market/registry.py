"""Durable install/enable state for marketplace skills.

Mirrors the connector and expert registries: single JSON document, atomic
writes, loud on corruption, and validation against the catalog before install.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Mapping

from alpha.skills_market.catalog import SkillMarketCatalog

SKILL_STORE_SCHEMA_VERSION: Final = 1


class SkillMarketError(RuntimeError):
    """Base class for marketplace-state failures."""


class SkillStoreUnreadable(SkillMarketError):
    """The on-disk store exists but could not be read/validated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SkillInstallState:
    skill_id: str
    enabled: bool = False
    installed_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not isinstance(self.skill_id, str) or not self.skill_id.strip():
            raise SkillMarketError("skill_id must be a non-empty string")
        self.skill_id = self.skill_id.strip().lower()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SkillInstallState:
        return cls(**dict(data))


def _default_store_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "skills_market" / "installed.json"
    except Exception:
        return Path.cwd() / ".alpha" / "skills_market" / "installed.json"


class SkillMarketRegistry:
    """Durable registry of installed/enabled marketplace skills."""

    def __init__(self, storage_path: str | Path | None = None, *, catalog: SkillMarketCatalog | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_store_path()
        self._catalog = catalog or SkillMarketCatalog()
        self._lock = threading.RLock()
        self._states: dict[str, SkillInstallState] = {}
        self._load()

    def store_label(self) -> str:
        return str(self.storage_path)

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillStoreUnreadable(f"skill store {self.store_label()} could not be read: {exc}.") from exc
        if not raw.strip():
            raise SkillStoreUnreadable(f"skill store {self.store_label()} is empty.")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SkillStoreUnreadable(f"skill store {self.store_label()} is not valid JSON: {exc}.") from exc
        if not isinstance(data, dict):
            raise SkillStoreUnreadable(f"skill store {self.store_label()} must contain an object.")
        if data.get("schema_version") != SKILL_STORE_SCHEMA_VERSION:
            raise SkillStoreUnreadable(f"skill store {self.store_label()} schema_version {data.get('schema_version')!r} != {SKILL_STORE_SCHEMA_VERSION}.")
        try:
            for item in data.get("skills", []):
                state = SkillInstallState.from_dict(item)
                self._states[state.skill_id] = state
        except SkillMarketError as exc:
            raise SkillStoreUnreadable(f"skill store {self.store_label()} failed validation: {exc}.") from exc

    def _save(self) -> None:
        payload = {
            "schema_version": SKILL_STORE_SCHEMA_VERSION,
            "skills": [s.to_dict() for s in self._states.values()],
            "updated_at": _now(),
        }
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.storage_path)
        except OSError as exc:
            raise SkillStoreUnreadable(f"could not persist skill store {self.store_label()}: {exc}") from exc

    def install(self, skill_id: str, *, enable: bool = True) -> SkillInstallState:
        key = (skill_id or "").strip().lower()
        if self._catalog.get(key) is None:
            raise SkillMarketError(f"unknown skill {skill_id!r}; not in the marketplace")
        with self._lock:
            state = self._states.get(key) or SkillInstallState(skill_id=key)
            state.enabled = bool(enable)
            self._states[key] = state
            self._save()
        return state

    def set_enabled(self, skill_id: str, enabled: bool) -> SkillInstallState:
        key = (skill_id or "").strip().lower()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                raise SkillMarketError(f"skill {skill_id!r} is not installed")
            state.enabled = bool(enabled)
            self._save()
            return state

    def get(self, skill_id: str) -> SkillInstallState | None:
        with self._lock:
            return self._states.get((skill_id or "").strip().lower())

    def list(self) -> list[SkillInstallState]:
        with self._lock:
            return sorted(self._states.values(), key=lambda s: s.skill_id)

    def enabled_ids(self) -> list[str]:
        with self._lock:
            return sorted(s.skill_id for s in self._states.values() if s.enabled)

    def uninstall(self, skill_id: str) -> bool:
        with self._lock:
            key = (skill_id or "").strip().lower()
            if key not in self._states:
                return False
            del self._states[key]
            self._save()
            return True


__all__ = [
    "SKILL_STORE_SCHEMA_VERSION",
    "SkillInstallState",
    "SkillMarketError",
    "SkillMarketRegistry",
    "SkillStoreUnreadable",
]
