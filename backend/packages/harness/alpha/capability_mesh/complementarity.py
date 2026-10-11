"""Measured pair value: complementarity is a measured property (plan §4.2).

Two agents may have similar individual scores but low combined value
because they make the same mistakes. This module records what was
actually measured about a collaboration pair — solo qualities, the
quality when they worked together, disagreement usefulness, redundant
call rate, added cost and latency — and refuses to assert a synergy
that was never observed.

Four rules decide the shape of this module:

* **Together without solo baselines is refused.** A ``together``
  quality without both ``solo_a`` and ``solo_b`` cannot be compared
  to anything, so the constructor refuses it: an unanchored
  measurement is not evidence of anything.
* **No samples means SPECULATIVE, and SPECULATIVE is half value.**
  ``sample_count == 0`` forces ``ComplementarityEvidence.SPECULATIVE``;
  ``OBSERVED`` (and ``CONTRADICTED`` — a measured verdict) requires
  ``sample_count >= PAIR_MIN_SAMPLES`` (5). Downstream
  (:mod:`alpha.capability_mesh.team`) a speculative pair contributes
  at HALF its value and flags the member speculative, so an
  unmeasured hunch can never drive an asserted synergy.
* **A contradiction is a measurement, and must look like one.**
  ``CONTRADICTED`` means collaboration measured WORSE than both
  members alone, so it requires enough samples AND
  ``together < solo_a`` AND ``together < solo_b`` — a contradiction
  that does not contradict is a fabrication.
* **No measurement without its profile version.** Any set mean
  (solo/together/disagreement/redundant-call) requires a non-empty
  ``profile_version``, so a pair estimate is always traceable to the
  rubric that produced it.

:class:`PairMemory` is durable JSON using the package's atomic write
pattern (temp file + fsync + ``os.replace``), keyed by the sorted
entity pair plus task category; a corrupt file raises
:class:`PairMemoryStoreError` rather than silently starting empty.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.capability_mesh.graph import CapabilityGraph, EdgeKind, NodeKind, node_id_for

#: The sample floor before a pair estimate may call itself OBSERVED (or
#: CONTRADICTED). Below it, the pair is a hypothesis — plan §4.2: "only
#: state that a pair is complementary after collecting sufficient
#: evidence; otherwise tag the estimate as speculative."
PAIR_MIN_SAMPLES = 5


class ComplementarityEvidence(StrEnum):
    """How strongly a pair estimate is backed (plan §4.2)."""

    SPECULATIVE = "speculative"
    OBSERVED = "observed"
    CONTRADICTED = "contradicted"


def _unit_interval(value: float | None, field: str) -> float | None:
    """Validate an optional 0..1 finite measurement."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number in [0, 1] or None, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{field} must be a finite number in [0, 1], got {value!r}")
    return number


@dataclass(frozen=True)
class PairEstimate:
    """One measured (or explicitly speculative) collaboration pair (plan §4.2).

    ``entity_a``/``entity_b`` are passport entity ids; ``task_category``
    scopes the estimate (pair value is task-relative, never global).
    ``solo_a``/``solo_b``/``together`` are 0..1 qualities;
    ``disagreement_usefulness`` and ``redundant_call_rate`` are 0..1
    rates; ``added_cost_usd``/``added_latency_ms`` are the pairing's
    measured overhead. ``complementarity_value()`` is the only synergy
    number this record will ever assert, and it is ``None`` unless all
    three qualities were measured — a partial record never fabricates a
    synergy.
    """

    entity_a: str
    entity_b: str
    task_category: str
    solo_a: float | None = None
    solo_b: float | None = None
    together: float | None = None
    disagreement_usefulness: float | None = None
    redundant_call_rate: float | None = None
    added_cost_usd: float | None = None
    added_latency_ms: int | None = None
    sample_count: int = 0
    evidence: ComplementarityEvidence = ComplementarityEvidence.SPECULATIVE
    profile_version: str = ""

    def __post_init__(self) -> None:
        for field in ("entity_a", "entity_b", "task_category"):
            if not getattr(self, field):
                raise ValueError(f"{field} must be non-empty")
        try:
            object.__setattr__(self, "evidence", ComplementarityEvidence(str(self.evidence)))
        except ValueError as error:
            allowed = ", ".join(member.value for member in ComplementarityEvidence)
            raise ValueError(f"unknown evidence {self.evidence!r}; expected one of: {allowed}") from error

        object.__setattr__(self, "solo_a", _unit_interval(self.solo_a, "solo_a"))
        object.__setattr__(self, "solo_b", _unit_interval(self.solo_b, "solo_b"))
        object.__setattr__(self, "together", _unit_interval(self.together, "together"))
        object.__setattr__(self, "disagreement_usefulness", _unit_interval(self.disagreement_usefulness, "disagreement_usefulness"))
        object.__setattr__(self, "redundant_call_rate", _unit_interval(self.redundant_call_rate, "redundant_call_rate"))

        if self.together is not None and (self.solo_a is None or self.solo_b is None):
            raise ValueError("together requires both solo_a and solo_b: a combined quality without its solo baselines is unanchored and refused")

        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 0:
            raise ValueError(f"sample_count must be an int >= 0, got {self.sample_count!r}")
        if self.sample_count == 0 and self.evidence is not ComplementarityEvidence.SPECULATIVE:
            raise ValueError(f"sample_count == 0 forces evidence SPECULATIVE (no pair synergy without observations), got {self.evidence.value}")
        if self.evidence in (ComplementarityEvidence.OBSERVED, ComplementarityEvidence.CONTRADICTED) and self.sample_count < PAIR_MIN_SAMPLES:
            raise ValueError(f"evidence '{self.evidence.value}' requires sample_count >= {PAIR_MIN_SAMPLES} (sufficient evidence before a pair claim), got {self.sample_count}")
        if self.evidence is ComplementarityEvidence.CONTRADICTED:
            # A contradiction is a measurement: collaboration measured WORSE than both alone.
            if self.together is None or self.together >= self.solo_a or self.together >= self.solo_b:
                raise ValueError("CONTRADICTED requires together < solo_a AND together < solo_b: collaboration measured worse than both members alone")

        if isinstance(self.added_cost_usd, bool) or (self.added_cost_usd is not None and (not isinstance(self.added_cost_usd, (int, float)) or not math.isfinite(float(self.added_cost_usd)) or float(self.added_cost_usd) < 0)):
            raise ValueError(f"added_cost_usd must be a finite number >= 0 or None, got {self.added_cost_usd!r}")
        if isinstance(self.added_latency_ms, bool) or (self.added_latency_ms is not None and (not isinstance(self.added_latency_ms, int) or self.added_latency_ms < 0)):
            raise ValueError(f"added_latency_ms must be an int >= 0 or None, got {self.added_latency_ms!r}")

        means = (self.solo_a, self.solo_b, self.together, self.disagreement_usefulness, self.redundant_call_rate)
        if any(mean is not None for mean in means) and not self.profile_version:
            raise ValueError("a measured mean requires a non-empty profile_version (no score without its profile version)")

    def complementarity_value(self) -> float | None:
        """The measured synergy: ``together - max(solo_a, solo_b)``.

        ``None`` unless all three qualities were measured — a partial
        record never fabricates a synergy number. A negative value is a
        real answer: the pair measured WORSE than the better member
        alone (a contradicted pair).
        """
        if self.together is None or self.solo_a is None or self.solo_b is None:
            return None
        return self.together - max(self.solo_a, self.solo_b)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_a": self.entity_a,
            "entity_b": self.entity_b,
            "task_category": self.task_category,
            "solo_a": self.solo_a,
            "solo_b": self.solo_b,
            "together": self.together,
            "disagreement_usefulness": self.disagreement_usefulness,
            "redundant_call_rate": self.redundant_call_rate,
            "added_cost_usd": self.added_cost_usd,
            "added_latency_ms": self.added_latency_ms,
            "sample_count": self.sample_count,
            "evidence": self.evidence.value,
            "profile_version": self.profile_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PairEstimate:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            entity_a=str(data["entity_a"]),
            entity_b=str(data["entity_b"]),
            task_category=str(data["task_category"]),
            solo_a=data.get("solo_a"),
            solo_b=data.get("solo_b"),
            together=data.get("together"),
            disagreement_usefulness=data.get("disagreement_usefulness"),
            redundant_call_rate=data.get("redundant_call_rate"),
            added_cost_usd=data.get("added_cost_usd"),
            added_latency_ms=data.get("added_latency_ms"),
            sample_count=int(data.get("sample_count", 0)),
            # Coerced (and validated with the friendly message) in __post_init__.
            evidence=data.get("evidence", ComplementarityEvidence.SPECULATIVE),
            profile_version=str(data.get("profile_version", "")),
        )


#: The single JSON document a :class:`PairMemory` owns in its directory.
PAIR_ESTIMATES_FILE_NAME = "pair_estimates.json"


class PairMemoryStoreError(RuntimeError):
    """The pair-estimate document is unreadable, corrupt, or dishonest."""


class PairMemory:
    """Durable store of :class:`PairEstimate` rows, keyed by the sorted pair.

    One memory is one directory holding one ``pair_estimates.json``.
    The key is ``(sorted(entity_a, entity_b), task_category)`` — the
    pair is unordered, so ``get("a", "b", ...)`` and ``get("b", "a",
    ...)`` are the same estimate. ``record()`` replaces the estimate for
    a key (a newer measurement supersedes an older one; the newest
    evidence wins). A missing file is a genuinely empty memory; a
    corrupt or dishonest one raises :class:`PairMemoryStoreError` — one
    bad sector must not become amnesia. Single-process JSON: atomic
    against a crash in one process, **never cross-process exactly-once**.
    """

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)
        self._pairs: dict[str, PairEstimate] = self._load()

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def path(self) -> Path:
        return self._directory / PAIR_ESTIMATES_FILE_NAME

    @staticmethod
    def _key(entity_a: str, entity_b: str, task_category: str) -> str:
        first, second = sorted((entity_a, entity_b))
        return f"{first}\x1f{second}\x1f{task_category}"

    def _load(self) -> dict[str, PairEstimate]:
        path = self.path
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError) as error:
            raise PairMemoryStoreError(f"pair memory '{path}' is unreadable: {error}") from error
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise PairMemoryStoreError(f"pair memory '{path}' is not valid JSON: {error}") from error
        if not isinstance(document, dict) or not isinstance(document.get("pairs", {}), dict):
            raise PairMemoryStoreError(f"pair memory '{path}' must be a JSON object with a 'pairs' mapping")
        pairs: dict[str, PairEstimate] = {}
        for key, entry in document["pairs"].items():
            try:
                pairs[str(key)] = PairEstimate.from_dict(entry)
            except (TypeError, KeyError, ValueError) as error:
                raise PairMemoryStoreError(f"pair memory '{path}' entry {key!r} is invalid: {error}") from error
        return pairs

    def save(self) -> None:
        """Atomically rewrite the document (temp + fsync + ``os.replace``)."""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({"pairs": {key: estimate.to_dict() for key, estimate in self._pairs.items()}}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ----------------------------------------------------------------- writes

    def record(self, estimate: PairEstimate) -> None:
        """Record (or supersede) the estimate for the pair's key."""
        if not isinstance(estimate, PairEstimate):
            raise ValueError(f"record() expects a PairEstimate, got {estimate!r}")
        self._pairs[self._key(estimate.entity_a, estimate.entity_b, estimate.task_category)] = estimate
        self.save()

    # ------------------------------------------------------------------ reads

    def get(self, entity_a: str, entity_b: str, task_category: str) -> PairEstimate | None:
        """The estimate for one unordered pair and task category, or None."""
        return self._pairs.get(self._key(entity_a, entity_b, task_category))

    def for_entity(self, entity_id: str) -> tuple[PairEstimate, ...]:
        """Every estimate involving ``entity_id``, deterministically sorted."""
        rows = [estimate for estimate in self._pairs.values() if entity_id in (estimate.entity_a, estimate.entity_b)]
        return tuple(sorted(rows, key=lambda estimate: (estimate.entity_a, estimate.entity_b, estimate.task_category)))

    def pairs(self) -> tuple[PairEstimate, ...]:
        """Every stored estimate, deterministically sorted."""
        return tuple(sorted(self._pairs.values(), key=lambda estimate: (estimate.entity_a, estimate.entity_b, estimate.task_category)))


@dataclass(frozen=True)
class CorrelationRisk:
    """The correlated-failure risk between two entities (plan §4.3's penalty input).

    ``risk`` is the Jaccard overlap of the two entities' *recorded*
    failure modes in ``[0, 1]``; ``shared_failure_modes`` names them
    (sorted). ``known`` is False — with ``risk`` 0.0 and no shared
    modes — when either entity has no recorded failure modes at all:
    **unknown is not safe, it is unknown**. A risk of 0.0 with
    ``known=False`` must never be read as "these two are independent";
    it must be read as "nothing was measured".
    """

    risk: float
    shared_failure_modes: tuple[str, ...] = ()
    known: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.risk, bool) or not isinstance(self.risk, (int, float)) or not math.isfinite(float(self.risk)) or not 0.0 <= float(self.risk) <= 1.0:
            raise ValueError(f"risk must be a finite number in [0, 1], got {self.risk!r}")
        object.__setattr__(self, "risk", float(self.risk))
        modes = tuple(self.shared_failure_modes)
        for mode in modes:
            if not mode:
                raise ValueError("shared_failure_modes entries must be non-empty strings")
        object.__setattr__(self, "shared_failure_modes", tuple(sorted(modes)))
        if not isinstance(self.known, bool):
            raise ValueError(f"known must be a bool, got {self.known!r}")


def correlated_failure_risk(graph: CapabilityGraph, entity_a: str, entity_b: str) -> CorrelationRisk:
    """The shared-failure-mode overlap of two entities, from the graph's ``OFTEN_MISSES`` edges.

    Each entity's recorded failure modes are the targets of its
    ``OFTEN_MISSES`` edges (what :func:`build_from_memory` derives
    from capability memory). The risk is the Jaccard index of the two
    mode sets: ``|shared| / |union|``. When either entity has no
    recorded failure modes the answer is ``risk=0.0, known=False`` —
    an absence of measurements, never a measurement of independence.
    """
    if not isinstance(graph, CapabilityGraph):
        raise ValueError(f"graph must be a CapabilityGraph, got {graph!r}")
    modes_a = _recorded_failure_modes(graph, entity_a)
    modes_b = _recorded_failure_modes(graph, entity_b)
    if not modes_a or not modes_b:
        return CorrelationRisk(risk=0.0, shared_failure_modes=(), known=False)
    shared = sorted(set(modes_a) & set(modes_b))
    union = set(modes_a) | set(modes_b)
    return CorrelationRisk(risk=len(shared) / len(union), shared_failure_modes=tuple(shared), known=True)


def _recorded_failure_modes(graph: CapabilityGraph, entity_id: str) -> tuple[str, ...]:
    """The failure modes recorded against one entity via ``OFTEN_MISSES`` edges."""
    entity_node_id = node_id_for(NodeKind.ENTITY, entity_id)
    if graph.node(entity_node_id) is None:
        return ()
    modes: list[str] = []
    for edge in graph.edges(kind=EdgeKind.OFTEN_MISSES, node_id=entity_node_id):
        if edge.source != entity_node_id:
            continue
        target = graph.node(edge.target)
        if target is not None and target.kind is NodeKind.FAILURE_MODE:
            modes.append(target.label)
    return tuple(modes)


__all__ = [
    "PAIR_MIN_SAMPLES",
    "PAIR_ESTIMATES_FILE_NAME",
    "PairEstimate",
    "PairMemory",
    "PairMemoryStoreError",
    "ComplementarityEvidence",
    "CorrelationRisk",
    "correlated_failure_risk",
]
