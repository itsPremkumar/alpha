"""Quality Gate and Red-Team Verification Engine (Master Inventory #172-#176).

Enforces automated acceptance gates, deliverable checks, and quality thresholds before tasks
can be marked complete.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_workspace.bots.profile import _now

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
    criteria_passed = 0
    total_criteria = len(acceptance_criteria)
    if total_criteria > 0:
        for criterion in acceptance_criteria:
            crit_tokens = [t for t in re.findall(r"[a-z0-9_]+", criterion.lower()) if len(t) > 2]
            # Check keyword presence in deliverable
            matched = sum(1 for t in crit_tokens if t in lower_text)
            coverage = matched / max(len(crit_tokens), 1)
            passed = coverage >= 0.40 or len(crit_tokens) == 0
            if passed:
                criteria_passed += 1
            checks.append(
                {
                    "name": f"Criterion: {criterion[:40]}...",
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

    # Hard rules for passing: must have substantive output and no failure signatures
    verdict = "passed" if (length_passed and not has_failure_phrases and score >= 0.60) else "rejected"

    feedback = "Deliverable passed all automated quality gate checks." if verdict == "passed" else f"Quality gate rejected deliverable: {passed_checks}/{total_checks} checks passed."

    return {
        "verdict": verdict,
        "score": score,
        "passed_checks": passed_checks,
        "total_checks": total_checks,
        "checks": checks,
        "feedback": feedback,
        "timestamp": _now(),
    }
