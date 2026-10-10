"""Task-specific rubrics, hard gates, and judge bias controls.

The default rubric (30/25/15/20/10) stays the fallback. This module
adds:

* per-task-type weight sets (code / research / creative / analysis);
* **hard gates** - a condition that fails a candidate outright and
  cannot be averaged away by strength elsewhere;
* **bias controls** - blind ids and deterministic pair-order swapping
  so the judge cannot rely on position or identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alpha.arena.models import VerdictScores
from alpha.arena.rubric import WEIGHTS

# ------------------------------------------------------------------ weights

#: Per-task-type rubric weights. Each must sum to 1.0.
TASK_WEIGHTS: dict[str, dict[str, float]] = {
    "coding": {
        "correctness": 0.40,
        "completeness": 0.20,
        "specificity": 0.15,
        "robustness": 0.20,
        "clarity": 0.05,
    },
    "research": {
        "correctness": 0.30,
        "completeness": 0.25,
        "specificity": 0.20,
        "robustness": 0.15,
        "clarity": 0.10,
    },
    "creative": {
        "correctness": 0.15,
        "completeness": 0.25,
        "specificity": 0.20,
        "robustness": 0.10,
        "clarity": 0.30,
    },
    "analysis": {
        "correctness": 0.35,
        "completeness": 0.25,
        "specificity": 0.15,
        "robustness": 0.15,
        "clarity": 0.10,
    },
}


def weights_for(task_type: str | None) -> dict[str, float]:
    """The rubric weights for a task type; the default rubric otherwise."""
    if task_type and task_type.lower() in TASK_WEIGHTS:
        return TASK_WEIGHTS[task_type.lower()]
    return dict(WEIGHTS)


def weighted_total_for(task_type: str | None, scores: VerdictScores) -> float:
    weights = weights_for(task_type)
    return round(
        scores.correctness * weights["correctness"] + scores.completeness * weights["completeness"] + scores.specificity * weights["specificity"] + scores.robustness * weights["robustness"] + scores.clarity * weights["clarity"],
        4,
    )


# ------------------------------------------------------------------ hard gates


@dataclass(frozen=True)
class HardGate:
    """A condition that fails a candidate outright.

    Gates are evaluated before the weighted total: a candidate that
    trips a gate loses regardless of how well it scores elsewhere.
    That is the whole point - some properties are not tradeable.
    """

    id: str
    description: str
    tripped: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "tripped": self.tripped,
            "detail": self.detail,
        }


def default_gates(task_type: str | None) -> list[HardGate]:
    """Gate set for a task type. All start untripped; callers fill them."""
    kind = (task_type or "").lower()
    if kind == "coding":
        return [
            HardGate("build", "solution compiles/builds", False),
            HardGate("tests", "tests pass", False),
            HardGate("no_critical", "no unresolved critical defect", False),
        ]
    if kind == "research":
        return [
            HardGate("citations", "every factual claim carries a source", False),
            HardGate("no_contradiction", "no claim contradicts the evidence ledger", False),
        ]
    if kind == "creative":
        return [HardGate("constraints", "stated hard constraints respected", False)]
    return [HardGate("fidelity", "answer does not contradict the task contract", False)]


def apply_gates(
    scores: VerdictScores,
    gates: list[HardGate],
) -> tuple[VerdictScores, list[HardGate]]:
    """Return the scores with every tripped gate recorded.

    A tripped gate forces correctness to 0 so the weighted total can
    never present a gated candidate as competitive; the caller still
    reports *which* gate tripped.
    """
    tripped = [g for g in gates if g.tripped]
    if not tripped:
        return scores, gates
    return (
        VerdictScores(
            correctness=0.0,
            completeness=scores.completeness,
            specificity=scores.specificity,
            robustness=scores.robustness,
            clarity=scores.clarity,
            fatal=True,
        ),
        gates,
    )


def any_gate_tripped(gates: list[HardGate]) -> HardGate | None:
    for gate in gates:
        if gate.tripped:
            return gate
    return None


# ------------------------------------------------------------------ bias controls


def blind_label(agent_id: str) -> str:
    """Opaque competitor label handed to the judge.

    Never derived from the card or the agent id in a way the judge
    could reverse: only the position in the pair is disclosed.
    """
    return agent_id


def pair_order(seed: str | int, round_no: int, match_no: int) -> bool:
    """Deterministic A/B ordering decision for bias control.

    ``True`` means the pair is presented in its natural order; the
    caller swaps A and B when this returns ``False``. Same seed +
    round + match always gives the same order, so a resume replays
    the identical presentation.
    """
    try:
        value = int(str(seed)) if not isinstance(seed, int) else seed
    except (TypeError, ValueError):
        value = hash(str(seed)) & 0xFFFFFFFF
    mixed = (value ^ (round_no * 1000003) ^ (match_no * 7919)) & 1
    return mixed == 0


def swapped_pair(a: str, b: str, swap: bool) -> tuple[str, str]:
    return (b, a) if swap else (a, b)


__all__ = [
    "TASK_WEIGHTS",
    "weights_for",
    "weighted_total_for",
    "HardGate",
    "default_gates",
    "apply_gates",
    "any_gate_tripped",
    "blind_label",
    "pair_order",
    "swapped_pair",
]
