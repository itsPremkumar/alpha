"""The Arena rubric: five criteria, fixed weights, and the verdict rule.

Every judge sub-agent scores both revised solutions 0-10 on five
criteria. The winner is **computed** from those scores with the
fixed weights below - never merely reported - because a judge is
itself a model and can write ``winner: A`` while its own numbers
say otherwise. ``decide()`` therefore recomputes, and overrides the
stated pick whenever the arithmetic disagrees.

The one exception is a **fatal** flag: a solution with a fatal
flaw loses outright, whatever its numeric total, because a
brilliant answer to the wrong problem is still wrong.
"""

from __future__ import annotations

from typing import Any

from alpha.arena.models import AttackRecord, DefenseRecord, VerdictScores

# Fixed weights. The test suite cross-checks this dict against the
# Markdown table in the rubric template so the two cannot drift.
WEIGHTS: dict[str, float] = {
    "correctness": 0.30,
    "completeness": 0.25,
    "specificity": 0.15,
    "robustness": 0.20,
    "clarity": 0.10,
}

CRITERIA: tuple[str, ...] = ("correctness", "completeness", "specificity", "robustness", "clarity")

# The full rubric text with anchors. The judge template embeds it,
# and the weights table inside it is the copy WEIGHTS must match.
RUBRIC_TEXT = """# Arena Judging Rubric

Score each solution 0-10 on every criterion. Judge the *revised*
solution as it now stands, not as it was, and not as its author
intended it to be.

| Criterion | Weight | 0-2 | 3-4 | 5-6 | 7-8 | 9-10 |
| --- | --- | --- | --- | --- | --- | --- |
| correctness | 30% | Fundamentally wrong; central claim false | Major errors in key claims | Mostly right; some errors | Nearly right; minor slips | Right; defensible step by step |
| completeness | 25% | Missing the core of the task | Missing important parts | Covers most; gaps remain | Covers all but a detail | Complete; nothing material missing |
| specificity | 15% | Vague generalities only | Some concrete detail | Mostly concrete | Specific throughout | Concrete, named, checkable |
| robustness | 20% | Breaks on ordinary edge cases | Breaks on plausible inputs | Handles common edges | Handles most edges | Survives hostile inputs |
| clarity | 10% | Unreadable | Confusing structure | Followable with effort | Clear | Clear, tight, well-ordered |

**Fatal flaw rule.** If a solution is wrong in a way that makes its
answer worthless - it solves the wrong problem, contradicts itself
in a load-bearing place, or its central mechanism cannot work -
flag it ``fatal: true``. A fatal solution loses outright regardless
of its numeric total.

**Survival rule.** An attack *survives* the defense when the
defender CONCEDEd it, or REBUTTed it weakly (the rebuttal does not
actually answer the attack's core). A surviving MAJOR or FATAL
attack counts against completeness and robustness; a surviving
FATAL attack is itself a fatal flaw.
"""


def weighted_total(scores: VerdictScores) -> float:
    """The rubric-weighted total of one solution (0-10)."""
    return sum(float(getattr(scores, name)) * weight for name, weight in WEIGHTS.items())


def _surviving_attacks(attacks: list[AttackRecord], defenses: list[DefenseRecord]) -> list[AttackRecord]:
    """The attacks that the defense did not kill.

    An attack is killed only by a REBUT whose note actually answers
    it; the arena cannot read minds, so the rule is the defender's
    own verdict plus a length floor: a bare ``REBUT.`` with nothing
    behind it does not count as an answer.
    """
    rebutted: set[int] = set()
    for defense in defenses:
        if defense.verdict.value == "REBUT" and len(defense.note.strip()) >= 20:
            rebutted.add(defense.attack_index)
    return [attack for attack in attacks if attack.index not in rebutted]


def _surviving_severity(attacks: list[AttackRecord], defenses: list[DefenseRecord]) -> str | None:
    """The worst severity that survived the defense, if any."""
    order = {"FATAL": 3, "MAJOR": 2, "MINOR": 1}
    surviving = _surviving_attacks(attacks, defenses)
    if not surviving:
        return None
    return max(surviving, key=lambda attack: order.get(attack.severity.value, 0)).severity.value


def judge_verdict(
    scores: VerdictScores,
    attacks: list[AttackRecord],
    defenses: list[DefenseRecord],
) -> dict[str, Any]:
    """Full judging record for one solution: weighted total plus
    which attacks survived. The judge supplies scores; this computes
    the rest so no prose can disagree with the arithmetic."""
    survived = _surviving_attacks(attacks, defenses)
    severity = _surviving_severity(attacks, defenses)
    return {
        "weighted_total": round(weighted_total(scores), 4),
        "survived": [attack.to_dict() for attack in survived],
        "surviving_severity": severity,
    }


def decide(
    a_scores: VerdictScores,
    b_scores: VerdictScores,
    a_attacks: list[AttackRecord] | None = None,
    a_defenses: list[DefenseRecord] | None = None,
    b_attacks: list[AttackRecord] | None = None,
    b_defenses: list[DefenseRecord] | None = None,
    judge_pick: str | None = None,
) -> dict[str, Any]:
    """Decide one match from the two solutions' scores.

    Returns a dict with ``winner`` (``"a"``/``"b"``), ``reason``,
    ``scores``, ``survived`` per side, ``fatal`` per side and
    ``judge_overridden``.

    Rules, in order:

    1. **Fatal beats non-fatal.** A solution flagged fatal loses
       outright, whatever its total.
    2. **Higher weighted total wins.** Ties go to the higher
       correctness, then the higher robustness, then the higher
       completeness - in that fixed order, because correctness is
       the one criterion a task cannot be wrong about.
    3. **A perfect tie is reported as such**, never broken by a
       coin flip.
    4. **The judge's stated pick is advisory.** When it contradicts
       the arithmetic, the arithmetic wins and ``judge_overridden``
       is set - the honest thing is to record the disagreement,
       not to hide it.
    """
    a_attacks = a_attacks or []
    a_defenses = a_defenses or []
    b_attacks = b_attacks or []
    b_defenses = b_defenses or []

    a_total = weighted_total(a_scores)
    b_total = weighted_total(b_scores)
    a_fatal = bool(a_scores.fatal) or _surviving_severity(a_attacks, a_defenses) == "FATAL"
    b_fatal = bool(b_scores.fatal) or _surviving_severity(b_attacks, b_defenses) == "FATAL"

    if a_fatal and not b_fatal:
        winner, reason = "b", "A carries a fatal flaw"
    elif b_fatal and not a_fatal:
        winner, reason = "a", "B carries a fatal flaw"
    elif a_fatal and b_fatal:
        winner, reason = None, "Both solutions carry fatal flaws"
    elif abs(a_total - b_total) < 1e-9:
        # Fixed-order tie-breaks: correctness, robustness, completeness.
        for name in ("correctness", "robustness", "completeness"):
            av, bv = getattr(a_scores, name), getattr(b_scores, name)
            if av != bv:
                winner = "a" if av > bv else "b"
                reason = f"tie on total, broken by {name}"
                break
        else:
            winner, reason = None, "dead tie on total and tie-breakers"
    else:
        winner = "a" if a_total > b_total else "b"
        reason = f"weighted total {a_total:.4f} vs {b_total:.4f}"

    judge_overridden = False
    if judge_pick and winner and judge_pick.lower().lstrip() not in (winner, f"side_{winner}"):
        judge_overridden = True

    return {
        "winner": winner,
        "reason": reason,
        "scores": {
            "a": {"total": round(a_total, 4), "criteria": a_scores.to_dict()},
            "b": {"total": round(b_total, 4), "criteria": b_scores.to_dict()},
        },
        "survived": {
            "a": [attack.to_dict() for attack in _surviving_attacks(a_attacks, a_defenses)],
            "b": [attack.to_dict() for attack in _surviving_attacks(b_attacks, b_defenses)],
        },
        "surviving_severity": {
            "a": _surviving_severity(a_attacks, a_defenses),
            "b": _surviving_severity(b_attacks, b_defenses),
        },
        "fatal": {"a": a_fatal, "b": b_fatal},
        "judge_overridden": judge_overridden,
    }


__all__ = [
    "CRITERIA",
    "RUBRIC_TEXT",
    "WEIGHTS",
    "decide",
    "judge_verdict",
    "weighted_total",
]
