"""Automated Semantic PR & Changelog Synthesizer.

Transforms completed worktree deliverables, verified task contracts, and ADR records
into production-ready Pull Request descriptions, conventional commit messages,
and formatted git patch packages.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.projects.contracts import get_contract_gatekeeper
from alpha.projects.decisions import get_decision_log
from alpha.projects.events import get_event_bus

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _prs_path(project_id: str) -> Path:
    return runtime_home() / "projects" / project_id / "prs"


@dataclass
class PullRequestPackage:
    """Consolidated Pull Request documentation and patch deliverable."""

    pr_id: str
    project_id: str
    task_id: str
    title: str
    branch_name: str
    conventional_commit: str
    body_markdown: str
    patch_diff: str
    author_bot: str = "coder"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PullRequestPackage:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)

    def export_patch(self, output_path: Path) -> Path:
        """Write the raw git diff patch to a file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self.patch_diff, encoding="utf-8")
        return output_path


def infer_conventional_commit(title: str, diff_content: str) -> str:
    """Derive standard conventional commit message from title and code modifications."""
    t_lower = title.lower()
    prefix = "feat"
    if any(k in t_lower for k in ("fix", "bug", "issue", "patch")):
        prefix = "fix"
    elif any(k in t_lower for k in ("refactor", "cleanup", "reorganize")):
        prefix = "refactor"
    elif any(k in t_lower for k in ("test", "qa", "verify")):
        prefix = "test"
    elif any(k in t_lower for k in ("doc", "guide", "readme")):
        prefix = "docs"

    clean_title = re.sub(r"^[a-zA-Z]+\s*:\s*", "", title).strip()
    return f"{prefix}: {clean_title}"


#: Evidence kind -> the checklist line it satisfies. Kept as data so the mapping
#: between a receipt and a claim is reviewable in one place, and so a new receipt
#: kind is added to the gate rather than to a string literal.
_CHECKLIST_FOR_EVIDENCE_KIND: dict[str, str] = {
    "security": "Static AST & secret scanning passed (Audit Council)",
    "tests_passed": "Automated unit and regression test suite passed",
    "lint": "Linting passed",
    "typecheck": "Type checking passed",
    "integration": "Integration suite passed",
    "commit": "Change is committed",
}


def _verification_checklist(contract: Any | None) -> list[str]:
    """Render the verification checklist from real evidence receipts.

    A line is checked only when a receipt of the corresponding kind exists on the
    task contract. Anything else is rendered unchecked with the reason it is
    unchecked, so an incomplete PR reads as incomplete instead of asserting work
    that was never performed.
    """
    receipts = list(getattr(contract, "evidence_receipts", []) or [])
    present: dict[str, list[Any]] = {}
    for r in receipts:
        kind = getattr(r, "kind", None) or (r.get("kind") if isinstance(r, dict) else None)
        if kind:
            present.setdefault(str(kind), []).append(r)

    required: list[str] = []
    dod = getattr(contract, "definition_of_done", None)
    if dod is not None:
        required = [str(k) for k in (getattr(dod, "required_evidence", None) or [])]

    # The contract's own required evidence drives the list, so the PR body cannot
    # quietly assert a check the task never asked for.
    kinds = list(dict.fromkeys(required + list(_CHECKLIST_FOR_EVIDENCE_KIND)))
    if not required:
        kinds = list(_CHECKLIST_FOR_EVIDENCE_KIND)

    lines: list[str] = []
    if not receipts:
        lines.append(
            "_No evidence receipts are attached to this task. Nothing below has been "
            "verified: this synthesizer does not run tests, linters or audits itself._"
        )
    for kind in kinds:
        label = _CHECKLIST_FOR_EVIDENCE_KIND.get(kind, f"Evidence supplied: {kind}")
        matched = present.get(kind)
        if matched:
            refs = ", ".join(
                f"`{getattr(r, 'reference', None) or (r.get('reference', '') if isinstance(r, dict) else '')}`"
                for r in matched
            )
            lines.append(f"- [x] {label} — evidence: {refs}")
        elif kind in required:
            lines.append(f"- [ ] {label} — **required by the task contract, no evidence attached**")
        else:
            lines.append(f"- [ ] {label} — no evidence attached")

    if required:
        outstanding = [k for k in required if k not in present]
        if outstanding:
            lines.append("")
            lines.append(
                "> **This PR is not verified.** Missing required evidence: "
                + ", ".join(f"`{k}`" for k in outstanding)
                + ". It must not be merged on the basis of this document alone."
            )
    return lines


class PRSynthesizer:
    """Synthesizes structured, audited Pull Requests from agent deliverables."""

    def __init__(self, project_id: str, storage_dir: Path | None = None, gatekeeper: Any = None):
        self.project_id = project_id
        self._dir = storage_dir or _prs_path(project_id)
        self._gk = gatekeeper
        self._lock = threading.Lock()

    def synthesize_pr(
        self,
        task_id: str,
        patch_diff: str,
        *,
        author_bot: str = "coder",
        branch_name: str | None = None,
        custom_summary: str | None = None,
        gatekeeper: Any = None,
    ) -> PullRequestPackage:
        """Assemble pull request package with verification evidence and ADR citations."""
        pr_id = f"PR-{uuid.uuid4().hex[:8].upper()}"
        branch = branch_name or f"task/{task_id.lower().replace('_', '-')}"

        # 1. Fetch Task Contract & Evidence Receipts
        gk = gatekeeper or self._gk or get_contract_gatekeeper(self.project_id)
        contract = gk.get_contract(task_id)
        task_title = contract.title if contract else f"Deliverable for {task_id}"
        commit_msg = infer_conventional_commit(task_title, patch_diff)

        # 2. Fetch Relevant ADRs
        dec_log = get_decision_log(self.project_id)
        adrs = dec_log.list()[-3:]

        # 3. Assemble Markdown Body
        lines = [
            f"# Pull Request: {task_title}",
            f"\n> **Task ID**: `{task_id}` | **Author**: @{author_bot} | **Target Branch**: `{branch}`",
            "\n## 1. Summary of Changes",
            custom_summary or f"Implements obligations defined in task contract `{task_id}`.",
            "\n### Key Code Modifications",
            f"```diff\n{patch_diff[:1500]}\n```" if patch_diff else "_No file changes recorded._",
        ]

        if len(patch_diff) > 1500:
            lines.append(f"\n_... [{len(patch_diff) - 1500} bytes of patch diff truncated for brevity]_")

        # 4. Evidence Receipts Section
        lines.append("\n## 2. Definition of Done & Quality Evidence")
        if contract and contract.evidence_receipts:
            for e in contract.evidence_receipts:
                lines.append(f"- **[Verified: {e.kind}]** `{e.reference}` certified by @{e.verified_by} *(at {e.timestamp})*")
        else:
            # Previously "Automated tests and verification verified in task worktree",
            # which asserted a verification that this function never performed.
            lines.append(
                "- _No evidence receipts attached to this task. Tests and verification "
                "were **not** confirmed by this synthesizer; see section 4._"
            )

        # 5. Linked Architectural Decisions
        lines.append("\n## 3. Architectural Alignment & ADRs")
        if adrs:
            for d in adrs:
                lines.append(f"- **{d.decision_id}**: {d.title} *(Ratified by @{d.made_by})*")
        else:
            lines.append("- _No conflicting architectural decisions recorded._")

        # 6. Verification Checklist
        #
        # This section previously printed three hardcoded `- [x]` lines, including
        # "Automated unit and regression test suite passed", for every PR regardless
        # of whether anything had been run — `synthesize_pr` invokes no test runner,
        # no linter and no audit council. A checked box in a PR body is a claim a
        # human reviewer acts on, so each line is now derived from the contract's
        # real evidence receipts: a checked box means a receipt of that kind exists.
        lines.append("\n## 4. Verification & Testing")
        lines.extend(_verification_checklist(contract))

        body_markdown = "\n".join(lines) + "\n"

        pkg = PullRequestPackage(
            pr_id=pr_id,
            project_id=self.project_id,
            task_id=task_id,
            title=task_title,
            branch_name=branch,
            conventional_commit=commit_msg,
            body_markdown=body_markdown,
            patch_diff=patch_diff,
            author_bot=author_bot,
        )

        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            save_path = self._dir / f"{pr_id}.json"
            save_path.write_text(json.dumps(pkg.to_dict(), indent=2), encoding="utf-8")

        get_event_bus(self.project_id).emit(
            "deployment_started",
            author_bot,
            {"pr_id": pr_id, "task_id": task_id, "branch": branch, "commit": commit_msg},
        )

        logger.info("Synthesized Pull Request %s for task %s", pr_id, task_id)
        return pkg

    def get_pr(self, pr_id: str) -> PullRequestPackage | None:
        p = self._dir / f"{pr_id}.json"
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return PullRequestPackage.from_dict(json.load(f))
        except Exception:
            return None


def get_pr_synthesizer(project_id: str) -> PRSynthesizer:
    return PRSynthesizer(project_id)
