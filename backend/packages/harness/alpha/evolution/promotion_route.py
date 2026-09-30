"""Evolution-surface routing for RSI promotion decisions (plan WP-C2/C2c wiring).

This is the C2c wiring file under ``alpha/evolution/``: it routes promotion
decisions through the EXISTING evolution dispatch surface — the same real
``EvolutionEngine.gate(candidate_id, baseline, *, human_approved,
autonomous_mode) -> (promoted, reason)`` API that the landed
``backend/app/gateway/routers/evolution.py`` ``GateRequest`` handler invokes
(verified against main, never invented). This wiring adds **no** HTTP router,
**no** new endpoint, and **no** auth change (plan §5 scope fences); the router
module itself is pinned in tests, not edited.

- :func:`route_evolution_gate` — one-to-one with the landed router handler's
  call, except ``autonomous_mode`` is pinned ``False`` here: auto-promote
  stays OFF for every risk class (plan §5.4) and omission/False never grants
  autonomy. The engine's REAL ``(promoted, reason)`` tuple is returned
  verbatim — this wrapper invents nothing and softens nothing.
- :func:`decide_promotion` — import-ready evolution-surface entry point that
  composes ``alpha.rsi.promotion.decide()`` (the WP-C2/C2c gate composition:
  bundle integrity, measured-only evidence standard, holdout gate, human
  review, lineage) before any routing can happen. Its consumer (a later HITL
  approve re-run / additive endpoint) lands separately, per the repo's
  import-ready seam precedent — nothing is faked as wired today.

Import discipline (no cycle, no eager weight): ``alpha.rsi.promotion`` imports
:func:`route_evolution_gate` at module scope, and this module imports
``alpha.rsi.promotion`` only inside :func:`decide_promotion`'s body — so
``import alpha.evolution`` stays exactly as light as it is on main.

The evidence gate (``alpha.evolution.evidence``) wiring
--------------------------------------------------------
That package documented itself as "not wired into any promotion path yet" and
named this module's neighbourhood as where it belongs. :func:`route_evolution_gate`
is now the seam that consults it, and the shape of the wiring is deliberately
narrow:

* **It can only block.** The evidence verdict is an additional conjunct on
  ``engine.gate(...)``: a non-``accepted`` verdict turns ``promoted`` into
  ``False``. Nothing here can turn a ``False`` into a ``True``, so the evidence
  gate cannot grant a promotion the engine refused.
* **It is default-OFF and behaviour-preserving.** ``EvolutionEvidenceConfig.enabled``
  defaults to ``False`` and ``AppConfig`` never sets it, so the gate does not run
  and the engine's tuple travels through untouched for every install that has not
  opted in. Flipping the key is a real, deliberate operator change.
* **Enabling it fails CLOSED, and the honest reason is reported.** With the flag
  on and no measurement source injected, ``evaluate_proposal`` returns
  ``insufficient_evidence`` (the reproducibility and rollback gates report
  ``unavailable`` without probes). That blocks promotion. This is correct and it
  is not a soft accept, but it means the flag means *"promotion requires measured
  evidence"*, and no deployment can currently supply measurements through config
  alone. The returned reason says so rather than reading as a mysterious
  rejection.
* **A gate that cannot run blocks.** An exception inside ``evaluate_proposal`` is
  caught and becomes a block, because a gate that cannot establish a verdict has
  not granted one.

This is **not** a second lifecycle owner: it gates a *change promotion*, never a
run, and touches no run state. ``RunManager`` remains the sole run lifecycle
owner and ``SafeRunRecoveryService`` the only safe-continuation authority.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from alpha.evolution.engine import get_evolution_engine

if TYPE_CHECKING:
    from alpha.evolution.evidence.models import Verdict
    from alpha.rsi.promotion import PromotionDecision

logger = logging.getLogger(__name__)

__all__ = ["EvidenceGateOutcome", "decide_promotion", "evidence_verdict_for", "route_evolution_gate"]


@dataclass(frozen=True, slots=True)
class EvidenceGateOutcome:
    """What the evidence gate said about one promotion, and whether it blocked.

    ``ran`` keeps "not evaluated" a third state, distinct from both "ran and
    accepted" and "ran and blocked". Collapsing unevaluated into either answer is
    the over-claim this record exists to prevent: a reader must always be able to
    tell whether a promotion was evidence-checked at all.
    """

    ran: bool
    blocking: bool
    status: str | None
    reasons: tuple[str, ...]
    verdict: Verdict | None = None

    def reason_text(self) -> str:
        """A single operator-facing line, or ``""`` when the gate did not block."""
        if not self.blocking:
            return ""
        detail = "; ".join(self.reasons)
        return f"evidence gate: {self.status}" + (f" ({detail})" if detail else "")


def _evidence_enabled() -> bool | None:
    """Whether the operator enabled the gate, or ``None`` when it cannot be read.

    ``AppConfig`` is resolved inside the function so importing this module stays
    free of the config package (the import-hygiene rule the module docstring
    already states). Reading ``enabled`` is itself guarded: a config object that
    raises on attribute access is a broken config, and it must not propagate out
    of a promotion route.

    ``None`` means "unknown, so the gate did not run" and is deliberately not
    the same answer as ``False``. A config the process cannot read is not
    evidence about the proposal, so it does not block; but it is also not the
    operator saying no, so it is never reported as a clean pass -- the outcome
    keeps ``ran=False``, which is what a caller branches on.
    """
    try:
        from alpha.config import get_app_config

        return bool(get_app_config().evolution_evidence.enabled)
    except Exception:  # noqa: BLE001 - an unreadable config is not a verdict
        logger.warning("evolution evidence config could not be read; the evidence gate did not run", exc_info=True)
        return None


def evidence_verdict_for(candidate_id: str, baseline: Mapping[str, Any] | None = None) -> EvidenceGateOutcome:
    """Run the evidence gate for one promotion candidate.

    Never raises. A gate that fails to run reports ``ran=True, blocking=True``
    with the real exception as its reason, because a gate that could not
    establish a verdict has not granted one.
    """
    if not _evidence_enabled():
        return EvidenceGateOutcome(ran=False, blocking=False, status=None, reasons=())

    # Function-local imports: `alpha.evolution.evidence` is a large package
    # behind a lazy facade, and this module must stay light because
    # `alpha.rsi.promotion` imports it at module scope.
    from alpha.evolution.evidence import Proposal, evaluate_proposal
    from alpha.evolution.evidence.config import EvolutionEvidenceConfig

    payload = dict(baseline or {})
    try:
        config = EvolutionEvidenceConfig(enabled=True)
        proposal = Proposal(
            id=str(candidate_id),
            kind=str(payload.get("kind") or "evolution_candidate"),
            author=str(payload.get("author") or "alpha.evolution"),
            created_at=float(payload.get("created_at") or 0.0),
            # A `Proposal` with no intent is refused by the model, and an empty
            # string is not a disclosure -- it is a claim the gate would have to
            # invent. When the caller supplies none, the *absence* is stated
            # rather than filled in, so a candidate that never declared what it
            # was for stays visibly that way to whoever reads the verdict.
            declared_intent=str(payload.get("declared_intent") or "").strip() or f"no declared intent was supplied for promotion candidate {candidate_id}",
            touched_paths=tuple(str(item) for item in payload.get("touched_paths") or ()),
        )
        evaluation = evaluate_proposal(proposal, config=config, repo_root=payload.get("repo_root"))
    except Exception as exc:  # noqa: BLE001 - an unrunnable gate blocks; it does not wave through
        logger.warning("evolution evidence gate could not evaluate %s: %s", candidate_id, exc, exc_info=True)
        return EvidenceGateOutcome(ran=True, blocking=True, status="gate_error", reasons=(f"{type(exc).__name__}: {exc}",))

    verdict = evaluation.verdict
    return EvidenceGateOutcome(
        ran=True,
        blocking=bool(verdict.blocking),
        status=str(verdict.status),
        reasons=tuple(verdict.reasons),
        verdict=verdict,
    )


def route_evolution_gate(candidate_id: str, baseline: Mapping[str, Any] | None = None, *, human_approved: bool) -> tuple[bool, str]:
    """Route one promotion decision through the landed evolution gate — the real dispatch API, verbatim result.

    Exactly the call shape the ``GateRequest`` HTTP handler makes
    (``get_evolution_engine().gate(candidate_id, baseline, human_approved=...,
    autonomous_mode=...)``), with ``autonomous_mode`` pinned ``False`` — the
    plan §5.4 guardrail that auto-promotion stays OFF everywhere; callers of
    this wiring can never opt into autonomy. The returned tuple is the
    engine's own ``(promoted, reason)`` with no post-processing: a real
    ``"missing candidate or benchmark"`` / ``"not strictly better than
    baseline"`` / ``"benchmark regressions vs baseline"`` travels unchanged.

    The one post-processing that *is* applied is the evidence gate above, and it
    is strictly subtractive: it can only turn ``promoted`` into ``False``. When
    the gate is disabled (the default) or did not run, the engine's tuple is
    returned byte-for-byte.
    """
    engine = get_evolution_engine()
    promoted, engine_reason = engine.gate(candidate_id, dict(baseline or {}), human_approved=human_approved, autonomous_mode=False)

    outcome = evidence_verdict_for(candidate_id, baseline)
    if not outcome.blocking:
        return promoted, engine_reason
    # The engine is still consulted and its own finding is kept in the reason,
    # so the two independent verdicts stay separable to whoever reads this.
    return False, f"{engine_reason} + {outcome.reason_text()}"


def decide_promotion(candidate_id: str, *, baseline: Mapping[str, Any] | None = None) -> PromotionDecision:
    """Evolution-surface entry point: compose the WP-C2/C2c gates, then route.

    Delegates to ``alpha.rsi.promotion.decide()``, which runs every hard gate
    (lineage, bundle integrity, measured-only evidence standard, holdout,
    human review) against the real landed modules and only routes a fully
    green composition through :func:`route_evolution_gate`. Any missing /
    failed / unverified / corrupt component yields ``promoted=False`` with the
    real reason before the engine is ever invoked.
    """
    # Function-local import: keeps `import alpha.evolution` as light as on
    # main and cannot cycle (alpha.rsi.promotion reaches this module's
    # route_evolution_gate only at call time, after both modules are loaded).
    from alpha.rsi.promotion import decide

    return decide(candidate_id, baseline=baseline)
