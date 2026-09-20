"""Comprehensive tests for AI Workforce OS Phase 3:
1. Human Approval Queue & Dual-Key Authorization
2. Autonomous Self-Healing Test & Debug Loop
3. Collaborative Living Specification Engine
4. Multi-Agent Pre-Merge Audit Council
5. Cross-Project Heuristic Knowledge Index
6. Gateway Approvals API Endpoints
"""

import pytest
from pathlib import Path
import tempfile
from fastapi.testclient import TestClient

from alpha.projects.approval_queue import (
    ApprovalQueue,
    ApprovalRequest,
    get_approval_queue,
)
from alpha.projects.self_healing_runner import (
    SelfHealingTestRunner,
    TestRunResult,
)
from alpha.projects.living_spec import (
    LivingSpecEngine,
    LivingSpec,
    LivingSpecSection,
)
from alpha.projects.audit_council import (
    AuditCouncil,
    AuditVerdict,
)
from alpha.projects.knowledge_index import (
    CrossProjectKnowledgeIndex,
    sanitize_text,
)
from alpha.projects.contracts import (
    ContractGatekeeper,
    DefinitionOfDone,
)
from alpha.projects.postmortem import ProjectPostmortemEngine


def test_human_approval_queue_lifecycle(tmp_path: Path):
    storage = tmp_path / "approvals.json"
    queue = ApprovalQueue("proj_delta", storage_path=storage)

    # 1. Bot requests approval for destructive production deploy
    req = queue.request_approval(
        bot_name="coder",
        action_type="deploy_production",
        risk_level="critical",
        details={"environment": "production", "target_tag": "v2.1.0"},
        diff_preview="--- a/config.py\n+++ b/config.py\n@@ -1 +1 @@\n-DEBUG = True\n+DEBUG = False",
    )

    assert req.status == "pending"
    assert req.risk_level == "critical"
    assert "DEBUG = False" in req.diff_preview

    # 2. Inspect pending
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].request_id == req.request_id

    # 3. Human resolves request (Approved)
    resolved = queue.resolve_request(
        req.request_id,
        approved=True,
        resolved_by="lead_devops",
        comment="Production verification tests passed. Signed off.",
    )
    assert resolved.status == "approved"
    assert resolved.resolved_by == "lead_devops"
    assert resolved.resolved_at is not None

    # 4. Attempt to re-resolve should raise ValueError
    with pytest.raises(ValueError):
        queue.resolve_request(req.request_id, approved=False)

    # 5. Second request rejected
    req2 = queue.request_approval(
        bot_name="tester",
        action_type="drop_database",
        risk_level="critical",
    )
    rejected = queue.resolve_request(
        req2.request_id,
        approved=False,
        resolved_by="dba_admin",
        comment="Cannot drop database in active sprint.",
    )
    assert rejected.status == "rejected"
    assert len(queue.list_pending()) == 0


def test_self_healing_runner(tmp_path: Path):
    runner = SelfHealingTestRunner("proj_echo")

    # Mock test state
    test_runs_count = 0

    def mock_test():
        nonlocal test_runs_count
        test_runs_count += 1
        if test_runs_count == 1:
            return TestRunResult(
                passed=False,
                stdout="test_auth.py: FAILED",
                stderr="AssertionError: Expected 200 OK, got 401 Unauthorized",
                exit_code=1,
            )
        return TestRunResult(passed=True, stdout="All 10 tests passed!", exit_code=0)

    def mock_patch_synthesizer(result, heuristics):
        return "diff --git a/auth.py b/auth.py\n+add_bearer_header()"

    # 1. Create a contract for this task
    contracts_file = tmp_path / "contracts.json"
    gk = ContractGatekeeper("proj_echo", storage_path=contracts_file)
    gk.create_contract(
        task_id="TASK-HEAL-1",
        title="Fix authentication header",
        assignee_bot="coder",
        definition_of_done=DefinitionOfDone(required_evidence=["tests_passed"]),
    )

    # 2. Run self healing loop
    outcome = runner.run_with_self_healing(
        task_id="TASK-HEAL-1",
        bot_name="coder",
        test_fn=mock_test,
        patch_synthesizer=mock_patch_synthesizer,
        max_attempts=3,
        auto_attach_contract_evidence=False,
    )

    assert outcome.success is True
    assert outcome.attempts == 2
    assert len(outcome.history) == 2
    assert outcome.history[0]["passed"] is False
    assert outcome.history[1]["passed"] is True


def test_living_specification_engine(tmp_path: Path):
    engine = LivingSpecEngine("proj_foxtrot", base_dir=tmp_path)

    # 1. Verify default sections initialized
    spec = engine.get_spec()
    assert "overview" in spec.sections
    assert "api_contracts" in spec.sections

    # 2. Update API contracts section
    sec = engine.update_section(
        section_key="api_contracts",
        title="3. API Interfaces & Data Contracts",
        content="```typescript\nexport interface UserSession {\n  id: string;\n  role: 'admin' | 'user';\n}\n```",
        author_bot="architect",
    )
    assert sec.version == 2
    assert sec.last_author_bot == "architect"

    # 3. Verify Markdown file written
    md_file = engine.get_markdown_path()
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    assert "# Project Living Specification" in content
    assert "export interface UserSession" in content
    assert "Author: @architect" in content


def test_audit_council_security_check(tmp_path: Path):
    council = AuditCouncil("proj_golf")

    # 1. Clean code snippet
    clean_code = """
def calculate_metrics(values: list[float]) -> float:
    return sum(values) / max(1, len(values))
"""
    clean_verdict = council.audit_code(clean_code)
    assert clean_verdict.passed is True
    assert clean_verdict.score == 1.0
    assert clean_verdict.council_signatures["security"] is True
    assert clean_verdict.council_signatures["reviewer"] is True

    # 2. Code with leaked token
    leaked_token_code = """
API_KEY = "AKIA1234567890ABCDEF"
def connect_aws():
    pass
"""
    leak_verdict = council.audit_code(leaked_token_code)
    assert leak_verdict.passed is False
    assert any("Security Violation" in issue for issue in leak_verdict.blocking_issues)
    assert any("Exposed API key" in issue or "AWS Access Key" in issue for issue in leak_verdict.blocking_issues)
    assert leak_verdict.council_signatures["security"] is False

    # 3. Code with unsafe eval
    unsafe_code = """
def run_dynamic(user_input):
    return eval(user_input)
"""
    unsafe_verdict = council.audit_code(unsafe_code)
    assert unsafe_verdict.passed is False
    assert any("eval()" in issue for issue in unsafe_verdict.blocking_issues)


def test_cross_project_knowledge_index(tmp_path: Path):
    storage = tmp_path / "global_index.json"
    index = CrossProjectKnowledgeIndex(storage_path=storage)

    # 1. Index heuristic from Project Alpha
    index.index_heuristic(
        origin_project="proj_alpha",
        origin_bot="coder",
        title="SQLite busy timeout resolution",
        problem_statement="database is locked under concurrent asyncio write tasks",
        solution_heuristic="Set PRAGMA busy_timeout = 5000 and enable WAL mode on database connect.",
        tags=["sqlite", "concurrency", "database"],
    )

    # 2. Index heuristic from Project Beta
    index.index_heuristic(
        origin_project="proj_beta",
        origin_bot="frontend",
        title="Next.js 15 route segment params fix",
        problem_statement="params Promise in dynamic routes throws sync access error in Next.js 15",
        solution_heuristic="Always await params before accessing params.id in async page components.",
        tags=["nextjs", "react", "frontend"],
    )

    # 3. Query index for sqlite lock
    results_sqlite = index.search("database locked concurrent sqlite")
    assert len(results_sqlite) >= 1
    assert "PRAGMA busy_timeout" in results_sqlite[0].solution_heuristic
    assert results_sqlite[0].origin_project_id == "proj_alpha"

    # 4. Query index for nextjs
    results_next = index.search("Next.js 15 params promise")
    assert len(results_next) >= 1
    assert "await params" in results_next[0].solution_heuristic

    # 5. Test sanitization helper
    raw_secret = "Bearer token='eyJhbGciOiJIUzI1NiIsIn...' and file is C:\\Users\\Administrator\\secret"
    sanitized = sanitize_text(raw_secret)
    assert "[REDACTED]" in sanitized


def test_gateway_approval_endpoints():
    from app.gateway.app import app

    with TestClient(app) as client:
        # Create a test project via API
        proj_res = client.post("/api/projects", json={"name": "ApprovalTestProject", "instructions": "Testing approvals"})
        assert proj_res.status_code in (200, 201)
        proj_id = proj_res.json()["id"]

        # Queue an approval request directly
        queue = get_approval_queue(proj_id)
        req = queue.request_approval(
            bot_name="coder",
            action_type="deploy_production",
            risk_level="high",
            details={"version": "v1.0.0"},
        )

        # 1. GET /api/projects/{id}/approvals
        list_res = client.get(f"/api/projects/{proj_id}/approvals")
        assert list_res.status_code == 200
        approvals = list_res.json()["approvals"]
        assert len(approvals) >= 1
        assert approvals[0]["request_id"] == req.request_id

        # 2. Check war room includes pending approval
        war_room_res = client.get(f"/api/projects/{proj_id}/war-room")
        assert war_room_res.status_code == 200
        assert "pending_approvals" in war_room_res.json()
        assert len(war_room_res.json()["pending_approvals"]) >= 1

        # 3. POST /api/projects/{id}/approvals/{request_id}/resolve
        resolve_res = client.post(
            f"/api/projects/{proj_id}/approvals/{req.request_id}/resolve",
            json={"approved": True, "resolved_by": "qa_lead", "comment": "Verified and approved."},
        )
        assert resolve_res.status_code == 200
        assert resolve_res.json()["status"] == "approved"
        assert resolve_res.json()["resolved_by"] == "qa_lead"
