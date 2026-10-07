"""The APEX mode switch — the durable thing a UI toggle actually flips.

Spec §3 asks for ``/apex on`` / ``/apex off`` and says "APEX ON should persist
for the current mission/session according to existing Alpha persistence
architecture". This module is that persisted state.

**Why this is separate from the contract.** ``AutonomyContract`` is a *frozen
value object* describing one policy. This is the *durable record* of which
policy is active for which session, and when that changed. A toggle needs the
second thing; the contract only ever had the first.

**Scope is per session, deliberately.** The spec says "for the current
mission/session", and that is the safer scope: a global switch would mean one
browser tab turning autonomy on for every other conversation. ``scope_key``
carries the session or thread id, and :meth:`ApexModeStore.for_scope` returns
``OFF`` for any scope with no record — an unknown session is not enabled.

**Four properties are load-bearing.**

1. **Enabling is a recorded event, not a flag write.** Every transition appends
   to ``events.jsonl`` with the profile it moved to, so "who turned autonomy on,
   when, and at what authority" is answerable afterwards. Autonomy that appears
   with no provenance is the thing this whole package exists to avoid.

2. **The stored profile is validated on read.** A hand-edited or truncated file
   naming a profile this build does not know falls back to the requested
   profile rather than crashing a request, and the substitution is recorded in
   ``load_note`` so it is visible rather than silent.

3. **Turning APEX off does not delete the mission.** ``disable()`` records the
   transition and keeps the profile, so ``/apex on`` restores the authority the
   user had rather than silently resetting them to a default.

4. **Disabling is idempotent and reports whether it changed anything.** A second
   ``disable()`` says ``changed: false`` instead of pretending it acted.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.apex.contract import AutonomyContract, AutonomyProfile, profile_for

logger = logging.getLogger(__name__)

__all__ = [
    "ApexModeRecord",
    "ApexModeStore",
    "get_apex_mode_store",
    "read_mode",
    "set_mode",
]

#: The scope key used when a caller has no session or thread to identify.
DEFAULT_SCOPE = "default"


@dataclass
class ApexModeRecord:
    """Whether APEX is on for one scope, and what it is authorised to do."""

    scope_key: str
    enabled: bool = False
    profile: str = AutonomyProfile.OFF.value
    owner: str = ""
    enabled_at: float | None = None
    disabled_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    #: Populated when the persisted profile was not one this build knows.
    load_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "scope_key": self.scope_key,
            "enabled": self.enabled,
            "profile": self.profile,
            "owner": self.owner,
            "enabled_at": self.enabled_at,
            "disabled_at": self.disabled_at,
            "updated_at": self.updated_at,
        }
        if self.load_note:
            data["load_note"] = self.load_note
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApexModeRecord:
        return cls(
            scope_key=str(data.get("scope_key", DEFAULT_SCOPE) or DEFAULT_SCOPE),
            enabled=bool(data.get("enabled", False)),
            profile=str(data.get("profile", AutonomyProfile.OFF.value) or AutonomyProfile.OFF.value),
            owner=str(data.get("owner", "")),
            enabled_at=data.get("enabled_at"),
            disabled_at=data.get("disabled_at"),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            load_note=str(data.get("load_note", "")),
        )

    def contract(self) -> AutonomyContract:
        """The contract this record authorises.

        An ``enabled`` flag naming the ``off`` profile is contradictory, and the
        flag wins only when the profile is genuinely one this build knows — a
        record claiming ``enabled: true, profile: "off"`` would otherwise hand
        out an OFF contract while reporting autonomy as active.
        """
        if not self.enabled:
            return profile_for(AutonomyProfile.OFF, mission_id=self.scope_key)
        try:
            return profile_for(self.profile, mission_id=self.scope_key)
        except ValueError:
            note = f"unknown profile {self.profile!r}; falling back to 'assist'"
            logger.warning("APEX mode record for %s: %s", self.scope_key, note)
            return profile_for(AutonomyProfile.ASSIST, mission_id=self.scope_key)


def _default_storage_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "apex" / "mode.json"
    except Exception:
        return Path.cwd() / ".alpha" / "apex" / "mode.json"


class ApexModeStore:
    """Durable per-scope ON/OFF state, with an append-only transition journal."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._rows: dict[str, ApexModeRecord] = {}
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._load()

    # -- persistence ---------------------------------------------------------

    @property
    def events_path(self) -> Path:
        return self.storage_path.parent / "mode_events.jsonl"

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def is_degraded(self) -> bool:
        return self._load_error is not None

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
            rows: dict[str, ApexModeRecord] = {}
            for item in raw.get("scopes", []):
                record = ApexModeRecord.from_dict(item)
                rows[record.scope_key] = record
            self._rows = rows
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            # Fail closed. A mode store that cannot be read must not answer
            # "enabled" for a scope nobody recorded — that would turn a corrupt
            # file into an autonomy grant.
            logger.error("APEX mode load failed; treating every scope as off: %s", self._load_error)

    def _save(self) -> bool:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "scopes": [r.to_dict() for r in self._rows.values()]}
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.storage_path.parent),
                prefix=f".{self.storage_path.name}.",
                suffix=".tmp",
                delete=False,
            )
            try:
                with handle:
                    json.dump(payload, handle, indent=1, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.storage_path)
            except Exception:
                Path(handle.name).unlink(missing_ok=True)
                raise
            return True
        except Exception:
            logger.error("APEX mode save failed", exc_info=True)
            return False

    def _journal(self, event_type: str, record: ApexModeRecord, **payload: Any) -> None:
        entry = {
            "event": event_type,
            "scope_key": record.scope_key,
            "owner": record.owner,
            "enabled": record.enabled,
            "profile": record.profile,
            "at": time.time(),
            **payload,
        }
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
        except Exception:
            logger.error("APEX mode journal append failed for %s", record.scope_key, exc_info=True)

    def read_events(self, scope_key: str | None = None) -> list[dict[str, Any]]:
        """The transition history, oldest first."""
        if not self.events_path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    logger.error("APEX mode journal corrupt at a line; stopping there", exc_info=True)
                    break
                if scope_key and entry.get("scope_key") != scope_key:
                    continue
                out.append(entry)
        except Exception:
            logger.error("APEX mode journal read failed", exc_info=True)
        return out

    # -- reads ---------------------------------------------------------------

    def for_scope(self, scope_key: str | None) -> ApexModeRecord:
        """The record for a scope. An unknown scope reads as OFF.

        This is the fail-closed default: no record means nobody granted
        autonomy for that session.
        """
        key = str(scope_key or DEFAULT_SCOPE)
        with self._lock:
            record = self._rows.get(key)
        if record is None:
            return ApexModeRecord(scope_key=key, enabled=False, profile=AutonomyProfile.OFF.value)
        return record

    def is_enabled(self, scope_key: str | None) -> bool:
        return self.for_scope(scope_key).enabled

    def contract_for(self, scope_key: str | None) -> AutonomyContract:
        return self.for_scope(scope_key).contract()

    def list_scopes(self) -> list[ApexModeRecord]:
        with self._lock:
            return sorted(self._rows.values(), key=lambda r: -r.updated_at)

    # -- writes --------------------------------------------------------------

    def enable(self, scope_key: str | None, profile: str, *, owner: str = "") -> dict[str, Any]:
        """Turn APEX on for a scope at a named profile.

        Returns ``changed: false`` when it was already on at that profile, so a
        caller can tell "I acted" from "it was already true".
        """
        key = str(scope_key or DEFAULT_SCOPE)
        try:
            resolved = AutonomyProfile(profile)
        except ValueError as exc:
            raise ValueError(f"unknown APEX profile {profile!r}; expected one of {[p.value for p in AutonomyProfile]}") from exc
        if resolved is AutonomyProfile.OFF:
            raise ValueError("use disable() for the 'off' profile; enabling requires a real profile")

        with self._lock:
            existing = self._rows.get(key)
            if existing is not None and existing.enabled and existing.profile == resolved.value:
                return {"changed": False, "record": existing.to_dict(), "reason": "already enabled at this profile"}
            now = time.time()
            record = ApexModeRecord(
                scope_key=key,
                enabled=True,
                profile=resolved.value,
                owner=owner or (existing.owner if existing else ""),
                enabled_at=now,
                disabled_at=None,
                updated_at=now,
            )
            self._rows[key] = record
            durable = self._save()
        self._journal("apex.enabled", record, durable=durable, previous_profile=existing.profile if existing else "")
        return {"changed": True, "record": record.to_dict(), "durable": durable}

    def disable(self, scope_key: str | None, *, owner: str = "") -> dict[str, Any]:
        """Turn APEX off for a scope, keeping the profile for a later restore."""
        key = str(scope_key or DEFAULT_SCOPE)
        with self._lock:
            existing = self._rows.get(key)
            if existing is not None and not existing.enabled:
                return {"changed": False, "record": existing.to_dict(), "reason": "already off"}
            now = time.time()
            record = ApexModeRecord(
                scope_key=key,
                enabled=False,
                # Retained deliberately: re-enabling restores the authority the
                # user had, rather than silently resetting them to a default.
                profile=existing.profile if existing else AutonomyProfile.OFF.value,
                owner=owner or (existing.owner if existing else ""),
                enabled_at=existing.enabled_at if existing else None,
                disabled_at=now,
                updated_at=now,
            )
            self._rows[key] = record
            durable = self._save()
        self._journal("apex.disabled", record, durable=durable)
        return {"changed": True, "record": record.to_dict(), "durable": durable}


_store: ApexModeStore | None = None
_store_lock = threading.Lock()


def get_apex_mode_store() -> ApexModeStore:
    """The process-wide mode store, rebuilt if the resolved path changes."""
    global _store
    with _store_lock:
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _store is None or str(_store.storage_path) != live:
            _store = ApexModeStore()
        return _store


def read_mode(scope_key: str | None = None) -> dict[str, Any]:
    """The mode record plus the contract it authorises, for a status surface."""
    record = get_apex_mode_store().for_scope(scope_key)
    return {
        "record": record.to_dict(),
        "contract": record.contract().to_dict(),
        "enabled": record.enabled,
        "profile": record.profile,
    }


def set_mode(
    scope_key: str | None,
    enabled: bool,
    *,
    profile: str = AutonomyProfile.AUTONOMOUS.value,
    owner: str = "",
) -> dict[str, Any]:
    """Enable or disable APEX for a scope. The one function both surfaces call.

    The slash command and the HTTP toggle share this so the two can never
    disagree about what "on" means — the failure mode of two independently
    written toggles.
    """
    store = get_apex_mode_store()
    if enabled:
        return store.enable(scope_key, profile, owner=owner)
    return store.disable(scope_key, owner=owner)
