"""RSI candidate factory: evidence-backed variant populations with dedup (WP-B2, feature #9).

Implements plan ``references/ALPHA_RSI_IMPLEMENTATION_PLAN.md`` §3 WP-B2.
Honesty contract (§5 guardrails, binding):

- Evidence is validated by the **same fail-closed rules** as skill evolution:
  :func:`alpha.skills.evolution_engine._normalize_evidence` is *imported*,
  never re-implemented (≥1 ref, ≤50 refs, ≤2000-char refs — one source of
  truth for the caps, so this module cannot drift from them).
- Forbidden surfaces reuse :data:`alpha.evolution.engine.FORBIDDEN_SURFACES`
  — there is exactly one forbidden list in Alpha and this module adds no
  second one. :data:`EVOLVABLE_SURFACES` is only the plan-declared
  ``Literal["skill", "prompt", "routing", "memory_retrieval", "code"]``
  allow-set (plan §3 WP-B2); the forbidden check runs first, so every
  forbidden surface fails with "not evolvable".
- Dedup is an exact ``sha256(canonical json)`` payload-hash match (the hash
  helper is imported from ``alpha.rsi.lineage``): exact duplicates are
  dropped and reported verbatim as
  ``{"skipped": "duplicate", "payload_hash": ...}`` (spec RSI-E017 duplicate
  reason — a string reason, never a metric). No diversity score, similarity
  number, or any other invented quantity exists anywhere in this module.
- Generation **never claims improvement**: :meth:`VariantSpec.to_dict`
  carries exactly the plan's ten fields — no ``score``, no ``confidence``,
  no ``improved`` — and no evaluation of any kind runs here.
- Template-driven operators only (doc §12 of
  ``references/RSI_AGENT_ARCHITECTURE.md``: conservative / alternative /
  performance-oriented / resilience-oriented patches): the pure functions
  :func:`op_conservative`, :func:`op_alternative`, :func:`op_perf_oriented`,
  :func:`op_resilience`. LLM-driven generation is explicitly out of scope
  for Phase B — this module makes no model calls and imports no model
  client (promptbreeder remains the opt-in LLM path behind its own
  ``eval_fn``).
- The strategy-memory prior consumed via
  :func:`alpha.rsi.strategy_memory.prior_for` is a **disclosed heuristic**
  over recorded ledger counts: it only reorders operator choice, every
  variant's payload discloses the prior as ``basis="heuristic"`` with the
  concrete counts note (``stat_note``) and an explicit "not a measurement of
  this variant" line, and exploration is never disabled — whenever a
  distinct non-dominant operator exists and ``population >= 2``, at least
  one non-dominant operator is always emitted. A ``None`` prior becomes the
  disclosed neutral default (``heuristic_value = 0.5``, never 0.0/1.0).

Identity decisions (plan leaves the sources open; stated honestly):

- ``parent_id`` is the generating ``hypothesis_id``, recorded verbatim by
  the lineage store — spec §12 requires every candidate to carry a parent
  link, and an unresolvable parent ends ``RsiLineageStore.ancestry()``
  instead of being invented.
- ``cycle_id`` is the honest string ``"unknown"``: ``generate()`` is not
  told a cycle (same precedent as ``lineage.record_from_evol_candidate``'s
  "no cycle concept → unknown"), and this factory never reads cycle state
  it was not given.

Persistence: each spec is written atomically (``atomic_write_json``:
tmp + ``os.replace``) to ``archive_root/<variant_id>.json`` with
``archive_root`` defaulting to ``runtime_home()/rsi/variants`` (resolved
lazily so ``AGENT_WORKSPACE_HOME`` is honored at call time). Lineage record
happens before the spec file: lineage is the durable source of truth, and
``variants_for()`` never reports a spec the lineage store does not know.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Literal

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.engine import FORBIDDEN_SURFACES
from alpha.evolution.identity import atomic_write_json
from alpha.rsi.lineage import RsiLineageStore, _canonical_payload_hash
from alpha.rsi.strategy_memory import prior_for, stat_note
from alpha.skills.evolution_engine import _normalize_evidence

logger = logging.getLogger(__name__)

Surface = Literal["skill", "prompt", "routing", "memory_retrieval", "code"]

#: The plan's declared evolvable-surface ``Literal`` as an allow-set (NOT a
#: second forbidden list: ``FORBIDDEN_SURFACES`` above remains the only one,
#: and it is checked first).
EVOLVABLE_SURFACES: frozenset[str] = frozenset({"skill", "prompt", "routing", "memory_retrieval", "code"})

#: Disclosed neutral prior for an operator with no recorded strategy memory
#: (strategy_memory.prior_for consumption contract: unverified → 0.5, never
#: a fabricated 0.0/1.0 that would read as a measurement).
_NEUTRAL_PRIOR_VALUE = 0.5

_PRIOR_SOURCE = "alpha.rsi.strategy_memory.prior_for"


@dataclass
class VariantSpec:
    """One generated variant: identity + provenance only — never a verdict.

    The field set is exactly the plan's (§3 WP-B2). ``to_dict()`` is the
    wire format and by construction contains no ``score``/``confidence``/
    ``improved`` field: generation has not evaluated anything, so it has
    nothing of the kind to emit.
    """

    variant_id: str
    cycle_id: str
    parent_id: str | None
    surface: Surface
    target: str
    mutation_operator: str
    payload: dict[str, Any]
    payload_hash: str
    evidence_refs: list[dict[str, Any]]
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        """Honest wire format: exactly the ten spec fields, nothing inferred."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> VariantSpec:
        """Rebuild a spec from persisted JSON; a malformed record is a ``ValueError`` (callers skip it honestly, never repair it)."""
        if not isinstance(data, dict):
            raise ValueError(f"variant spec must be a JSON object, got {type(data).__name__}.")
        missing = [field.name for field in fields(cls) if field.name not in data]
        if missing:
            raise ValueError(f"variant spec is missing field(s): {missing}.")
        if not isinstance(data["payload"], dict):
            raise ValueError("variant spec payload must be a JSON object.")
        if not isinstance(data["evidence_refs"], list):
            raise ValueError("variant spec evidence_refs must be a list.")
        return cls(
            variant_id=str(data["variant_id"]),
            cycle_id=str(data["cycle_id"]),
            parent_id=None if data["parent_id"] is None else str(data["parent_id"]),
            surface=data["surface"],  # runtime surface validation lives in generate(); persisted specs are read, not re-gated
            target=str(data["target"]),
            mutation_operator=str(data["mutation_operator"]),
            payload=data["payload"],
            payload_hash=str(data["payload_hash"]),
            evidence_refs=data["evidence_refs"],
            created_at=float(data["created_at"]),
        )


# --- Template operators (doc §12; pure functions, no I/O, no model calls) ---


def op_conservative(payload: dict[str, Any]) -> dict[str, Any]:
    """Doc §12 "conservative patch": smallest low-risk change toward the hypothesis."""
    out = dict(payload)
    out["operator"] = "conservative"
    out["mutation"] = {
        "class": "conservative",
        "action": "minimal_delta",
        "description": "smallest low-risk template change toward the hypothesis; existing behavior is preserved unless the hypothesis requires the change",
    }
    return out


def op_alternative(payload: dict[str, Any]) -> dict[str, Any]:
    """Doc §12 "alternative implementation": a structurally different route to the same hypothesis."""
    out = dict(payload)
    out["operator"] = "alternative"
    out["mutation"] = {
        "class": "alternative",
        "action": "alternate_implementation",
        "description": "template change taking a structurally different route to the same hypothesis goal (doc §12 alternative implementation)",
    }
    return out


def op_perf_oriented(payload: dict[str, Any]) -> dict[str, Any]:
    """Doc §12 "performance-oriented patch": biased toward latency/resource cost at equal behavior."""
    out = dict(payload)
    out["operator"] = "perf_oriented"
    out["mutation"] = {
        "class": "performance",
        "action": "throughput_latency_focus",
        "description": "template change biased toward lower latency and lower resource cost at behavior otherwise equal (doc §12 performance-oriented patch)",
    }
    return out


def op_resilience(payload: dict[str, Any]) -> dict[str, Any]:
    """Doc §12 "resilience-oriented patch": biased toward failure-path handling."""
    out = dict(payload)
    out["operator"] = "resilience"
    out["mutation"] = {
        "class": "resilience",
        "action": "failure_path_focus",
        "description": "template change biased toward retry, timeout, and degradation paths (doc §12 resilience-oriented patch)",
    }
    return out


#: Phase-B operator registry — template-driven only, ordered default set.
TEMPLATE_OPERATORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "conservative": op_conservative,
    "alternative": op_alternative,
    "perf_oriented": op_perf_oriented,
    "resilience": op_resilience,
}


def _prior_value(stat: Any) -> float:
    """Scalar for ordering: the recorded promotion rate, or the disclosed neutral 0.5.

    ``None`` prior / zero attempts / absent rate ⇒ unverified ⇒ 0.5 — the
    strategy_memory contract; never a fabricated 0.0/1.0.
    """
    if stat is None:
        return _NEUTRAL_PRIOR_VALUE
    rate = getattr(stat, "promotion_rate", None)
    attempts = getattr(stat, "attempts", 0)
    if attempts <= 0 or rate is None:
        return _NEUTRAL_PRIOR_VALUE
    try:
        return float(rate)
    except (TypeError, ValueError):
        return _NEUTRAL_PRIOR_VALUE


def _prior_disclosure(name: str, surface: str, stat: Any) -> dict[str, Any]:
    """Wire-format disclosure for one operator's prior — always ``basis="heuristic"``, never a measurement claim.

    A recorded stat carries strategy_memory's own ``stat_note`` (formula +
    concrete counts); a missing stat carries the neutral-default note.
    """
    attempts = getattr(stat, "attempts", None) if stat is not None else None
    if stat is None or not attempts:
        note = (
            "no recorded prior for this (operator, problem_class); disclosed neutral default heuristic_value=0.5 "
            "(unverified, never 0.0/1.0) — heuristic prior for operator choice only, not a measurement of this variant"
        )
        return {
            "basis": "heuristic",
            "source": _PRIOR_SOURCE,
            "strategy": name,
            "problem_class": surface,
            "heuristic_value": _NEUTRAL_PRIOR_VALUE,
            "status": "no_recorded_prior",
            "recorded_attempts": None if stat is None else 0,
            "note": note,
        }
    return {
        "basis": "heuristic",
        "source": _PRIOR_SOURCE,
        "strategy": name,
        "problem_class": surface,
        "heuristic_value": _prior_value(stat),
        "status": "recorded_prior",
        "recorded_attempts": attempts,
        "note": f"{stat_note(stat)} — disclosed heuristic prior over recorded strategy-memory counts (basis=heuristic); not a measurement of this variant",
    }


def _order_operators(requested: list[str], surface: str) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Prior-informed emission order that never disables exploration.

    With a *distinct* dominant operator (strictly highest disclosed
    heuristic), the order is ``[first dominant, all non-dominants, remaining
    dominants]`` — so any ``population >= 2`` slice contains at least one
    non-dominant operator whenever one exists. All-tied (e.g. no recorded
    prior) keeps input order. The prior only reorders; it never drops a
    requested operator from consideration and never fabricates a value.
    """
    unique = list(dict.fromkeys(requested))
    stats = {name: prior_for(name, surface) for name in unique}
    values = {name: _prior_value(stats[name]) for name in unique}
    disclosures = {name: _prior_disclosure(name, surface, stats[name]) for name in unique}
    top = max(values.values())
    if any(value < top for value in values.values()):
        dominant = [name for name in unique if values[name] == top]
        non_dominant = [name for name in unique if values[name] < top]
        ordered = [dominant[0]] + non_dominant + dominant[1:]
    else:
        ordered = unique
    return ordered, disclosures


class CandidateFactory:
    """Generate evidence-backed variant populations with lineage + dedup (plan §3 WP-B2).

    ``lineage`` defaults (lazily, at first use) to the wave-mate
    ``RsiLineageStore`` rooted at ``runtime_home()/rsi``; ``archive_root``
    defaults to ``runtime_home()/rsi/variants`` for per-variant spec files.
    Both resolve lazily so a test-set ``AGENT_WORKSPACE_HOME`` is honored.

    ``skipped`` holds the honest dedup report of the most recent
    ``generate()`` call (reset at entry, including on a failed call): a list
    of exactly ``{"skipped": "duplicate", "payload_hash": ...}`` dicts —
    one per dropped exact-duplicate payload. Dedup scope is one call: two
    separate ``generate()`` calls are separate proposals with distinct
    durable identities even when their payloads coincide (lineage keys on
    ``variant_id``, not on payload).
    """

    def __init__(self, lineage: RsiLineageStore | None = None, archive_root: Path | str | None = None) -> None:
        self._lineage = lineage
        self._archive_root = Path(archive_root) if archive_root is not None else None
        self.skipped: list[dict[str, Any]] = []

    @property
    def lineage(self) -> RsiLineageStore:
        """The lineage store (lazily created default keeps env-scoped roots honest)."""
        if self._lineage is None:
            self._lineage = RsiLineageStore()
        return self._lineage

    @property
    def archive_root(self) -> Path:
        """Where variant spec files persist; default ``runtime_home()/rsi/variants`` (lazy)."""
        if self._archive_root is None:
            return runtime_home() / "rsi" / "variants"
        return self._archive_root

    def generate(
        self,
        hypothesis_id: str,
        *,
        target: str,
        surface: str,
        evidence_refs: list,
        operators: list[str] | None = None,
        population: int = 3,
    ) -> list[VariantSpec]:
        """Build up to ``population`` distinct variants of ``hypothesis_id`` (plan §3 WP-B2).

        Validation order follows the plan: (1) evidence with the imported
        fail-closed ``_normalize_evidence`` semantics, (2) surface against the
        reused ``FORBIDDEN_SURFACES`` constant, (3) the remaining arguments.
        Each accepted variant is recorded into the lineage store with its
        ``parent_id`` (the hypothesis) and ``mutation_operator``, then
        persisted atomically. Returns fewer than ``population`` only when
        template operators are exhausted (duplicates are reported via
        ``skipped``; the population is never padded with fabricated
        diversity). LLM generation is out of scope: no model call exists on
        this path.
        """
        self.skipped = []
        # (1) evidence — imported, never copied (one source of truth for the caps)
        normalized = _normalize_evidence(evidence_refs)
        try:
            json.dumps(normalized, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            # fail closed BEFORE any write so a non-persistable ref cannot cause a half-recorded population
            raise ValueError(f"evidence references must be JSON-serializable to persist honestly: {exc}") from exc
        # (2) forbidden surface first (reused constant — no second forbidden list), then the plan's evolvable Literal allow-set
        if not isinstance(surface, str) or not surface:
            raise ValueError("surface must be a non-empty string.")
        if surface in FORBIDDEN_SURFACES:
            raise ValueError(f"surface {surface!r} is not evolvable: it is listed in alpha.evolution.engine.FORBIDDEN_SURFACES.")
        if surface not in EVOLVABLE_SURFACES:
            raise ValueError(f"surface {surface!r} is not evolvable: allowed surfaces are {sorted(EVOLVABLE_SURFACES)}.")
        # (3) remaining arguments
        if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
            raise ValueError("hypothesis_id must be a non-empty string.")
        if not isinstance(target, str) or not target:
            raise ValueError("target must be a non-empty string.")
        if isinstance(population, bool) or not isinstance(population, int) or population < 1:
            raise ValueError(f"population must be an integer >= 1, got {population!r}.")
        if operators is None:
            requested = list(TEMPLATE_OPERATORS)
        else:
            if not isinstance(operators, (list, tuple)) or not operators:
                raise ValueError("operators must be a non-empty list of template operator names (or None for the default template set).")
            requested = []
            for name in operators:
                if not isinstance(name, str) or name not in TEMPLATE_OPERATORS:
                    raise ValueError(f"unknown template operator {name!r}; Phase B is template-driven only ({', '.join(TEMPLATE_OPERATORS)}) — LLM-driven generation is out of scope.")
                requested.append(name)

        order, disclosures = _order_operators(requested, surface)
        base_payload: dict[str, Any] = {"hypothesis_id": hypothesis_id, "surface": surface, "target": target}
        seen: set[str] = set()
        specs: list[VariantSpec] = []
        for index in range(population):
            name = order[index % len(order)]
            payload = TEMPLATE_OPERATORS[name](base_payload)  # pure: base_payload is never mutated
            payload["operator_choice"] = dict(disclosures[name])
            payload_hash = _canonical_payload_hash(payload)
            if payload_hash in seen:
                # spec RSI-E017: an honest string reason + the real hash — no fabricated diversity metric
                self.skipped.append({"skipped": "duplicate", "payload_hash": payload_hash})
                logger.info("duplicate candidate payload skipped (spec RSI-E017 duplicate reason, string reason only): payload_hash=%s operator=%s", payload_hash, name)
                continue
            seen.add(payload_hash)
            spec = VariantSpec(
                variant_id=f"var-{uuid.uuid4().hex}",
                cycle_id="unknown",  # honest placeholder: generate() is not told a cycle (lineage precedent: "no cycle concept → unknown")
                parent_id=hypothesis_id,  # the parent link: variants are children of their generating hypothesis, recorded verbatim
                surface=surface,  # type: ignore[arg-type]  # validated against EVOLVABLE_SURFACES above
                target=target,
                mutation_operator=name,
                payload=payload,
                payload_hash=payload_hash,
                evidence_refs=normalized,
                created_at=time.time(),
            )
            # lineage first (durable source of truth), then the derived spec file (atomic tmp + os.replace)
            self.lineage.record(spec, parent_id=spec.parent_id, mutation_operator=spec.mutation_operator)
            atomic_write_json(self.archive_root / f"{spec.variant_id}.json", spec.to_dict())
            specs.append(spec)
        return specs

    def variants_for(self, hypothesis_id: str) -> list[VariantSpec]:
        """All persisted specs generated for ``hypothesis_id``, oldest first.

        Honest reads: a corrupt/unreadable spec file is skipped and counted
        in a warning (lineage-store precedent) — never repaired, never
        guessed at. A missing directory is an honest empty result.
        """
        if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
            raise ValueError("hypothesis_id must be a non-empty string.")
        root = self.archive_root
        specs: list[VariantSpec] = []
        skipped = 0
        for path in sorted(root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                spec = VariantSpec.from_dict(data)
            except FileNotFoundError:  # raced deletion: an honest gap, not an error
                continue
            except (OSError, ValueError, TypeError):
                skipped += 1
                continue
            linked = spec.parent_id == hypothesis_id or (isinstance(spec.payload, dict) and spec.payload.get("hypothesis_id") == hypothesis_id)
            if linked:
                specs.append(spec)
        if skipped:
            logger.warning("Skipped %d corrupt/unreadable variant spec file(s) in %s", skipped, root)
        specs.sort(key=lambda spec: (spec.created_at, spec.variant_id))
        return specs
