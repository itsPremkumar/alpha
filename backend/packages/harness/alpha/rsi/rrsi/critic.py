"""Regularizer **S1** — the leakage screening critic.

RRSI's first selection-side regularizer runs *before* evaluation: a candidate
that scores well by recognizing the benchmark rather than by improving the
harness must never receive the inflated score in the first place. Screening
after the fact cannot undo a promotion decision that was already made on the
leaked number, so this critic is deliberately positioned at the front of the
selection pipeline (:func:`alpha.rsi.rrsi.selection.admit`).

Three finding classes, each matching the paper's description of "benchmark-
specific content or inert machinery":

``benchmark_reference``
    the diff names a benchmark/suite by identifier (``terminal-bench``,
    ``swe-bench``, ``gsm8k``, ...). A harness change has no legitimate reason
    to special-case the suite it is about to be scored on.
``answer_marker``
    the diff references ground-truth scaffolding (``expected_answer``,
    ``gold_answer``, ``ground_truth``, ...) — machinery that exists to *supply*
    an answer rather than to compute one.
``task_literal``
    a sufficiently long literal token from the supplied task text appears
    verbatim in the diff. Requires ``task_text``; without it this class simply
    does not apply (it is never reported as "clean").

Binding rules:

* **Static findings fail closed; a reviewer can only fail, never pass.**
  :func:`screen_candidate` runs the deterministic rules first, then an optional
  caller-supplied ``reviewer``. A reviewer that raises, returns a malformed
  verdict, or says ``False`` fails the screen with the real reason. There is
  no path by which a reviewer can *override* a static finding — a judge that
  could clear a finding it disliked would make the whole critic advisory.
* **Repairs are bounded and attempted in order.** ``repair_attempts`` are
  alternative diffs tried only after the current one failed, up to
  ``max_repairs``. The outcome names how many were tried; exhausting them
  reports ``passed=False`` with the last real reason rather than a summary of
  "best effort".
* **The screen is a heuristic, and says so.** ``basis`` records which engine
  produced the verdict (``static``, ``static+reviewer`` or
  ``reviewer_error``). A static pass means "the declared rules found nothing",
  which is not the same as "this candidate is clean" — the disclosure carries
  the rule count that was applied so the reader can weigh it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = ["Reviewer", "ScreenVerdict", "screen_candidate"]

#: Benchmark / evaluation-suite identifiers that a legitimate harness change
#: has no cause to hard-code. Compared case-insensitively with word edges so
#: ``swebenchlib`` (a plausible unrelated library) still matches but a
#: substring in a longer identifier does not silently skip.
BENCHMARK_MARKERS: tuple[str, ...] = (
    "terminal-bench",
    "terminal_bench",
    "swe-bench",
    "swe_bench",
    "swebench",
    "harvey",
    "jobbench",
    "job_bench",
    "gdpval",
    "gsm8k",
    "gpqa",
    "mmlu",
    "humaneval",
    "mbpp",
    "apex-agents",
    "apex_agents",
    "engdesign",
    "frontier-ench",
    "tau-bench",
    "tau_bench",
    "webarena",
    "visualwebarena",
    "agentbench",
    "agent-bench",
    "gaia",
    "bbh",
)

#: Ground-truth scaffolding identifiers. Present in a *harness diff* these are
#: inert machinery whose only function is to hand the scorer the answer.
ANSWER_MARKERS: tuple[str, ...] = (
    "expected_answer",
    "gold_answer",
    "ground_truth_answer",
    "ground_truth_label",
    "solution_string",
    "correct_answer",
    "reference_answer",
    "answer_key",
)

#: Minimum length of a task-derived literal before verbatim appearance in the
#: diff counts as a finding. Short tokens (``def``, ``the``, ``return``) are
#: shared by every program in the domain and would flag every diff.
MIN_TASK_LITERAL = 16

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{4,}")

Reviewer = Callable[[str], "tuple[bool, str] | bool"]


@dataclass(frozen=True)
class ScreenVerdict:
    """One leakage screen over one candidate diff.

    ``findings`` are human-checkable strings naming the rule and the matched
    text (truncated) — never a score or a confidence. ``rules_applied`` counts
    the deterministic rules that ran, so ``passed=True`` can be weighed as "N
    rules found nothing" rather than read as a clean bill of health.
    """

    passed: bool
    reason: str
    findings: tuple[str, ...]
    basis: str
    reviewer_ran: bool
    repairs_attempted: int
    rules_applied: int
    screened_text_length: int

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "findings": list(self.findings),
            "basis": self.basis,
            "reviewer_ran": self.reviewer_ran,
            "repairs_attempted": self.repairs_attempted,
            "rules_applied": self.rules_applied,
            "screened_text_length": self.screened_text_length,
        }


def _findings(diff_text: str, task_text: str) -> tuple[str, ...]:
    """Run the three deterministic rules over ``diff_text``."""
    lowered = diff_text.lower()
    found: list[str] = []
    for marker in BENCHMARK_MARKERS:
        if marker in lowered:
            found.append(f"benchmark_reference: diff mentions benchmark marker {marker!r}")
    for marker in ANSWER_MARKERS:
        if marker in lowered:
            found.append(f"answer_marker: diff references ground-truth scaffolding {marker!r}")
    if task_text:
        literals = {token for token in _TOKEN_RE.findall(task_text) if len(token) >= MIN_TASK_LITERAL}
        for token in sorted(literals):
            if token in diff_text or token.lower() in lowered:
                found.append(f"task_literal: task token {token[:60]!r} appears verbatim in the diff")
                break  # one is enough — the finding is about the class, not the count
    return tuple(found)


def screen_candidate(
    diff_text: str,
    *,
    task_text: str = "",
    reviewer: Reviewer | None = None,
    repair_attempts: Sequence[str] = (),
    max_repairs: int = 2,
) -> ScreenVerdict:
    """Screen one candidate diff for benchmark leakage, with bounded repairs.

    ``diff_text`` is the change under consideration. ``task_text`` is optional
    context whose long literals are checked for verbatim reproduction.
    ``reviewer`` is an optional caller-supplied judge — see the module
    docstring for why it can only fail a screen.

    Raises ``ValueError`` for a malformed argument (a non-string diff, a
    negative ``max_repairs``): those are caller bugs, and returning a verdict
    for them would launder a programming error into a leakage judgement.
    """
    if not isinstance(diff_text, str):
        raise ValueError(f"diff_text must be a string; got {type(diff_text).__name__}.")
    if not isinstance(task_text, str):
        raise ValueError(f"task_text must be a string; got {type(task_text).__name__}.")
    if isinstance(max_repairs, bool) or not isinstance(max_repairs, int) or max_repairs < 0:
        raise ValueError(f"max_repairs must be an integer >= 0; got {max_repairs!r}.")
    if isinstance(repair_attempts, (str, bytes)):
        raise ValueError("repair_attempts must be a sequence of alternative diff strings, not a single string.")

    rules = 3 if task_text else 2
    candidates = [diff_text, *(str(item) for item in repair_attempts)][: 1 + max_repairs]
    attempts = 0
    last_findings: tuple[str, ...] = ()
    last_text = diff_text
    for index, text in enumerate(candidates):
        if index > 0:
            attempts += 1
        findings = _findings(text, task_text)
        last_findings, last_text = findings, text
        if not findings:
            break

    if last_findings:
        prefix = f"{attempts} repair attempt(s) tried; " if attempts else ""
        verdict_passed = False
        verdict_reason = f"{prefix}{len(last_findings)} leakage finding(s): " + "; ".join(last_findings)
        basis = "static"
        reviewer_ran = False
    else:
        verdict_passed = True
        verdict_reason = f"{rules} deterministic rule(s) found no leakage; {attempts} repair attempt(s) tried."
        basis = "static"
        reviewer_ran = False

    if reviewer is not None:
        try:
            outcome = reviewer(last_text)
            if isinstance(outcome, bool):
                reviewer_passed, reviewer_reason = outcome, "reviewer returned a bare boolean"
            elif isinstance(outcome, tuple) and len(outcome) == 2 and isinstance(outcome[0], bool):
                reviewer_passed, reviewer_reason = bool(outcome[0]), str(outcome[1])
            else:
                reviewer_passed, reviewer_reason = False, f"reviewer returned a malformed verdict of type {type(outcome).__name__}; expected (bool, str) or bool"
        except Exception as exc:  # noqa: BLE001 - a failing judge fails the screen, and the real error is the reason
            reviewer_passed, reviewer_reason = False, f"reviewer raised {type(exc).__name__}: {exc}"
            basis = "reviewer_error"
            verdict_passed = False
            verdict_reason = reviewer_reason
            reviewer_ran = True
        else:
            reviewer_ran = True
            basis = "static+reviewer"
            if not reviewer_passed:
                verdict_passed = False
                verdict_reason = f"reviewer rejected the candidate: {reviewer_reason}"

    return ScreenVerdict(
        passed=verdict_passed,
        reason=verdict_reason,
        findings=last_findings,
        basis=basis,
        reviewer_ran=reviewer_ran,
        repairs_attempted=attempts,
        rules_applied=rules,
        screened_text_length=len(last_text),
    )
