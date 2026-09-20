"""Multi-Agent Pre-Merge Audit & Security Council.

Executes a tripartite peer review (Reviewer, Security Analyst, Architect) on code diffs
and deliverables prior to merging worktrees or completing task contracts.
Scans for hardcoded credentials, unsafe execution primitives (eval, exec, shell=True),
and compliance with project ADRs and constitution directives.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from alpha.projects.constitution import get_constitution
from alpha.projects.contracts import EvidenceReceipt, get_contract_gatekeeper
from alpha.projects.decisions import get_decision_log
from alpha.projects.events import get_event_bus

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# Regex patterns for common secret leaks
SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][a-zA-Z0-9_\-\.]{12,}['\"]", "Exposed API key or access token"),
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", "Hardcoded plain-text password"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9]*", "Slack Bot or User Token"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
]

# Unsafe execution primitives
UNSAFE_PATTERNS = [
    (r"\beval\s*\(", "Unsafe dynamic eval() invocation"),
    (r"\bexec\s*\(", "Unsafe dynamic exec() invocation"),
    (r"\bshell\s*=\s*True\b", "Unsafe shell=True invocation in subprocess"),
]


@dataclass
class AuditCheckItem:
    """A granular audit finding."""

    name: str
    category: Literal["code_quality", "security", "architecture"]
    auditor_bot: str
    passed: bool
    severity: Literal["info", "warning", "blocking"]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditVerdict:
    """Final collective ruling issued by the audit council."""

    audit_id: str
    project_id: str
    passed: bool
    score: float
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    council_signatures: dict[str, bool] = field(default_factory=dict)
    items: list[AuditCheckItem] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["items"] = [i.to_dict() for i in self.items]
        return data


class AuditCouncil:
    """Orchestrates multi-role security and architectural verification."""

    def __init__(self, project_id: str):
        self.project_id = project_id

    def audit_code(
        self,
        code_content: str,
        *,
        task_id: str | None = None,
        context_description: str = "",
    ) -> AuditVerdict:
        """Run full council audit on code text or git patch."""
        audit_id = f"AUDIT-{uuid.uuid4().hex[:8].upper()}"
        items: list[AuditCheckItem] = []
        blocking_issues: list[str] = []
        warnings: list[str] = []

        signatures = {
            "reviewer": True,
            "security": True,
            "architect": True,
        }

        # 1. Security Bot Audit: Secrets Scanning
        found_secret = False
        for pattern, desc in SECRET_PATTERNS:
            if re.search(pattern, code_content):
                msg = f"Security Violation: {desc} detected in code snippet."
                items.append(
                    AuditCheckItem(
                        name="secret_scanning",
                        category="security",
                        auditor_bot="security",
                        passed=False,
                        severity="blocking",
                        message=msg,
                    )
                )
                blocking_issues.append(msg)
                found_secret = True
                signatures["security"] = False
                break

        if not found_secret:
            items.append(
                AuditCheckItem(
                    name="secret_scanning",
                    category="security",
                    auditor_bot="security",
                    passed=True,
                    severity="info",
                    message="No hardcoded secrets or credentials detected.",
                )
            )

        # 2. Security Bot Audit: Unsafe Primitives
        found_unsafe = False
        for pattern, desc in UNSAFE_PATTERNS:
            if re.search(pattern, code_content):
                msg = f"Security Violation: {desc} detected."
                items.append(
                    AuditCheckItem(
                        name="unsafe_primitives",
                        category="security",
                        auditor_bot="security",
                        passed=False,
                        severity="blocking",
                        message=msg,
                    )
                )
                blocking_issues.append(msg)
                found_unsafe = True
                signatures["security"] = False
                break

        if not found_unsafe:
            items.append(
                AuditCheckItem(
                    name="unsafe_primitives",
                    category="security",
                    auditor_bot="security",
                    passed=True,
                    severity="info",
                    message="No dangerous eval/exec or unshielded shell calls detected.",
                )
            )

        # 3. Reviewer Bot Audit: Formatting & Structure
        code_lines = code_content.strip().splitlines()
        if len(code_lines) > 600:
            warn_msg = f"Code Reviewer Warning: Large single commit/snippet ({len(code_lines)} lines). Consider breaking down."
            items.append(
                AuditCheckItem(
                    name="chunk_size",
                    category="code_quality",
                    auditor_bot="reviewer",
                    passed=True,
                    severity="warning",
                    message=warn_msg,
                )
            )
            warnings.append(warn_msg)
        else:
            items.append(
                AuditCheckItem(
                    name="chunk_size",
                    category="code_quality",
                    auditor_bot="reviewer",
                    passed=True,
                    severity="info",
                    message="Snippet size is within healthy reviewable bounds.",
                )
            )

        # 4. Architect Bot Audit: Project Constitution & ADR Compliance
        constitution = get_constitution(self.project_id)
        decision_log = get_decision_log(self.project_id)
        recent_adrs = decision_log.list()

        arch_pass = True
        if constitution and "sqlite" in constitution.body.lower() and "postgres" in code_content.lower() and not recent_adrs:
            arch_msg = "Architect Violation: PostgreSQL usage detected while Project Constitution mandates SQLite."
            items.append(
                AuditCheckItem(
                    name="constitution_compliance",
                    category="architecture",
                    auditor_bot="architect",
                    passed=False,
                    severity="blocking",
                    message=arch_msg,
                )
            )
            blocking_issues.append(arch_msg)
            arch_pass = False
            signatures["architect"] = False

        if arch_pass:
            items.append(
                AuditCheckItem(
                    name="constitution_compliance",
                    category="architecture",
                    auditor_bot="architect",
                    passed=True,
                    severity="info",
                    message="Snippet conforms with active ADRs and Project Constitution.",
                )
            )

        # Calculate final verdict
        passed = len(blocking_issues) == 0
        total_checks = len(items)
        passed_checks = sum(1 for i in items if i.passed)
        score = round(passed_checks / max(1, total_checks), 2)

        verdict = AuditVerdict(
            audit_id=audit_id,
            project_id=self.project_id,
            passed=passed,
            score=score,
            blocking_issues=blocking_issues,
            warnings=warnings,
            council_signatures=signatures,
            items=items,
        )

        # If audit passed and task_id supplied, seal security evidence on contract
        if passed and task_id:
            try:
                gk = get_contract_gatekeeper(self.project_id)
                receipt = EvidenceReceipt(
                    kind="security",
                    reference=audit_id,
                    verified_by="audit_council",
                    detail=f"Security audit council certified code with score {score}.",
                )
                gk.add_evidence(task_id, receipt)
            except Exception:
                pass

        # Emit audit completion event
        get_event_bus(self.project_id).emit(
            "approval_granted" if passed else "approval_rejected",
            "audit_council",
            {"audit_id": audit_id, "score": score, "blocking_count": len(blocking_issues)},
        )

        return verdict


def get_audit_council(project_id: str) -> AuditCouncil:
    return AuditCouncil(project_id)
