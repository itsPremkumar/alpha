"""The PR body must not assert verification that never happened.

`synthesize_pr` runs no test suite, no linter and no audit council. It used to
print three hardcoded `- [x]` lines anyway, including "Automated unit and
regression test suite passed", so a reviewer reading a generated PR saw a green
checklist for work that had never been verified. These tests pin the honest
rendering: a checked box means a real evidence receipt exists.
"""

from pathlib import Path

from alpha.projects.contracts import ContractGatekeeper, EvidenceReceipt
from alpha.projects.pr_synthesizer import PRSynthesizer

_DIFF = """--- a/middleware.py
+++ b/middleware.py
@@ -10,3 +10,5 @@
+def check_role(user, role):
+    return user.role == role
"""


def _synth(gk: ContractGatekeeper, tmp_path: Path, task_id: str) -> str:
    """Synthesize against the *same* gatekeeper the test populated.

    Reusing one gatekeeper matters: the synthesizer reads the contract back out of
    the store, so a second instance over a different file would find no contract.
    """
    synth = PRSynthesizer("proj_honest", storage_dir=tmp_path / "prs", gatekeeper=gk)
    pkg = synth.synthesize_pr(task_id=task_id, patch_diff=_DIFF)
    return pkg.body_markdown


def test_checklist_is_unchecked_when_no_evidence_attached(tmp_path: Path):
    """The core bug: a generated PR claimed tests had passed without running any."""
    gk = ContractGatekeeper("proj_honest", storage_path=tmp_path / "c.json")
    gk.create_contract(task_id="TASK-BARE", title="Add role check", assignee_bot="coder")
    body = _synth(gk, tmp_path, "TASK-BARE")

    assert "- [x] Automated unit and regression test suite passed" not in body
    assert "No evidence receipts attached" in body
    # Unchecked boxes must be genuinely unchecked.
    assert "- [ ] Automated unit and regression test suite passed" in body
    assert "This PR is not verified" in body


def test_checklist_reflects_real_evidence_receipts(tmp_path: Path):
    """A checked box is earned by an attached receipt, and cites it."""
    gk = ContractGatekeeper("proj_honest", storage_path=tmp_path / "c2.json")
    contract = gk.create_contract(task_id="TASK-PROVEN", title="Add role check", assignee_bot="coder")
    contract.evidence_receipts.append(EvidenceReceipt(kind="tests_passed", reference="pytest-412-pass", verified_by="coder"))
    contract.evidence_receipts.append(EvidenceReceipt(kind="security", reference="AUDIT-SAFE-91", verified_by="auditor"))
    gk._save()

    body = _synth(gk, tmp_path, "TASK-PROVEN")
    assert "- [x] Automated unit and regression test suite passed" in body
    assert "pytest-412-pass" in body
    assert "- [x] Static AST & secret scanning passed (Audit Council)" in body
    assert "AUDIT-SAFE-91" in body
    # A receipt for tests does not certify lint.
    assert "- [ ] Linting passed" in body


def test_missing_required_evidence_is_called_out(tmp_path: Path):
    """Required-but-absent evidence must be named, not quietly ticked."""
    gk = ContractGatekeeper("proj_honest", storage_path=tmp_path / "c3.json")
    contract = gk.create_contract(task_id="TASK-PARTIAL", title="Add role check", assignee_bot="coder")
    contract.evidence_receipts.append(EvidenceReceipt(kind="tests_passed", reference="pytest-1-pass", verified_by="coder"))
    gk._save()

    body = _synth(gk, tmp_path, "TASK-PARTIAL")
    assert "Missing required evidence" in body
    assert "`lint`" in body
    assert "must not be merged" in body
    # The one receipt that exists is still honoured.
    assert "- [x] Automated unit and regression test suite passed" in body


def test_evidence_section_does_not_claim_autonomous_verification(tmp_path: Path):
    """The receipts section previously said 'verified in task worktree' for nothing."""
    gk = ContractGatekeeper("proj_honest", storage_path=tmp_path / "c4.json")
    gk.create_contract(task_id="TASK-NOWORK", title="Add role check", assignee_bot="coder")
    body = _synth(gk, tmp_path, "TASK-NOWORK")
    assert "verified in task worktree" not in body
    assert "were **not** confirmed" in body


def test_missing_contract_still_renders_a_checklist(tmp_path: Path):
    """No contract at all must not crash and must not fabricate evidence."""
    gk = ContractGatekeeper("proj_honest", storage_path=tmp_path / "c5.json")
    synth = PRSynthesizer("proj_honest", storage_dir=tmp_path / "prs", gatekeeper=gk)
    pkg = synth.synthesize_pr(task_id="TASK-UNKNOWN", patch_diff=_DIFF)
    assert "## 4. Verification & Testing" in pkg.body_markdown
    assert "Nothing below has been" in pkg.body_markdown
    assert "- [x]" not in pkg.body_markdown


def test_real_diff_still_surfaces(tmp_path: Path):
    """The honesty fix must not cost the package its actual content."""
    gk = ContractGatekeeper("proj_honest", storage_path=tmp_path / "c6.json")
    gk.create_contract(task_id="TASK-CONTENT", title="Add role check", assignee_bot="coder")
    body = _synth(gk, tmp_path, "TASK-CONTENT")
    assert "def check_role" in body
    assert "```diff" in body
