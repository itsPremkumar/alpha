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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from alpha.evolution.engine import get_evolution_engine

if TYPE_CHECKING:
    from alpha.rsi.promotion import PromotionDecision

__all__ = ["decide_promotion", "route_evolution_gate"]


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
    """
    engine = get_evolution_engine()
    return engine.gate(candidate_id, dict(baseline or {}), human_approved=human_approved, autonomous_mode=False)


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
