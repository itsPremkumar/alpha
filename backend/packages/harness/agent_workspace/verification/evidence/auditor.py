from __future__ import annotations

import logging

from .matrix import EvidenceMatrix

logger = logging.getLogger("agent_workspace.verification.evidence.auditor")


class FinishFirstAuditor:
    """
    Finish-First Auditor gatekeeper.
    Strictly forbids declaring a task or mission 'done' unless:
      1. Every substantive claim has an attached physical proof.
      2. Zero contradictions exist.
      3. Completeness ratio is 100%.
    """

    def __init__(self, matrix: EvidenceMatrix) -> None:
        self.matrix = matrix

    def audit_finalization(self) -> tuple[bool, str, list[str]]:
        """
        Audits the matrix for task completion.
        Returns: (can_finalize, status_message, list_of_blocking_issues)
        """
        summary = self.matrix.summary()
        blocking_issues: list[str] = []

        if summary["total_claims"] == 0:
            blocking_issues.append("No claims or deliverables were recorded in the evidence matrix.")

        if summary["unverified_claims"] > 0:
            blocking_issues.append(
                f"{summary['unverified_claims']} claim(s) lack physical verification proof."
            )

        if summary["contradictions_count"] > 0:
            blocking_issues.append(
                f"{summary['contradictions_count']} contradiction(s) detected between claims and execution logs."
            )

        can_finalize = (len(blocking_issues) == 0)

        if can_finalize:
            msg = (
                f"AUDIT_PASSED: All {summary['total_claims']} claim(s) certified with 100% "
                "physical verification proof. Task can safely finalize."
            )
            logger.info(msg)
        else:
            msg = (
                f"AUDIT_BLOCKED: Finish-First criteria unmet. "
                f"Issues: {'; '.join(blocking_issues)}"
            )
            logger.warning(msg)

        return can_finalize, msg, blocking_issues
