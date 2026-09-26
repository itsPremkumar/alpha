"""Comprehensive Unit & Integration Test Suite for Autonomous AI Software Enterprise."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.enterprise import (
    EnterpriseDepartment,
    EnterpriseHeartbeatCoordinator,
    EnterpriseHierarchyEngine,
    EnterpriseRFCProtocol,
    EnterpriseTelemetry,
    MissionToSprintPipeline,
    QualityCouncilQuorumEngine,
    get_discovery_and_optimization_engine,
)
from alpha.enterprise.governance import DepartmentTokenTreasury
from app.gateway.routers.enterprise import (
    gateway_router as enterprise_gateway_router,
)
from app.gateway.routers.enterprise import (
    router as enterprise_router,
)

# ============================================================================
# 1. Dynamic Enterprise Hierarchy & C-Suite Swarm Tests
# ============================================================================


def test_enterprise_csuite_and_departments():
    hierarchy = EnterpriseHierarchyEngine()
    csuite = hierarchy.get_csuite()

    # 4 C-Suite roles verified
    assert "bot-ceo" in csuite
    assert "bot-cto" in csuite
    assert "bot-cpo" in csuite
    assert "bot-ciso" in csuite

    assert csuite["bot-ceo"].title == "Executive Director (CEO)"
    assert csuite["bot-cto"].title == "Lead Architect (CTO)"
    assert csuite["bot-cpo"].title == "Product & Market Strategist (CPO)"
    assert csuite["bot-ciso"].title == "Quality & Security Director (CISO)"

    # 5 Departments synthesized
    depts = hierarchy.get_departments()
    assert EnterpriseDepartment.ENGINEERING.value in depts
    assert EnterpriseDepartment.ARCHITECTURE.value in depts
    assert EnterpriseDepartment.SECURITY.value in depts
    assert EnterpriseDepartment.PERFORMANCE.value in depts
    assert EnterpriseDepartment.DOCUMENTATION.value in depts

    # Capability contract tests
    assert hierarchy.verify_capability("bot-ciso", "ast_boundary_scan")
    assert hierarchy.verify_authority("bot-cto", "architecture_sign_off")
    assert hierarchy.verify_authority("bot-ciso", "security_sign_off")

    # Dynamic sub-department synthesis test
    new_dept = hierarchy.synthesize_custom_subdepartment(
        name="Agent Meta-Compiler Lab",
        department_key="meta-compiler",
        lead_bot_name="bot-compiler-lead",
        lead_title="Meta-Compiler Lead",
        capabilities=["ast_transformation", "bytecode_tuning"],
    )
    assert new_dept.dept_id == "dept-meta-compiler"
    assert hierarchy.get_node("bot-compiler-lead") is not None


# ============================================================================
# 2. Mission-to-Sprint Pipeline & Dynamic DAG Sprint Tests
# ============================================================================


def test_mission_to_sprint_pipeline_and_dag_execution():
    pipeline = MissionToSprintPipeline()
    mission_id = "msn-ai-enterprise-test"
    objective = "Build next-generation self-improving agent runtime with holdout benchmark guarantees."

    # 1. Mission Decomposition
    epics = pipeline.decompose_strategic_mission(mission_id=mission_id, objective=objective)
    assert len(epics) == 5
    dept_targets = {e.target_department for e in epics}
    assert "architecture" in dept_targets
    assert "engineering" in dept_targets
    assert "security" in dept_targets

    # 2. Technical Spec Formulation with Definition of Done (DoD)
    spec = pipeline.synthesize_technical_spec(epics[0].epic_id)
    assert spec.architect_bot == "bot-cto"
    assert len(spec.requirements) >= 3
    assert len(spec.definition_of_done) >= 3

    # 3. Dynamic DAG Sprint Compilation
    sprint = pipeline.compile_dynamic_dag_sprint(spec.spec_id)
    assert len(sprint.tasks) == 6
    assert len(sprint.topology_layers) == 4
    assert sprint.status == "active"
    assert sprint.progress_percent == 0.0

    # 4. Step DAG Sprint Execution & Dependency Enforcement
    step1 = pipeline.step_sprint_dag(sprint.sprint_id)
    assert step1["completed_tasks_count"] >= 1
    assert step1["progress_percent"] > 0.0

    # Continue stepping until completion
    for _ in range(5):
        pipeline.step_sprint_dag(sprint.sprint_id)

    assert sprint.status == "completed"
    assert sprint.progress_percent == 100.0


# ============================================================================
# 3. Blackboard & Cross-Department RFC Protocol with Epistemic Debate Tests
# ============================================================================


def test_rfc_consensus_gating_and_epistemic_debate():
    rfc_engine = EnterpriseRFCProtocol()

    # 1. Propose RFC
    rfc = rfc_engine.create_rfc(
        title="RFC-010: Scoped Subagent Lease Fencing",
        author_bot="bot-backend-swe",
        department="engineering",
        summary="Introduce strict TTL lease fences for child workers to prevent resource leakage.",
        proposal_content="Add lease fence token passed to child processes with heartbeat revocation.",
        affected_departments=["engineering", "architecture", "security"],
    )
    assert rfc.status.value == "under_review"
    assert not rfc.gating_passed

    # 2. Submit Debate Arguments
    arg1 = rfc_engine.submit_debate_argument(
        rfc_id=rfc.rfc_id,
        speaker_bot="bot-system-architect",
        department="architecture",
        stance="pro",
        claim="Lease fences eliminate orphan zombie processes across worktrees.",
        evidence="Observed 100% reclamation of sandbox locks under lease test benchmarks.",
        epistemic_weight=0.9,
    )
    assert arg1.claim != ""
    assert rfc.status.value == "debating"

    # 3. Multi-Agent Review & Consensus Calculation
    rfc_engine.submit_review(
        rfc_id=rfc.rfc_id,
        reviewer_bot="bot-cto",
        department="architecture",
        verdict="approve",
        argument="Architecturally sound and backwards-compatible with lease API.",
        epistemic_confidence=0.95,
    )
    rfc_engine.submit_review(
        rfc_id=rfc.rfc_id,
        reviewer_bot="bot-ciso",
        department="security",
        verdict="approve",
        argument="Security boundary integrity validated. No permission leaks.",
        epistemic_confidence=0.90,
    )

    # 4. Verify Gating Passed
    passed, score, reason = rfc_engine.evaluate_consensus_gating(rfc.rfc_id)
    assert passed is True
    assert score >= 0.75
    assert rfc.gating_passed is True
    assert rfc.status.value == "approved"


def test_rfc_leadership_veto_blocks_gating():
    rfc_engine = EnterpriseRFCProtocol()
    rfc = rfc_engine.create_rfc(
        title="RFC-099: Disable AST Scanning to Accelerate Speed",
        author_bot="bot-backend-swe",
        department="engineering",
        summary="Proposal to remove AST scan to save 5ms.",
        proposal_content="Disable ast.parse in cyclic loop.",
        affected_departments=["security", "engineering"],
    )
    # CISO issues veto
    rfc_engine.submit_review(
        rfc_id=rfc.rfc_id,
        reviewer_bot="bot-ciso",
        department="security",
        verdict="reject",
        argument="CRITICAL VETO: Disabling AST scanning violates Zero-Trust Astra security boundaries.",
        epistemic_confidence=1.0,
    )
    passed, score, reason = rfc_engine.evaluate_consensus_gating(rfc.rfc_id)
    assert passed is False
    assert rfc.gating_passed is False
    assert "leadership veto" in reason.lower()


# ============================================================================
# 4. Department Token Treasury & Fiscal Governance Tests
# ============================================================================


def test_department_treasury_burn_and_circuit_breaker():
    treasury = DepartmentTokenTreasury()

    alloc = treasury.get_allocation("dept-engineering")
    assert alloc is not None
    initial_balance = alloc.balance_tokens

    # 1. Normal Token Burn
    alloc, tripped = treasury.record_token_burn("dept-engineering", tokens_burned=5000, tasks_completed=2)
    assert not tripped
    assert not alloc.circuit_breaker_active
    assert alloc.balance_tokens == initial_balance - 5000
    assert alloc.roi_velocity > 0

    # 2. Extreme Spike Burn to Trip Circuit Breaker (> 80,000 TPM)
    alloc, tripped = treasury.record_token_burn("dept-engineering", tokens_burned=25000, tasks_completed=1)
    assert alloc.circuit_breaker_active is True
    assert tripped is True
    assert not treasury.can_spend_tokens("dept-engineering", 1000)

    # 3. Reset Circuit Breaker with New Safe Threshold
    reset_alloc = treasury.reset_circuit_breaker("dept-engineering", new_threshold_tpm=150000.0)
    assert reset_alloc.circuit_breaker_active is False
    assert treasury.can_spend_tokens("dept-engineering", 1000)


# ============================================================================
# 5. Quality Council Quorum & Multi-Sig Promotion Tests
# ============================================================================


def test_council_starts_without_invented_active_release():
    council = QualityCouncilQuorumEngine()
    assert council.list_releases() == []
    assert council.get_active_release() is None
    with pytest.raises(KeyError):
        council.run_holdout_benchmark("missing")
    with pytest.raises(KeyError):
        council.sign_release("missing", "CTO_ARCH", "bot-cto")
    with pytest.raises(KeyError):
        council.promote_release_zero_downtime("missing")


def test_council_holdout_benchmark_and_3_signature_release():
    council = QualityCouncilQuorumEngine()

    # 1. Stage Candidate Release
    candidate = council.stage_candidate_release(
        version="v2.2.0",
        component="enterprise-core",
        description="Release adding dynamic C-suite swarm and epistemic RFC consensus.",
        diff_content="git diff unified content for v2.2.0 release candidate",
    )
    assert candidate.status == "staged"
    assert candidate.evidence_kind == "unknown"
    with pytest.raises(ValueError, match="Invalid signatory role"):
        council.sign_release(candidate.release_id, "INVALID", "untrusted")

    with pytest.raises(PermissionError, match="deployment adapter"):
        council.promote_release_zero_downtime(candidate.release_id)

    score = council.run_holdout_benchmark(candidate.release_id)
    assert isinstance(score, float)
    assert score >= 90.0
    assert candidate.holdout_passed is False
    assert candidate.preview_holdout_passed is True
    assert candidate.signatures == []
    assert candidate.evidence_kind == "simulated"

    for role, bot in [("CTO_ARCH", "bot-cto"), ("CISO_ASTRA", "bot-ciso"), ("SWE_BENCHMARK", "bot-perf-lead")]:
        before = candidate.model_dump()
        with pytest.raises(PermissionError, match="trusted signing authority"):
            council.sign_release(candidate.release_id, role, bot)
        assert candidate.model_dump() == before

    assert candidate.status == "preview"
    with pytest.raises(PermissionError):
        council.promote_release_zero_downtime(candidate.release_id)
    assert candidate.promoted_at is None
    assert candidate.deployed is False
    assert council.get_active_release("enterprise-core") is None
    payload = candidate.model_dump(mode="json")
    from alpha.enterprise.council import PreviewReleaseCandidate

    assert PreviewReleaseCandidate.model_validate(payload).model_dump(mode="json") == payload


# ============================================================================
# 6. Cyclic Heartbeat & Continuous Telemetry Tests
# ============================================================================


def test_enterprise_heartbeat_coordinator_and_telemetry():
    coordinator = EnterpriseHeartbeatCoordinator()

    # Execute cyclic heartbeat
    result = coordinator.step_heartbeat_cycle()
    assert result["cycle"] >= 1
    assert "system_latency_p95_ms" in result
    assert "security_score" in result
    assert result["stagnation_status"] == "nominal"

    # Telemetry roll-up
    telemetry = coordinator.get_telemetry()
    assert telemetry.heartbeat_cycle >= 1
    assert telemetry.departments_count == 5
    assert telemetry.active_workers_count >= 10
    assert telemetry.security_posture_score >= 90.0


def test_enterprise_telemetry_defaults_do_not_fabricate_measurements():
    """Honesty pins: a fresh EnterpriseTelemetry invents no org size or latency.

    The old defaults (5 departments / 15 workers / 42.0ms p95) were fabricated
    on any fresh construction; counts are 0 and latency is None until
    get_telemetry() overwrites them with real values.
    """
    telemetry = EnterpriseTelemetry()
    assert telemetry.system_latency_p95_ms is None
    assert telemetry.departments_count == 0
    assert telemetry.active_workers_count == 0


# ============================================================================
# 7. Gateway REST API Integration Tests
# ============================================================================


def test_enterprise_gateway_api():
    app = FastAPI()
    app.include_router(enterprise_router)
    client = TestClient(app)

    # 1. Hierarchy endpoint
    res_hier = client.get("/api/enterprise/hierarchy")
    assert res_hier.status_code == 200
    hier_data = res_hier.json()
    assert "departments" in hier_data
    assert len(hier_data["departments"]) >= 5

    # 2. CSuite endpoint
    res_csuite = client.get("/api/enterprise/csuite")
    assert res_csuite.status_code == 200
    assert "bot-ceo" in res_csuite.json()["csuite"]

    # 3. RFC endpoints
    res_rfcs = client.get("/api/enterprise/rfcs")
    assert res_rfcs.status_code == 200
    assert len(res_rfcs.json()) >= 2

    # Create RFC via API
    res_create_rfc = client.post(
        "/api/enterprise/rfcs",
        json={
            "title": "RFC-020: Streaming Telemetry for War Room",
            "author_bot": "bot-frontend-swe",
            "department": "engineering",
            "summary": "Implement Server-Sent Events for real-time heartbeat updates.",
            "proposal_content": "FastAPI SSE endpoint mounted at /api/enterprise/stream.",
            "affected_departments": ["engineering", "performance"],
        },
    )
    assert res_create_rfc.status_code == 201
    created_rfc = res_create_rfc.json()
    rfc_id = created_rfc["rfc_id"]

    # Review via API
    res_rev = client.post(
        f"/api/enterprise/rfcs/{rfc_id}/review",
        json={
            "reviewer_bot": "bot-perf-lead",
            "department": "performance",
            "verdict": "approve",
            "argument": "Streaming eliminates 1000ms polling latency with minimal memory footprint.",
            "epistemic_confidence": 0.92,
        },
    )
    assert res_rev.status_code == 201

    # 4. Treasury endpoints
    res_treasury = client.get("/api/enterprise/treasury")
    assert res_treasury.status_code == 200
    assert "total_allocated_tokens" in res_treasury.json()

    # Allocate tokens
    res_alloc = client.post(
        "/api/enterprise/treasury/allocate",
        json={"dept_id": "dept-engineering", "tokens": 50000},
    )
    assert res_alloc.status_code == 200
    assert res_alloc.json()["allocated_tokens"] > 50000

    # 5. Council releases
    res_rels = client.get("/api/enterprise/council/releases")
    assert res_rels.status_code == 200
    assert isinstance(res_rels.json(), list)
    assert all(release["status"] != "promoted_active" for release in res_rels.json())

    # 6. Heartbeat and telemetry
    res_hb = client.post("/api/enterprise/heartbeat")
    assert res_hb.status_code == 200
    assert "cycle" in res_hb.json()

    res_telemetry = client.get("/api/enterprise/telemetry")
    assert res_telemetry.status_code == 200
    assert res_telemetry.json()["departments_count"] == 5


# ============================================================================
# 8. Advanced Edge Case Tests
# ============================================================================


def test_holdout_preview_clears_stale_quorum():
    council = QualityCouncilQuorumEngine()
    candidate = council.stage_candidate_release(
        version="v2.3.0",
        component="enterprise-core",
        description="Candidate where benchmark runs last",
        diff_content="sample diff content for v2.3.0",
    )
    from alpha.enterprise.models import CryptographicSignature

    candidate.signatures = [CryptographicSignature(signatory_role=role, signatory_bot="untrusted", signature_hash="invalid", payload_digest=candidate.diff_hash, verified=True) for role in ("CTO_ARCH", "CISO_ASTRA", "SWE_BENCHMARK")]
    candidate.status = "multi_sig_verified"
    candidate.holdout_passed = True
    candidate.architecture_approved = True
    candidate.security_scan_passed = True
    with pytest.raises(PermissionError):
        council.promote_release_zero_downtime(candidate.release_id)
    assert candidate.status == "staged"
    score = council.run_holdout_benchmark(candidate.release_id)
    assert score >= 90.0
    assert candidate.status == "preview"
    assert candidate.signatures == []
    assert not candidate.holdout_passed
    assert not candidate.architecture_approved
    assert not candidate.security_scan_passed
    assert candidate.promoted_at is None
    assert council.get_active_release() is None


def test_dag_sprint_dependency_isolation_between_layers():
    """Verify that a task depending on another does not execute in the same tick as its prerequisite."""
    pipeline = MissionToSprintPipeline()
    epics = pipeline.decompose_strategic_mission("msn-dag-dep", "Layer dependency isolation test")
    spec = pipeline.synthesize_technical_spec(epics[0].epic_id)
    sprint = pipeline.compile_dynamic_dag_sprint(spec.spec_id)

    # In sprint: t1 has no deps. t2 depends on t1.
    # In tick 1: only t1 should execute. t2 must NOT execute in tick 1.
    step1 = pipeline.step_sprint_dag(sprint.sprint_id)
    assert len(step1["advanced_tasks"]) == 1
    t1_task = sprint.tasks[0]
    t2_task = sprint.tasks[1]
    assert t1_task.status == "completed"
    assert t2_task.status == "pending"

    # In tick 2: now t2 is eligible and should execute.
    step2 = pipeline.step_sprint_dag(sprint.sprint_id)
    assert t2_task.task_id in step2["advanced_tasks"]
    assert t2_task.status == "completed"


def test_treasury_roi_velocity_calculation_accuracy():
    """Verify ROI velocity does not double count tasks completed."""
    treasury = DepartmentTokenTreasury()
    alloc = treasury.get_allocation("dept-engineering")
    assert alloc is not None

    # Step 1: burn 10000 tokens with 2 tasks completed
    alloc, _ = treasury.record_token_burn("dept-engineering", tokens_burned=10000, tasks_completed=2)
    # ROI velocity = (2 tasks / 10000 tokens) * 10000 = 2.0
    assert alloc.roi_velocity == 2.0


def test_multi_cycle_stagnation_recovery_and_perpetual_mission_progression():
    """Verify stagnation recovery advances through all epics and spawns next-phase mission."""
    coordinator = EnterpriseHeartbeatCoordinator()

    # Step through until Genesis mission sprint completes
    sprints = coordinator.pipeline.list_sprints()
    assert len(sprints) >= 1

    for _ in range(6):
        coordinator.step_heartbeat_cycle()

    # Sprints should be completed, and stagnation recovery triggers new sprint
    all_sprints = coordinator.pipeline.list_sprints()
    assert len(all_sprints) >= 2


def test_ast_boundary_scanner_attribute_calls_and_code_snippet():
    """Verify AST scanner detects dangerous attribute calls and handles snippet scanning."""
    disc = get_discovery_and_optimization_engine()

    # Safe snippet
    safe_code = "def add(a, b):\n    return a + b\n"
    passed, violations = disc.scan_code_snippet(safe_code)
    assert passed is True
    assert len(violations) == 0

    # Dangerous snippet with os.system
    unsafe_code = "import os\nos.system('whoami')\n"
    passed, violations = disc.scan_code_snippet(unsafe_code)
    assert passed is False
    assert any("os.system" in v["type"] for v in violations)

    # Dangerous snippet with subprocess.Popen
    unsafe_subp = "import subprocess\nsubprocess.Popen(['echo', 'pwned'])\n"
    passed, violations = disc.scan_code_snippet(unsafe_subp)
    assert passed is False
    assert any("subprocess.Popen" in v["type"] for v in violations)


def test_enterprise_gateway_api_both_prefixes():
    """Verify endpoints respond identically under /api/enterprise and /api/gateway/enterprise."""
    app = FastAPI()
    app.include_router(enterprise_router)
    app.include_router(enterprise_gateway_router)
    client = TestClient(app)

    # Under /api/enterprise
    res1 = client.get("/api/enterprise/hierarchy")
    assert res1.status_code == 200
    assert "csuite" in res1.json()

    # Under /api/gateway/enterprise
    res2 = client.get("/api/gateway/enterprise/hierarchy")
    assert res2.status_code == 200
    assert "csuite" in res2.json()
