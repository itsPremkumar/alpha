"""Regularizer **S4** — Lasso/L1 structural pruning targets ``B_t``.

RRSI's structural regularizer is the L1 penalty on the *set of practiced
components*: a component whose best recent gain is non-positive has a zero
coefficient under the penalty, so the search reports it as a deletion target
and the proposer is instructed to remove the machinery it carries.

    ``B_t = { ℓ ∈ T_t : g_t(ℓ) ≤ 0 }``,  ``g_t(ℓ) = max{ΔS_i : ℓ_i = ℓ, t − t_i ≤ n_prune}``

with ``T_t`` the components that have at least one recorded edit and — the
part that decides half the outcomes — **``max ∅ = −∞``**.

Binding rules:

* **Never-exercised components are never targets.** They are not in ``T_t``.
  This is the opposite decision from :func:`alpha.rsi.rrsi.exploration
  .plan_exploration`, which names *only* untried components; conflating the
  two would prune exactly what exploration was reserving capacity for.
* **Exercised-but-unmeasured components ARE targets, and are flagged.** Under
  ``max ∅ = −∞`` a mechanism that has produced no measured gain in the window
  has not earned its place — the paper's own framing. The report still splits
  those into :attr:`PruningReport.unmeasured`, because "measured as no gain"
  and "never produced a number" are different facts about a mechanism, and a
  reader deciding whether to trust a target needs to know which one it is.
* **An unreadable ledger reports ``status="unavailable"``**, never an empty
  target list. An empty list says "nothing needs pruning", which is a
  conclusion; unavailable says "could not look".
* **Pruning is advice, not an action.** :func:`pruning_targets` mutates
  nothing: it reports targets and the measured reason for each, and whether a
  target is actually dropped stays the caller's decision.
* **Output is bounded by ``|K| = 9``** regardless of ledger size, and each
  target carries its own reason so the report can be audited without
  re-running the query.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from alpha.rsi.rrsi.components import COMPONENTS
from alpha.rsi.rrsi.config import RrsiParams
from alpha.rsi.rrsi.history import RrsiHistory

logger = logging.getLogger(__name__)

__all__ = ["PruningReport", "pruning_targets"]


@dataclass(frozen=True)
class PruningReport:
    """``B_t`` plus the measured reason behind every membership decision.

    ``status`` is ``"ok"`` or ``"unavailable"``. ``reasons`` maps each target
    to a string carrying the actual best gain (or an explicit statement that
    none was measured) and the record counts it rests on.

    ``unmeasured`` is the subset of ``targets`` whose membership comes from
    *absence* — zero measured ``ΔS`` records inside the window — rather than
    from a measured non-positive number. Both are targets under
    ``max ∅ = −∞``; they are not the same evidence and the report keeps them
    distinguishable.

    ``untouched`` are components never exercised: neither targets nor
    exploration evidence, reported so a reader can see the whole of ``K``.
    """

    status: str
    targets: tuple[str, ...]
    reasons: dict[str, str]
    unmeasured: tuple[str, ...]
    untouched: tuple[str, ...]
    window: int
    considered: tuple[str, ...]
    reason: str

    @property
    def available(self) -> bool:
        """Whether the ledger could be read well enough to decide anything."""
        return self.status == "ok"

    @property
    def productive(self) -> tuple[str, ...]:
        """Exercised components that earned a strictly positive measured gain in-window."""
        return tuple(name for name in self.considered if name not in self.targets)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "targets": list(self.targets),
            "reasons": dict(self.reasons),
            "unmeasured": list(self.unmeasured),
            "productive": list(self.productive),
            "untouched": list(self.untouched),
            "window": self.window,
            "considered": list(self.considered),
            "reason": self.reason,
        }


def pruning_targets(history: RrsiHistory, params: RrsiParams | None = None) -> PruningReport:
    """Compute ``B_t`` from the credit-assignment ledger.

    A component is a target exactly when it has **no strictly positive
    measured ``ΔS``** inside the pruning window. That single predicate covers
    both the measured-and-failed case and the never-measured case the paper
    folds in with ``max ∅ = −∞``; the report's ``unmeasured`` field is what
    keeps the two visible to a reader afterwards.
    """
    params = (params or RrsiParams()).validate()
    window = params.prune_window
    exercised = history.exercised_components()
    if exercised is None:
        report = PruningReport(
            status="unavailable",
            targets=(),
            reasons={},
            unmeasured=(),
            untouched=(),
            window=window,
            considered=(),
            reason=f"structural pruning not evaluated: the credit-assignment ledger could not be read ({history.load_error}). An unreadable ledger yields no target list — empty would read as 'nothing to prune'.",
        )
        logger.warning("RRSI structural pruning unavailable: %s", report.reason)
        return report

    targets: list[str] = []
    reasons: dict[str, str] = {}
    unmeasured: list[str] = []
    considered: list[str] = []
    for component in COMPONENTS:
        if component not in exercised:
            continue
        considered.append(component)
        stat = history.gain_window(component, window=window)
        if stat is None:
            # Ledger became unreadable mid-scan — unreachable while
            # `readable` was checked first, kept so the None contract
            # cannot silently invert into "no gain".
            continue
        if stat.positive:
            continue
        targets.append(component)
        if stat.measured == 0:
            unmeasured.append(component)
            basis = (
                f"no measured ΔS among {stat.records} record(s) inside the {window}-round window "
                f"(rounds {stat.floor_round}..{stat.top_round}); under g_t(ℓ) = max ∅ = −∞ this satisfies g_t(ℓ) ≤ 0. "
                "The target rests on absence of measurement, not on a measured non-positive gain."
            )
        else:
            basis = f"best measured ΔS over the last {window} round(s) is {stat.best:+.6g} from {stat.measured} of {stat.records} record(s); g_t(ℓ) ≤ 0, so the L1 penalty assigns this component a zero coefficient."
        reasons[component] = basis

    untouched = tuple(name for name in COMPONENTS if name not in exercised)
    summary = (
        f"evaluated {len(considered)} exercised component(s) of |K| = {len(COMPONENTS)} over a {window}-round window: "
        f"{len(targets)} target(s) ({len(unmeasured)} resting on zero measured records), "
        f"{len(considered) - len(targets)} productive, {len(untouched)} never exercised."
    )
    if unmeasured:
        summary += f" Targets flagged unmeasured: {', '.join(unmeasured)} — they have not failed, they have not been measured."
    return PruningReport(
        status="ok",
        targets=tuple(targets),
        reasons=reasons,
        unmeasured=tuple(unmeasured),
        untouched=untouched,
        window=window,
        considered=tuple(considered),
        reason=summary,
    )
