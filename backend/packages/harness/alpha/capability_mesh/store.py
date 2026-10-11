"""Durable capability passport store: one JSON document per directory.

Deliberately simple, because the data contract in ``models.py`` is the whole
feature:

* One store is one directory holding a single ``passports.json``.
* Every write is atomic (temp file in the destination directory, fsync,
  ``os.replace``), so a crash mid-write leaves the previous document intact
  rather than a truncated one. UTF-8, ``ensure_ascii=False`` throughout.
* Reads are honest, following the arena/``GoalStore`` house rule: a corrupt
  or unreadable document reports ``StoreState(unreadable=True, passports=())``
  with the real cause in ``error`` — it NEVER silently resets to an
  empty-but-valid store, so a caller can distinguish "genuinely empty" from
  "could not read". ``get``/``list`` raise :class:`PassportStoreError` on an
  unreadable store rather than answering with nothing, and every mutator
  refuses to overwrite unreadable state: one bad sector must not become
  amnesia.
* A duplicate ``entity_id`` inside the file keeps the FIRST occurrence and
  records the duplicates in ``StoreState.error`` — rows are never silently
  merged.

The store is single-process JSON state: atomic against a crash, not
cross-process exactly-once. No lock is taken here; a second Gateway worker
concurrently mutating the same directory would race read-modify-write, and
callers that need coordination must serialize writes themselves.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.capability_mesh.models import CapabilityPassport, EntityKind

#: The single document the store owns inside its directory.
STORE_FILE_NAME = "passports.json"

#: Document schema version written by this implementation.
STORE_DOCUMENT_VERSION = 1


class PassportStoreError(RuntimeError):
    """The passport store is unusable or its document is corrupt."""


@dataclass(frozen=True)
class StoreState:
    """The honest read result: rows, plus whether they could be read at all.

    ``unreadable=True`` always travels with an empty ``passports`` tuple and
    a non-None ``error`` naming the cause; a readable store may still carry
    a non-None ``error`` for recoverable findings (duplicate ``entity_id``
    rows kept first). Callers that only look at ``passports`` cannot tell
    "empty" from "broken", which is exactly why they must look here.
    """

    passports: tuple[CapabilityPassport, ...]
    unreadable: bool
    error: str | None = None


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    """Write ``document`` to ``path`` atomically (temp file + ``os.replace``).

    The temp file lives in the destination directory so ``os.replace`` never
    crosses a filesystem, and it is flushed + fsynced before the replace so a
    crash leaves the previous document rather than a truncated one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class PassportStore:
    """Restart-safe persistence for capability passports."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def path(self) -> Path:
        return self._directory / STORE_FILE_NAME

    # ------------------------------------------------------------------ reads

    def read_state(self) -> StoreState:
        """The full honest state of the store (see :class:`StoreState`).

        A missing file is a genuinely empty, readable store — absence is not
        corruption. Everything else that fails to parse (bad JSON, wrong
        shape, a malformed entry, an entry that violates the honesty rules)
        reports the whole document unreadable rather than dropping rows.
        """
        path = self.path
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        except FileNotFoundError:
            return StoreState(passports=(), unreadable=False, error=None)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            return StoreState(passports=(), unreadable=True, error=f"passport store '{path}' is unreadable: {error}")
        if not isinstance(document, dict) or not isinstance(document.get("passports"), list):
            return StoreState(passports=(), unreadable=True, error=f"passport store '{path}' is not a passport document")

        passports: list[CapabilityPassport] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        for index, entry in enumerate(document["passports"]):
            try:
                passport = CapabilityPassport.from_dict(entry)
            except (TypeError, KeyError, ValueError) as error:
                return StoreState(passports=(), unreadable=True, error=f"passport store '{path}' entry #{index} is invalid: {error}")
            if passport.entity_id in seen:
                duplicates.append(passport.entity_id)
                continue
            seen.add(passport.entity_id)
            passports.append(passport)
        error = f"duplicate entity_id(s) in '{path}' kept first occurrence: {', '.join(duplicates)}" if duplicates else None
        return StoreState(passports=tuple(passports), unreadable=False, error=error)

    def get(self, entity_id: str) -> CapabilityPassport | None:
        """One passport by id, or None when genuinely absent.

        Raises :class:`PassportStoreError` on an unreadable store — "could
        not read" must never answer like "does not exist".
        """
        state = self._require_readable()
        for passport in state.passports:
            if passport.entity_id == entity_id:
                return passport
        return None

    def list(self, entity_type: EntityKind | str | None = None) -> list[CapabilityPassport]:
        """All passports, sorted by ``entity_id``; optionally one entity kind."""
        state = self._require_readable()
        passports = state.passports
        if entity_type is not None:
            try:
                kind = EntityKind(str(entity_type))
            except ValueError as error:
                allowed = ", ".join(member.value for member in EntityKind)
                raise ValueError(f"unknown entity_type {entity_type!r}; expected one of: {allowed}") from error
            passports = tuple(passport for passport in passports if passport.entity_type == kind)
        return sorted(passports, key=lambda passport: passport.entity_id)

    # ----------------------------------------------------------------- writes

    def upsert(self, passport: CapabilityPassport) -> None:
        """Insert or overwrite one passport, by ``entity_id``."""
        state = self._require_readable()
        rows = [existing for existing in state.passports if existing.entity_id != passport.entity_id]
        rows.append(passport)
        self._write(rows)

    def replace_all(self, passports: Iterable[CapabilityPassport]) -> None:
        """Replace the whole document with ``passports``.

        A duplicate ``entity_id`` in the input is refused by name before
        anything is written — silently keeping one would make the winner
        depend on iteration order.
        """
        self._require_readable()
        rows = list(passports)
        seen: set[str] = set()
        duplicates: list[str] = []
        for passport in rows:
            if passport.entity_id in seen:
                duplicates.append(passport.entity_id)
            seen.add(passport.entity_id)
        if duplicates:
            raise ValueError(f"replace_all refuses duplicate entity_id(s): {', '.join(sorted(set(duplicates)))}")
        self._write(rows)

    def remove(self, entity_id: str) -> bool:
        """Delete one passport; True when a row was actually removed."""
        state = self._require_readable()
        remaining = [passport for passport in state.passports if passport.entity_id != entity_id]
        if len(remaining) == len(state.passports):
            return False
        self._write(remaining)
        return True

    # --------------------------------------------------------------- internals

    def _require_readable(self) -> StoreState:
        state = self.read_state()
        if state.unreadable:
            raise PassportStoreError(state.error or f"passport store '{self.path}' is unreadable")
        return state

    def _write(self, passports: list[CapabilityPassport]) -> None:
        ordered = sorted(passports, key=lambda passport: passport.entity_id)
        document = {
            "version": STORE_DOCUMENT_VERSION,
            "passports": [passport.to_dict() for passport in ordered],
        }
        _atomic_write_json(self.path, document)


__all__ = [
    "STORE_DOCUMENT_VERSION",
    "STORE_FILE_NAME",
    "PassportStore",
    "PassportStoreError",
    "StoreState",
]
