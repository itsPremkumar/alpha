"""RRSI regularizer hyperparameters: values, validation and domain presets.

Source of the *idea* and of the reported settings:
*RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*
(arXiv:2609.24972) §3 (Algorithms 1–2), §6 Table 5 and Appendix C.3. This
module owns only the knobs; the rules that consume them live in
:mod:`alpha.rsi.rrsi.budget`, :mod:`alpha.rsi.rrsi.exploration`,
:mod:`alpha.rsi.rrsi.pruning`, :mod:`alpha.rsi.rrsi.acceptance` and
:mod:`alpha.rsi.rrsi.selection`.

Binding rules for this module:

* **Bounds are refused, never clamped.** :meth:`RrsiParams.validate` raises
  ``ValueError`` naming the field and the violated bound. A silent clamp would
  turn an operator typo into a silently different regularizer that still
  reports the typo's field name back as if it had been honoured (house rule:
  ``alpha.bots.model_config`` refuses rather than clamps).
* **Nothing here is a measurement.** Every field is an operator-chosen
  regularizer knob. No function in this module reports a score, a confidence,
  an improvement or a verdict, so none of them can be quoted as evidence.
* **Preset provenance is per-field, not per-preset.** :data:`PRESET_SOURCES`
  says which values the paper reports and which are local starting points, so a
  reader can tell "δ = 0.004 was calibrated by the authors" from "w_c = 1.0 was
  chosen here". :func:`preset` refuses an unknown name rather than silently
  falling back to defaults — a typo must never look like a choice.

Units follow the paper: scores ``Ŝ`` are fractions in ``[0,1]``; ``ΔC`` is the
*relative* change in policy tokens per trial, so ``δ`` and ``β₁`` share those
units.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

__all__ = [
    "DOMAIN_PRESETS",
    "PRESET_SOURCES",
    "RrsiParams",
    "preset",
    "preset_names",
]


@dataclass(frozen=True)
class RrsiParams:
    """One validated set of RRSI regularizer hyperparameters.

    Field ↔ paper symbol, in the order the two algorithms consume them:

    ``rounds`` ``T``
        the annealing horizon of the edit budget and the horizon the stall
        window is evaluated against (Alg. 1 step 2).
    ``trials_per_task`` ``k``
        independent trials per task per evaluation — the ``k`` in
        ``Evaluate(H', D_evolve, k)`` (Alg. 2 step 3). Recorded so a round
        discloses the trial count its scores came from; this module never
        runs trials.
    ``noise_delta`` ``δ``
        the empirically calibrated noise band. It appears **three** times and
        the three uses are not interchangeable: the acceptance floor
        (``Ŝ' ≥ S* − δ``), the branch selector between the cost rule and the
        within-band rule (``ΔS > δ``), and the stall threshold in
        :func:`~alpha.rsi.rrsi.exploration.detect_stall`.
    ``budget_min`` / ``budget_max`` ``b_min`` / ``b_max``
        the endpoints of the cosine-annealed edit budget (Alg. 1 step 2).
    ``stall_window`` ``w``
        the window of the stall indicator ``σ_t = 1[Ŝ_t − Ŝ_{t−w} ≤ δ]``.
    ``exploratory_slots`` ``m_draft``
        how many proposal slots are reserved for untried components while
        stalled (Alg. 1 step 6).
    ``prune_window`` ``n_prune``
        the recent-round window a component's best gain ``g_t(ℓ)`` is measured
        over before it becomes an L1 structural-pruning target (Alg. 1 step 7).
    ``cost_base`` ``β₀`` / ``cost_slope`` ``β₁``
        the gain-dependent complexity allowance ``ΔC ≤ β₀ + β₁·ΔS`` applied
        when ``ΔS > δ`` (Alg. 2 step 7).
    ``weight_score`` ``w_s`` / ``weight_cost`` ``w_c`` / ``weight_novelty`` ``w_n``
        the within-band utility ``w_s·ΔS − w_c·ΔC + w_n·ν > 0`` applied when
        ``ΔS ≤ δ`` (Alg. 2 step 9). **This is a per-candidate admissibility
        test, not a ranking score** — it is evaluated once per candidate and
        feeds the same conjunction as every other rule.
    """

    rounds: int = 20
    trials_per_task: int = 2
    noise_delta: float = 0.004
    budget_min: int = 1
    budget_max: int = 3
    stall_window: int = 3
    exploratory_slots: int = 1
    prune_window: int = 4
    cost_base: float = 0.10
    cost_slope: float = 35.4
    weight_score: float = 1.0
    weight_cost: float = 1.0
    weight_novelty: float = 1.0

    def validate(self) -> RrsiParams:
        """Return ``self`` when every bound holds; otherwise raise ``ValueError``.

        Refused, never clamped — the message always names both the field and
        the bound so the operator can see which of the two they meant.
        """
        _refuse(_is_int(self.rounds) and self.rounds >= 1, "rounds", "an integer >= 1", self.rounds)
        _refuse(_is_int(self.trials_per_task) and self.trials_per_task >= 1, "trials_per_task", "an integer >= 1", self.trials_per_task)
        _refuse(_is_number(self.noise_delta) and self.noise_delta >= 0, "noise_delta", "a number >= 0", self.noise_delta)
        _refuse(_is_int(self.budget_min) and self.budget_min >= 1, "budget_min", "an integer >= 1", self.budget_min)
        _refuse(_is_int(self.budget_max) and self.budget_max >= self.budget_min, "budget_max", f"an integer >= budget_min ({self.budget_min})", self.budget_max)
        _refuse(_is_int(self.stall_window) and self.stall_window >= 1, "stall_window", "an integer >= 1", self.stall_window)
        _refuse(_is_int(self.exploratory_slots) and self.exploratory_slots >= 0, "exploratory_slots", "an integer >= 0", self.exploratory_slots)
        _refuse(_is_int(self.prune_window) and self.prune_window >= 1, "prune_window", "an integer >= 1", self.prune_window)
        _refuse(_is_number(self.cost_base) and self.cost_base >= 0, "cost_base", "a number >= 0", self.cost_base)
        _refuse(_is_number(self.cost_slope) and self.cost_slope >= 0, "cost_slope", "a number >= 0", self.cost_slope)
        for name in ("weight_score", "weight_cost", "weight_novelty"):
            value = getattr(self, name)
            _refuse(_is_number(value) and value >= 0, name, "a number >= 0", value)
        _refuse(self.weight_score > 0 or self.weight_cost > 0 or self.weight_novelty > 0, "weight_*", "at least one within-band weight > 0", (self.weight_score, self.weight_cost, self.weight_novelty))
        return self

    def with_overrides(self, **overrides: Any) -> RrsiParams:
        """A validated copy with ``overrides`` applied (unknown fields are refused)."""
        unknown = sorted(set(overrides) - {field.name for field in fields(self)})
        if unknown:
            raise ValueError(f"unknown RRSI hyperparameter(s): {', '.join(unknown)}; known fields are {sorted(field.name for field in fields(self))}.")
        return replace(self, **overrides).validate()

    def to_dict(self) -> dict[str, Any]:
        """A plain mapping of every hyperparameter (disclosure only — not a measurement)."""
        return {field.name: getattr(self, field.name) for field in fields(self)}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _refuse(condition: bool, field: str, bound: str, value: Any) -> None:
    if not condition:
        raise ValueError(f"RRSI hyperparameter {field!r} must be {bound}; got {value!r}. Bounds are refused, never clamped.")


#: Domain presets — **the paper's Table 5 values, verbatim**, for all three
#: domains Alpha can be run as. Column order in the paper is
#: Coding / Agentic workspace / Engineering design.
DOMAIN_PRESETS: dict[str, RrsiParams] = {
    "coding": RrsiParams(
        rounds=20, trials_per_task=2, noise_delta=0.017, budget_min=1, budget_max=4, stall_window=3, exploratory_slots=1, prune_window=4, cost_base=0.10, cost_slope=44.5, weight_score=0.0, weight_cost=1.0, weight_novelty=1.0
    ),
    "workspace": RrsiParams(
        rounds=20, trials_per_task=2, noise_delta=0.004, budget_min=1, budget_max=3, stall_window=3, exploratory_slots=1, prune_window=4, cost_base=0.10, cost_slope=35.4, weight_score=1.0, weight_cost=1.0, weight_novelty=1.0
    ),
    "engineering": RrsiParams(
        rounds=40, trials_per_task=4, noise_delta=0.020, budget_min=1, budget_max=4, stall_window=3, exploratory_slots=1, prune_window=5, cost_base=0.15, cost_slope=24.4, weight_score=1.0, weight_cost=1.0, weight_novelty=1.0
    ),
}

#: Provenance, per field where the paper is silent. Asserted by
#: ``tests/test_rrsi_config.py`` so a preset can never be silently re-tuned
#: into a different claim.
PRESET_SOURCES: dict[str, str] = {
    "coding": (
        "T, k, δ, b_min, b_max, w, m_draft, n_prune, β₀, β₁ from arXiv:2609.24972 Table 5 (coding column); "
        "w_s = 0 reported for coding; w_c = 1.0 and w_n = 1.0 are local starting points "
        "(the paper names the weight family but does not report their values)"
    ),
    "workspace": ("T, k, δ, b_min, b_max, w, m_draft, n_prune, β₀, β₁ from arXiv:2609.24972 Table 5 (agentic-workspace column); w_s > 0 is reported but its value is not, so w_s = 1.0 with w_c = 1.0 and w_n = 1.0 are local starting points"),
    "engineering": (
        "T, k, δ, b_min, b_max, w, m_draft, n_prune, β₀, β₁ from arXiv:2609.24972 Table 5 (engineering-design column); w_s > 0 is reported but its value is not, so w_s = 1.0 with w_c = 1.0 and w_n = 1.0 are local starting points"
    ),
}


def preset_names() -> tuple[str, ...]:
    """The known preset names, in declaration order."""
    return tuple(DOMAIN_PRESETS)


def preset(name: str) -> RrsiParams:
    """Return the named preset, validated.

    An unknown name raises — falling back to defaults would make a typo
    indistinguishable from a deliberate choice of the default setting.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"RRSI preset name must be a non-empty string; got {name!r}.")
    found = DOMAIN_PRESETS.get(name)
    if found is None:
        raise ValueError(f"unknown RRSI preset {name!r}; known presets are {preset_names()}.")
    return found.validate()
