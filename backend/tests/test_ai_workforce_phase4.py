"""Comprehensive tests for AI Workforce OS Phase 4:
1. Dynamic Token Burn-Rate & Cost Governor
2. Automated Semantic PR & Changelog Synthesizer
3. Multi-Agent Async Standup & Blocker Detection Engine
4. Multi-Perspective Red-Team & Adversary Deliberator
5. Reputation-Aware Task Auction & Bidding Matchmaker
"""

import pytest
from pathlib import Path
import tempfile

from alpha.models.cost_governor import (
    CostGovernor,
    BudgetConfig,
    TokenUsageRecord,
)
from alpha.projects.pr_synthesizer import (
    PRSynthesizer,
    PullRequestPackage,
    infer_conventional_commit,
)
from alpha.projects.standup_engine import (
    StandupEngine,
    StandupReport,
)
from alpha.deliberation.adversary_deliberator import (
    AdversaryDeliberator,
    AdversaryCritique,
)
from alpha.projects.auction_engine import (
    AuctionEngine,
    TaskBid,
)
from alpha.projects.contracts import (
    ContractGatekeeper,
    EvidenceReceipt,
)
from alpha.projects.locks import get_lock_manager
from alpha.bots.registry import get_bot_registry


def test_cost_governor_budget_tracking(tmp_path: Path):
    storage = tmp_path / "costs.json"
    governor = CostGovernor(storage_path=storage)

    # 1. Configure custom budget ($5.00 daily limit, 85% threshold = $4.25)
    config = BudgetConfig(daily_budget_usd=5.0, alert_threshold_ratio=0.85)
    governor.set_project_budget("proj_hotel", config)
    assert governor.get_project_budget("proj_hotel").daily_budget_usd == 5.0

    # 2. Record standard usage for Frontier model (claude-3-7-sonnet)
    # 50,000 in ($0.15) + 20,000 out ($0.30) = $0.45
    rec1 = governor.record_usage(
        project_id="proj_hotel",
        bot_name="architect",
        model_name="claude-3-7-sonnet",
        input_tokens=50_000,
        output_tokens=20_000,
    )
    assert rec1.cost_usd > 0.4
    assert governor.get_project_spend("proj_hotel") == rec1.cost_usd

    # 3. Record heavy usage pushing spend past 85% ($4.25)
    # 250,000 in ($0.75) + 250,000 out ($3.75) = $4.50
    rec2 = governor.record_usage(
        project_id="proj_hotel",
        bot_name="coder",
        model_name="claude-3-7-sonnet",
        input_tokens=250_000,
        output_tokens=250_000,
    )
    total_spend = governor.get_project_spend("proj_hotel")
    assert total_spend >= 4.25

    # 4. Check project summary
    summary = governor.get_project_summary("proj_hotel")
    assert summary["project_id"] == "proj_hotel"
    assert summary["current_spend_24h"] == total_spend
    assert "architect" in summary["bot_breakdown"]
    assert "coder" in summary["bot_breakdown"]


def test_pr_synthesizer_generation(tmp_path: Path):
    prs_dir = tmp_path / "prs"
    gk = ContractGatekeeper("proj_india", storage_path=tmp_path / "contracts.json")
    synthesizer = PRSynthesizer("proj_india", storage_dir=prs_dir, gatekeeper=gk)

    # 1. Set up contract with evidence
    gk.create_contract(
        task_id="TASK-PR-10",
        title="Add user role authorization middleware",
        assignee_bot="coder",
    )
    gk.add_evidence(
        "TASK-PR-10",
        EvidenceReceipt(kind="tests_passed", reference="pytest-100-pass", verified_by="tester"),
    )
    gk.add_evidence(
        "TASK-PR-10",
        EvidenceReceipt(kind="security", reference="AUDIT-SAFE", verified_by="audit_council"),
    )

    diff_content = """--- a/middleware.py
+++ b/middleware.py
@@ -10,3 +10,12 @@
+def check_role(user, role):
+    if user.role != role:
+        raise Forbidden()
+    return True
"""

    # 2. Synthesize PR
    pkg = synthesizer.synthesize_pr(
        task_id="TASK-PR-10",
        patch_diff=diff_content,
        author_bot="coder",
        custom_summary="Adds role authorization middleware with forbidden exception.",
    )

    assert pkg.pr_id.startswith("PR-")
    assert pkg.task_id == "TASK-PR-10"
    assert pkg.conventional_commit.startswith("feat:")
    assert "pytest-100-pass" in pkg.body_markdown
    assert "AUDIT-SAFE" in pkg.body_markdown
    assert "def check_role" in pkg.body_markdown

    # 3. Export patch
    patch_file = tmp_path / "deliverable.patch"
    pkg.export_patch(patch_file)
    assert patch_file.exists()
    assert "Forbidden()" in patch_file.read_text(encoding="utf-8")


def test_standup_engine_aggregation(tmp_path: Path):
    gk = ContractGatekeeper("proj_juliet", storage_path=tmp_path / "contracts.json")
    engine = StandupEngine("proj_juliet", gatekeeper=gk)

    # 1. Set up contracts
    c1 = gk.create_contract(task_id="TASK-DONE-1", title="Database schema v1", assignee_bot="coder")
    c1.status = "done"
    gk._save()

    c2 = gk.create_contract(task_id="TASK-WORK-2", title="Payment webhook handler", assignee_bot="developer")
    c2.status = "in_progress"
    gk._save()

    # 2. Set up lock
    lock_mgr = get_lock_manager()
    lock_mgr.acquire(
        project_id="proj_juliet",
        owner_bot="coder",
        scope="file",
        path="src/payments.py",
        ttl_seconds=3600,
    )

    # 3. Generate standup briefing
    report = engine.generate_standup(stagnation_threshold_minutes=60)
    assert report.project_id == "proj_juliet"
    assert any(t["task_id"] == "TASK-DONE-1" for t in report.completed_tasks)
    assert any(t["task_id"] == "TASK-WORK-2" for t in report.in_progress_tasks)
    assert any(l["path"] == "src/payments.py" for l in report.active_locks)

    md = report.render_markdown()
    assert "# Async Standup Briefing" in md
    assert "Database schema v1" in md
    assert "src/payments.py" in md


def test_adversary_deliberator_stress_test():
    deliberator = AdversaryDeliberator()

    # Highly optimistic and vulnerable proposal text
    flawed_proposal = """
# Payment Ingestion Pipeline
This design is 100% reliable and foolproof. It processes incoming transactions in a tight loop.
No locks are needed because memory is fast. Once written, it directly returns success.
"""

    critique = deliberator.stress_test_design("Payment Ingestion", flawed_proposal)
    assert critique.sycophancy_flagged is True
    assert len(critique.attack_vectors) >= 2
    assert any(v.category == "concurrency" for v in critique.attack_vectors)
    assert any(v.category == "fault_tolerance" for v in critique.attack_vectors)
    assert critique.risk_score > 0.5
    assert len(critique.recommendations) >= 2


def test_auction_engine_bidding_match(tmp_path: Path):
    engine = AuctionEngine("proj_kilo")
    registry = get_bot_registry()

    # Solicit bids for a Python backend engineering task
    bids = engine.solicit_bids(
        task_id="TASK-AUC-1",
        required_capabilities=["python", "coding"],
        candidate_bots=["coder", "researcher", "reviewer"],
    )

    assert len(bids) == 3
    # coder has python/coding capabilities and should outscore researcher
    coder_bid = next(b for b in bids if b.bot_name == "coder")
    researcher_bid = next(b for b in bids if b.bot_name == "researcher")
    assert coder_bid.capability_score >= researcher_bid.capability_score
    assert coder_bid.bid_score >= researcher_bid.bid_score

    # Award the task contract
    winner, contract = engine.evaluate_and_award(
        task_id="TASK-AUC-1",
        required_capabilities=["python", "coding"],
        candidate_bots=["coder", "researcher"],
    )
    assert winner is not None
    assert winner.bot_name == "coder"
    assert contract is not None
    assert contract.assignee_bot == "coder"
    assert contract.status == "in_progress"
