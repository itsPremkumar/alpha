"""Adversarial Hyperplan Multi-Reviewer Pipeline.

Hostile multi-agent pre-execution plan stress-testing:
Before touching any code, 4 orthogonal reviewer agents evaluate the plan:
1. plan-consultant: Gap analysis and unstated prerequisite discovery.
2. plan-reviewer: Architectural failure mode and scaling bottleneck analysis.
3. omo-code-reviewer: Security posture, secret leaks, and breaking API diff risks.
4. omo-qa-executor: Acceptance criteria completeness and testability audit.

The gate reviewer aggregates verdicts into a final pass/fail authorization.

Heuristic implementation note: these are deterministic pre-LLM gates.
They never replace LLM reviewers — they catch cheap, high-signal failures
before spending model calls. Mentioning a risk WITH mitigation must not
reject; proposing a risk WITHOUT mitigation must.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewerVerdict:
    reviewer_role: str
    status: str  # "APPROVED", "REJECTED", "NEEDS_REVISION"
    critique: str
    identified_risks: list[str] = field(default_factory=list)
    missing_prerequisites: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_role": self.reviewer_role,
            "status": self.status,
            "critique": self.critique,
            "identified_risks": list(self.identified_risks),
            "missing_prerequisites": list(self.missing_prerequisites),
        }


@dataclass
class HyperplanReport:
    plan_title: str
    overall_status: str  # "APPROVED", "BLOCKED"
    verdicts: list[ReviewerVerdict]
    gatekeeper_summary: str
    plan_hash: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.overall_status == "BLOCKED"

    @property
    def is_approved(self) -> bool:
        return self.overall_status == "APPROVED"

    def blocking_verdicts(self) -> list[ReviewerVerdict]:
        return [v for v in self.verdicts if v.status in ("REJECTED", "NEEDS_REVISION")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_title": self.plan_title,
            "overall_status": self.overall_status,
            "plan_hash": self.plan_hash,
            "gatekeeper_summary": self.gatekeeper_summary,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


def hash_plan(plan_title: str, plan_text: str) -> str:
    """Stable short hash for plan pinning (C3: execution pins plan hash)."""
    digest = hashlib.sha256(f"{plan_title}\n{plan_text}".encode()).hexdigest()
    return digest[:16]


_MITIGATION_RE = re.compile(
    r"(fallback|mitigat|redundan|isolat|modular|rollback|retry|retries|barrier|"
    r"worktree|blue.?green|canary|avoid|eliminate|resolve|graceful|replica|quorum)",
    re.IGNORECASE,
)

_MODULARITY_RE = re.compile(
    r"(modular|isolat|interface|layer|service|fallback|rollback|retry|worktree|"
    r"checkpoint|barrier|scaling|shard|replica)",
    re.IGNORECASE,
)

_ARCH_RISKS = [
    (re.compile(r"single point of failure", re.IGNORECASE), "Single point of failure"),
    (re.compile(r"tight(ly)?\s+coup", re.IGNORECASE), "Tight coupling"),
    (re.compile(r"circular\s+depend", re.IGNORECASE), "Circular dependency"),
    (
        re.compile(r"unbounded|no\s+limit|infinite\s+(loop|growth)|no\s+timeout", re.IGNORECASE),
        "Unbounded resource/scale risk",
    ),
    (
        re.compile(r"global\s+mutable|shared\s+mutable[^.]{0,60}no.{0,20}lock", re.IGNORECASE),
        "Unsafe shared mutable state",
    ),
    (
        re.compile(r"no\s+fallback|no\s+rollback|ignore\s+(scaling|concurrency|failure|backpressure)", re.IGNORECASE),
        "Missing resilience (no fallback/rollback)",
    ),
]

# Negated mitigation ("no fallback", "without rollback") must not count as mitigation.
_NEGATED_MITIGATION_RE = re.compile(
    r"(?i)\b(no|without|lack(?:ing|s)?|missing|absent|zero|never)\b.{0,25}"
    r"(fallback|mitigat|redundan|isolat|modular|rollback|retry|retries|barrier|"
    r"worktree|blue.?green|canary|replica|quorum|graceful)",
)


def _has_real_mitigation(text: str) -> bool:
    cleaned = _NEGATED_MITIGATION_RE.sub("", text)
    return bool(_MITIGATION_RE.search(cleaned))


# Lines that explicitly negate secret storage must not trigger secret-leak rules.
_NEGATED_SECRET_LINE_RE = re.compile(r"(?i)\bno\b.{0,30}(secret|password|plaintext|credential|token)")

_DANGEROUS_COMMAND_RES = [
    (re.compile(r"\brm\s+-rf\b", re.IGNORECASE), "Destructive `rm -rf` operation"),
    (re.compile(r"\b(mkfs|dd\s+if=.*of=/dev)\b", re.IGNORECASE), "Disk-destructive command"),
    (re.compile(r"\bdrop\s+database\b", re.IGNORECASE), "Destructive `DROP DATABASE`"),
    (re.compile(r"\beval\s*\(", re.IGNORECASE), "Dynamic `eval()` execution"),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b", re.IGNORECASE), "Overly permissive `chmod 777`"),
    (re.compile(r"\bsudo\s+(rm|dd|mkfs|chmod)\b", re.IGNORECASE), "Privileged destructive `sudo` command"),
    (
        re.compile(r"(curl|wget)[^\n]*\|\s*(sh|bash|sudo)", re.IGNORECASE),
        "Piped remote-shell execution (curl|wget | sh)",
    ),
    (
        re.compile(r"disable.{0,20}auth|bypass.{0,20}auth|auth.{0,20}disabled", re.IGNORECASE),
        "Authentication bypass/disable",
    ),
    (
        re.compile(r"delete.{0,30}production|production.{0,30}delete", re.IGNORECASE),
        "Production deletion operation",
    ),
]

_SECRET_LEAK_RES = [
    (re.compile(r"sk-[A-Za-z0-9]{10,}"), "Embedded secret key material (`sk-...`)"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "Embedded AWS access key"),
    (re.compile(r"ghp_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]+"), "Embedded GitHub token"),
    (re.compile(r"xox[bap]-[A-Za-z0-9-]+"), "Embedded Slack token"),
    (re.compile(r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----"), "Embedded private key block"),
    (
        re.compile(r"(?i)(api[_-]?key|password|passwd|client[_-]?secret)\s*[:=]\s*['\"][^'\"]{3,}['\"]"),
        "Hardcoded credential assignment",
    ),
]

_TEST_SPEC_RE = re.compile(
    r"(pytest|npm test|cargo test|go test|unittest|jest|vitest|rspec|benchmark|"
    r"integration test|e2e|acceptance criteria|verification plan|automated test|"
    r"regression suite|manual verification checklist)",
    re.IGNORECASE,
)
_TEST_NEGATION_RE = re.compile(
    r"(no\s+(need\s+for\s+)?tests?|no\s+verification|skip\s+tests?|should just work|trust me)",
    re.IGNORECASE,
)


class HyperplanPipeline:
    """Executes multi-agent hostile plan audit."""

    def __init__(self, strict_acceptance_required: bool = True):
        self.strict_acceptance = strict_acceptance_required

    def review_plan(
        self,
        plan_title: str,
        plan_text: str,
        strict_acceptance_required: bool | None = None,
    ) -> HyperplanReport:
        verdicts: list[ReviewerVerdict] = []

        # Lens 1: Gap Analysis (plan-consultant)
        v1 = self._review_gaps(plan_text)
        verdicts.append(v1)

        # Lens 2: Architecture & Scalability (plan-reviewer)
        v2 = self._review_architecture(plan_text)
        verdicts.append(v2)

        # Lens 3: Security & Breaking Diffs (omo-code-reviewer)
        v3 = self._review_security(plan_text)
        verdicts.append(v3)

        # Lens 4: Acceptance Testability (omo-qa-executor)
        v4 = self._review_testability(plan_text)
        verdicts.append(v4)

        # Gatekeeper Consensus
        strict = self.strict_acceptance if strict_acceptance_required is None else strict_acceptance_required
        rejections = [v for v in verdicts if v.status == "REJECTED"]
        revisions = [v for v in verdicts if v.status == "NEEDS_REVISION"]

        if rejections or (strict and revisions):
            overall = "BLOCKED"
            summary = f"Plan blocked by {len(rejections)} rejection(s) and {len(revisions)} revision request(s)."
        else:
            overall = "APPROVED"
            summary = "Plan approved across all 4 adversarial review dimensions."

        return HyperplanReport(
            plan_title=plan_title,
            overall_status=overall,
            verdicts=verdicts,
            gatekeeper_summary=summary,
            plan_hash=hash_plan(plan_title, plan_text),
        )

    def _review_gaps(self, text: str) -> ReviewerVerdict:
        risks: list[str] = []
        missing: list[str] = []
        stripped = text.strip()
        has_prereq = bool(
            re.search(
                r"(?i)(prerequisite|dependency|dependencies|setup|environment|assumption|scope|objective)",
                text,
            )
        )
        has_steps = bool(re.search(r"(?i)(step|phase|task|milestone|wave|stage)", text))
        has_structure = text.count("##") >= 2 or text.count("\n-") >= 3 or text.count("\n*") >= 3
        has_design = bool(re.search(r"(?i)(architecture|security|modular|fallback|rollback)", text))
        has_verification = bool(_TEST_SPEC_RE.search(text))
        # Completeness over length: a compact plan covering the four review
        # dimensions (prereqs, structure/design, verification) is verifiable.
        dimensions = sum([has_prereq, has_steps or has_structure or has_design, has_verification])
        if not has_prereq:
            risks.append("Plan lacks explicit prerequisite or environment setup specification.")
            missing.append("explicit prerequisites / environment setup section")
        if len(stripped) < 120 and dimensions < 3:
            risks.append("Plan is too brief to verify scope, steps, and ownership.")
            missing.append("expanded scope and step breakdown")
        if not has_steps and not has_structure and not (has_design and has_verification):
            risks.append("Plan has no decomposed steps/phases to track and verify.")
            missing.append("decomposed steps or phases")

        status = "NEEDS_REVISION" if risks else "APPROVED"
        return ReviewerVerdict(
            reviewer_role="plan-consultant (Gap Analysis)",
            status=status,
            critique="Audited for scope completeness and unstated assumptions.",
            identified_risks=risks,
            missing_prerequisites=missing,
        )

    def _review_architecture(self, text: str) -> ReviewerVerdict:
        risks: list[str] = []
        worst = "APPROVED"

        for pattern, label in _ARCH_RISKS:
            if pattern.search(text):
                if _has_real_mitigation(text):
                    risks.append(f"{label} acknowledged — confirm mitigation (fallback/isolation/rollback) covers it.")
                    worst = "NEEDS_REVISION" if worst == "APPROVED" else worst
                else:
                    risks.append(f"Unmitigated {label.lower()} — add isolation/fallback or redesign.")
                    worst = "REJECTED"

        if not risks and len(text.strip()) > 400 and not _MODULARITY_RE.search(text):
            risks.append("Plan lacks modularity/isolation/fallback description for its size.")
            worst = "NEEDS_REVISION"

        return ReviewerVerdict(
            reviewer_role="plan-reviewer (Architecture Stress)",
            status=worst,
            critique="Evaluated component modularity and failure isolation. Mentioning a risk with mitigation requests revision; without mitigation rejects.",
            identified_risks=risks,
        )

    def _review_security(self, text: str) -> ReviewerVerdict:
        risks: list[str] = []
        for pattern, label in _DANGEROUS_COMMAND_RES:
            if pattern.search(text):
                # Production-delete needs a backup mention to be acceptable.
                if "production" in label.lower() and re.search(r"(?i)backup", text):
                    continue
                risks.append(f"Detected high-risk pattern: {label}.")
        for pattern, label in _SECRET_LEAK_RES:
            for line in text.splitlines():
                if _NEGATED_SECRET_LINE_RE.search(line):
                    continue
                if pattern.search(line):
                    risks.append(f"Detected credential exposure: {label}.")
                    break

        status = "REJECTED" if risks else "APPROVED"
        return ReviewerVerdict(
            reviewer_role="omo-code-reviewer (Security & Diff Risk)",
            status=status,
            critique="Audited plan for destructive commands, credential exposure, and blast radius.",
            identified_risks=risks,
        )

    def _review_testability(self, text: str) -> ReviewerVerdict:
        risks: list[str] = []
        # Check for genuine testing / evidence verification commands
        has_test_spec = bool(_TEST_SPEC_RE.search(text))
        has_negation = bool(_TEST_NEGATION_RE.search(text))

        if not has_test_spec or has_negation:
            risks.append("Plan contains zero verification or automated test commands (violates evidence doctrine).")

        status = "REJECTED" if risks else "APPROVED"
        return ReviewerVerdict(
            reviewer_role="omo-qa-executor (QA Acceptance Audit)",
            status=status,
            critique="Checked for executable test verification and evidence-capture criteria.",
            identified_risks=risks,
        )
