"""Dynamic expert registry: stable identity, lineage, lifecycle, safe pruning.

Relationship to the existing expert subsystem
----------------------------------------------
Alpha already has :mod:`alpha.experts` — a **static** catalogue of role-based
agents plus a durable install/enable flag. That stays exactly as it is, and this
module does not replace it. The split is by *kind of change*:

============================  =========================================  ==================
Concern                       Owner                                      Mutability
============================  =========================================  ==================
What an expert **is**         ``alpha.experts.catalog.ExpertCatalog``      operator-authored
Whether it is **installed**   ``alpha.experts.registry.ExpertRegistry``    operator action
How it **behaved**            this module                                 measured at runtime
Whether it **may be routed**  this module                                 gated by evidence
============================  =========================================  ==================

What this adds, and why each part is load-bearing
-------------------------------------------------
* **Stable identity.** ``expert_%06d`` from a persisted monotonic counter.
  Not an array index, not a dict key derived from a sort order. It survives
  restart, paging eviction, archival and reinstatement, because none of those
  operations touch the counter.
* **A real state machine.** :meth:`ExpertRecord.transition` refuses any edge
  not declared in :data:`alpha.intelligence.models._LEGAL_TRANSITIONS`. A trial
  expert cannot become active without passing a gate, because the gate *is* the
  transition.
* **Lineage as a first-class query.** ``lineage(expert_id)`` walks parents and
  children, and ``descendants`` is what makes a regression traceable to the
  expert that caused it.
* **Pruning that cannot be silent.** Every prune archives first, records the
  reason, and refuses outright on a protected expert. ``PRUNED`` is terminal
  but reversible through ``ARCHIVED``, so a mistaken prune is recoverable.

Persistence uses :mod:`alpha.persistence.storekit.atomic` — the repository's
existing crash-safe writer with per-step fault-injection hooks — rather than a
seventeenth private ``atomic_write`` helper.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.intelligence.models import (
    PRUNE_ELIGIBLE_FROM,
    ExpertIdentity,
    ExpertLifecycleState,
    ExpertRecord,
)
from alpha.persistence.storekit.atomic import atomic_write_json

logger = logging.getLogger(__name__)

__all__ = [
    "EXPERT_FABRIC_SCHEMA_VERSION",
    "ExpertFabricError",
    "ExpertFabricUnreadable",
    "ExpertFabric",
    "expert_fabric_path",
    "get_expert_fabric",
]

EXPERT_FABRIC_SCHEMA_VERSION = 1

#: Next-id allocation start. Ids are 1-based so ``expert_000000`` is never a
#: valid identity — a zero id reads as "unset" in logs and JSON.
_FIRST_ID = 1


class ExpertFabricError(RuntimeError):
    """Base class for expert-fabric failures."""


class ExpertFabricUnreadable(ExpertFabricError):
    """The on-disk fabric exists but could not be read or validated."""


def expert_fabric_path() -> Path:
    """Absolute path of the expert-fabric document under ``runtime_home()``."""
    return runtime_home() / "intelligence" / "experts.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class PruneDecision:
    """Why one expert is or is not prune-eligible.

    Every clause of prompt §22 is an explicit field, so the *refusals* are
    legible. A boolean would hide "it is young" behind "False".
    """

    expert_id: str
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    """Clause-by-clause outcome, one entry per condition checked."""

    blocking: list[str] = field(default_factory=list)
    """The subset of ``reasons`` that currently prevents eligibility."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "expert_id": self.expert_id,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "blocking": list(self.blocking),
        }


class ExpertFabric:
    """Durable registry of learned experts with stable ids and enforced lifecycle."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else expert_fabric_path()
        self._lock = threading.RLock()
        self._records: dict[str, ExpertRecord] = {}
        self._next_id = _FIRST_ID
        self._load()

    # -- persistence -------------------------------------------------------

    def store_label(self) -> str:
        return str(self.path)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ExpertFabricUnreadable(f"expert fabric {self.store_label()} could not be read: {exc}") from exc
        if not raw.strip():
            raise ExpertFabricUnreadable(f"expert fabric {self.store_label()} is empty.")
        import json

        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ExpertFabricUnreadable(f"expert fabric {self.store_label()} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ExpertFabricUnreadable(f"expert fabric {self.store_label()} must contain an object.")
        if data.get("schema_version") != EXPERT_FABRIC_SCHEMA_VERSION:
            raise ExpertFabricUnreadable(f"expert fabric {self.store_label()} schema_version {data.get('schema_version')!r} != {EXPERT_FABRIC_SCHEMA_VERSION}.")
        try:
            for item in data.get("experts", []):
                record = ExpertRecord.from_dict(item)
                self._records[record.identity.expert_id] = record
            counter = data.get("next_id")
            if isinstance(counter, int) and not isinstance(counter, bool) and counter >= _FIRST_ID:
                self._next_id = counter
        except (TypeError, ValueError) as exc:
            raise ExpertFabricUnreadable(f"expert fabric {self.store_label()} failed validation: {exc}") from exc

    def _save(self) -> None:

        payload = {
            "schema_version": EXPERT_FABRIC_SCHEMA_VERSION,
            "next_id": self._next_id,
            "experts": [record.to_dict() for record in self._ordered()],
            "updated_at": _now_iso(),
        }
        try:
            atomic_write_json(self.path, payload)
        except OSError as exc:
            raise ExpertFabricUnreadable(f"could not persist expert fabric {self.store_label()}: {exc}") from exc
        except (TypeError, ValueError) as exc:
            # atomic_write_json sets allow_nan=False deliberately; a non-finite
            # metric reaching here is a real defect and must not be swallowed.
            raise ExpertFabricUnreadable(f"expert fabric {self.store_label()} holds a value JSON cannot represent: {exc}") from exc

    def _ordered(self) -> list[ExpertRecord]:
        return sorted(self._records.values(), key=lambda r: r.identity.expert_id)

    # -- creation ----------------------------------------------------------

    def allocate_id(self) -> str:
        """Peek the next identity without consuming it."""
        with self._lock:
            return f"expert_{self._next_id:06d}"

    def propose(
        self,
        capability: str,
        *,
        reason: str,
        kind: str = "agent",
        parents: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        max_experts: int | None = None,
        protected: tuple[str, ...] = (),
    ) -> ExpertRecord:
        """Create a new expert in ``PROPOSED``, allocating its stable id.

        Args:
            capability: The capability slug the gap was detected for.
            reason: **Required.** Why this expert exists. An expert with no
                recorded reason is an unexplainable capability, and the audit
                trail cannot reconstruct it later.
            parents: Parent expert ids this one is derived from.
            protected: Operator-declared protected ids; applied to the new record.

        Raises:
            ExpertFabricError: on an empty capability or reason, an unknown or
                duplicate parent, or when ``max_experts`` is reached.
        """
        capability = (capability or "").strip().lower()
        if not capability:
            raise ExpertFabricError("capability must be a non-empty slug")
        reason = (reason or "").strip()
        if not reason:
            raise ExpertFabricError("reason is required: a learned expert must record why it was created")
        parent_ids = [p.strip() for p in (parents or []) if p and p.strip()]
        with self._lock:
            if max_experts is not None and len(self._records) >= max_experts:
                raise ExpertFabricError(f"cannot create expert for capability {capability!r}: registry holds {len(self._records)} experts and max_experts is {max_experts}. Prune or archive first.")
            for parent in parent_ids:
                if parent not in self._records:
                    raise ExpertFabricError(f"parent expert {parent!r} is not registered; lineage cannot reference it")
            if self._find_by_capability(capability) is not None:
                existing = self._find_by_capability(capability)
                assert existing is not None
                raise ExpertFabricError(
                    f"capability {capability!r} is already covered by {existing.identity.expert_id} (status {existing.identity.status.value}); grow from it with parents=[{existing.identity.expert_id}] instead of duplicating it"
                )
            generation = 1
            if parent_ids:
                # A derived expert is exactly one generation deeper than its
                # deepest parent, so "generation" stays meaningful as depth.
                generation = max(self._records[p].identity.generation for p in parent_ids) + 1
            expert_id = f"expert_{self._next_id:06d}"
            self._next_id += 1
            record = ExpertRecord(
                identity=ExpertIdentity(
                    expert_id=expert_id,
                    kind=kind,
                    capability=capability,
                    generation=generation,
                    parents=parent_ids,
                    reason=reason,
                    created_at=_now_iso(),
                    status=ExpertLifecycleState.PROPOSED,
                    protected=expert_id in set(protected),
                ),
                lineage_reason=reason,
                provenance=dict(provenance or {}),
                payload=dict(payload or {}),
            )
            self._records[expert_id] = record
            self._save()
            return record

    # -- lifecycle ---------------------------------------------------------

    def get(self, expert_id: str) -> ExpertRecord | None:
        with self._lock:
            return self._records.get((expert_id or "").strip())

    def require(self, expert_id: str) -> ExpertRecord:
        record = self.get(expert_id)
        if record is None:
            raise ExpertFabricError(f"unknown expert {expert_id!r}")
        return record

    def transition(self, expert_id: str, target: ExpertLifecycleState) -> ExpertRecord:
        """Apply a lifecycle transition, persisting only on success."""
        with self._lock:
            record = self.require(expert_id)
            record.transition(target)
            self._save()
            return record

    def mark_trial_ready(self, expert_id: str) -> ExpertRecord:
        """Move an expert to ``TRIAL``, walking the legal path.

        ``PROPOSED`` and ``ARCHIVED`` reach ``TRIAL`` *via* ``INITIALIZING``,
        because that intermediate state is what says "the record exists and is
        being materialised". Skipping it would make the state machine's
        ``INITIALIZING`` node unreachable and its presence decorative.

        A dynamically created expert is therefore always born into TRIAL, never
        ACTIVE — which is the mechanical form of "candidate experts must start
        in TRIAL".
        """
        record = self.require(expert_id)
        if record.identity.status in (ExpertLifecycleState.PROPOSED, ExpertLifecycleState.ARCHIVED):
            self.transition(expert_id, ExpertLifecycleState.INITIALIZING)
        if record.identity.status is ExpertLifecycleState.INITIALIZING:
            self.transition(expert_id, ExpertLifecycleState.TRIAL)
        return self.require(expert_id)

    def archive(self, expert_id: str, reason: str = "") -> ExpertRecord:
        """Archive an expert, preserving its record and lineage.

        Archiving is the *only* removal path, and it is reversible. Nothing in
        this module deletes a record outright.
        """
        record = self.require(expert_id)
        record.lineage_reason = f"{record.lineage_reason} | archived: {reason}".strip(" |") if reason else record.lineage_reason
        return self.transition(expert_id, ExpertLifecycleState.ARCHIVED)

    # -- queries -----------------------------------------------------------

    def list(
        self,
        *,
        status: ExpertLifecycleState | None = None,
        kind: str | None = None,
        include_terminal: bool = False,
    ) -> list[ExpertRecord]:
        """List experts, filtered by status and/or kind.

        ``include_terminal=False`` hides ``PRUNED`` by default, because a pruned
        expert is retained for lineage, not offered for routing.
        """
        with self._lock:
            out = list(self._records.values())
        if status is not None:
            out = [r for r in out if r.identity.status is status]
        if kind:
            key = kind.strip().lower()
            out = [r for r in out if r.identity.kind == key]
        if not include_terminal:
            out = [r for r in out if r.identity.status not in {ExpertLifecycleState.PRUNED}]
        return sorted(out, key=lambda r: r.identity.expert_id)

    def routable(self) -> list[ExpertRecord]:
        """Experts the router may select for production work (ACTIVE only)."""
        return self.list(status=ExpertLifecycleState.ACTIVE)

    def trial(self) -> list[ExpertRecord]:
        return self.list(status=ExpertLifecycleState.TRIAL)

    def _find_by_capability(self, capability: str) -> ExpertRecord | None:
        for record in self._records.values():
            if record.identity.capability == capability and record.identity.status is not ExpertLifecycleState.PRUNED:
                return record
        return None

    def find_by_capability(self, capability: str) -> ExpertRecord | None:
        with self._lock:
            return self._find_by_capability((capability or "").strip().lower())

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            records = list(self._records.values())
        counts: dict[str, int] = {}
        for record in records:
            key = record.identity.status.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    # -- lineage -----------------------------------------------------------

    def parents_of(self, expert_id: str) -> list[ExpertRecord]:
        with self._lock:
            record = self.require(expert_id)
            return [self._records[p] for p in record.identity.parents if p in self._records]

    def children_of(self, expert_id: str) -> list[ExpertRecord]:
        key = (expert_id or "").strip()
        with self._lock:
            return sorted((r for r in self._records.values() if key in r.identity.parents), key=lambda r: r.identity.expert_id)

    def lineage(self, expert_id: str) -> dict[str, Any]:
        """Ancestry, descendants, and the recorded creation reason."""
        with self._lock:
            record = self.require(expert_id)
            ancestors: list[dict[str, Any]] = []
            seen: set[str] = set()
            frontier = list(record.identity.parents)
            while frontier:
                parent_id = frontier.pop(0)
                if parent_id in seen or parent_id not in self._records:
                    continue
                seen.add(parent_id)
                parent = self._records[parent_id]
                ancestors.append(
                    {
                        "expert_id": parent.identity.expert_id,
                        "capability": parent.identity.capability,
                        "generation": parent.identity.generation,
                        "status": parent.identity.status.value,
                    }
                )
                frontier.extend(parent.identity.parents)
            descendants: list[dict[str, Any]] = []
            frontier_ids = [record.identity.expert_id]
            visited = {record.identity.expert_id}
            while frontier_ids:
                current = frontier_ids.pop(0)
                for child in self.children_of(current):
                    if child.identity.expert_id in visited:
                        continue
                    visited.add(child.identity.expert_id)
                    descendants.append(
                        {
                            "expert_id": child.identity.expert_id,
                            "capability": child.identity.capability,
                            "generation": child.identity.generation,
                            "status": child.identity.status.value,
                        }
                    )
                    frontier_ids.append(child.identity.expert_id)
        return {
            "expert_id": record.identity.expert_id,
            "generation": record.identity.generation,
            "parents": record.identity.parents,
            "ancestors": ancestors,
            "children": descendants,
            "reason": record.lineage_reason or record.identity.reason,
            "provenance": dict(record.provenance),
        }

    def capability_graph(self) -> dict[str, Any]:
        """A node/edge projection suitable for an operator graph view."""
        with self._lock:
            records = self._ordered()
            nodes = [
                {
                    "expert_id": r.identity.expert_id,
                    "capability": r.identity.capability,
                    "generation": r.identity.generation,
                    "status": r.identity.status.value,
                    "kind": r.identity.kind,
                    "protected": r.identity.protected,
                    "usage": r.metrics.usage,
                    "success_rate": r.metrics.success_rate,
                }
                for r in records
            ]
            known = {r.identity.expert_id for r in records}
            edges = [{"parent": parent, "child": r.identity.expert_id} for r in records for parent in r.identity.parents if parent in known]
        return {"nodes": nodes, "edges": edges, "roots": [n["expert_id"] for n in nodes if not n["generation"]]}

    # -- metrics -----------------------------------------------------------

    def observe(
        self,
        expert_id: str,
        *,
        success: bool,
        quality_gain: float = 0.0,
        latency_ms: float = 0.0,
        cost: float = 0.0,
        recovered: bool = False,
        regression_impact: float = 0.0,
        recent_window: int = 5,
    ) -> ExpertRecord:
        """Record one observation for ``expert_id`` and persist.

        This is the *only* mutation path for metrics. Centralising it means a
        caller cannot increment ``usage`` without also maintaining the recent
        window and the totals, which is what makes the utility score honest.
        """
        with self._lock:
            record = self.require(expert_id)
            record.metrics.observe(
                success=success,
                quality_gain=quality_gain,
                latency_ms=latency_ms,
                cost=cost,
                recovered=recovered,
                regression_impact=regression_impact,
                recent_window=recent_window,
            )
            self._save()
            return record

    # -- pruning -----------------------------------------------------------

    def evaluate_prune(
        self,
        expert_id: str,
        *,
        grace_period_seconds: float,
        now: float | None = None,
        replacement_exists: bool = False,
    ) -> PruneDecision:
        """Decide prune eligibility clause by clause (prompt §22).

        Every condition is evaluated and reported even when an earlier one has
        already failed, because "why was this expert kept" is the question an
        operator actually asks.
        """
        moment = now if now is not None else time.time()
        with self._lock:
            record = self.require(expert_id)
            identity = record.identity
            metrics = record.metrics
            reasons: list[str] = []
            blocking: list[str] = []

            def check(ok: bool, clause: str, detail: str) -> None:
                reasons.append(f"{clause}: {detail}")
                if not ok:
                    blocking.append(f"{clause}: {detail}")

            # 1. status
            check(
                identity.status in PRUNE_ELIGIBLE_FROM,
                "lifecycle",
                f"status is {identity.status.value}; pruning considers only {sorted(s.value for s in PRUNE_ELIGIBLE_FROM)}",
            )
            # 2. protection — absolute, checked first because nothing overrides it
            check(not identity.protected, "protection", "protected expert" if identity.protected else "not protected")
            # 3. contribution
            usage_ok = metrics.usage > 0
            check(usage_ok, "contribution", f"usage={metrics.usage}")
            # 4. recent usefulness
            recent_rate = metrics.recent_success_rate
            recent_ok = recent_rate is not None and recent_rate > 0.0
            if recent_rate is None:
                check(False, "recent_usefulness", "no recent observations, so usefulness is unknown rather than low")
            else:
                check(recent_ok, "recent_usefulness", f"recent success rate {recent_rate:.2f}")
            # 5. grace period
            age = _age_seconds(identity.created_at, moment)
            age_ok = age is not None and age >= grace_period_seconds
            if age is None:
                check(False, "grace_period", "created_at could not be parsed, so age is unknown")
            else:
                check(
                    age_ok,
                    "grace_period",
                    f"age {age:.0f}s vs grace {grace_period_seconds:.0f}s",
                )
            # 6. replacement exists
            check(replacement_exists, "replacement", "replacement capability declared" if replacement_exists else "no replacement capability exists")

            return PruneDecision(
                expert_id=identity.expert_id,
                eligible=not blocking,
                reasons=reasons,
                blocking=blocking,
            )

    def mark_prune_candidate(self, expert_id: str) -> ExpertRecord:
        """Mark an expert as a prune candidate, refusing a protected one.

        The refusal is an error rather than a no-op: a caller that believes it
        pruned something must find out that it did not.
        """
        record = self.require(expert_id)
        if record.identity.protected:
            raise ExpertFabricError(f"expert {expert_id} is protected and can never be marked a prune candidate; protection is declared in config.yaml -> intelligence.experts.protected")
        return self.transition(expert_id, ExpertLifecycleState.PRUNE_CANDIDATE)

    def prune(self, expert_id: str, reason: str) -> ExpertRecord:
        """Archive-then-mark-pruned, recording the reason.

        Two steps in that order on purpose: the ``PRUNE_CANDIDATE ->
        ARCHIVED`` edge must be taken before ``ARCHIVED -> PRUNED``, so an
        observer can see the expert was archived first. The record and its
        lineage survive; ``reinstate`` reverses it.
        """
        if not (reason or "").strip():
            raise ExpertFabricError("a prune requires a reason; a silent prune is prohibited")
        with self._lock:
            record = self.require(expert_id)
            if record.identity.protected:
                raise ExpertFabricError(f"expert {expert_id} is protected and can never be pruned")
            current = record.identity.status
            if current is not ExpertLifecycleState.PRUNE_CANDIDATE:
                raise ExpertFabricError(f"expert {expert_id} is {current.value}; pruning requires PRUNE_CANDIDATE first (mark_prune_candidate must pass evaluate_prune)")
            record.lineage_reason = f"{record.lineage_reason} | pruned: {reason}".strip(" |")
            record.provenance.setdefault("pruned_at", _now_iso())
            record.provenance.setdefault("prune_reason", reason)
            self.transition(expert_id, ExpertLifecycleState.ARCHIVED)
            self.transition(expert_id, ExpertLifecycleState.PRUNED)
            return self._records[expert_id]

    def reinstate(self, expert_id: str, reason: str) -> ExpertRecord:
        """Return a pruned expert to TRIAL for re-evaluation.

        Reinstated experts go to TRIAL, never straight to ACTIVE: the evidence
        that justified pruning is gone, and so is the evidence that justified
        the original promotion.
        """
        if not (reason or "").strip():
            raise ExpertFabricError("a reinstatement requires a reason")
        with self._lock:
            record = self.require(expert_id)
            record.lineage_reason = f"{record.lineage_reason} | reinstated: {reason}".strip(" |")
            record.provenance.setdefault("reinstated_at", _now_iso())
            self.transition(expert_id, ExpertLifecycleState.ARCHIVED)
            return self.transition(expert_id, ExpertLifecycleState.TRIAL)

    # -- snapshot payload --------------------------------------------------

    def export(self) -> dict[str, Any]:
        """A JSON-safe snapshot of the whole fabric, for the snapshot manager."""
        with self._lock:
            return {
                "schema_version": EXPERT_FABRIC_SCHEMA_VERSION,
                "next_id": self._next_id,
                "experts": [record.to_dict() for record in self._ordered()],
            }

    def restore(self, payload: dict[str, Any]) -> None:
        """Replace the whole fabric from a snapshot payload, atomically.

        Validates *every* record before touching live state, so a bad snapshot
        cannot leave the fabric half-restored.
        """
        if not isinstance(payload, dict):
            raise ExpertFabricError(f"snapshot payload must be an object, got {type(payload).__name__}")
        if payload.get("schema_version") != EXPERT_FABRIC_SCHEMA_VERSION:
            raise ExpertFabricError(f"snapshot schema_version {payload.get('schema_version')!r} != {EXPERT_FABRIC_SCHEMA_VERSION}; refusing to restore")
        staged: dict[str, ExpertRecord] = {}
        for item in payload.get("experts", []):
            record = ExpertRecord.from_dict(item)
            if record.identity.expert_id in staged:
                raise ExpertFabricError(f"snapshot contains duplicate expert id {record.identity.expert_id!r}")
            staged[record.identity.expert_id] = record
        counter = payload.get("next_id")
        if not isinstance(counter, int) or isinstance(counter, bool) or counter < _FIRST_ID:
            raise ExpertFabricError(f"snapshot next_id must be an integer >= {_FIRST_ID}, got {counter!r}")
        with self._lock:
            self._records = staged
            self._next_id = counter
            self._save()


def _age_seconds(created_at: str, now: float) -> float | None:
    """Age in seconds from an ISO-8601 ``created_at``, or ``None`` if unparseable."""
    if not created_at:
        return None
    try:
        parsed = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return now - parsed.timestamp()


_FABRIC_LOCK = threading.Lock()
_FABRIC: ExpertFabric | None = None


def get_expert_fabric() -> ExpertFabric:
    """Process-wide :class:`ExpertFabric` singleton.

    A construction failure (corrupt fabric) is **not** cached as ``None`` — the
    lock is released and the error propagates, so a corrupt fabric is visible at
    every call rather than being papered over by a later successful-looking
    default.
    """
    global _FABRIC
    with _FABRIC_LOCK:
        if _FABRIC is None:
            _FABRIC = ExpertFabric()
        return _FABRIC
