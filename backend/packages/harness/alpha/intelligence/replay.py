"""Replay reservoir: bounded, stratified, prioritised experience sampling.

The gap this fills
------------------
Alpha already has an experience bank
(:class:`alpha.learning.experience.store.ExperienceStore`) with a hygiene pass
that merges and decays records. What it has no equivalent of is **sampling for
learning**. Without it, the newest experiences are the ones most available, so a
long-running installation slowly forgets everything older than its current
domain — exactly the failure the reference implementation's replay reservoir
exists to prevent.

Design
------
**Bounded and prioritised.** :class:`ReplayItem` carries a composite priority
combining recency, importance, failure value, regression value and reuse. When
capacity is reached, the *lowest-priority* item is evicted, and the eviction
trace is reported so a caller can see what the reservoir is forgetting.

**Stratified sampling.** :meth:`ReplayReservoir.sample` draws from named strata
with configured weights rather than one global top-N. This is the mechanism that
prevents the newest domain from erasing old knowledge: ``RECENT`` and
``OLD_KNOWLEDGE`` are separate strata with separate weights, so a burst of new
``RECENT`` items cannot consume the whole budget.

**Honest about what is missing.** :meth:`ReplayReservoir.stratum_sizes` reports
a configured stratum that currently holds zero items as ``0`` rather than
omitting it, so an operator sees "this stratum is configured and empty" instead
of assuming it does not exist.

Relationship to the existing bank
---------------------------------
This is a **sampling index over** experience, not a second experience store.
:class:`ReplayItem` holds a reference (``experience_id`` plus the routing fields
needed to rank it), never a second copy of the record's content. The bank stays
the system of record; the reservoir is the query plan.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.intelligence.models import ReplayStratum
from alpha.persistence.storekit.atomic import atomic_write_json

logger = logging.getLogger(__name__)

__all__ = [
    "REPLAY_RELATIVE_PATH",
    "REPLAY_SCHEMA_VERSION",
    "DEFAULT_STRATUM_WEIGHTS",
    "PRIORITY_WEIGHTS",
    "ReplayItem",
    "ReplayReport",
    "ReplayReservoir",
    "replay_path",
]

REPLAY_RELATIVE_PATH = Path("intelligence") / "replay.json"
REPLAY_SCHEMA_VERSION = 1

#: Default per-stratum weights. Sum to 1.0 so a configured weight reads as a
#: share of the sampling budget rather than an unnormalised score.
DEFAULT_STRATUM_WEIGHTS: dict[str, float] = {
    ReplayStratum.RECENT.value: 0.25,
    ReplayStratum.HIGH_VALUE.value: 0.15,
    ReplayStratum.RARE_FAILURE.value: 0.15,
    ReplayStratum.REGRESSION.value: 0.15,
    ReplayStratum.STRATEGY.value: 0.10,
    ReplayStratum.OLD_KNOWLEDGE.value: 0.10,
    ReplayStratum.TOOL_RECOVERY.value: 0.05,
    ReplayStratum.CODING_FAILURE.value: 0.02,
    ReplayStratum.GIT_FAILURE.value: 0.01,
    ReplayStratum.MEMORY_FAILURE.value: 0.01,
    ReplayStratum.COORDINATION_FAILURE.value: 0.01,
    ReplayStratum.SECURITY_FAILURE.value: 0.01,
}


def replay_path() -> Path:
    """Absolute path of the reservoir document under ``runtime_home()``."""
    return runtime_home() / REPLAY_RELATIVE_PATH


#: Weights for :meth:`ReplayItem.priority`, separate from stratum sampling
#: weights because they answer different questions: "is this item worth
#: keeping?" versus "how much of the sampling budget does this stratum get?".
#: They are separate dictionaries on purpose — conflating them makes a change to
#: the sampling mix silently alter what gets evicted.
PRIORITY_WEIGHTS: dict[str, float] = {
    "recency": 0.30,
    "importance": 0.25,
    "failure_value": 0.20,
    "regression_value": 0.15,
    "reuse": 0.05,
    "frequency": 0.05,
}


def _digest(payload: Mapping[str, Any]) -> str:
    """Stable content digest, used to refuse a duplicate insert."""
    import json

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


@dataclass
class ReplayItem:
    """A reference into the experience bank plus the fields ranking needs.

    ``content_digest`` is the dedup key. Two items with the same digest are the
    same experience and the second insert is refused — so a re-captured run
    cannot crowd the reservoir by being logged twice.
    """

    experience_id: str
    strata: set[str] = field(default_factory=set)
    importance: float = 0.0
    """0..1. Operator/scorer-assigned; drives the importance term."""

    failure_value: float = 0.0
    """0..1. Rarity-weighted: a failure you have seen once is worth more than
    one you have seen a hundred times."""

    regression_value: float = 0.0
    """0..1. Non-zero only for an item tied to a known regression."""

    reuse_count: int = 0
    success: bool = True
    created_at: float = field(default_factory=time.time)
    source_task: str = ""
    content_digest: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.experience_id = (self.experience_id or "").strip()
        if not self.experience_id:
            raise ValueError("experience_id must be a non-empty string")
        self.strata = {str(s) for s in self.strata if str(s)}
        for name in ("importance", "failure_value", "regression_value"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0.0, 1.0], got {getattr(self, name)!r}")
            setattr(self, name, value)
        if self.reuse_count < 0:
            raise ValueError(f"reuse_count must be >= 0, got {self.reuse_count!r}")
        if not self.content_digest:
            self.content_digest = _digest({"id": self.experience_id, "created_at": self.created_at, "meta": self.metadata})

    def recency(self, *, now: float | None = None, half_life_seconds: float = 604800.0) -> float:
        """Exponential recency in ``(0, 1]``.

        Half-life, not a cliff: an item one half-life old scores 0.5 and one two
        half-lives old scores 0.25. A hard "recent/not recent" boundary is what
        makes a reservoir forget everything the instant a domain changes.
        """
        moment = now if now is not None else time.time()
        age = max(0.0, moment - self.created_at)
        if half_life_seconds <= 0:
            return 1.0
        return 0.5 ** (age / half_life_seconds)

    def priority(
        self,
        *,
        now: float | None = None,
        half_life_seconds: float = 604800.0,
        weights: Mapping[str, float] | None = None,
    ) -> float:
        """Composite priority in ``(0, 1]``.

        Weighted sum, not a product: a valuable old failure should not be driven
        to zero purely by age, which is what makes ``OLD_KNOWLEDGE`` and
        ``REGRESSION`` strata survive a long quiet period.
        """
        w = PRIORITY_WEIGHTS if weights is None else dict(weights)
        recency = self.recency(now=now, half_life_seconds=half_life_seconds)
        diversity = min(1.0, math.log1p(self.reuse_count) / math.log(21.0))
        score = (
            float(w.get("recency", 0.30)) * recency
            + float(w.get("importance", 0.25)) * self.importance
            + float(w.get("failure_value", 0.20)) * self.failure_value
            + float(w.get("regression_value", 0.15)) * self.regression_value
            + float(w.get("reuse", 0.05)) * diversity
            + float(w.get("frequency", 0.05)) * min(1.0, self.reuse_count / 10.0)
        )
        return max(0.0, min(1.0, score))

    def to_dict(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "strata": sorted(self.strata),
            "importance": self.importance,
            "failure_value": self.failure_value,
            "regression_value": self.regression_value,
            "reuse_count": self.reuse_count,
            "success": self.success,
            "created_at": self.created_at,
            "source_task": self.source_task,
            "content_digest": self.content_digest,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReplayItem:
        if not isinstance(data, dict):
            raise ValueError(f"ReplayItem payload must be an object, got {type(data).__name__}")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"ReplayItem has unknown field(s): {unknown}")
        payload = dict(data)
        payload["strata"] = set(payload.get("strata") or [])
        return cls(**payload)


@dataclass
class ReplayReport:
    """What one sampling call did, including what it could not do."""

    sampled: list[ReplayItem]
    requested: int
    strata_drawn: dict[str, int]
    unmet_request: bool
    """True when fewer items were returned than asked for. A caller that wants
    ``n`` and receives fewer must be able to see it rather than infer it."""

    reason: str = ""
    """Why the request could not be fully met (e.g. an empty stratum)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sampled_ids": [item.experience_id for item in self.sampled],
            "requested": self.requested,
            "returned": len(self.sampled),
            "strata_drawn": dict(self.strata_drawn),
            "unmet_request": self.unmet_request,
            "reason": self.reason,
        }


class ReplayReservoir:
    """Bounded, stratified experience sampler.

    Thread-safe for the single-Gateway-process case. Like the journal, this is
    process-local: two workers would each hold a different item set. That is the
    same declared limitation the swarm/workflow sinks carry, and it is stated
    rather than implied.
    """

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        capacity: int = 512,
        stratum_weights: Mapping[str, float] | None = None,
        half_life_seconds: float = 604800.0,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self.path = Path(path) if path is not None else replay_path()
        self.capacity = int(capacity)
        self.half_life_seconds = float(half_life_seconds)
        self._stratum_weights: dict[str, float] = dict(stratum_weights or {})
        self._priority_weights: dict[str, float] = PRIORITY_WEIGHTS.copy()
        if self._stratum_weights:
            self._priority_weights.update(self._stratum_weights)
        self._lock = threading.RLock()
        self._items: dict[str, ReplayItem] = {}
        self._evicted: list[dict[str, Any]] = []
        self._load()

    # -- configuration ------------------------------------------------------

    @property
    def stratum_weights(self) -> dict[str, float]:
        """Effective per-stratum sampling weights, normalised to sum to 1.0.

        A configured stratum name that is not a known
        :class:`~alpha.intelligence.models.ReplayStratum` is preserved here (so
        an operator's label is honoured) but reported by
        :meth:`unknown_strata` so a typo cannot pass unnoticed.
        """
        weights = dict(DEFAULT_STRATUM_WEIGHTS)
        weights.update(self._stratum_weights)
        total = sum(weights.values())
        if total <= 0:
            return weights
        return {key: value / total for key, value in weights.items()}

    def unknown_strata(self) -> list[str]:
        """Configured stratum names that are not known strata."""
        known = {s.value for s in ReplayStratum}
        return sorted(name for name in self._stratum_weights if name not in known)

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        import json

        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"replay reservoir {self.path} is unreadable: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != REPLAY_SCHEMA_VERSION:
            raise RuntimeError(f"replay reservoir {self.path} has an unrecognised schema_version; refusing to load")
        try:
            for item in data.get("items", []):
                record = ReplayItem.from_dict(item)
                self._items[record.content_digest] = record
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"replay reservoir {self.path} failed validation: {exc}") from exc

    def _save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "schema_version": REPLAY_SCHEMA_VERSION,
                "capacity": self.capacity,
                "half_life_seconds": self.half_life_seconds,
                "stratum_weights": dict(self._stratum_weights),
                "items": [item.to_dict() for item in self._items.values()],
            },
        )

    # -- mutation -----------------------------------------------------------

    def add(self, item: ReplayItem) -> dict[str, Any]:
        """Insert ``item``, evicting the lowest-priority item if over capacity.

        Returns a result dict, never a bare ``None``:

        * ``{"stored": False, "reason": "duplicate"}`` for a duplicate digest.
        * ``{"stored": True, "evicted": <id or None>}`` otherwise.
        """
        with self._lock:
            existing = self._items.get(item.content_digest)
            if existing is not None:
                # Refresh recency-driven reuse rather than creating a twin.
                existing.reuse_count += 1
                existing.created_at = item.created_at
                self._save()
                return {"stored": False, "reason": "duplicate", "experience_id": existing.experience_id, "reused": True}

            self._items[item.content_digest] = item
            evicted_id: str | None = None
            while len(self._items) > self.capacity:
                victim = self._lowest_priority()
                if victim is None:
                    break
                removed = self._items.pop(victim.content_digest)
                evicted_id = removed.experience_id
                self._evicted.append(
                    {
                        "experience_id": removed.experience_id,
                        "strata": sorted(removed.strata),
                        "priority": removed.priority(now=None, half_life_seconds=self.half_life_seconds, weights=self._priority_weights),
                        "at": time.time(),
                    }
                )
            if len(self._evicted) > 100:
                del self._evicted[: len(self._evicted) - 100]
            self._save()
            return {"stored": True, "experience_id": item.experience_id, "evicted": evicted_id}

    def _rank(self, item: ReplayItem, moment: float, bonuses: dict[str, float] | None = None) -> float:
        """Ordering score: composite priority plus any capped curiosity bonus.

        Split out so every sort in :meth:`sample` uses the same definition. Three
        separate sort keys built from slightly different expressions is how a
        "required strata come first" guarantee silently stops holding.
        """
        base = item.priority(now=moment, half_life_seconds=self.half_life_seconds, weights=self._priority_weights)
        if not bonuses:
            return base
        return base + bonuses.get(item.content_digest, 0.0)

    def _lowest_priority(self) -> ReplayItem | None:
        candidates = list(self._items.values())
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item.priority(now=None, half_life_seconds=self.half_life_seconds, weights=self._priority_weights), item.experience_id))

    def remove(self, experience_id: str) -> bool:
        with self._lock:
            for digest, item in list(self._items.items()):
                if item.experience_id == experience_id:
                    del self._items[digest]
                    self._save()
                    return True
            return False

    def note_reuse(self, experience_id: str, *, success: bool = True) -> bool:
        """Record a reuse outcome, which feeds the diversity/frequency terms."""
        with self._lock:
            for item in self._items.values():
                if item.experience_id == experience_id:
                    item.reuse_count += 1
                    item.success = success
                    self._save()
                    return True
            return False

    # -- reading ------------------------------------------------------------

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, experience_id: str) -> ReplayItem | None:
        with self._lock:
            for item in self._items.values():
                if item.experience_id == experience_id:
                    return item
            return None

    def items(self) -> list[ReplayItem]:
        with self._lock:
            return sorted(self._items.values(), key=lambda item: item.content_digest)

    def stratum_sizes(self) -> dict[str, int]:
        """Size of **every configured stratum**, including the empty ones.

        Reporting only non-empty strata would make "this stratum does not exist"
        and "this stratum is configured and currently empty" look identical,
        which is exactly the ambiguity that hides a broken weight.
        """
        sizes = {name: 0 for name in self.stratum_weights}
        with self._lock:
            for item in self._items.values():
                for stratum in item.strata:
                    sizes[stratum] = sizes.get(stratum, 0) + 1
        return dict(sorted(sizes.items()))

    def oldest_age_seconds(self, *, now: float | None = None) -> float | None:
        """Age of the oldest retained item, or ``None`` when empty.

        This is the direct measurement of "is the reservoir actually retaining
        old knowledge?" — a reservoir whose oldest item is minutes old is not a
        reservoir, whatever its capacity.
        """
        moment = now if now is not None else time.time()
        with self._lock:
            if not self._items:
                return None
            return moment - min(item.created_at for item in self._items.values())

    def sample(
        self,
        n: int = 5,
        *,
        strata: tuple[str, ...] = (),
        required_strata: tuple[str, ...] = (),
        now: float | None = None,
        # `curiosity`: a CuriosityLedger (Phase H). When supplied, intrinsic
        # value contributes a CAPPED, additive bonus to the ordering. It can
        # break ties between plausible items but can never pull an unexplored,
        # un-capable target above a proven one, because the ledger's degeneracy
        # guard zeroes the bonus in exactly that case. Pass None to disable it.
        curiosity: Any = None,
        curiosity_cap: float | None = None,
    ) -> ReplayReport:
        """Draw up to ``n`` items, stratified by configured weight.

        Args:
            n: How many items to return. ``<= 0`` returns an empty, *honest*
                report rather than raising — "I was asked for nothing" is a
                valid outcome.
            strata: Restrict the draw to these strata. Empty means all.
            required_strata: Strata that must contribute at least one item when
                they hold any. This is how ``OLD_KNOWLEDGE`` survives a flood of
                new ``RECENT`` items: the requirement is honoured ahead of the
                weighted draw.

        The weighted allocation is computed from the *weights*, then each
        stratum's quota is filled highest-priority-first. Strata short of their
                quota release the remainder to other strata rather than leaving
                the request unmet — so a burst in one stratum cannot starve a
        learning cycle, while :attr:`ReplayReport.strata_drawn` still shows
        exactly where the items came from.
        """
        if n <= 0:
            return ReplayReport(sampled=[], requested=n, strata_drawn={}, unmet_request=n > 0, reason="requested zero items")

        moment = now if now is not None else time.time()
        allowed = {s for s in strata if s} or set(self.stratum_weights)
        required = {s for s in required_strata if s}

        # Phase H: a bounded, additive curiosity term. It is computed once per
        # item and added to the priority used for ordering, so the strata quota
        # arithmetic below is unchanged - curiosity reorders within a stratum, it
        # cannot move an item between strata, which is what keeps required_strata
        # guarantees intact.
        cap = float(curiosity_cap) if curiosity_cap is not None else 0.0
        bonuses: dict[str, float] = {}
        if curiosity is not None and cap > 0.0:
            with self._lock:
                candidates = list(self._items.values())
            for item in candidates:
                target = curiosity.target_for(
                    {"strata": sorted(item.strata), "importance": item.importance},
                    competence=min(1.0, item.importance),
                    attempts=item.reuse_count,
                )
                bonuses[item.content_digest] = target.bonus(cap=cap)

        with self._lock:
            pool: dict[str, list[ReplayItem]] = {}
            for item in self._items.values():
                if not (item.strata & allowed):
                    continue
                for stratum in item.strata & allowed:
                    pool.setdefault(stratum, []).append(item)
            for entries in pool.values():
                entries.sort(key=lambda item: (-self._rank(item, moment, bonuses), item.experience_id))
            if not pool:
                return ReplayReport(
                    sampled=[],
                    requested=n,
                    strata_drawn={},
                    unmet_request=True,
                    reason=f"no replay items in the requested strata: {sorted(allowed)}",
                )

            chosen: list[ReplayItem] = []
            chosen_digests: set[str] = set()

            def take(item: ReplayItem) -> None:
                if item.content_digest in chosen_digests:
                    return
                chosen_digests.add(item.content_digest)
                chosen.append(item)

            # 1. required strata first — these are what a pure-weighted draw would lose.
            for stratum in sorted(required):
                for item in pool.get(stratum, []):
                    if len(chosen) >= n:
                        break
                    take(item)
                if len(chosen) >= n:
                    break

            # 2. weighted allocation over what remains.
            weights = self.stratum_weights
            remaining_strata = sorted(pool)
            base_quota = {stratum: int(n * weights.get(stratum, 0.0)) for stratum in remaining_strata}
            # Guarantee at least one slot per non-empty stratum when budget allows.
            for stratum in remaining_strata:
                if base_quota[stratum] == 0:
                    base_quota[stratum] = 1
            # Trim quotas back to the remaining budget, largest weight first.
            budget = n - len(chosen)
            ordered = sorted(remaining_strata, key=lambda s: (-weights.get(s, 0.0), s))
            for stratum in ordered:
                if budget <= 0:
                    break
                quota = min(base_quota[stratum], budget)
                drawn = 0
                for item in pool[stratum]:
                    if drawn >= quota or len(chosen) >= n:
                        break
                    before = len(chosen)
                    take(item)
                    if len(chosen) > before:
                        drawn += 1
                budget -= drawn
            # 3. top up from the global pool if quotas under-delivered.
            if len(chosen) < n:
                leftovers = sorted(
                    (item for entries in pool.values() for item in entries),
                    key=lambda item: (-self._rank(item, moment, bonuses), item.experience_id),
                )
                for item in leftovers:
                    if len(chosen) >= n:
                        break
                    take(item)

        drawn_counts: dict[str, int] = {}
        for item in chosen:
            for stratum in item.strata & allowed:
                drawn_counts[stratum] = drawn_counts.get(stratum, 0) + 1
        unmet = len(chosen) < n
        return ReplayReport(
            sampled=chosen,
            requested=n,
            strata_drawn=dict(sorted(drawn_counts.items())),
            unmet_request=unmet,
            reason="reservoir holds fewer items than requested" if unmet else "",
        )

    # -- introspection ------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._lock:
            items = list(self._items.values())
            moment = time.time()
            return {
                "size": len(items),
                "capacity": self.capacity,
                "utilisation": round(len(items) / self.capacity, 4),
                "strata": self.stratum_sizes(),
                "unknown_configured_strata": self.unknown_strata(),
                "oldest_age_seconds": self.oldest_age_seconds(now=moment),
                "recent_evictions": list(self._evicted[-10:]),
                "path": str(self.path),
            }

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._evicted.clear()
            self._save()
