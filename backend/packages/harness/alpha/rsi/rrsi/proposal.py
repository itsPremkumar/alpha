"""Algorithm 1 — RRSI's proposal half: ``F_t, L_t, b_t, σ_t, U_t, B_t`` → one instruction.

The proposal side does not decide anything. It computes four bounded
quantities for round ``t`` and renders them into a single instruction the
proposer is bound by:

1. ``b_t`` — the annealed edit-cardinality budget (P1, :mod:`alpha.rsi.rrsi.budget`),
2. ``σ_t`` + the reserved exploratory slots (P3, :mod:`alpha.rsi.rrsi.exploration`),
3. ``B_t`` — the L1 structural-pruning targets (S4, :mod:`alpha.rsi.rrsi.pruning`),
4. ``L_t`` — the credit-assignment ledger disclosure (P2, :mod:`alpha.rsi.rrsi.history`).

Binding rules:

* **A plan is a constraint and a disclosure, never a result.** Nothing in a
  :class:`ProposalPlan` is a score, a confidence or an approval, and
  :meth:`ProposalPlan.to_dict` says so explicitly in its ``kind`` field. The
  instruction it renders tells the proposer what it may do; it does not tell
  anyone what happened.
* **Unavailable state is rendered as unavailable.** An unreadable ledger
  produces a plan that says the ledger could not be read and omits the
  credit-assignment section — it does not render an empty ledger as "no prior
  work", which would invite the proposer to re-test a falsified hypothesis.
* **The instruction is bounded.** Every section caps its output, so a 10 000
  record ledger cannot turn one round's instruction into an unbounded prompt.
* **Composition is the caller's.** :func:`build_proposal_plan` is pure: it
  reads what it is handed and writes nothing. Persistence stays with
  :mod:`alpha.rsi.rrsi.store`.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.rsi.rrsi.budget import BudgetOutcome, apply_edit_budget, edit_budget
from alpha.rsi.rrsi.components import STRUCTURAL_COMPONENTS, component_for
from alpha.rsi.rrsi.config import RrsiParams
from alpha.rsi.rrsi.exploration import ExplorationPlan, StallSignal, detect_stall, plan_exploration
from alpha.rsi.rrsi.history import RrsiHistory
from alpha.rsi.rrsi.pruning import PruningReport, pruning_targets

__all__ = ["MAX_INSTRUCTION_CHARS", "MAX_REASONS", "ProposalPlan", "build_proposal_plan"]

#: Hard caps so one round's instruction cannot grow without bound regardless
#: of ledger size. Truncation is always announced in the rendered text.
MAX_REASONS = 5
MAX_INSTRUCTION_CHARS = 4000


@dataclass(frozen=True)
class ProposalPlan:
    """Everything Algorithm 1 computes for round ``t``, plus its rendered instruction.

    ``kind`` is a fixed label (``"rrsi_proposal_plan"``) so a consumer that
    receives a serialized plan can tell it apart from an evaluation result
    without inspecting fields. ``usable`` is ``False`` when any section could
    not be computed — a plan that silently dropped its pruning section would
    be a constraint that looked complete.
    """

    kind: str
    round_index: int
    horizon: int
    params: RrsiParams
    budget: BudgetOutcome
    stall: StallSignal
    exploration: ExplorationPlan
    pruning: PruningReport
    history_available: bool
    history_error: str | None
    credit: dict[str, Any]
    instruction: str
    created_at: float = field(default_factory=time.time)

    @property
    def usable(self) -> bool:
        """Whether every section was computed (a degraded ledger makes a partial plan)."""
        return self.history_available and self.pruning.available and self.exploration.status != "unavailable"

    @property
    def constraints(self) -> dict[str, Any]:
        """The machine-readable constraint block (no measurements, no verdicts)."""
        return {
            "edit_budget": self.budget.budget,
            "edit_budget_saturated": self.budget.saturated,
            "exploration_status": self.exploration.status,
            "exploration_slots": self.exploration.slots,
            "exploration_targets": list(self.exploration.reserved_components),
            "pruning_targets": list(self.pruning.targets),
            "pruning_status": self.pruning.status,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "round_index": self.round_index,
            "horizon": self.horizon,
            "params": self.params.to_dict(),
            "usable": self.usable,
            "constraints": self.constraints,
            "budget": self.budget.to_dict(),
            "stall": self.stall.to_dict(),
            "exploration": self.exploration.to_dict(),
            "pruning": self.pruning.to_dict(),
            "history_available": self.history_available,
            "history_error": self.history_error,
            "credit": dict(self.credit),
            "instruction_length": len(self.instruction),
        }

    def apply(self, edits: Sequence[str]) -> BudgetOutcome:
        """Cut a proposed edit list to this round's budget (P1, disclosed)."""
        return apply_edit_budget(edits, self.round_index, self.params)


def build_proposal_plan(
    round_index: int,
    *,
    round_scores: Sequence[float | None],
    history: RrsiHistory | None = None,
    params: RrsiParams | None = None,
    surface: str = "",
    target: str = "",
) -> ProposalPlan:
    """Compute Algorithm 1's four quantities for round ``round_index``.

    ``round_scores`` is the best-score-per-round series the stall indicator
    reads. ``None`` entries are tolerated and **dropped**: a round that
    completed without a measurement supplies no point on that series, and
    treating an absence as ``0.0`` would invent a catastrophic regression
    where nothing was measured. The window is therefore counted across the
    measured rounds, which is disclosed through
    :attr:`~alpha.rsi.rrsi.exploration.StallSignal.observed`.

    ``surface``/``target`` optionally attribute the round's intended change so
    the instruction can name the component it is for; they are advisory and
    never change any of the four computed quantities.
    """
    params = (params or RrsiParams()).validate()
    if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
        raise ValueError(f"round_index must be an integer >= 0; got {round_index!r}.")

    ledger = history if history is not None else RrsiHistory()
    observed = tuple(float(score) for score in round_scores if isinstance(score, (int, float)) and not isinstance(score, bool))

    saturated = round_index >= params.rounds
    budget_value = edit_budget(round_index, params)
    budget_reason = (
        f"edit budget b_{round_index} = {budget_value} is the annealed cap for this round "
        f"(b_min = {params.budget_min}, b_max = {params.budget_max}, T = {params.rounds}). "
        "It bounds independently attributable edits in the *update*, not parameters in the model."
    )
    if saturated:
        budget_reason += f" Round {round_index} is at/past the horizon T = {params.rounds}, so the schedule is saturated at b_min and may only tune."
    budget = BudgetOutcome(budget=budget_value, round_index=round_index, kept=(), dropped=(), saturated=saturated, reason=budget_reason)

    stall = detect_stall(observed, params)
    exploration = plan_exploration(stall, ledger, params)
    pruning = pruning_targets(ledger, params)
    credit = ledger.to_disclosure()

    history_available = ledger.readable
    instruction = _render_instruction(
        round_index=round_index,
        params=params,
        budget=budget,
        stall=stall,
        exploration=exploration,
        pruning=pruning,
        history=ledger,
        credit=credit,
        history_available=history_available,
        surface=surface,
        target=target,
    )

    return ProposalPlan(
        kind="rrsi_proposal_plan",
        round_index=round_index,
        horizon=params.rounds,
        params=params,
        budget=budget,
        stall=stall,
        exploration=exploration,
        pruning=pruning,
        history_available=history_available,
        history_error=ledger.load_error,
        credit=credit,
        instruction=instruction,
    )


def _truncate(items: Sequence[str], limit: int) -> tuple[str, ...]:
    if len(items) <= limit:
        return tuple(items)
    return (*items[:limit], f"... and {len(items) - limit} more")


def _render_instruction(
    *,
    round_index: int,
    params: RrsiParams,
    budget: BudgetOutcome,
    stall: StallSignal,
    exploration: ExplorationPlan,
    pruning: PruningReport,
    history: RrsiHistory,
    credit: dict[str, Any],
    history_available: bool,
    surface: str,
    target: str,
) -> str:
    """Render the four quantities into one bounded, non-measurement instruction."""
    lines: list[str] = [
        f"RRSI regularized proposal constraints — round {round_index} of T = {params.rounds}.",
        "These are proposal-side constraints and disclosures. Nothing below is a score, a result, or an approval.",
        "",
        f"1. Edit budget (P1): at most {budget.budget} independently attributable edit(s) this round.",
        f"   {budget.reason}",
    ]

    if target:
        tag = component_for(surface or "code", target=target) if surface else None
        attributed = f" Intended target {target!r}" + (f" maps to component {tag.component!r} ({tag.basis})." if tag else ".")
        lines.append(f"   {attributed.strip()}")

    lines += [
        "",
        f"2. Structured exploration (P3): {stall.reason}",
        f"   Status: {exploration.status}; reserved slots: {exploration.slots}.",
        f"   {exploration.reason}",
    ]
    if exploration.reserved_components:
        lines.append(f"   While stalled, direct reserved slot(s) at untried component(s): {', '.join(exploration.reserved_components)}.")

    lines += ["", "3. Structural pruning (S4): " + pruning.reason]
    if pruning.targets:
        lines.append("   Report the following as deletion targets and remove machinery that no longer earns its place:")
        for name in _truncate(pruning.targets, MAX_REASONS):
            if name in pruning.reasons:
                lines.append(f"   - {name}: {pruning.reasons[name]}")
            else:
                lines.append(f"   - {name}")
        if len(pruning.targets) > MAX_REASONS:
            lines.append(f"   ({len(pruning.targets) - MAX_REASONS} further target(s) truncated from this instruction.)")
    elif pruning.status == "ok":
        lines.append("   No component currently qualifies as an unproductive deletion target.")
    else:
        lines.append("   Pruning could not be evaluated; do not assume there is nothing to prune.")

    lines += ["", "4. Credit assignment (P2):"]
    if not history_available:
        lines.append(f"   The credit-assignment ledger could not be read ({history.load_error}).")
        lines.append("   Do not treat that as an empty history: rejected hypotheses remain negative evidence you cannot currently see, so do not re-test a falsified mechanism on the assumption it was never tried.")
    else:
        exercised = credit.get("exercised_components")
        lines.append(f"   Ledger records: {credit.get('ledger', {}).get('count')} across components: {', '.join(exercised) if exercised else 'none'}.")
        unmeasured = credit.get("attempts_without_measured_delta")
        if unmeasured:
            lines.append(f"   {unmeasured} attempt(s) carry no measured ΔS — they are not evidence of no gain and must not be read as such.")
        if pruning.unmeasured:
            lines.append(f"   Components with no measured gain in-window (target rests on absence, not on a measured failure): {', '.join(pruning.unmeasured)}.")
        structural = ", ".join(sorted(STRUCTURAL_COMPONENTS))
        lines.append(f"   Structural components ({structural}) earn novelty credit when newly exercised.")
        lines.append("   Rejected mechanisms remain negative evidence; successful mechanisms retain explicit credit.")

    lines += [
        "",
        f"Overall plan usable: {pruning.available and history_available and exploration.status != 'unavailable'}.",
        "A section above marked unavailable is a gap in the constraint, not permission to ignore it.",
    ]

    rendered = "\n".join(lines)
    if len(rendered) > MAX_INSTRUCTION_CHARS:
        rendered = rendered[: MAX_INSTRUCTION_CHARS - 40] + "\n[... instruction truncated]"
    return rendered
