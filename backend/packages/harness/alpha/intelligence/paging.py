"""Disk -> RAM cache -> resident paging for the expert pool (prompt §23).

Adapted from the reference implementation at the level Alpha needs
-------------------------------------------------------------------
The idea worth keeping is that **the pool must not have to be resident**. A
fabric with 64 experts and a 4-slot resident tier is fine; the failure mode is a
pool that grows to fit memory. This module therefore separates three tiers and
makes residency an explicit, budgeted, evictable resource:

* **Disk** — :class:`ExpertStore`, the durable source. Always available.
* **RAM cache** — :class:`ExpertCache`, bounded by ``ram_cache_size``.
* **Resident / active** — the top ``max_resident`` by utility.

:mod:`alpha.intelligence.paging` defines the interfaces; :mod:`paging` also
provides :class:`LRUResidentSet`, the one shipped policy. A replacement policy
implements :class:`EvictionPolicy` and is injected — the same replaceable-seam
argument that governs :mod:`alpha.intelligence.scoring`.

The two invariants that make this safe
--------------------------------------
1. **An executing expert is never evicted.** :meth:`ResidentSet.evict` skips
   pinned entries, and :func:`PagingManager.acquire` pins for the duration of a
   use. Evicting a running expert would fail it mid-execution and attribute the
   failure to the expert.
2. **Eviction is a cache miss, not a data loss.** Evicting from the RAM cache
   only drops the in-memory copy; :func:`PagingManager.acquire` reloads from
   :class:`ExpertStore`. There is no path in this module that deletes a record.

What is deliberately *not* here
-------------------------------
Real VRAM accounting. Alpha's experts are prompt/policy records, not GPU
weights, so ``max_resident`` models "actively materialised" rather than
"bytes on a GPU". Wiring this to an actual accelerator would mean a real
``torch``/CUDA dependency for no current benefit; the seam is
:class:`ResidentSet`, so that change is local to this module.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from alpha.intelligence.models import ExpertIdentity, ExpertRecord

__all__ = [
    "PagingError",
    "ExpertStore",
    "ExpertCache",
    "ResidentSet",
    "EvictionPolicy",
    "LRUResidentSet",
    "PagingManager",
    "PagingSnapshot",
]


class PagingError(RuntimeError):
    """Base class for paging failures."""


@dataclass
class PagingSnapshot:
    """One point-in-time residency report.

    Returned by :meth:`ResidentSet.snapshot` so the UI can render real runtime
    state (prompt §48) instead of a guess.
    """

    resident: list[str]
    cache: list[str]
    pinned: list[str]
    capacity: int
    policy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "resident": list(self.resident),
            "cache": list(self.cache),
            "pinned": list(self.pinned),
            "capacity": self.capacity,
            "policy": self.policy,
        }


# ---------------------------------------------------------------------------
# Tier interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class ExpertStore(Protocol):
    """The durable tier. Every implementation must survive losing the other two."""

    def load(self, expert_id: str) -> ExpertRecord | None:
        """Return the record, or ``None`` when it does not exist."""
        ...

    def exists(self, expert_id: str) -> bool: ...


@runtime_checkable
class ResidentSet(Protocol):
    """The active tier plus its eviction policy."""

    def admit(self, expert_id: str) -> bool:
        """Make ``expert_id`` resident. ``False`` when it could not be admitted."""
        ...

    def release(self, expert_id: str) -> None: ...

    def contains(self, expert_id: str) -> bool: ...

    def snapshot(self) -> PagingSnapshot: ...


@runtime_checkable
class EvictionPolicy(Protocol):
    """Chooses victims when the resident tier is over capacity."""

    name: str

    def victims(self, entries: list[tuple[str, float, bool]]) -> list[str]:
        """Return expert ids to evict, best-first.

        Args:
            entries: ``(expert_id, score, pinned)`` for each resident entry.
                Higher ``score`` means more worth keeping.
        """
        ...


# ---------------------------------------------------------------------------
# Shipped implementations
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    record: ExpertRecord
    score: float
    pinned: bool = False


class ExpertCache:
    """Bounded in-RAM LRU cache over an :class:`ExpertStore`.

    Plain LRU, deliberately: the *policy* lives in the resident set, so a
    utility-aware cache would be tuning two coupled things at once. The cache's
    only job is "keep recently used records around without growing".
    """

    name = "lru"

    def __init__(self, store: ExpertStore, capacity: int) -> None:
        if capacity < 1:
            raise PagingError(f"RAM cache capacity must be >= 1, got {capacity}")
        self._store = store
        self.capacity = int(capacity)
        self._lock = threading.RLock()
        self._entries: dict[str, _CacheEntry] = {}
        self._order: list[str] = []
        self.loads = 0
        self.hits = 0
        self.evictions = 0

    def get(self, expert_id: str) -> ExpertRecord | None:
        with self._lock:
            entry = self._entries.get(expert_id)
            if entry is None:
                return None
            self.hits += 1
            self._touch(expert_id)
            return entry.record

    def put(self, record: ExpertRecord, *, score: float = 0.0, pinned: bool = False) -> None:
        with self._lock:
            expert_id = record.identity.expert_id
            self._entries[expert_id] = _CacheEntry(record=record, score=score, pinned=pinned)
            self._touch(expert_id)
            while len(self._order) > self.capacity:
                self._evict_one()

    def _touch(self, expert_id: str) -> None:
        if expert_id in self._order:
            self._order.remove(expert_id)
        self._order.append(expert_id)

    def _evict_one(self) -> str | None:
        """Drop the least-recently-used *unpinned* entry.

        Returns the evicted id, or ``None`` when everything resident is pinned.
        The scan is over insertion order, so the cost is O(capacity) — fine for
        the tens-of-experts pool this models, and it avoids maintaining a second
        ordering structure that could disagree with the first.
        """
        for index, candidate in enumerate(self._order):
            entry = self._entries.get(candidate)
            if entry is not None and entry.pinned:
                continue
            self._order.pop(index)
            self._entries.pop(candidate, None)
            self.evictions += 1
            return candidate
        return None

    def mark_pinned(self, expert_id: str, pinned: bool) -> None:
        with self._lock:
            entry = self._entries.get(expert_id)
            if entry is not None:
                entry.pinned = bool(pinned)

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._order)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "capacity": self.capacity,
                "size": len(self._entries),
                "hits": self.hits,
                "loads": self.loads,
                "evictions": self.evictions,
                "pinned": sorted(key for key, entry in self._entries.items() if entry.pinned),
            }

    def load(self, expert_id: str) -> ExpertRecord | None:
        """Fetch from the durable store, counting the load. Caching is the caller's job."""
        with self._lock:
            self.loads += 1
        return self._store.load(expert_id)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._order.clear()


class LRUResidentSet:
    """The shipped eviction policy: least-useful-first, never a pinned entry.

    ``score`` combines utility and recency so a frequently-used-but-useless
    expert is not immortal and a high-utility-but-rarely-used one is not evicted
    the moment it arrives:

    ``rank = score * (1 + recency_boost)`` where ``recency_boost`` decays as
    ``1 / (1 + uses_since_admission)``.

    Pinned entries are excluded from candidacy outright, which is the mechanism
    behind invariant 1 above.
    """

    name = "lru"

    def __init__(self, capacity: int, *, policy: EvictionPolicy | None = None) -> None:
        if capacity < 1:
            raise PagingError(f"resident capacity must be >= 1, got {capacity}")
        self.capacity = int(capacity)
        self.policy: EvictionPolicy = policy or self
        self._lock = threading.RLock()
        self._entries: dict[str, _CacheEntry] = {}
        self._uses: dict[str, int] = {}
        self.evictions = 0

    # -- EvictionPolicy ----------------------------------------------------

    def victims(self, entries: list[tuple[str, float, bool]]) -> list[str]:
        """Rank entries worst-first, skipping pinned ones."""
        unpinned = [(eid, score) for eid, score, pinned in entries if not pinned]
        ranked = sorted(unpinned, key=lambda item: (item[1], item[0]))
        return [eid for eid, _score in ranked]

    # -- ResidentSet --------------------------------------------------------

    def admit(self, expert_id: str) -> bool:
        """Make ``expert_id`` resident, evicting worst-first if over capacity."""
        with self._lock:
            if expert_id in self._entries:
                self._uses[expert_id] = self._uses.get(expert_id, 0) + 1
                return True
            while len(self._entries) >= self.capacity:
                if not self._evict_one():
                    # Everything resident is pinned; refuse rather than evicting a
                    # running expert. The caller retries after a release.
                    return False
            self._entries[expert_id] = _CacheEntry(record=_PLACEHOLDER, score=0.0)
            self._uses[expert_id] = 0
            return True

    def attach(self, expert_id: str, record: ExpertRecord, *, score: float) -> None:
        """Bind a real record to a slot previously reserved by :meth:`admit`."""
        with self._lock:
            entry = self._entries.get(expert_id)
            if entry is not None:
                entry.record = record
                entry.score = score

    def set_score(self, expert_id: str, score: float) -> None:
        """Set the keep-worthiness score of a resident entry.

        Named ``set_score`` rather than ``score`` so it cannot be confused with
        the scorer's ``score(metrics, expert_id)`` method, which computes a
        different number for a different input.
        """
        with self._lock:
            entry = self._entries.get(expert_id)
            if entry is not None:
                entry.score = score

    def note_use(self, expert_id: str) -> None:
        with self._lock:
            self._uses[expert_id] = self._uses.get(expert_id, 0) + 1

    def release(self, expert_id: str) -> None:
        with self._lock:
            entry = self._entries.get(expert_id)
            if entry is not None:
                entry.pinned = False

    def pin(self, expert_id: str) -> None:
        with self._lock:
            entry = self._entries.get(expert_id)
            if entry is not None:
                entry.pinned = True

    def unpin(self, expert_id: str) -> None:
        self.release(expert_id)

    def contains(self, expert_id: str) -> bool:
        with self._lock:
            return expert_id in self._entries

    def remove(self, expert_id: str) -> None:
        with self._lock:
            self._entries.pop(expert_id, None)
            self._uses.pop(expert_id, None)

    def _evict_one(self) -> bool:
        entries = [(eid, self._rank(eid), entry.pinned) for eid, entry in self._entries.items()]
        victims = self.policy.victims(entries)
        if not victims:
            return False
        victim = victims[0]
        self._entries.pop(victim, None)
        self._uses.pop(victim, None)
        self.evictions += 1
        return True

    def _rank(self, expert_id: str) -> float:
        entry = self._entries[expert_id]
        uses = self._uses.get(expert_id, 0)
        return entry.score * (1.0 + 1.0 / (1.0 + uses))

    def snapshot(self) -> PagingSnapshot:
        with self._lock:
            return PagingSnapshot(
                resident=sorted(self._entries),
                cache=[],
                pinned=sorted(key for key, entry in self._entries.items() if entry.pinned),
                capacity=self.capacity,
                policy=getattr(self.policy, "name", type(self.policy).__name__),
            )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "capacity": self.capacity,
                "resident": len(self._entries),
                "pinned": sum(1 for entry in self._entries.values() if entry.pinned),
                "evictions": self.evictions,
                "policy": getattr(self.policy, "name", type(self.policy).__name__),
            }


#: Placeholder record for a reserved-but-not-yet-loaded slot. Replaced by
#: :meth:`LRUResidentSet.attach` before the entry is ever read, so a placeholder
#: that somehow escaped would be an obviously invalid record (``PROPOSED``,
#: generation 0, no capability) rather than a plausible-looking one.
_PLACEHOLDER = ExpertRecord(identity=ExpertIdentity(expert_id="unloaded"))


class PagingManager:
    """Composes the three tiers into one load/acquire/release surface.

    The manager is the only object callers need. It guarantees the two paging
    invariants by owning the pin window:

    * :meth:`acquire` pins the entry for the caller's whole ``with`` block, so a
      concurrent eviction cannot drop an expert mid-use.
    * Every miss reloads from the durable :class:`ExpertStore`, so eviction costs
      a reload and never a record.
    """

    def __init__(
        self,
        store: ExpertStore,
        *,
        cache: ExpertCache | None = None,
        resident: LRUResidentSet | None = None,
        ram_cache_size: int = 32,
        max_resident: int = 4,
    ) -> None:
        self.store = store
        self.cache = cache or ExpertCache(store, capacity=ram_cache_size)
        self.resident = resident or LRUResidentSet(capacity=max_resident)
        self.resident_cache = self.cache
        self.misses = 0
        self.resident_misses = 0
        self._lock = threading.RLock()

    def acquire(self, expert_id: str, *, scorer: Callable[[ExpertRecord], float] | None = None) -> ExpertRecord:
        """Return a pinned, resident record, loading it if necessary.

        Raises:
            PagingError: when the record is absent from the durable store, or
                when the resident tier is full of pinned entries. Both are real
                failures that must not be answered with a fabricated record.
        """
        with self._lock:
            record = self.cache.get(expert_id)
            if record is None:
                self.misses += 1
                record = self.store.load(expert_id)
                if record is None:
                    raise PagingError(f"expert {expert_id!r} is not in the durable store; it cannot be paged in")
                self.cache.put(record, score=scorer(record) if scorer else 0.0)

            if not self.resident.contains(expert_id):
                if not self.resident.admit(expert_id):
                    # Every slot is pinned by a concurrent user. Waiting here
                    # would risk deadlock; refusing is honest and bounded.
                    raise PagingError(f"cannot make expert {expert_id!r} resident: all {self.resident.capacity} resident slots are pinned by in-flight work. Retry after a release.")
                self.resident_misses += 1
                self.resident.attach(expert_id, record, score=scorer(record) if scorer else 0.0)
            else:
                self.resident.note_use(expert_id)
                if scorer is not None:
                    self.resident.set_score(expert_id, scorer(record))
            self.resident.pin(expert_id)
            self.cache.mark_pinned(expert_id, True)
            return record

    def release(self, expert_id: str) -> None:
        """Unpin an expert. Idempotent — releasing an unreleased id is not an error."""
        with self._lock:
            self.resident.unpin(expert_id)
            self.cache.mark_pinned(expert_id, False)

    def hold(self, expert_id: str, *, scorer: Callable[[ExpertRecord], float] | None = None) -> _Hold:
        """Context manager form of acquire/release. ``with manager.hold(eid) as rec:``"""
        return _Hold(self, expert_id, scorer=scorer)

    def snapshot(self) -> PagingSnapshot:
        """Residency report. The cache tier is filled in here so one call answers §48."""
        snapshot = self.resident.snapshot()
        return PagingSnapshot(
            resident=snapshot.resident,
            cache=self.cache.ids(),
            pinned=snapshot.pinned,
            capacity=self.resident.capacity,
            policy=snapshot.policy,
        )

    def stats(self) -> dict[str, Any]:
        return {
            "resident": self.resident.stats(),
            "cache": self.cache.stats(),
            "cache_misses": self.misses,
            "resident_misses": self.resident_misses,
            "enabled": True,
        }


@dataclass
class _Hold:
    """Scoped pin window. See :meth:`PagingManager.hold`."""

    manager: PagingManager
    expert_id: str
    scorer: Callable[[ExpertRecord], float] | None = None
    record: ExpertRecord | None = field(default=None, init=False)

    def __enter__(self) -> ExpertRecord:
        self.record = self.manager.acquire(self.expert_id, scorer=self.scorer)
        return self.record

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.manager.release(self.expert_id)
