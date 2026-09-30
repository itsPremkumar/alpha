"""Durable models for captured routines (demonstration -> reusable workflow).

A *routine* is a recorded, parameterised sequence of tool invocations that a
user demonstrated once and can replay on demand or on a schedule. This closes
the "learn the workflow by watching" gap: Alpha already ships authored skills
(``alpha.skills``), but there was no path that turns an observed multi-step
sequence into a reusable, re-runnable routine.

Design commitments mirror the rest of the harness:

- **Durable and loud when not.** The store is a single JSON document written
  atomically (tmp + ``os.replace``). A store that is unreadable or fails schema
  validation raises :class:`RoutineStoreUnreadable`; it never silently reverts
  to an empty roster, because silent loss of a captured routine is worse than a
  loud refusal to start. A *missing* file is a fresh install, not corruption.
- **Recorded arguments are untrusted.** Values captured from a demonstration
  are stored verbatim for audit but never participate in authorisation. Replay
  is a pure substitution step; the caller decides whether to execute.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Mapping

#: Bump when the on-disk shape changes incompatibly. A store written by a newer
#: version is REFUSED, not guessed at.
ROUTINE_STORE_SCHEMA_VERSION: Final = 1

_NAME_MAX = 64
_DESCRIPTION_MAX = 2000
_TOOL_MAX = 128
_MAX_STEPS = 500


class RoutineError(RuntimeError):
    """Base class for routine failures."""


class RoutineValidationError(RoutineError, ValueError):
    """A routine document is malformed or fails validation."""


class RoutineStoreUnreadable(RoutineError):
    """The on-disk store exists but could not be read or validated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise RoutineValidationError("routine name must be a string")
    clean = name.strip().lower()
    if not clean:
        raise RoutineValidationError("routine name must be non-empty")
    if len(clean) > _NAME_MAX:
        raise RoutineValidationError(f"routine name must be <= {_NAME_MAX} characters")
    if not all(ch.isalnum() or ch in "._-" for ch in clean):
        raise RoutineValidationError(f"routine name {name!r} may only contain letters, digits, dot, underscore and dash")
    if clean.startswith(".") or ".." in clean:
        raise RoutineValidationError(f"routine name {name!r} may not contain dot-runs")
    return clean


@dataclass
class RoutineStep:
    """One recorded tool invocation inside a routine."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    #: Argument keys whose captured value should be replaced on replay.
    parameterize: list[str] = field(default_factory=list)
    #: Optional free-text note the recorder attached to this step.
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise RoutineValidationError("step tool must be a non-empty string")
        self.tool = self.tool.strip()
        if len(self.tool) > _TOOL_MAX:
            raise RoutineValidationError(f"step tool must be <= {_TOOL_MAX} characters")
        if not isinstance(self.args, dict):
            raise RoutineValidationError("step args must be an object")
        if not isinstance(self.parameterize, list) or not all(isinstance(k, str) for k in self.parameterize):
            raise RoutineValidationError("step parameterize must be a list of strings")
        missing = [k for k in self.parameterize if k not in self.args]
        if missing:
            raise RoutineValidationError(f"parameterize references unknown args: {missing}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RoutineStep:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise RoutineValidationError(f"step has unknown field(s): {unknown}")
        if "tool" not in data:
            raise RoutineValidationError("step is missing required field 'tool'")
        return cls(**dict(data))


@dataclass
class Routine:
    """A named, replayable sequence of steps captured from a demonstration."""

    name: str
    description: str = ""
    steps: list[RoutineStep] = field(default_factory=list)
    #: Parameter name -> default value. A ``None`` default means the parameter
    #: is REQUIRED and must be supplied on replay.
    parameters: dict[str, Any] = field(default_factory=dict)
    source: str = "demonstration"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    version: int = 1

    def __post_init__(self) -> None:
        self.name = _validate_name(self.name)
        if not isinstance(self.description, str) or len(self.description) > _DESCRIPTION_MAX:
            raise RoutineValidationError(f"description must be a string <= {_DESCRIPTION_MAX} characters")
        if not isinstance(self.steps, list) or not all(isinstance(s, RoutineStep) for s in self.steps):
            raise RoutineValidationError("steps must be a list of RoutineStep")
        if len(self.steps) > _MAX_STEPS:
            raise RoutineValidationError(f"a routine may have at most {_MAX_STEPS} steps")
        if not isinstance(self.parameters, dict):
            raise RoutineValidationError("parameters must be an object")

    @property
    def required_parameters(self) -> list[str]:
        return sorted(k for k, v in self.parameters.items() if v is None)

    @property
    def optional_parameters(self) -> list[str]:
        return sorted(k for k, v in self.parameters.items() if v is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "parameters": dict(self.parameters),
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Routine:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise RoutineValidationError(f"routine has unknown field(s): {unknown}")
        if "name" not in data:
            raise RoutineValidationError("routine is missing required field 'name'")
        payload = dict(data)
        payload["steps"] = [RoutineStep.from_dict(s) for s in payload.get("steps", [])]
        return cls(**payload)


def _default_store_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "routines" / "routines.json"
    except Exception:
        return Path.cwd() / ".alpha" / "routines" / "routines.json"


class RoutineStore:
    """Durable, schema-versioned, thread-safe registry of captured routines."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_store_path()
        self._lock = threading.RLock()
        self._routines: dict[str, Routine] = {}
        self._load()

    def store_label(self) -> str:
        return str(self.storage_path)

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RoutineStoreUnreadable(f"routine store {self.store_label()} exists but could not be read: {exc}. Refusing to start empty.") from exc
        if not raw.strip():
            raise RoutineStoreUnreadable(f"routine store {self.store_label()} is empty. Refusing to treat an empty document as 'no routines'.")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RoutineStoreUnreadable(f"routine store {self.store_label()} is not valid JSON: {exc}.") from exc
        if not isinstance(data, dict):
            raise RoutineStoreUnreadable(f"routine store {self.store_label()} must contain an object.")
        if data.get("schema_version") != ROUTINE_STORE_SCHEMA_VERSION:
            raise RoutineStoreUnreadable(f"routine store {self.store_label()} has schema_version {data.get('schema_version')!r}; this build understands {ROUTINE_STORE_SCHEMA_VERSION}.")
        try:
            for item in data.get("routines", []):
                routine = Routine.from_dict(item)
                self._routines[routine.name] = routine
        except RoutineValidationError as exc:
            raise RoutineStoreUnreadable(f"routine store {self.store_label()} failed schema validation: {exc}. Refusing to partially load it.") from exc

    def _save(self) -> None:
        payload = {
            "schema_version": ROUTINE_STORE_SCHEMA_VERSION,
            "routines": [r.to_dict() for r in self._routines.values()],
            "updated_at": _now(),
        }
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.storage_path)
        except OSError as exc:
            raise RoutineStoreUnreadable(f"could not persist routine store {self.store_label()}: {exc}") from exc

    def save(self, routine: Routine) -> Routine:
        with self._lock:
            existing = self._routines.get(routine.name)
            if existing is not None:
                routine.version = existing.version + 1
            routine.updated_at = _now()
            self._routines[routine.name] = routine
            self._save()
        return routine

    def get(self, name: str) -> Routine | None:
        with self._lock:
            return self._routines.get((name or "").strip().lower())

    def list(self) -> list[Routine]:
        with self._lock:
            return sorted(self._routines.values(), key=lambda r: r.name)

    def delete(self, name: str) -> bool:
        with self._lock:
            key = (name or "").strip().lower()
            if key not in self._routines:
                return False
            del self._routines[key]
            self._save()
            return True


__all__ = [
    "ROUTINE_STORE_SCHEMA_VERSION",
    "Routine",
    "RoutineError",
    "RoutineStep",
    "RoutineStore",
    "RoutineStoreUnreadable",
    "RoutineValidationError",
]
