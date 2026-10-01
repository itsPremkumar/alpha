"""Automations: scheduled, repeatable tasks (RRULE-style).

WorkBuddy lets a tested workflow become an *automation* — a task that runs on a
schedule (once / daily / weekly / monthly / yearly) outside the live session.
This module owns the schedule model, next-run computation, RRULE emission, and a
durable store. The scheduler that actually fires runs is separate; this is the
declarative layer a user edits.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Final, Mapping

AUTOMATION_STORE_SCHEMA_VERSION: Final = 1

_BYDAY = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


class AutomationError(RuntimeError):
    """Base class for automation failures."""


class AutomationValidationError(AutomationError, ValueError):
    """A schedule or automation document is malformed."""


class AutomationStoreUnreadable(AutomationError):
    """The on-disk store exists but could not be read/validated."""


class Frequency(str, Enum):
    """How often an automation repeats."""

    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_hhmm(value: str) -> time:
    try:
        hour_s, minute_s = str(value).split(":")
        hour, minute = int(hour_s), int(minute_s)
    except (ValueError, AttributeError) as exc:
        raise AutomationValidationError(f"time must be 'HH:MM', got {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise AutomationValidationError(f"time {value!r} is out of range")
    return time(hour, minute)


@dataclass(frozen=True)
class AutomationSchedule:
    """A recurrence rule. Fields are interpreted per ``frequency``."""

    frequency: Frequency
    at: str = "09:00"           # HH:MM local
    weekday: int | None = None   # 0=Mon .. 6=Sun, for WEEKLY
    day_of_month: int | None = None  # 1..31, for MONTHLY/YEARLY
    month: int | None = None     # 1..12, for YEARLY
    date: str | None = None      # ISO date/datetime, for ONCE

    def __post_init__(self) -> None:
        if not isinstance(self.frequency, Frequency):
            object.__setattr__(self, "frequency", Frequency(self.frequency))
        _parse_hhmm(self.at)
        if self.frequency == Frequency.WEEKLY and (self.weekday is None or not 0 <= self.weekday <= 6):
            raise AutomationValidationError("WEEKLY requires weekday in 0..6 (Mon..Sun)")
        if self.frequency in (Frequency.MONTHLY, Frequency.YEARLY) and (self.day_of_month is None or not 1 <= self.day_of_month <= 31):
            raise AutomationValidationError("MONTHLY/YEARLY require day_of_month in 1..31")
        if self.frequency == Frequency.YEARLY and (self.month is None or not 1 <= self.month <= 12):
            raise AutomationValidationError("YEARLY requires month in 1..12")
        if self.frequency == Frequency.ONCE and not self.date:
            raise AutomationValidationError("ONCE requires a date")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["frequency"] = self.frequency.value
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AutomationSchedule:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise AutomationValidationError(f"schedule has unknown field(s): {unknown}")
        payload = dict(data)
        payload["frequency"] = Frequency(payload["frequency"])
        return cls(**payload)


def next_run(schedule: AutomationSchedule, after: datetime) -> datetime | None:
    """Return the first occurrence strictly after *after*, or ``None``.

    ``after`` and the result share the schedule's naive/local convention.
    """
    hhmm = _parse_hhmm(schedule.at)
    freq = schedule.frequency

    if freq == Frequency.ONCE:
        raw = schedule.date or ""
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise AutomationValidationError(f"ONCE date {raw!r} is not ISO-8601") from exc
        if dt.tzinfo is not None and after.tzinfo is None:
            dt = dt.replace(tzinfo=None)
        return dt if dt > after else None

    candidate = datetime.combine(after.date(), hhmm)

    if freq == Frequency.DAILY:
        return candidate if candidate > after else candidate + timedelta(days=1)

    if freq == Frequency.WEEKLY:
        assert schedule.weekday is not None
        days_ahead = (schedule.weekday - after.weekday()) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= after:
            candidate += timedelta(days=7)
        return candidate

    if freq in (Frequency.MONTHLY, Frequency.YEARLY):
        assert schedule.day_of_month is not None
        year, month = after.year, after.month
        # Scan forward up to ~10 years so a leap-day (Feb 29) yearly rule is
        # reachable; skip months that lack the target day.
        for _ in range(120):
            if freq == Frequency.YEARLY and schedule.month is not None and month != schedule.month:
                month += 1
                if month > 12:
                    month, year = 1, year + 1
                continue
            try:
                cand = datetime(year, month, schedule.day_of_month, hhmm.hour, hhmm.minute)
            except ValueError:
                month += 1
                if month > 12:
                    month, year = 1, year + 1
                continue
            if cand > after:
                return cand
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return None

    return None


def to_rrule(schedule: AutomationSchedule) -> str | None:
    """Emit an RFC-5545 RRULE for the schedule (``None`` for a one-shot)."""
    hhmm = _parse_hhmm(schedule.at)
    head = f"BYHOUR={hhmm.hour};BYMINUTE={hhmm.minute}"
    if schedule.frequency == Frequency.ONCE:
        return None
    if schedule.frequency == Frequency.DAILY:
        return f"FREQ=DAILY;{head}"
    if schedule.frequency == Frequency.WEEKLY:
        assert schedule.weekday is not None
        return f"FREQ=WEEKLY;BYDAY={_BYDAY[schedule.weekday]};{head}"
    if schedule.frequency == Frequency.MONTHLY:
        return f"FREQ=MONTHLY;BYMONTHDAY={schedule.day_of_month};{head}"
    return f"FREQ=YEARLY;BYMONTH={schedule.month};BYMONTHDAY={schedule.day_of_month};{head}"


@dataclass
class Automation:
    """A named, scheduled task."""

    id: str
    name: str
    prompt: str
    schedule: AutomationSchedule
    enabled: bool = True
    workspace: str = ""
    connectors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise AutomationValidationError("automation id must be a non-empty string")
        if not self.name:
            raise AutomationValidationError("automation name must be non-empty")
        if not self.prompt:
            raise AutomationValidationError("automation prompt must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule": self.schedule.to_dict(),
            "enabled": self.enabled,
            "workspace": self.workspace,
            "connectors": list(self.connectors),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Automation:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise AutomationValidationError(f"automation has unknown field(s): {unknown}")
        payload = dict(data)
        payload["schedule"] = AutomationSchedule.from_dict(payload["schedule"])
        return cls(**payload)


def _default_store_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "automations" / "automations.json"
    except Exception:
        return Path.cwd() / ".alpha" / "automations" / "automations.json"


class AutomationStore:
    """Durable, schema-versioned, thread-safe registry of automations."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_store_path()
        self._lock = threading.RLock()
        self._items: dict[str, Automation] = {}
        self._load()

    def store_label(self) -> str:
        return str(self.storage_path)

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AutomationStoreUnreadable(f"automation store {self.store_label()} could not be read: {exc}.") from exc
        if not raw.strip():
            raise AutomationStoreUnreadable(f"automation store {self.store_label()} is empty.")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AutomationStoreUnreadable(f"automation store {self.store_label()} is not valid JSON: {exc}.") from exc
        if not isinstance(data, dict):
            raise AutomationStoreUnreadable(f"automation store {self.store_label()} must contain an object.")
        if data.get("schema_version") != AUTOMATION_STORE_SCHEMA_VERSION:
            raise AutomationStoreUnreadable(f"automation store {self.store_label()} schema_version {data.get('schema_version')!r} != {AUTOMATION_STORE_SCHEMA_VERSION}.")
        try:
            for item in data.get("automations", []):
                a = Automation.from_dict(item)
                self._items[a.id] = a
        except (AutomationValidationError, KeyError, TypeError) as exc:
            raise AutomationStoreUnreadable(f"automation store {self.store_label()} failed validation: {exc}.") from exc

    def _save(self) -> None:
        payload = {
            "schema_version": AUTOMATION_STORE_SCHEMA_VERSION,
            "automations": [a.to_dict() for a in self._items.values()],
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
            raise AutomationStoreUnreadable(f"could not persist automation store {self.store_label()}: {exc}") from exc

    def add(self, automation: Automation) -> Automation:
        with self._lock:
            if automation.id in self._items:
                raise AutomationValidationError(f"automation {automation.id!r} already exists")
            self._items[automation.id] = automation
            self._save()
        return automation

    def get(self, automation_id: str) -> Automation | None:
        with self._lock:
            return self._items.get((automation_id or "").strip())

    def list(self) -> list[Automation]:
        with self._lock:
            return sorted(self._items.values(), key=lambda a: a.name.lower())

    def enabled(self) -> list[Automation]:
        with self._lock:
            return [a for a in self.list() if a.enabled]

    def set_enabled(self, automation_id: str, enabled: bool) -> Automation:
        key = (automation_id or "").strip()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                raise AutomationError(f"automation {automation_id!r} not found")
            item.enabled = bool(enabled)
            self._save()
            return item

    def delete(self, automation_id: str) -> bool:
        with self._lock:
            key = (automation_id or "").strip()
            if key not in self._items:
                return False
            del self._items[key]
            self._save()
            return True


__all__ = [
    "AUTOMATION_STORE_SCHEMA_VERSION",
    "Automation",
    "AutomationError",
    "AutomationSchedule",
    "AutomationStore",
    "AutomationStoreUnreadable",
    "AutomationValidationError",
    "Frequency",
    "next_run",
    "to_rrule",
]
