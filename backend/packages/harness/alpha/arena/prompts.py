"""The five Arena prompt templates.

One template per job kind. Placeholders are ``{{name}}``
and substitution is a single regex pass - deliberately
*not* ``str.format``, because the judge template carries a
JSON example whose braces ``format`` would try to interpret.

The templates are the model-facing contract of the engine:
they say what each sub-agent is, what it must read, what
it must write, and the exact output format the parsers in
``models.py`` expect. The output formats are load-bearing:
``parse_attacks`` reads ``ATTACK n [SEV]``, and
``parse_defenses`` reads ``ATTACK n: CONCEDE|REBUT``, so an
edit to one side of that contract must be an edit to both.

Every competitor prompt carries its strategy card in the
task text (never in a system prompt): operator- and
engine-supplied text is only ever downgraded to the
untrusted channel, so a card cannot masquerade as an
instruction the executor itself issued.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# One regex pass over {{placeholder}} tokens. A missing
# placeholder is left as-is rather than blanked, so a
# template/engine mismatch surfaces in the prompt the
# sub-agent actually sees.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

COMPETITOR_TEMPLATE = """You are a competitor in an agent arena. Several agents are solving the same task independently, each dealt a different strategy card. Your card is below; it is your identity for this run: reason and work the way it describes, even when a different way seems faster.

TASK:
{{task}}

YOUR STRATEGY CARD:
- Reasoning mode: {{reasoning_name}} - {{reasoning_how}}
- Workflow: {{workflow_name}} - {{workflow_how}}
- Strategy: {{strategy_name}} - {{strategy_how}}

Deliverable:
1. Solve the task now, your way, in full.
2. Write your complete solution to the file at exactly this path: {{solution_path}}
3. Your solution exists only on disk. If you do not write the file, you have nothing.

Rules:
- Work alone. Do not read or reference any other agent's files; every other file in the arena directory belongs to a competitor.
- Your file must be self-contained: a reader who has seen nothing else can understand and evaluate it.
- Prefer a concrete, checkable answer over a confident-sounding one. State assumptions explicitly.
- When you finish, reply with exactly one line: `WROTE {{solution_path}}`
"""

ATTACKER_TEMPLATE = """You are an attacker in an agent arena. A competitor produced a solution to a task. Your job is to find real, concrete, checkable flaws in it - not to rewrite it, not to praise it, not to nitpick style. You attack with your own strategy card, so your angle differs from every other attacker's.

TASK:
{{task}}

OPPONENT'S SOLUTION:
Read the file at exactly this path: {{solution_path}}

YOUR STRATEGY CARD:
- Reasoning mode: {{reasoning_name}} - {{reasoning_how}}
- Workflow: {{workflow_name}} - {{workflow_how}}
- Strategy: {{strategy_name}} - {{strategy_how}}

Attack the solution. Look for: incorrect logic, edge cases it mishandles, unstated or false assumptions, missing requirements, off-by-one and boundary errors, inputs that make it produce the wrong answer or fail entirely, claims it makes that it cannot back.

Severity: FATAL = the answer is wrong or worthless as it stands. MAJOR = a real flaw a user would hit. MINOR = a small weakness, real but not the core.

Output format - one block per attack, and nothing else:
ATTACK 1 [FATAL] <short title>
Where: <the place in the solution, or the requirement, it fails>
Problem: <what is wrong, and the concrete input or case that proves it>

ATTACK 2 [MAJOR] <short title>
Where: ...
Problem: ...

Rules:
- Every attack must be checkable: name the input, the case, or the requirement that exposes it.
- Do not attack the writing style; attack what the solution says or fails to do.
- If you genuinely cannot find a flaw, write exactly: `NO ATTACKS`. Do not invent flaws to fill the format.
"""

DEFENDER_TEMPLATE = """You are a defender in an agent arena. A competitor (you) produced a solution; attackers have now found flaws in it. Your job is to answer every attack honestly - concede the ones that are real, rebut the ones that are wrong - and then revise your solution to fix every conceded flaw.

TASK:
{{task}}

YOUR CURRENT SOLUTION:
Read the file at exactly this path: {{solution_path}}

ATTACKS AGAINST YOUR SOLUTION:
{{attacks_text}}

YOUR STRATEGY CARD:
- Reasoning mode: {{reasoning_name}} - {{reasoning_how}}
- Workflow: {{workflow_name}} - {{workflow_how}}
- Strategy: {{strategy_name}} - {{strategy_how}}

For EACH attack, decide:
- CONCEDE - the attack found a real flaw (you will fix it), or
- REBUT - the attack is wrong, or already handled (say why, in a sentence that actually answers it).

Then revise your solution: fix every conceded attack and every real flaw the attacks exposed. Keep everything that was right.

Output format - first the point-by-point answers, then the revised solution:
ATTACK 1: CONCEDE. <one or two sentences>
ATTACK 2: REBUT. <the answer to the attack, not a restatement>

Rules:
- Answer every attack. A skipped attack counts as conceded.
- A bare `REBUT.` with nothing behind it does not count as an answer - it will be read as a concession.
- Write your revised solution to exactly this path: {{revised_path}}
- When you finish, reply with exactly one line: `WROTE {{revised_path}}`
"""

JUDGE_TEMPLATE = """You are the judge of one match in an agent arena. Two competitors each solved the same task, were attacked, defended, and revised. You now score BOTH revised solutions on a fixed rubric and decide the match.

TASK:
{{task}}

Read both revised solutions:
- Solution A: {{a_path}}
- Solution B: {{b_path}}

Judge each solution as it now stands - not as it was before the attacks, and not as its author intended it to be. Score both on every criterion of this rubric:

{{rubric_text}}

Also decide, from the attack/defense record of the match, whether either solution still carries an unresolved fatal attack. The match record is supplied by the arena; treat a surviving FATAL attack as a fatal flaw for that side.

Output format - valid JSON only, nothing else:
{
  "scores": {
    "a": {"correctness": 0-10, "completeness": 0-10, "specificity": 0-10, "robustness": 0-10, "clarity": 0-10, "fatal": true-or-false},
    "b": {"correctness": 0-10, "completeness": 0-10, "specificity": 0-10, "robustness": 0-10, "clarity": 0-10, "fatal": true-or-false}
  },
  "winner": "a" or "b",
  "reason": "<which criteria decided it, in one or two sentences>"
}
"""

FINAL_TEMPLATE = """You are the final verifier of an agent arena. A bracket of competitors has produced one champion solution. Verify it against the original task - and against the baseline solution when one was supplied.

TASK:
{{task}}

BASELINE SOLUTION:
{{baseline_text}}

CHAMPION SOLUTION:
Read the file at exactly this path: {{champion_path}}

Answer two questions:
1. Does the champion solution actually solve the task? Judge it on correctness, completeness and robustness, not on style.
2. If a baseline is supplied, is the champion at least as good as the baseline on the substance of the task?

Output format - exactly these two lines:
VERDICT: PASS or FAIL
REASON: <one or two sentences naming the deciding evidence>

Rules:
- PASS only when the solution is substantively correct and complete. A plausible-sounding but wrong or hollow answer is a FAIL.
- You are the last line of defence: the bracket already ranked the competitors, but ranking is not correctness. Judge the champion on the task, not on the tournament.
"""

TEMPLATES: dict[str, str] = {
    "competitor": COMPETITOR_TEMPLATE,
    "attacker": ATTACKER_TEMPLATE,
    "defender": DEFENDER_TEMPLATE,
    "judge": JUDGE_TEMPLATE,
    "final": FINAL_TEMPLATE,
}


def fill(template: str, values: Mapping[str, Any]) -> str:
    """Substitute ``{{placeholder}}`` tokens in a single pass.

    Single-pass matters: a value containing ``{{...}}``
    text (a solution excerpt, a JSON example) is inserted
    literally, never re-substituted. Unknown placeholders
    are left untouched so a template/engine mismatch is
    visible in the rendered prompt instead of silently
    blank.
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return str(values[key])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(replace, template)


def render(kind: str, values: Mapping[str, Any]) -> str:
    """Render one of the five templates by kind."""
    if kind not in TEMPLATES:
        raise KeyError(f"no arena template '{kind}' (expected one of {sorted(TEMPLATES)})")
    return fill(TEMPLATES[kind], values)


def card_values(card: Any) -> dict[str, str]:
    """The template values for one strategy card."""
    return {
        "reasoning_name": card.reasoning.name,
        "reasoning_how": card.reasoning.how,
        "workflow_name": card.workflow.name,
        "workflow_how": card.workflow.how,
        "strategy_name": card.strategy.name,
        "strategy_how": card.strategy.how,
    }


__all__ = [
    "ATTACKER_TEMPLATE",
    "COMPETITOR_TEMPLATE",
    "DEFENDER_TEMPLATE",
    "FINAL_TEMPLATE",
    "JUDGE_TEMPLATE",
    "TEMPLATES",
    "card_values",
    "fill",
    "render",
]
