"""Durable, owner-scoped storage for Company OS records.

The discipline here is copied from :mod:`alpha.routines` because it already
solved the same problem correctly, and because getting it wrong is silent:

* **Atomic.** Every write is ``tmp`` + :func:`os.replace`, so a crash mid-write
  leaves the previous document intact rather than a half-written one.
* **Versioned.** A document written by a newer schema is **refused**, not
  guessed at. Reinterpreting an unknown shape is how a company quietly loses its
  workforce.
* **Loud when unreadable.** A missing file is a fresh install. A file that exists
  but will not parse or validate raises :class:`CompanyStoreUnreadable` rather
  than reverting to an empty roster, because an empty roster reads exactly like
  "this owner has no companies" and would be indistinguishable from data loss.
* **Append-only ledger.** Tick records go to JSONL and are never rewritten, so
  the audit trail survives a later repair of the summary document.

**Scope.** This adapter is atomic and restart-recoverable for a single Gateway
process. It is not a shared multi-worker lease repository, and must never be
described as cross-process exactly-once.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from alpha.company_os.models import (
    COMPANY_STORE_SCHEMA_VERSION,
    Company,
    TickRecord,
)

logger = logging.getLogger(__name__)

_STORE_DIRNAME = "companies"
_INDEX_FILENAME = "registry.json"
_RECORD_FILENAME = "company.json"
_LEDGER_FILENAME = "ledger.jsonl"


class CompanyStoreError(RuntimeError):
    """Base class for company store failures."""


class CompanyStoreUnreadable(CompanyStoreError):
    """The store exists on disk but could not be read or validated.

    Raised instead of silently degrading to an empty roster. Silent loss of a
    company is worse than a loud refusal to serve.
    """


class CompanyNotFound(CompanyStoreError):
    """No such company is visible to this owner."""


def _default_root() -> Path:
    """Resolve the company store root under the writable runtime home.

    Uses ``runtime_home()`` so the Gateway, the Electron shell, Docker, and an
    embedded client all share one location instead of resolving CWD at import
    time.
    """
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / _STORE_DIRNAME
    except Exception:
        return Path.cwd() / ".alpha" / _STORE_DIRNAME


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return data


def _safe_slug(company_id: str) -> str:
    """Validate an id before it is ever interpolated into a filesystem path.

    Ids are minted server-side, but a store is also read from disk where a hand
    edit or a migration could introduce anything, so the path component is
    constrained rather than trusted.
    """
    cleaned = "".join(ch for ch in str(company_id) if ch.isalnum() or ch in ("-", "_"))
    if not cleaned or cleaned != str(company_id):
        raise CompanyStoreError(f"Unsafe company id: {company_id!r}")
    return cleaned


class CompanyStore:
    """Owner-scoped durable store for companies and their tick ledgers."""

    def __init__(self, root: Path | str | None = None):
        self._root = Path(root) if root else _default_root()
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root

    # -- paths ------------------------------------------------------------- #

    def _index_path(self) -> Path:
        return self._root / _INDEX_FILENAME

    def _company_dir(self, company_id: str) -> Path:
        return self._root / _safe_slug(company_id)

    def _record_path(self, company_id: str) -> Path:
        return self._company_dir(company_id) / _RECORD_FILENAME

    def _ledger_path(self, company_id: str) -> Path:
        return self._company_dir(company_id) / _LEDGER_FILENAME

    # -- index ------------------------------------------------------------- #

    def _read_index(self) -> dict[str, Any]:
        path = self._index_path()
        if not path.exists():
            return {"version": COMPANY_STORE_SCHEMA_VERSION, "owners": {}}
        try:
            data = _read_json(path)
        except Exception as exc:
            raise CompanyStoreUnreadable(f"Company index at {path} is unreadable: {exc}") from exc
        version = data.get("version")
        if isinstance(version, int) and version > COMPANY_STORE_SCHEMA_VERSION:
            raise CompanyStoreUnreadable(f"Company index version {version} is newer than the supported schema {COMPANY_STORE_SCHEMA_VERSION}. Refusing to reinterpret it — upgrade Alpha or restore a compatible store.")
        owners = data.get("owners")
        if not isinstance(owners, dict):
            raise CompanyStoreUnreadable(f"Company index at {path} has no 'owners' mapping.")
        return data

    def _write_index(self, data: dict[str, Any]) -> None:
        data["version"] = COMPANY_STORE_SCHEMA_VERSION
        _atomic_write_text(self._index_path(), json.dumps(data, indent=2, sort_keys=True))

    def _owner_key(self, owner_id: str | None) -> str:
        return (owner_id or "default").strip() or "default"

    def _owner_ids(self, owner_id: str | None, index: dict[str, Any]) -> list[str]:
        """Company ids visible to an owner, sorted for a stable listing.

        The index is the fast path. A directory scan is a *rescue* path that
        recovers a company written but never indexed — and every rescued id is
        **ownership-checked by reading its document** before it is returned. An
        unguarded scan would leak another tenant's companies into this owner's
        listing, which is precisely the boundary this class exists to hold.
        """
        owners = index.get("owners") or {}
        key = self._owner_key(owner_id)
        ids = {str(v) for v in (owners.get(key) or []) if v}
        if self._root.exists():
            for child in self._root.iterdir():
                if not child.is_dir() or not (child / _RECORD_FILENAME).exists():
                    continue
                if child.name in ids:
                    continue
                try:
                    data = _read_json(child / _RECORD_FILENAME)
                except Exception:
                    logger.warning("Skipping unreadable company document while listing: %s", child / _RECORD_FILENAME)
                    continue
                if self._owner_key(data.get("owner_id")) == key:
                    ids.add(child.name)
        return sorted(ids)

    # -- public API -------------------------------------------------------- #

    def list_company_ids(self, owner_id: str | None) -> list[str]:
        """Ids of every company owned by ``owner_id``."""
        with self._lock:
            return self._owner_ids(owner_id, self._read_index())

    def list_owner_ids(self) -> list[str]:
        """Every owner key the index knows about.

        This is a **system-wide** read and exists for one caller: the background
        autonomy sweep, which has no request context and therefore no owner. It
        must not be reachable from an HTTP route — the router resolves the owner
        from the authenticated principal and never enumerates tenants.
        """
        with self._lock:
            index = self._read_index()
        return sorted(str(k) for k in (index.get("owners") or {}))

    def get(self, company_id: str, owner_id: str | None) -> Company | None:
        """Load one company, or ``None`` when it does not exist for this owner.

        An existing document belonging to *another* owner is reported as ``None``,
        not as a permission error, so the route layer can answer 404 without
        confirming the existence of another tenant's company.
        """
        path = self._record_path(company_id)
        if not path.exists():
            return None
        try:
            data = _read_json(path)
        except Exception as exc:
            raise CompanyStoreUnreadable(f"Company document at {path} is unreadable: {exc}") from exc
        company = self._parse_company(data, path)
        key = self._owner_key(owner_id)
        if self._owner_key(company.owner_id) != key:
            return None
        return company

    def require(self, company_id: str, owner_id: str | None) -> Company:
        company = self.get(company_id, owner_id)
        if company is None:
            raise CompanyNotFound(f"Company '{company_id}' not found.")
        return company

    def _parse_company(self, data: dict[str, Any], path: Path) -> Company:
        revision = data.get("revision")
        if isinstance(revision, int) and revision > COMPANY_STORE_SCHEMA_VERSION:
            raise CompanyStoreUnreadable(f"Company document at {path} has revision {revision}, newer than the supported schema {COMPANY_STORE_SCHEMA_VERSION}. Refusing to reinterpret it.")
        payload = {k: v for k, v in data.items() if k != "schema_version"}
        try:
            return Company.model_validate(payload)
        except Exception as exc:
            raise CompanyStoreUnreadable(f"Company document at {path} failed validation: {exc}") from exc

    def save(self, company: Company, owner_id: str | None) -> Company:
        """Persist a company and index it under its owner."""
        with self._lock:
            key = self._owner_key(owner_id)
            if self._owner_key(company.owner_id) != key:
                raise CompanyStoreError("Refusing to save a company under an owner that does not match the request scope.")
            company.touch()
            self._company_dir(company.company_id).mkdir(parents=True, exist_ok=True)
            _atomic_write_text(
                self._record_path(company.company_id),
                json.dumps(company.model_dump(mode="json"), indent=2, sort_keys=True),
            )
            index = self._read_index()
            owners = index.setdefault("owners", {})
            ids = {str(v) for v in (owners.get(key) or [])}
            ids.add(company.company_id)
            owners[key] = sorted(ids)
            self._write_index(index)
            return company

    def delete(self, company_id: str, owner_id: str | None) -> None:
        """Remove a company and its ledger.

        Callers archive rather than delete in normal operation; this exists for
        an operator explicitly discarding a record.
        """
        with self._lock:
            if self.get(company_id, owner_id) is None:
                raise CompanyNotFound(f"Company '{company_id}' not found.")
            directory = self._company_dir(company_id)
            for name in (_RECORD_FILENAME, _LEDGER_FILENAME):
                target = directory / name
                if target.exists():
                    target.unlink()
            try:
                directory.rmdir()
            except OSError:
                # Non-empty (e.g. an evidence subdirectory): leave the shell in
                # place rather than deleting files this module does not own.
                logger.info("Company directory %s not empty after delete; left in place", directory)
            index = self._read_index()
            owners = index.setdefault("owners", {})
            key = self._owner_key(owner_id)
            owners[key] = [v for v in (owners.get(key) or []) if v != company_id]
            self._write_index(index)

    # -- tick ledger ------------------------------------------------------- #

    def append_tick(self, record: TickRecord) -> TickRecord:
        """Append one tick to the company's JSONL ledger.

        Append-only: a tick is never rewritten, so the record of what the loop
        actually did survives a later repair of the summary document.
        """
        with self._lock:
            path = self._ledger_path(record.company_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.model_dump(mode="json"), sort_keys=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return record

    def list_ticks(self, company_id: str, owner_id: str | None, limit: int = 50) -> list[TickRecord]:
        """The most recent ticks, newest first.

        A malformed line is skipped with a warning rather than aborting the read:
        one torn append must not make the whole ledger unreadable.
        """
        with self._lock:
            if self.get(company_id, owner_id) is None:
                raise CompanyNotFound(f"Company '{company_id}' not found.")
            path = self._ledger_path(company_id)
            if not path.exists():
                return []
            records: list[TickRecord] = []
            try:
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(TickRecord.model_validate(json.loads(line)))
                        except Exception as exc:
                            logger.warning("Skipping malformed company tick line in %s: %s", path, exc)
            except Exception as exc:
                raise CompanyStoreUnreadable(f"Tick ledger at {path} is unreadable: {exc}") from exc
        return list(reversed(records))[: max(1, limit)]

    def tick_count(self, company_id: str) -> int:
        path = self._ledger_path(company_id)
        if not path.exists():
            return 0
        try:
            with open(path, encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:
            return 0

    def forget_owner(self, owner_id: str) -> None:
        """Drop the index entry for an owner. Does not delete company documents."""
        with self._lock:
            index = self._read_index()
            owners = index.setdefault("owners", {})
            owners.pop(self._owner_key(owner_id), None)
            self._write_index(index)


_STORE: CompanyStore | None = None
_STORE_LOCK = threading.Lock()


def get_company_store(root: Path | str | None = None) -> CompanyStore:
    """Process-wide store accessor. Pass ``root`` only in tests."""
    global _STORE
    if root is not None:
        return CompanyStore(root)
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = CompanyStore()
    return _STORE


def reset_company_store() -> None:
    """Drop the cached store. Tests use this to isolate the runtime home."""
    global _STORE
    with _STORE_LOCK:
        _STORE = None
