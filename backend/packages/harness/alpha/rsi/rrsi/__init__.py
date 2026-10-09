"""RRSI — Regularized Recursive Self-Improvement of Alpha's agent harness.

An implementation of the regularizers from *RRSI: Regularized Recursive
Self-Improvement of Agent Harnesses* (arXiv:2609.24972), layered onto the
existing :mod:`alpha.rsi` cycle rather than replacing it. The existing package
still owns hypothesis generation, evaluation, holdout and promotion; this
subpackage owns the **seven regularizers** that decide what may be proposed
and what may be admitted:

================  ==========================================================
Regularizer       Module
================  ==========================================================
P1 edit budget    :mod:`alpha.rsi.rrsi.budget`
P2 credit history :mod:`alpha.rsi.rrsi.history`
P3 exploration    :mod:`alpha.rsi.rrsi.exploration`
S1 leakage critic :mod:`alpha.rsi.rrsi.critic`
S2 noise floor    :mod:`alpha.rsi.rrsi.acceptance`
S3 cost rules     :mod:`alpha.rsi.rrsi.acceptance`
S4 pruning        :mod:`alpha.rsi.rrsi.pruning`
S5 domain guard   :mod:`alpha.rsi.rrsi.acceptance`
================  ==========================================================

Supporting modules: :mod:`alpha.rsi.rrsi.config` (hyperparameters),
:mod:`alpha.rsi.rrsi.components` (the nine-component vocabulary ``K``),
:mod:`alpha.rsi.rrsi.proposal` (Algorithm 1),
:mod:`alpha.rsi.rrsi.selection` (Algorithm 2 and the promotion conjunct),
:mod:`alpha.rsi.rrsi.store` (durable round state).

**What this package does not claim.** It computes constraints, disclosures
and verdicts over numbers the caller supplies. It never runs a benchmark,
never invents a measurement, and never marks a completed run as verified. The
paper's own caveats apply here too: its regularizer hyperparameters were tuned
on the evolve set they police, its ablation is group-level, and it reports no
matched-budget test-time-scaling comparison. Treat the presets in
:mod:`alpha.rsi.rrsi.config` as starting points, not as calibrated guarantees.

The normative contract lives in ``alpha/rsi/rrsi/AGENTS.md``.
"""

from alpha.rsi.rrsi.acceptance import (
    Admissibility,
    Check,
    Measurement,
    admit,
    attribution_guard,
    cost_rule,
    domain_guard_default,
    floor_rule,
    relative_cost_change,
    score_change,
    selected_branch,
    within_band_rule,
    within_band_utility,
)
from alpha.rsi.rrsi.budget import BudgetOutcome, apply_edit_budget, edit_budget
from alpha.rsi.rrsi.components import (
    COMPONENTS,
    STRUCTURAL_COMPONENTS,
    SURFACE_COMPONENTS,
    ComponentTag,
    component_for,
    components_for,
    novelty,
    novelty_breakdown,
    validate_component,
)
from alpha.rsi.rrsi.config import DOMAIN_PRESETS, PRESET_SOURCES, RrsiParams, preset, preset_names
from alpha.rsi.rrsi.critic import BENCHMARK_MARKERS, ScreenVerdict, screen_candidate
from alpha.rsi.rrsi.exploration import ExplorationPlan, StallSignal, detect_stall, plan_exploration
from alpha.rsi.rrsi.history import GainWindow, HistoryEntry, RrsiHistory, history_path
from alpha.rsi.rrsi.proposal import ProposalPlan, build_proposal_plan
from alpha.rsi.rrsi.pruning import PruningReport, pruning_targets
from alpha.rsi.rrsi.selection import RoundDecision, RrsiGateOutcome, ScoredCandidate, election_gate_for, select_round
from alpha.rsi.rrsi.store import DurableWrite, RoundState, RrsiRoundStore, round_store_path

__all__ = [
    # config
    "DOMAIN_PRESETS",
    "PRESET_SOURCES",
    "RrsiParams",
    "preset",
    "preset_names",
    # components
    "COMPONENTS",
    "STRUCTURAL_COMPONENTS",
    "SURFACE_COMPONENTS",
    "ComponentTag",
    "component_for",
    "components_for",
    "novelty",
    "novelty_breakdown",
    "validate_component",
    # P1 budget
    "BudgetOutcome",
    "apply_edit_budget",
    "edit_budget",
    # P2 history
    "GainWindow",
    "HistoryEntry",
    "RrsiHistory",
    "history_path",
    # P3 exploration
    "ExplorationPlan",
    "StallSignal",
    "detect_stall",
    "plan_exploration",
    # S4 pruning
    "PruningReport",
    "pruning_targets",
    # S1 critic
    "BENCHMARK_MARKERS",
    "ScreenVerdict",
    "screen_candidate",
    # S2/S3/S5 acceptance
    "Admissibility",
    "Check",
    "Measurement",
    "admit",
    "attribution_guard",
    "cost_rule",
    "domain_guard_default",
    "floor_rule",
    "relative_cost_change",
    "score_change",
    "selected_branch",
    "within_band_rule",
    "within_band_utility",
    # Algorithms 1 and 2
    "ProposalPlan",
    "build_proposal_plan",
    "RrsiGateOutcome",
    "RoundDecision",
    "ScoredCandidate",
    "election_gate_for",
    "select_round",
    # durable state
    "DurableWrite",
    "RoundState",
    "RrsiRoundStore",
    "round_store_path",
]
