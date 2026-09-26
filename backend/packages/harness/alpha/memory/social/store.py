"""Thread-safe, bounded, per-scope persistence for social memory.

One JSON document owns all social records for one exact scope. Writes are
atomic and serialized by a per-scope re-entrant lock. Genuinely corrupt
documents are renamed for forensics and never overwritten in place. A
document that parses but declares a format this build does not implement is
*not* corruption: it is refused, left byte-for-byte in place, and written
only by a build that understands it. As with L1, the locks coordinate threads
in one store instance; independent processes need an external transactional
store before this can be described as exactly-once.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from alpha.agents.memory.l1.paths import atomic_write_text
from alpha.memory._store_format import (
    STORE_FORMAT_UNSUPPORTED,
    StoreFormatVerdict,
    classify_store_format,
    format_disclosure,
)

from .config import SocialConfig
from .models import (
    AudienceGrant,
    Counterpart,
    InteractionSummary,
    Relationship,
    SharedFact,
    normalize_scope,
)
from .paths import iter_state_paths, scope_bucket, state_path

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_STORE_ID = "social.state"
_T = TypeVar("_T")


class SocialStoreCapacityError(RuntimeError):
    """Raised when a new active grant would exceed the hard grant budget."""


class SocialStoreCorruptError(RuntimeError):
    """Raised when corrupt state cannot be preserved and isolation is uncertain."""


class SocialStoreUnavailable(RuntimeError):
    """Raised when a write is refused because the scope is not safely writable."""


@dataclass(slots=True)
class ScopeState:
    """Mutable state used only while a scope lock is held."""

    owner_scope: str
    counterparts: dict[str, Counterpart]
    relationships: dict[str, Relationship]
    facts: dict[str, SharedFact]
    grants: dict[str, AudienceGrant]
    summaries: list[InteractionSummary]


@dataclass(frozen=True, slots=True)
class SocialSnapshot:
    """Deep-copied point-in-time view of one scope."""

    owner_scope: str
    counterparts: list[Counterpart]
    relationships: list[Relationship]
    facts: list[SharedFact]
    grants: list[AudienceGrant]
    summaries: list[InteractionSummary]


class SocialStore:
    """Atomic per-scope social state with deterministic capacity bounds."""

    def __init__(self, config: SocialConfig | None = None) -> None:
        self.config = config or SocialConfig()
        self._root = self.config.resolved_root()
        self._max_grants = max(8, self.config.max_shared_facts * 4)
        self._max_summaries = max(8, self.config.max_counterparts * 4)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._cache: dict[str, ScopeState] = {}
        self._format_refusals: dict[str, StoreFormatVerdict] = {}

    @property
    def root(self) -> Path:
        return self._root

    @property
    def max_grants(self) -> int:
        return self._max_grants

    @property
    def max_summaries(self) -> int:
        return self._max_summaries

    def scope_path(self, owner_scope: str) -> Path:
        return state_path(self._root, owner_scope)

    def _scope_lock(self, owner_scope: str) -> threading.RLock:
        scope = normalize_scope(owner_scope)
        with self._locks_guard:
            lock = self._locks.get(scope)
            if lock is None:
                lock = threading.RLock()
                self._locks[scope] = lock
            return lock

    @staticmethod
    def _empty(scope: str) -> ScopeState:
        return ScopeState(
            owner_scope=scope,
            counterparts={},
            relationships={},
            facts={},
            grants={},
            summaries=[],
        )

    def _parse_document(self, scope: str, raw: dict[str, object]) -> ScopeState:
        if normalize_scope(str(raw.get("owner_scope") or "")) != scope:
            raise ValueError("owner scope does not match state path")
        doc = self._empty(scope)
        for item in raw.get("counterparts") or []:
            record = Counterpart.model_validate(item)
            doc.counterparts[record.id] = record
        for item in raw.get("relationships") or []:
            record = Relationship.model_validate(item)
            if record.owner_scope != scope:
                raise ValueError("relationship owner scope mismatch")
            doc.relationships[record.counterpart_id] = record
        for item in raw.get("facts") or []:
            record = SharedFact.model_validate(item)
            if record.owner_scope != scope:
                raise ValueError("fact owner scope mismatch")
            doc.facts[record.id] = record
        for item in raw.get("grants") or []:
            record = AudienceGrant.model_validate(item)
            if record.owner_scope != scope:
                raise ValueError("grant owner scope mismatch")
            doc.grants[record.id] = record
        for item in raw.get("summaries") or []:
            doc.summaries.append(InteractionSummary.model_validate(item))
        return doc

    def _refuse_format(self, scope: str, verdict: StoreFormatVerdict) -> ScopeState:
        """Hold a version-incompatible document untouched and refuse to write.

        The previous code raised ``ValueError("unsupported social state
        schema")`` from inside the same ``try`` that handles torn bytes, so a
        document written by a newer build was renamed to ``.corrupt-*`` and
        the scope silently restarted empty.  A version mismatch is not
        corruption; the file belongs to whichever build wrote it.
        """

        self._format_refusals[scope] = verdict
        logger.error("Social store refusing %s (%s); document left in place and writes blocked", verdict.path, format_disclosure(verdict))
        return self._empty(scope)

    def format_refusal(self, owner_scope: str) -> StoreFormatVerdict | None:
        """Return the version refusal held for a scope, or ``None``."""

        return self._format_refusals.get(normalize_scope(owner_scope))

    def read_status(self, owner_scope: str) -> str:
        """Report the load disclosure for a scope, loading it if needed."""

        scope = normalize_scope(owner_scope)
        with self._scope_lock(scope):
            self._load(scope)
        return STORE_FORMAT_UNSUPPORTED if scope in self._format_refusals else "ok"

    def _load(self, owner_scope: str) -> ScopeState:
        scope = normalize_scope(owner_scope)
        cached = self._cache.get(scope)
        if cached is not None:
            return cached
        path = self.scope_path(scope)
        if not path.exists():
            self._cache[scope] = self._empty(scope)
            return self._cache[scope]
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SocialStoreCorruptError(f"could not read social state at {path}: {exc}") from exc
        except (json.JSONDecodeError, ValueError, TypeError, UnicodeError) as exc:
            self._quarantine(scope, path, exc)
            self._cache[scope] = self._empty(scope)
            return self._cache[scope]
        if not isinstance(raw, dict):
            self._quarantine(scope, path, ValueError("social state root must be an object"))
            self._cache[scope] = self._empty(scope)
            return self._cache[scope]
        verdict = classify_store_format(store=_STORE_ID, path=path, raw=raw, supported_version=_SCHEMA_VERSION)
        if verdict.refusal:
            self._cache[scope] = self._refuse_format(scope, verdict)
            return self._cache[scope]
        try:
            doc = self._parse_document(scope, raw)
        except (ValueError, TypeError) as exc:
            self._quarantine(scope, path, exc)
            self._cache[scope] = self._empty(scope)
            return self._cache[scope]
        self._cache[scope] = doc
        return doc

    def _quarantine(self, scope: str, path: Path, exc: BaseException) -> None:
        """Preserve genuinely unreadable bytes; never used for a version mismatch."""

        backup = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
        try:
            path.replace(backup)
        except OSError as preserve_exc:
            raise SocialStoreCorruptError(f"could not preserve corrupt social state at {path}: {preserve_exc}") from exc
        logger.error("Social store preserved corrupt document %s as %s", path, backup.name)

    def _payload(self, doc: ScopeState) -> dict[str, object]:
        return {
            "schema": _SCHEMA_VERSION,
            "owner_scope": doc.owner_scope,
            "counterparts": [record.model_dump(mode="json") for record in doc.counterparts.values()],
            "relationships": [record.model_dump(mode="json") for record in doc.relationships.values()],
            "facts": [record.model_dump(mode="json") for record in doc.facts.values()],
            "grants": [record.model_dump(mode="json") for record in doc.grants.values()],
            "summaries": [record.model_dump(mode="json") for record in doc.summaries],
        }

    def _persist(self, doc: ScopeState) -> None:
        payload = self._payload(doc)
        atomic_write_text(
            self.scope_path(doc.owner_scope),
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
        )

    def _enforce_bounds(self, doc: ScopeState, now: float) -> None:
        expired_fact_ids = [fact_id for fact_id, fact in doc.facts.items() if fact.is_expired(now)]
        for fact_id in expired_fact_ids:
            doc.facts.pop(fact_id, None)

        if len(doc.facts) > self.config.max_shared_facts:
            ordered = sorted(doc.facts.values(), key=lambda fact: (fact.created_at, fact.id))
            excess = len(doc.facts) - self.config.max_shared_facts
            for fact in ordered[:excess]:
                doc.facts.pop(fact.id, None)

        if len(doc.counterparts) > self.config.max_counterparts:
            ordered = sorted(doc.counterparts.values(), key=lambda counterpart: (counterpart.last_seen, counterpart.id))
            excess = len(doc.counterparts) - self.config.max_counterparts
            evicted_ids = {counterpart.id for counterpart in ordered[:excess]}
            for counterpart_id in evicted_ids:
                doc.counterparts.pop(counterpart_id, None)
                doc.relationships.pop(counterpart_id, None)
            doc.summaries = [summary for summary in doc.summaries if summary.counterpart_id not in evicted_ids]

        for grant in list(doc.grants.values()):
            if grant.fact_id and grant.fact_id not in doc.facts:
                doc.grants.pop(grant.id, None)

        inactive_grants = [grant for grant in doc.grants.values() if not grant.is_active(now)]
        if len(inactive_grants) > self._max_grants:
            inactive_grants.sort(key=lambda grant: (grant.granted_at, grant.id))
            excess = len(inactive_grants) - self._max_grants
            for grant in inactive_grants[:excess]:
                doc.grants.pop(grant.id, None)

        if len(doc.summaries) > self._max_summaries:
            doc.summaries.sort(key=lambda summary: (summary.created_at, summary.id))
            del doc.summaries[: len(doc.summaries) - self._max_summaries]

    def update_scope(self, owner_scope: str, update: Callable[[ScopeState], _T], *, now: float | None = None) -> _T:
        """Run one locked read-modify-write and return the callback result."""

        scope = normalize_scope(owner_scope)
        at = time.time() if now is None else float(now)
        with self._scope_lock(scope):
            doc = self._load(scope)
            if scope in self._format_refusals:
                # Refuse rather than republish our own format over a document a
                # newer build still owns.  The refusal survives the cache reset,
                # so it is re-read from the gate on every attempt.
                raise SocialStoreUnavailable(format_disclosure(self._format_refusals[scope]))
            before = copy.deepcopy(doc)
            try:
                result = update(doc)
                self._enforce_bounds(doc, at)
                self._persist(doc)
            except Exception:
                self._cache[scope] = before
                raise
            return result

    def snapshot(self, owner_scope: str) -> SocialSnapshot:
        scope = normalize_scope(owner_scope)
        with self._scope_lock(scope):
            doc = self._load(scope)
            return SocialSnapshot(
                owner_scope=scope,
                counterparts=[record.model_copy(deep=True) for record in doc.counterparts.values()],
                relationships=[record.model_copy(deep=True) for record in doc.relationships.values()],
                facts=[record.model_copy(deep=True) for record in doc.facts.values()],
                grants=[record.model_copy(deep=True) for record in doc.grants.values()],
                summaries=[record.model_copy(deep=True) for record in doc.summaries],
            )

    def list_scope_ids(self) -> list[str]:
        """Discover persisted exact scopes without trusting path labels alone."""

        scopes: set[str] = set(self._cache)
        for path in iter_state_paths(self._root):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                scope = normalize_scope(str(raw.get("owner_scope") or ""))
                if path.parent.parent.name == scope_bucket(scope):
                    scopes.add(scope)
            except (AttributeError, json.JSONDecodeError, TypeError, ValueError, OSError):
                logger.warning("Social store could not identify scope from %s", path)
        return sorted(scopes)

    def list_counterparts(self, owner_scope: str) -> list[Counterpart]:
        return self.snapshot(owner_scope).counterparts

    def get_counterpart(self, owner_scope: str, counterpart_id: str) -> Counterpart | None:
        snapshot = self.snapshot(owner_scope)
        return next((item for item in snapshot.counterparts if item.id == counterpart_id), None)

    def list_relationships(self, owner_scope: str) -> list[Relationship]:
        return self.snapshot(owner_scope).relationships

    def get_relationship(self, owner_scope: str, counterpart_id: str) -> Relationship | None:
        snapshot = self.snapshot(owner_scope)
        return next((item for item in snapshot.relationships if item.counterpart_id == counterpart_id), None)

    def list_facts(self, owner_scope: str) -> list[SharedFact]:
        return self.snapshot(owner_scope).facts

    def get_fact(self, owner_scope: str, fact_id: str) -> SharedFact | None:
        snapshot = self.snapshot(owner_scope)
        return next((item for item in snapshot.facts if item.id == fact_id), None)

    def list_grants(self, owner_scope: str) -> list[AudienceGrant]:
        return self.snapshot(owner_scope).grants

    def get_grant(self, owner_scope: str, grant_id: str) -> AudienceGrant | None:
        snapshot = self.snapshot(owner_scope)
        return next((item for item in snapshot.grants if item.id == grant_id), None)

    def list_summaries(self, owner_scope: str) -> list[InteractionSummary]:
        return self.snapshot(owner_scope).summaries

    def put_counterpart(
        self,
        owner_scope: str,
        counterpart: Counterpart,
        *,
        now: float | None = None,
    ) -> Counterpart:
        scope = normalize_scope(owner_scope)

        def update(doc: ScopeState) -> Counterpart:
            doc.counterparts[counterpart.id] = counterpart.model_copy(deep=True)
            return counterpart.model_copy(deep=True)

        return self.update_scope(scope, update, now=now)

    def put_relationship(self, relationship: Relationship, *, now: float | None = None) -> Relationship:
        def update(doc: ScopeState) -> Relationship:
            doc.relationships[relationship.counterpart_id] = relationship.model_copy(deep=True)
            return relationship.model_copy(deep=True)

        return self.update_scope(relationship.owner_scope, update, now=now)

    def put_fact(self, fact: SharedFact, *, now: float | None = None) -> SharedFact:
        def update(doc: ScopeState) -> SharedFact:
            doc.facts[fact.id] = fact.model_copy(deep=True)
            return fact.model_copy(deep=True)

        return self.update_scope(fact.owner_scope, update, now=now)

    def put_grant(self, grant: AudienceGrant, *, now: float | None = None) -> AudienceGrant:
        def update(doc: ScopeState) -> AudienceGrant:
            existing = doc.grants.get(grant.id)
            if existing is None:
                active_count = sum(item.is_active(float(now if now is not None else time.time())) for item in doc.grants.values())
                if active_count >= self._max_grants:
                    raise SocialStoreCapacityError(f"active audience grant limit reached ({self._max_grants})")
            doc.grants[grant.id] = grant.model_copy(deep=True)
            return grant.model_copy(deep=True)

        return self.update_scope(grant.owner_scope, update, now=now)

    def remove_grants(self, owner_scope: str, grant_ids: set[str]) -> int:
        def update(doc: ScopeState) -> int:
            removed = 0
            for grant_id in grant_ids:
                if doc.grants.pop(grant_id, None) is not None:
                    removed += 1
            return removed

        return self.update_scope(owner_scope, update)


__all__ = [
    "ScopeState",
    "SocialSnapshot",
    "SocialStore",
    "SocialStoreCapacityError",
    "SocialStoreCorruptError",
    "SocialStoreUnavailable",
]
