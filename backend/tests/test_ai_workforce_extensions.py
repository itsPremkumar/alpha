"""Comprehensive tests for AI Workforce OS Extensions:
1. Task Contracts and Definition of Done Gatekeeper
2. Automated Markdown ADR Generator
3. Dynamic Temporary Specialist Bot Lifecycle
4. Episodic Project Postmortem and RSI Heuristics Engine
5. Workforce Model and Cost Tier Router with Fallbacks
"""

import pytest
from pathlib import Path
import tempfile
import time

from agent_workspace.projects.contracts import (
    ContractGatekeeper,
    DefinitionOfDone,
    EvidenceReceipt,
    TaskContract,
)
from agent_workspace.projects.decisions import Decision, DecisionLog
from agent_workspace.projects.adr_generator import (
    generate_markdown_adr,
    sync_all_adrs,
    read_adr_markdown,
)
from agent_workspace.bots.ephemeral import EphemeralBotManager
from agent_workspace.projects.postmortem import ProjectPostmortemEngine
from agent_workspace.models.workforce_router import (
    WorkforceModelRouter,
    ModelTier,
    get_workforce_model_router,
)
from agent_workspace.projects.context_router import ThreeLevelContextRouter


def test_task_contract_gatekeeper(tmp_path: Path):
    contracts_file = tmp_path / "contracts.json"
    gatekeeper = ContractGatekeeper("proj_alpha", storage_path=contracts_file)

    # 1. Create contract with strict DoD
    dod = DefinitionOfDone(
        required_evidence=["tests_passed", "lint"],
        require_verifier_signoff=True,
    )
    contract = gatekeeper.create_contract(
        task_id="TASK-101",
        title="Implement OAuth2 Authentication Flow",
        assignee_bot="coder",
        verifier_bot="reviewer",
        definition_of_done=dod,
    )
    assert contract.status == "in_progress"
    assert contract.task_id == "TASK-101"

    # 2. Attempt completion without evidence -> Should fail & reject
    success, reasons = gatekeeper.verify_and_complete("TASK-101")
    assert not success
    assert len(reasons) >= 2
    c = gatekeeper.get_contract("TASK-101")
    assert c is not None
    assert c.status == "rejected"

    # 3. Attach partial evidence (only lint)
    gatekeeper.add_evidence(
        "TASK-101",
        EvidenceReceipt(kind="lint", reference="flake8-clean", verified_by="coder"),
    )
    success, reasons = gatekeeper.verify_and_complete("TASK-101")
    assert not success
    assert any("tests_passed" in r for r in reasons)

    # 4. Attach tests_passed evidence
    gatekeeper.add_evidence(
        "TASK-101",
        EvidenceReceipt(kind="tests_passed", reference="pytest-100-percent", verified_by="tester"),
    )

    # 5. Signoff with verifier_bot -> Should succeed
    success, reasons = gatekeeper.verify_and_complete(
        "TASK-101",
        verifier_bot="reviewer",
        verification_detail="Code review passed without objections.",
    )
    assert success
    assert reasons == []

    c_done = gatekeeper.get_contract("TASK-101")
    assert c_done is not None
    assert c_done.status == "done"
    assert len(c_done.evidence_receipts) == 3


def test_adr_markdown_generator(tmp_path: Path):
    output_dir = tmp_path / "adrs"

    decision = Decision(
        decision_id="ADR-042",
        title="Migrate SQLite Storage to PostgreSQL with pgvector",
        body="High-concurrency vector searches require a dedicated vector database layer.",
        reason="PostgreSQL pgvector provides unified transactions and vector indexing.",
        made_by="architect",
        approved_by="tech_lead",
        arch_version="2.4.0",
    )

    # 1. Generate markdown ADR
    adr_path = generate_markdown_adr(decision, "proj_beta", output_dir=output_dir)
    assert adr_path.exists()
    assert "ADR-042" in adr_path.name

    # 2. Read markdown and inspect contents
    content = adr_path.read_text(encoding="utf-8")
    assert "# ADR-042: Migrate SQLite Storage to PostgreSQL with pgvector" in content
    assert "- **Status**: Accepted" in content
    assert "- **Author**: @architect" in content
    assert "- **Approved By**: @tech_lead" in content
    assert "pgvector provides unified transactions" in content
    assert "All agents working on project `proj_beta` are bound by this decision" in content


def test_ephemeral_specialist_lifecycle(tmp_path: Path):
    storage_path = tmp_path / "ephemeral.json"
    mgr = EphemeralBotManager(storage_path=storage_path)

    # 1. Spawn a temporary specialist with 2-second TTL
    bot = mgr.spawn_specialist(
        domain="cuda_kernel_opt",
        prompt_objective="Optimize matrix multiplication fused kernels for Ampere GPUs.",
        ttl_seconds=2,
    )
    assert bot.name.startswith("spec_cuda_kernel_opt_")
    assert "cuda_kernel_opt" in bot.capabilities
    assert bot.department == "specialist"
    assert "Ampere GPUs" in bot.soul

    # 2. Verify active in manager
    active = mgr.list_active_specialists()
    assert any(s["bot_name"] == bot.name for s in active)

    # 3. Wait for TTL to expire
    time.sleep(2.1)
    expired = mgr.check_leases()
    assert bot.name in expired

    # 4. Check active list is now empty of this specialist
    active_after = mgr.list_active_specialists()
    assert not any(s["bot_name"] == bot.name for s in active_after)

    # 5. Test manual archive
    bot2 = mgr.spawn_specialist(
        domain="solidity_security",
        prompt_objective="Audit reentrancy vulnerabilities in Vault contract.",
        ttl_seconds=3600,
    )
    assert mgr.archive_specialist(bot2.name, reason="audit_completed")
    lease = mgr.get_lease(bot2.name)
    assert lease is not None
    assert lease.status == "archived"


def test_postmortem_heuristics_capture(tmp_path: Path):
    storage_path = tmp_path / "postmortems.json"
    engine = ProjectPostmortemEngine("proj_gamma", storage_path=storage_path)

    # Record a failure postmortem
    record = engine.analyze_failure(
        task_id="TASK-888",
        bot_name="coder",
        error_summary="Database transaction deadlock on concurrent user inserts",
        root_cause="Missing row-level lock ordering in checkout service",
        erroneous_assumptions=["Assumed transactions are automatically serialized"],
        preventative_rule="Always acquire database row locks in primary key sorted order.",
        sync_to_memory=True,
    )

    assert record.postmortem_id == "PM-001"
    assert record.applied_to_bot_memory
    assert record.applied_to_project_memory

    # Verify postmortems list
    pms = engine.list_postmortems()
    assert len(pms) == 1
    assert pms[0].task_id == "TASK-888"

    # Verify heuristics summary
    rules = engine.get_heuristics_summary()
    assert len(rules) == 1
    assert "sorted order" in rules[0]

    # Verify ThreeLevelContextRouter Level 1 & Level 2 memory contains the heuristic
    router = ThreeLevelContextRouter()
    bot_mem = router.get_bot_memory("coder")
    assert any("sorted order" in str(lesson) for lesson in bot_mem.get("learned_lessons", []))

    proj_mem = router.get_project_memory("proj_gamma")
    assert any("sorted order" in str(h) for h in proj_mem.get("failure_heuristics", []))


def test_workforce_model_routing():
    router = WorkforceModelRouter()

    # 1. Architecture task -> Frontier tier
    decision_arch = router.route_model("architecture", complexity="medium")
    assert decision_arch.tier == ModelTier.FRONTIER.value
    assert decision_arch.cost_tier == "$$$"
    assert decision_arch.primary_model in ["claude-3-7-sonnet", "o3-mini", "gpt-4o", "ollama/qwen3:32b"]
    assert len(decision_arch.fallback_chain) > 0

    # 2. Coding task -> Coding tier
    decision_coding = router.route_model("coding", complexity="medium")
    assert decision_coding.tier == ModelTier.CODING.value
    assert decision_coding.cost_tier == "$$"

    # 3. High complexity coding -> Upgraded to Frontier tier
    decision_hard_coding = router.route_model("coding", complexity="high")
    assert decision_hard_coding.tier == ModelTier.FRONTIER.value

    # 4. Low complexity coding -> Downgraded to Fast tier
    decision_simple_coding = router.route_model("coding", complexity="low")
    assert decision_simple_coding.tier == ModelTier.FAST.value
    assert decision_simple_coding.cost_tier == "$"

    # 5. Local mode override -> Local tier
    decision_local = router.route_model("coding", prefer_local=True)
    assert decision_local.tier == ModelTier.LOCAL.value
    assert decision_local.cost_tier == "free"

    # 6. Fallback model progression
    next_model = router.get_fallback_model("claude-3-7-sonnet", tier=ModelTier.FRONTIER)
    assert next_model == "o3-mini"
