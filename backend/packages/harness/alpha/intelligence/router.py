"""Capability router: candidates -> ranking -> exploration -> admission (prompt §17/§18).

The pipeline is four explicit stages, each of which can refuse:

1. **Generate candidates.** Only experts whose lifecycle state permits
   selection. ``ACTIVE`` is routable in production; ``TRIAL`` is routable only
   when ``allow_trial=True``. ``PROPOSED``, ``ARCHIVED`` and ``PRUNED`` are never
   candidates — an archived expert is not "available with a lower score".
2. **Rank** by utility from the injected :class:`~alpha.intelligence.scoring.UtilityScorer`.
3. **Explore**, only when asked. The exploration bonus is added *after* ranking
   and is capped by config.
4. **Admit**, bounded by ``limit``, and reported with a per-candidate reason.

Why exploration is separated rather than folded into the score
--------------------------------------------------------------
Folding it in would make "we are exploring" indistinguishable from "this expert
is genuinely the best fit", which is precisely the confusion that makes an
exploration-driven routing change unreviewable. Keeping it a separate additive
term means every :class:`RouteDecision` can report
:attr:`RouteDecision.exploration_bonus` and a reader can see exactly how much of
a ranking came from curiosity.

The production-safety rule from prompt §18 is enforced structurally: with
``exploring=False`` (the default) the bonus is ``0.0`` for every candidate, so
there is no code path where exploration can influence production routing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from alpha.intelligence.models import ExpertLifecycleState, ExpertRecord
from alpha.intelligence.scoring import DefaultUtilityScorer, ExpertScore, UtilityScorer

__all__ = [
    "RouteCandidate",
    "RouteDecision",
    "CapabilityRouter",
    "reason_for_state",
]

#: Which lifecycle states may be selected, and under what conditions.
_SELECTABLE_STATES: dict[ExpertLifecycleState, str] = {
    ExpertLifecycleState.ACTIVE: "routable",
    ExpertLifecycleState.TRIAL: "trial",
}


def reason_for_state(state: ExpertLifecycleState) -> str:
    """Why an expert in ``state`` is or is not a routing candidate."""
    if state in _SELECTABLE_STATES:
        return _SELECTABLE_STATES[state]
    if state is ExpertLifecycleState.PROMOTED:
        return "promoted but not yet activated"
    if state is ExpertLifecycleState.UNDERUSED:
        return "underused; not in the routable set"
    if state is ExpertLifecycleState.PRUNE_CANDIDATE:
        return "prune candidate; not routable"
    if state is ExpertLifecycleState.PRUNED:
        return "pruned"
    return f"status {state.value} is not selectable"


@dataclass(frozen=True)
class RouteCandidate:
    """One scored candidate, before or after exploration credit."""

    expert_id: str
    capability: str
    status: str
    utility: float
    exploration_bonus: float = 0.0
    score: float = 0.0
    score_components: dict[str, float] = field(default_factory=dict)
    trial: bool = False
    admitted: bool = False
    excluded_reason: str = ""
    """Set when the candidate was generated but not admitted. Empty when admitted."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "expert_id": self.expert_id,
            "capability": self.capability,
            "status": self.status,
            "utility": round(self.utility, 6),
            "exploration_bonus": round(self.exploration_bonus, 6),
            "score": round(self.score, 6),
            "trial": self.trial,
            "admitted": self.admitted,
            "excluded_reason": self.excluded_reason,
            "score_components": {k: round(v, 6) for k, v in self.score_components.items()},
        }


@dataclass(frozen=True)
class RouteDecision:
    """The full, explainable result of one routing call."""

    admitted: list[RouteCandidate]
    considered: list[RouteCandidate]
    excluded: list[dict[str, str]]
    """``{"expert_id", "reason"}`` for every record that was never a candidate."""
    exploring: bool
    scorer: str
    exploration_bonus_cap: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": [c.to_dict() for c in self.admitted],
            "considered": [c.to_dict() for c in self.considered],
            "excluded": [dict(item) for item in self.excluded],
            "exploring": self.exploring,
            "scorer": self.scorer,
            "exploration_bonus_cap": round(self.exploration_bonus_cap, 6),
        }

    @property
    def admitted_ids(self) -> list[str]:
        return [candidate.expert_id for candidate in self.admitted]


class CapabilityRouter:
    """Ranks expert records for a task and admits a bounded top-N.

    Holds no global state. The scorer is injected, so replacing the scoring
    policy is a constructor argument rather than an edit here.
    """

    def __init__(
        self,
        *,
        scorer: UtilityScorer | None = None,
        exploration_bonus_cap: float = 0.1,
        limit: int = 3,
        max_candidates: int = 32,
    ) -> None:
        if limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")
        if max_candidates < limit:
            raise ValueError(f"max_candidates ({max_candidates}) cannot be below limit ({limit})")
        if exploration_bonus_cap < 0:
            raise ValueError(f"exploration_bonus_cap must be >= 0, got {exploration_bonus_cap}")
        self.scorer: UtilityScorer = scorer or DefaultUtilityScorer()
        self.exploration_bonus_cap = float(exploration_bonus_cap)
        self.limit = int(limit)
        self.max_candidates = int(max_candidates)

    # -- exploration credit -------------------------------------------------

    def exploration_bonus(self, record: ExpertRecord) -> float:
        """Credit for an underused expert, capped and monotone in disuse.

        An expert with zero usage earns the full cap. An expert with usage earns
        ``cap / (1 + usage)``, so the bonus decays with use and is
        asymptotically 0 — it can never *outrank* a genuinely better expert once
        that expert has real reuse, which is the property that keeps exploration
        from degrading production quality.
        """
        if self.exploration_bonus_cap <= 0.0:
            return 0.0
        return self.exploration_bonus_cap / (1.0 + record.metrics.usage)

    # -- routing ------------------------------------------------------------

    def route(
        self,
        records: list[ExpertRecord],
        *,
        required_capabilities: set[str] | None = None,
        exploring: bool = False,
        allow_trial: bool = False,
        limit: int | None = None,
    ) -> RouteDecision:
        """Rank ``records`` and admit the top-N that may serve the task.

        Args:
            records: Candidate pool. Not filtered here — the caller decides the
                pool, so this function is usable with a full registry, a paging
                cache, or a hand-built test list.
            required_capabilities: When non-empty, only experts whose capability
                is in this set are admitted.
            exploring: Adds the capped exploration bonus. **Default ``False``**,
                which zeroes the bonus for every candidate, so no production path
                can be steered by it.
            allow_trial: Admit ``TRIAL`` experts. Required for a trial expert to
                ever gather the evidence that would promote it.
            limit: Overrides the instance limit for this call.

        Returns:
            A :class:`RouteDecision` carrying admitted, considered and excluded
            candidates with reasons.
        """
        bound = self.limit if limit is None else int(limit)
        if bound < 0:
            raise ValueError(f"limit must be >= 0, got {bound}")

        wanted = {c.strip().lower() for c in (required_capabilities or set()) if c and c.strip()}
        excluded: list[dict[str, str]] = []
        candidates: list[RouteCandidate] = []

        # Stage 1: generate. Cap the pool before scoring so a 10k registry
        # cannot turn one routing decision into 10k score computations.
        scored: list[tuple[ExpertRecord, ExpertScore]] = []
        for record in records[: self.max_candidates]:
            state = record.identity.status
            if state not in _SELECTABLE_STATES:
                excluded.append({"expert_id": record.identity.expert_id, "reason": reason_for_state(state)})
                continue
            if state is ExpertLifecycleState.TRIAL and not allow_trial:
                excluded.append({"expert_id": record.identity.expert_id, "reason": "trial expert; pass allow_trial=True to route to it"})
                continue
            if wanted and record.identity.capability not in wanted:
                excluded.append({"expert_id": record.identity.expert_id, "reason": f"capability {record.identity.capability!r} is not required for this task"})
                continue
            scored.append((record, self.scorer.score(record.metrics, expert_id=record.identity.expert_id)))

        # Stage 2 + 3: rank, then apply exploration credit as a separate term.
        for record, score in scored:
            bonus = self.exploration_bonus(record) if exploring else 0.0
            candidates.append(
                RouteCandidate(
                    expert_id=record.identity.expert_id,
                    capability=record.identity.capability,
                    status=record.identity.status.value,
                    utility=score.utility,
                    exploration_bonus=bonus,
                    score=score.utility + bonus,
                    score_components=dict(score.components),
                    trial=record.identity.status is ExpertLifecycleState.TRIAL,
                )
            )
        # Deterministic ordering: score desc, then id. Ties broken by id so two
        # identical scores never produce order-dependent results.
        candidates.sort(key=lambda c: (-c.score, c.expert_id))

        # Stage 4: admit. The admitted entries are rewritten in place in
        # `candidates` as well as collected into `admitted`, so `considered` is a
        # single consistent view of every scored candidate. Leaving the admitted
        # rows in `considered` stamped `admitted=False` would make the two lists
        # disagree about the same expert, and a reader checking `considered` for
        # the exclusion reason would find an admitted expert with no reason.
        admitted: list[RouteCandidate] = []
        for index, candidate in enumerate(candidates):
            if index < bound:
                winner = RouteCandidate(
                    expert_id=candidate.expert_id,
                    capability=candidate.capability,
                    status=candidate.status,
                    utility=candidate.utility,
                    exploration_bonus=candidate.exploration_bonus,
                    score=candidate.score,
                    score_components=candidate.score_components,
                    trial=candidate.trial,
                    admitted=True,
                )
                candidates[index] = winner
                admitted.append(winner)
            else:
                candidates[index] = RouteCandidate(
                    expert_id=candidate.expert_id,
                    capability=candidate.capability,
                    status=candidate.status,
                    utility=candidate.utility,
                    exploration_bonus=candidate.exploration_bonus,
                    score=candidate.score,
                    score_components=candidate.score_components,
                    trial=candidate.trial,
                    admitted=False,
                    excluded_reason=f"ranked #{index + 1}; only the top {bound} are admitted",
                )

        return RouteDecision(
            admitted=admitted,
            considered=candidates,
            excluded=excluded,
            exploring=bool(exploring),
            scorer=getattr(self.scorer, "name", type(self.scorer).__name__),
            exploration_bonus_cap=self.exploration_bonus_cap,
        )

    # -- process-wide access ------------------------------------------------

    _POOL_LOCK = threading.Lock()
    _pool: CapabilityRouter | None = None

    @classmethod
    def shared(cls) -> CapabilityRouter:
        """A router built from the current configuration.

        Rebuilt on each call rather than cached forever, because the exploration
        cap and the limit are hot-reloadable config values and a cached router
        would silently ignore an operator's edit.
        """
        from alpha.intelligence.config import intelligence_config

        config = intelligence_config()
        with cls._POOL_LOCK:
            cls._pool = CapabilityRouter(
                exploration_bonus_cap=config.experts.exploration_bonus,
                limit=min(3, config.experts.max_experts),
            )
            return cls._pool
