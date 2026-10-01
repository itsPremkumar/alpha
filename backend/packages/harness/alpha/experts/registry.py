"""Durable install/enable state for experts and expert groups.

Mirrors the connector registry: a single JSON document, atomic writes, loud on
corruption, and validation that the id exists in the catalog before install.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Mapping

from alpha.experts.catalog import ExpertCatalog

EXPERT_STORE_SCHEMA_VERSION: Final = 1


class ExpertError(RuntimeError):
    """Base class for expert-state failures."""


class ExpertStoreUnreadable(ExpertError):
    """The on-disk expert store exists but could not be read/validated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExpertInstallState:
    """Install/enable record for one expert or expert group."""

    expert_id: str
    kind: str = "expert"  # "expert" | "group"
    enabled: bool = False
    installed_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not isinstance(self.expert_id, str) or not self.expert_id.strip():
            raise ExpertError("expert_id must be a non-empty string")
        self.expert_id = self.expert_id.strip().lower()
        if self.kind not in {"expert", "group"}:
            raise ExpertError("kind must be 'expert' or 'group'")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExpertInstallState:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ExpertError(f"expert state has unknown field(s): {unknown}")
        return cls(**dict(data))


def _default_store_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "experts" / "experts.json"
    except Exception:
        return Path.cwd() / ".alpha" / "experts" / "experts.json"


class ExpertRegistry:
    """Durable registry of installed/enabled experts and expert groups."""

    def __init__(self, storage_path: str | Path | None = None, *, catalog: ExpertCatalog | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_store_path()
        self._catalog = catalog or ExpertCatalog()
        self._lock = threading.RLock()
        self._states: dict[str, ExpertInstallState] = {}
        self._load()

    def store_label(self) -> str:
        return str(self.storage_path)

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ExpertStoreUnreadable(f"expert store {self.store_label()} could not be read: {exc}.") from exc
        if not raw.strip():
            raise ExpertStoreUnreadable(f"expert store {self.store_label()} is empty.")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExpertStoreUnreadable(f"expert store {self.store_label()} is not valid JSON: {exc}.") from exc
        if not isinstance(data, dict):
            raise ExpertStoreUnreadable(f"expert store {self.store_label()} must contain an object.")
        if data.get("schema_version") != EXPERT_STORE_SCHEMA_VERSION:
            raise ExpertStoreUnreadable(f"expert store {self.store_label()} schema_version {data.get('schema_version')!r} != {EXPERT_STORE_SCHEMA_VERSION}.")
        try:
            for item in data.get("experts", []):
                state = ExpertInstallState.from_dict(item)
                self._states[state.expert_id] = state
        except ExpertError as exc:
            raise ExpertStoreUnreadable(f"expert store {self.store_label()} failed validation: {exc}.") from exc

    def _save(self) -> None:
        payload = {
            "schema_version": EXPERT_STORE_SCHEMA_VERSION,
            "experts": [s.to_dict() for s in self._states.values()],
            "updated_at": _now(),
        }
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.storage_path)
        except OSError as exc:
            raise ExpertStoreUnreadable(f"could not persist expert store {self.store_label()}: {exc}") from exc

    def install_expert(self, expert_id: str, *, enable: bool = True) -> ExpertInstallState:
        key = (expert_id or "").strip().lower()
        if self._catalog.get_expert(key) is None:
            raise ExpertError(f"unknown expert {expert_id!r}; not in the catalog")
        return self._install(key, "expert", enable)

    def install_group(self, group_id: str, *, enable: bool = True) -> ExpertInstallState:
        key = (group_id or "").strip().lower()
        group = self._catalog.get_group(key)
        if group is None:
            raise ExpertError(f"unknown expert group {group_id!r}; not in the catalog")
        missing = self._catalog.validate_group(key)
        if missing:
            raise ExpertError(f"expert group {group_id!r} references missing experts: {missing}")
        return self._install(key, "group", enable)

    def _install(self, key: str, kind: str, enable: bool) -> ExpertInstallState:
        with self._lock:
            state = self._states.get(key) or ExpertInstallState(expert_id=key, kind=kind)
            state.enabled = bool(enable)
            self._states[key] = state
            self._save()
        return state

    def set_enabled(self, expert_id: str, enabled: bool) -> ExpertInstallState:
        key = (expert_id or "").strip().lower()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                raise ExpertError(f"{expert_id!r} is not installed")
            state.enabled = bool(enabled)
            self._save()
            return state

    def get(self, expert_id: str) -> ExpertInstallState | None:
        with self._lock:
            return self._states.get((expert_id or "").strip().lower())

    def list(self) -> list[ExpertInstallState]:
        with self._lock:
            return sorted(self._states.values(), key=lambda s: s.expert_id)

    def enabled_ids(self) -> list[str]:
        with self._lock:
            return sorted(s.expert_id for s in self._states.values() if s.enabled)

    def uninstall(self, expert_id: str) -> bool:
        with self._lock:
            key = (expert_id or "").strip().lower()
            if key not in self._states:
                return False
            del self._states[key]
            self._save()
            return True


__all__ = [
    "EXPERT_STORE_SCHEMA_VERSION",
    "ExpertError",
    "ExpertInstallState",
    "ExpertRegistry",
    "ExpertStoreUnreadable",
]
