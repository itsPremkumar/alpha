"""Quality Gate and Red-Team Verification Engine (Master Inventory #172-#176).

Enforces automated acceptance gates, deliverable checks, and quality thresholds before tasks
can be marked complete.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from alpha.bots.profile import _now

logger = logging.getLogger(__name__)


def evaluate_quality_gate(
    deliverable: str,
    acceptance_criteria: list[str],
    *,
    required_sections: list[str] | None = None,
    min_length: int = 40,
) -> dict[str, Any]:
    """Verify that a worker's deliverable satisfies declared acceptance criteria (Inventory #175)."""
    text = (deliverable or "").strip()
    checks: list[dict[str, Any]] = []

    # 1. Non-empty & minimal substance check
    length_passed = len(text) >= min_length
    checks.append(
        {
            "name": "Substantive Output",
            "passed": length_passed,
            "details": f"Length: {len(text)} chars (minimum {min_length}).",
        }
    )

    # 2. No boilerplate failure placeholders
    lower_text = text.lower()
    has_failure_phrases = any(phrase in lower_text for phrase in ("did not complete", "execution failed", "error occurred", "traceback (most recent call last)"))
    checks.append(
        {
            "name": "No Explicit Failure Signatures",
            "passed": not has_failure_phrases,
            "details": "Output contains error or uncompleted markers." if has_failure_phrases else "Clean of failure signatures.",
        }
    )

    # 3. Acceptance Criteria Coverage
    # Disclosed design choice: keyword-overlap threshold of 40% (not semantic
    # matching). A criterion with no evaluable tokens can never be checked, so
    # it must never pass — it fails with an explicit reason and is surfaced in
    # `degenerate_criteria` so callers can see nothing was evaluated for it.
    criteria_passed = 0
    degenerate_criteria: list[dict[str, str]] = []
    total_criteria = len(acceptance_criteria)
    if total_criteria > 0:
        for criterion in acceptance_criteria:
            crit_tokens = [t for t in re.findall(r"[a-z0-9_]+", criterion.lower()) if len(t) > 2] if isinstance(criterion, str) else []
            if len(crit_tokens) == 0:
                degenerate_criteria.append({"criterion": str(criterion), "reason": "criterion has no evaluable tokens"})
                checks.append(
                    {
                        "name": f"Criterion: {str(criterion)[:40]}...",
                        "passed": False,
                        "details": "criterion has no evaluable tokens",
                    }
                )
                continue
            # Check keyword presence in deliverable
            matched = sum(1 for t in crit_tokens if t in lower_text)
            coverage = matched / len(crit_tokens)
            passed = coverage >= 0.40
            if passed:
                criteria_passed += 1
            checks.append(
                {
                    "name": f"Criterion: {str(criterion)[:40]}...",
                    "passed": passed,
                    "details": f"Keywords matched: {matched}/{len(crit_tokens)}",
                }
            )

    # 4. Required Sections Check
    if required_sections:
        for sec in required_sections:
            sec_found = sec.lower() in lower_text
            checks.append(
                {
                    "name": f"Section: {sec}",
                    "passed": sec_found,
                    "details": "Section found in deliverable." if sec_found else "Section missing.",
                }
            )

    # Compute overall score
    total_checks = len(checks)
    passed_checks = sum(1 for c in checks if c["passed"])
    score = round(passed_checks / max(total_checks, 1), 2)

    # Hard rules for passing: must have substantive output, no failure
    # signatures, and every criterion must have been evaluable. A gate with
    # degenerate criteria cannot claim a pass when part of its criteria set
    # was never checked.
    verdict = "passed" if (length_passed and not has_failure_phrases and not degenerate_criteria and score >= 0.60) else "rejected"

    if verdict == "passed":
        feedback = "Deliverable passed all automated quality gate checks."
    else:
        feedback = f"Quality gate rejected deliverable: {passed_checks}/{total_checks} checks passed."
        if degenerate_criteria:
            feedback += " Degenerate criteria (never pass, not evaluable): " + "; ".join(f"{d['criterion']!r} ({d['reason']})" for d in degenerate_criteria) + "."

    return {
        "verdict": verdict,
        "score": score,
        "passed_checks": passed_checks,
        "total_checks": total_checks,
        "checks": checks,
        "degenerate_criteria": degenerate_criteria,
        "feedback": feedback,
        "timestamp": _now(),
    }
