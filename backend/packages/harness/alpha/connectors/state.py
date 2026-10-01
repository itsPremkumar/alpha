"""Durable install/enable/health state for connectors.

Kept separate from the catalog so the catalog stays a pure, immutable
declaration. This store records which connectors a user has installed, whether
they are enabled, and best-effort health/rate-limit metadata. It is durable
(atomic JSON) and loud on corruption, matching the harness convention.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Final, Mapping

from alpha.connectors.catalog import ConnectorCatalog

CONNECTOR_STORE_SCHEMA_VERSION: Final = 1


class ConnectorHealth(str, Enum):
    """Best-effort health of a connected service."""

    UNKNOWN = "unknown"
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class ConnectorError(RuntimeError):
    """Base class for connector-state failures."""


class ConnectorStoreUnreadable(ConnectorError):
    """The on-disk connector store exists but could not be read/validated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConnectorInstallState:
    """Per-connector install/enable/health record."""

    connector_id: str
    enabled: bool = False
    installed_at: str = field(default_factory=_now)
    health: ConnectorHealth = ConnectorHealth.UNKNOWN
    rate_limit_per_min: int = 0
    last_error: str | None = None
    last_checked_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.connector_id, str) or not self.connector_id.strip():
            raise ConnectorError("connector_id must be a non-empty string")
        self.connector_id = self.connector_id.strip().lower()
        if not isinstance(self.health, ConnectorHealth):
            self.health = ConnectorHealth(self.health)
        if self.rate_limit_per_min < 0:
            raise ConnectorError("rate_limit_per_min must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["health"] = self.health.value
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConnectorInstallState:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConnectorError(f"connector state has unknown field(s): {unknown}")
        payload = dict(data)
        payload["health"] = ConnectorHealth(payload.get("health", "unknown"))
        return cls(**payload)


def _default_store_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "connectors" / "connectors.json"
    except Exception:
        return Path.cwd() / ".alpha" / "connectors" / "connectors.json"


class ConnectorStore:
    """Durable, schema-versioned, thread-safe registry of connector state."""

    def __init__(self, storage_path: str | Path | None = None, *, catalog: ConnectorCatalog | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_store_path()
        self._catalog = catalog or ConnectorCatalog()
        self._lock = threading.RLock()
        self._states: dict[str, ConnectorInstallState] = {}
        self._load()

    def store_label(self) -> str:
        return str(self.storage_path)

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConnectorStoreUnreadable(f"connector store {self.store_label()} exists but could not be read: {exc}.") from exc
        if not raw.strip():
            raise ConnectorStoreUnreadable(f"connector store {self.store_label()} is empty.")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConnectorStoreUnreadable(f"connector store {self.store_label()} is not valid JSON: {exc}.") from exc
        if not isinstance(data, dict):
            raise ConnectorStoreUnreadable(f"connector store {self.store_label()} must contain an object.")
        if data.get("schema_version") != CONNECTOR_STORE_SCHEMA_VERSION:
            raise ConnectorStoreUnreadable(f"connector store {self.store_label()} has schema_version {data.get('schema_version')!r}; expected {CONNECTOR_STORE_SCHEMA_VERSION}.")
        try:
            for item in data.get("connectors", []):
                state = ConnectorInstallState.from_dict(item)
                self._states[state.connector_id] = state
        except ConnectorError as exc:
            raise ConnectorStoreUnreadable(f"connector store {self.store_label()} failed validation: {exc}.") from exc

    def _save(self) -> None:
        payload = {
            "schema_version": CONNECTOR_STORE_SCHEMA_VERSION,
            "connectors": [s.to_dict() for s in self._states.values()],
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
            raise ConnectorStoreUnreadable(f"could not persist connector store {self.store_label()}: {exc}") from exc

    def install(self, connector_id: str, *, enable: bool = True, rate_limit_per_min: int = 0) -> ConnectorInstallState:
        """Install a connector known to the catalog. Unknown ids are refused."""
        key = (connector_id or "").strip().lower()
        if self._catalog.get(key) is None:
            raise ConnectorError(f"unknown connector {connector_id!r}; not present in the catalog")
        # Validate the input on EVERY call. Re-installing an existing connector
        # reuses the stored state object, so the dataclass ``__post_init__``
        # validation does not run again — without this guard a negative rate
        # limit would be written silently on the second install.
        if not isinstance(rate_limit_per_min, int) or rate_limit_per_min < 0:
            raise ConnectorError("rate_limit_per_min must be a non-negative integer")
        with self._lock:
            state = self._states.get(key) or ConnectorInstallState(connector_id=key)
            state.enabled = bool(enable)
            state.rate_limit_per_min = rate_limit_per_min
            self._states[key] = state
            self._save()
        return state

    def set_enabled(self, connector_id: str, enabled: bool) -> ConnectorInstallState:
        key = (connector_id or "").strip().lower()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                raise ConnectorError(f"connector {connector_id!r} is not installed")
            state.enabled = bool(enabled)
            self._save()
            return state

    def record_health(self, connector_id: str, health: ConnectorHealth | str, *, error: str | None = None, rate_limit_per_min: int | None = None) -> ConnectorInstallState:
        key = (connector_id or "").strip().lower()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                raise ConnectorError(f"connector {connector_id!r} is not installed")
            state.health = ConnectorHealth(health) if not isinstance(health, ConnectorHealth) else health
            # Cap upstream error text so a chatty/ hostile provider cannot grow
            # the store without bound.
            state.last_error = None if error is None else str(error)[:2000]
            state.last_checked_at = _now()
            if rate_limit_per_min is not None:
                if not isinstance(rate_limit_per_min, int) or rate_limit_per_min < 0:
                    raise ConnectorError("rate_limit_per_min must be a non-negative integer")
                state.rate_limit_per_min = rate_limit_per_min
            self._save()
            return state

    def get(self, connector_id: str) -> ConnectorInstallState | None:
        with self._lock:
            return self._states.get((connector_id or "").strip().lower())

    def list(self) -> list[ConnectorInstallState]:
        with self._lock:
            return sorted(self._states.values(), key=lambda s: s.connector_id)

    def enabled_ids(self) -> list[str]:
        with self._lock:
            return sorted(s.connector_id for s in self._states.values() if s.enabled)

    def uninstall(self, connector_id: str) -> bool:
        with self._lock:
            key = (connector_id or "").strip().lower()
            if key not in self._states:
                return False
            del self._states[key]
            self._save()
            return True


__all__ = [
    "CONNECTOR_STORE_SCHEMA_VERSION",
    "ConnectorError",
    "ConnectorHealth",
    "ConnectorInstallState",
    "ConnectorStore",
    "ConnectorStoreUnreadable",
]
